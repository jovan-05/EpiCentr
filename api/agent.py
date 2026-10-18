"""Keystone's agent: a plain tool-calling loop (no framework) + the grounding guard + an incident CLI.

    .venv\\Scripts\\python.exe -m api.agent incident payments-api              # LLM picks the tools (max 8 steps)
    .venv\\Scripts\\python.exe -m api.agent incident payments-api --mode playbook   # fixed tool order, 1 LLM call
    add --no-act to skip writing the incident, --json for the full result, --graph <name> for another graph

Flow (agent mode):
  1. The LLM gets the incident playbook and the read tools, and calls tools (each one is a Cypher query).
  2. Every tool returns `keys` (e.g. "Person:marco"); the union is the evidence this run actually saw.
  3. The LLM answers with JSON: {summary, claims:[{text, keys, tool}], actions:[...]}.
  4. Grounding guard: a claim survives only if it has keys and ALL of them were returned by a tool in this run.
     Dropped claims are logged and returned, so nothing unbacked reaches the incident.
  5. Actions run server-side only if grounded: open_incident (assignee must be a person the tools returned),
     then cite every surviving claim on the incident (CITES edges with stable keys).
If the model can't do tool calling (free models vary), agent mode falls back to playbook mode.
"""

import argparse
import inspect
import json
import logging
import re
import sys
import time

from api import llm
from api.db import clock, graph
from api.tools import (blast_radius, check_decision, cite, current_owners, decision_trace, open_incident,
                       prior_warnings, stamp_chains, suspect_changes, who_knows)

log = logging.getLogger("keystone.agent")
_real_chat = llm.chat
MAX_STEPS = 8
RESULT_CHARS = 3500   # cap per tool result sent to the LLM (free models have small budgets)

# Read tools the LLM may call. check_decision runs with log=False inside the agent.
AGENT_TOOLS = {
    "suspect_changes": suspect_changes, "blast_radius": blast_radius, "current_owners": current_owners,
    "who_knows": who_knows, "decision_trace": decision_trace, "prior_warnings": prior_warnings,
    "stamp_chains": stamp_chains, "check_decision": check_decision,
}


def _spec(name, description, props, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props, "required": required}}}


SVC = {"type": "string", "description": "service name, e.g. payments-api"}
TOOL_SPECS = [
    _spec("suspect_changes", "PRs merged on the service in the last hours: author (agent or human), who prompted the agent, "
          "how it was reviewed (rubber_stamped = approved in < 2 min with no comments).",
          {"service": SVC, "hours": {"type": "integer", "description": "look-back window, default 72"}}, ["service"]),
    _spec("blast_radius", "Services that break when this one is down (upstream callers), with hops and owner team.",
          {"service": SVC}, ["service"]),
    _spec("current_owners", "Owner team, its members, and this week's on-call. on_call_departed=true means the "
          "schedule points at someone who left.", {"service": SVC}, ["service"]),
    _spec("who_knows", "Active people who actually understand the service (comprehension score from PRs, reviews, "
          "incidents), best first, with recent PRs as evidence.", {"service": SVC, "k": {"type": "integer"}}, ["service"]),
    _spec("decision_trace", "Architecture decisions affecting the service: why it is built this way, who decided, "
          "the chat message it came from, rejected options.", {"service": SVC}, ["service"]),
    _spec("prior_warnings", "Chat messages and still-open tickets about the service matching search terms. Terms use "
          "full-text syntax: | is OR, * is prefix, e.g. 'timeout|circuit*|slow*|%risk%'.",
          {"service": SVC, "terms": {"type": "string"}}, ["service", "terms"]),
    _spec("stamp_chains", "Agent-written PRs on the service that a human approved in under 2 minutes with no comments.",
          {"service": SVC}, ["service"]),
    _spec("check_decision", "Is a proposed change something the team already rejected? Returns verdict block/warn/ok.",
          {"service": SVC, "proposal": {"type": "string"}}, ["service", "proposal"]),
]

ANSWER_FORMAT = """Answer with ONE JSON object and nothing else:
{"summary": "3-5 sentences for the incident channel",
 "claims": [{"text": "one fact", "keys": ["Label:key", "..."], "tool": "tool that returned it"}],
 "actions": [{"type": "open_incident", "assignee": "<handle>", "title": "...", "severity": "sev1", "reason": "..."}]}
Rules: every claim must copy its keys EXACTLY from the "keys" of a tool result (e.g. "Person:marco", "PR:PR-4471").
A claim without keys from a tool result will be deleted. Never invent people, PRs, decisions or numbers.
The assignee MUST be the first person in the who_knows rows (the one who actually understands the service),
not the on-call person, not the PR author or reviewer, never someone departed. Include a claim about them with their keys."""

SYSTEM = f"""You are Keystone, an incident commander that runs on a company knowledge graph.
You may only state facts returned by tools. Each tool runs a Cypher query on the graph.

Incident playbook (follow it, in this order):
1. suspect_changes: what changed right before it broke? Note agent-written and rubber-stamped PRs.
2. blast_radius: what else breaks.
3. current_owners: who does the org chart say? Check on_call_departed.
4. who_knows: who ACTUALLY understands it now. Assign the incident to the top person, never to someone departed.
5. decision_trace: why is it built this way?
6. prior_warnings: did anyone warn about this? Use risk terms such as "timeout|circuit*|slow*|%risk%".
Then stop calling tools and answer.

{ANSWER_FORMAT}"""

PLAYBOOK = [("suspect_changes", {}), ("blast_radius", {}), ("current_owners", {}), ("who_knows", {}),
            ("decision_trace", {}), ("prior_warnings", {"terms": "timeout|circuit*|slow*|%risk%"})]


class Run:
    """Everything one incident run saw and did. `on_event` (optional) receives each step as it happens,
    so the API can stream the agent's reasoning to the UI."""

    def __init__(self, g, on_event=None):
        self.g, self.steps, self.keys, self.results, self.llm_calls, self.models = g, [], [], {}, 0, []
        self.on_event = on_event or (lambda ev: None)

    def call_tool(self, name, args):
        if name not in AGENT_TOOLS:
            return {"error": f"unknown tool {name}"}
        fn = AGENT_TOOLS[name]
        allowed = set(inspect.signature(fn).parameters) - {"g", "log"}
        args = {k: v for k, v in (args or {}).items() if k in allowed}
        extra = {"log": False} if name == "check_decision" else {}
        try:
            res = fn(g=self.g, **args, **extra)
        except Exception as ex:  # noqa: BLE001  (a bad argument from the LLM must not crash the run)
            return {"error": f"{type(ex).__name__}: {ex}"}
        step = {"tool": name, "args": args, "keys": res["keys"], "rows": len(res["rows"]),
                "ms": res["ms"], "cypher": res["cypher"]}
        self.steps.append(step)
        self.on_event({"type": "step", "n": len(self.steps), **step, "data": res["rows"][:8]})
        self.results[name] = res
        for k in res["keys"]:
            if k not in self.keys:
                self.keys.append(k)
        return res

    def chat(self, messages, tools=None):
        self.llm_calls += 1
        self.on_event({"type": "thinking", "llm_call": self.llm_calls})
        msg = llm.chat(messages, tools)
        self.models.append(msg.get("model"))
        return msg


def compact(res):
    """What the LLM sees of a tool result: rows and keys only, size-capped."""
    if "error" in res:
        return json.dumps(res)
    out = {"rows": res["rows"][:8], "keys": res["keys"]}
    for k in ("verdict", "reason", "orphaned"):
        if k in res:
            out[k] = res[k]
    text = json.dumps(out, default=str)
    return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + '..."(truncated)"'


def parse_json(text):
    """The first JSON object in the model's text (models often wrap it in ``` fences or prose)."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    while start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
        start = text.find("{", start + 1)
    return None


def ground(answer, run_keys, called_tools):
    """The grounding guard. Keep a claim only if it cites keys and every key was returned by a tool in this run."""
    kept, dropped = [], []
    seen = set(run_keys)
    for c in answer.get("claims") or []:
        keys = [k for k in (c.get("keys") or []) if isinstance(k, str)]
        missing = [k for k in keys if k not in seen]
        why = ("no keys" if not keys else f"keys not returned by any tool: {missing}" if missing
               else f"tool {c.get('tool')!r} was not called" if c.get("tool") and c.get("tool") not in called_tools
               else None)
        if why:
            dropped.append({**c, "dropped_because": why})
            log.warning("grounding guard dropped claim %r: %s", c.get("text"), why)
        else:
            kept.append({"text": c.get("text", ""), "keys": keys, "tool": c.get("tool")})
    return kept, dropped


def execute_actions(run, service, answer, claims, act):
    """Run the agent's proposed actions, only when grounded. Returns what happened."""
    done = []
    backed = {k for c in claims for k in c["keys"]}
    for a in answer.get("actions") or []:
        if a.get("type") != "open_incident":
            done.append({**a, "status": "skipped: unsupported action"})
            continue
        assignee = a.get("assignee", "").lstrip("@")
        experts = [r["person"] for r in run.results.get("who_knows", {}).get("rows", [])]
        if f"Person:{assignee}" not in backed or (experts and assignee not in experts):
            if not experts:
                done.append({**a, "status": f"refused: @{assignee} is not backed by a grounded claim / who_knows"})
                continue
            # Safety net: the graph's answer wins over the model's guess. Recorded so the UI can show it.
            a = {**a, "reassigned_from": assignee, "reason": f"@{assignee} not backed by who_knows; "
                 f"assigned to its top person instead. {a.get('reason', '')}".strip()}
            assignee = experts[0]
            log.warning("action corrected: @%s -> @%s (top who_knows)", a["reassigned_from"], assignee)
        downstream = [r["service"] for r in run.results.get("blast_radius", {}).get("rows", [])]
        if not act:
            done.append({**a, "assignee": assignee, "downstream": downstream, "status": "planned (--no-act)"})
            continue
        inc = open_incident(service, assignee, a.get("title") or f"{service} down", brief=answer.get("summary", ""),
                            severity=a.get("severity", "sev1"), reason=a.get("reason", ""), downstream=downstream, g=run.g)
        inc_id = inc["rows"][0]["id"]
        cited = cite(inc_id, [{"claim": c["text"], "keys": c["keys"]} for c in claims], g=run.g) if claims else None
        done.append({**a, "assignee": assignee, "id": inc_id, "downstream": downstream, "status": "done",
                     "cited": cited["rows"][0]["cited"] if cited else 0})
    return done


def _alert(service, g):
    now, _ = clock(g)
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now / 1000))
    return f"ALERT {when}: {service} is DOWN (error rate 100%). Investigate with the playbook, then answer."


def _agent_loop(run, service):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _alert(service, run.g)}]
    for _ in range(MAX_STEPS):
        msg = run.chat(messages, TOOL_SPECS)
        calls = msg.get("tool_calls") or []
        if not calls:
            answer = parse_json(msg.get("content"))
            if answer is None and not run.steps:
                raise llm.LLMError("model answered without calling any tool")
            if answer is None:   # one nudge for the JSON
                messages += [{"role": "assistant", "content": msg.get("content") or ""},
                             {"role": "user", "content": "Return ONLY the JSON object now."}]
                answer = parse_json(run.chat(messages).get("content"))
            if answer is None:
                raise llm.LLMError("model did not return the JSON answer")
            return answer
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for i, c in enumerate(calls):
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            res = run.call_tool(c["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": c.get("id") or f"call_{len(run.steps)}_{i}",
                             "content": compact(res)})
    messages.append({"role": "user", "content": "Step limit reached. " + ANSWER_FORMAT})
    answer = parse_json(run.chat(messages).get("content"))
    if answer is None:
        raise llm.LLMError("model did not return the JSON answer after the step limit")
    return answer


def _playbook(run, service):
    """Fixed tool order, then one LLM call to write the grounded answer."""
    blocks = []
    for name, extra in PLAYBOOK:
        res = run.call_tool(name, {"service": service, **extra})
        blocks.append(f"### {name}\n{compact(res)}")
    messages = [{"role": "system", "content": SYSTEM.split("Incident playbook")[0] + ANSWER_FORMAT},
                {"role": "user", "content": _alert(service, run.g) + "\n\nTool results:\n\n" + "\n\n".join(blocks)}]
    answer = parse_json(run.chat(messages).get("content"))
    if answer is None:
        raise llm.LLMError("model did not return the JSON answer")
    return answer


def run_incident(service, mode="agent", act=True, g=None, on_event=None):
    """Investigate an outage on `service`. Returns summary, grounded claims, dropped claims, actions and the steps.
    on_event(dict) gets live events: thinking, step, fallback, grounded, action."""
    if llm.chat is _real_chat:   # tests swap in a fake LLM and need no key
        llm.ensure_configured()
    run = Run(g or graph(), on_event)
    t = time.time()
    used = mode
    if mode == "agent":
        try:
            answer = _agent_loop(run, service)
        except llm.LLMError as ex:
            log.warning("agent mode failed (%s); falling back to playbook mode", ex)
            run.on_event({"type": "fallback", "reason": str(ex)})
            run = Run(run.g, on_event)
            used, answer = "playbook (fallback)", _playbook(run, service)
    else:
        answer = _playbook(run, service)
    claims, dropped = ground(answer, run.keys, {s["tool"] for s in run.steps})
    run.on_event({"type": "grounded", "kept": len(claims), "dropped": dropped})
    actions = execute_actions(run, service, answer, claims, act)
    for a in actions:
        run.on_event({"type": "action", "action": a})
    return {"service": service, "mode": used, "models": sorted(set(filter(None, run.models))),
            "llm_calls": run.llm_calls, "seconds": round(time.time() - t, 1), "summary": answer.get("summary", ""),
            "claims": claims, "dropped": dropped, "actions": actions, "steps": run.steps}


def print_result(r):
    print(f"\nKeystone incident run: {r['service']}  mode={r['mode']}  models={r['models']}  "
          f"llm_calls={r['llm_calls']}  {r['seconds']}s")
    print("\nSteps (each one a Cypher query on FalkorDB):")
    for i, s in enumerate(r["steps"], 1):
        print(f"  {i}. {s['tool']}({', '.join(f'{k}={v!r}' for k, v in s['args'].items())})  "
              f"{s['rows']} rows, {s['ms']} ms, {len(s['keys'])} keys")
    print(f"\nSummary:\n  {r['summary']}")
    print(f"\nClaims kept by the grounding guard ({len(r['claims'])}):")
    for c in r["claims"]:
        print(f"  ✓ {c['text']}\n      keys={c['keys']} tool={c['tool']}")
    print(f"\nClaims DROPPED by the grounding guard ({len(r['dropped'])}):")
    for c in r["dropped"]:
        print(f"  ✗ {c.get('text')}\n      {c['dropped_because']}")
    print("\nActions:")
    for a in r["actions"] or [{"status": "none proposed"}]:
        print(f"  - {a}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m api.agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    inc = sub.add_parser("incident", help="investigate an outage and open an incident")
    inc.add_argument("service")
    inc.add_argument("--mode", choices=["agent", "playbook"], default="agent")
    inc.add_argument("--no-act", action="store_true", help="don't write the incident to the graph")
    inc.add_argument("--json", action="store_true", help="print the full result as JSON")
    inc.add_argument("--graph", default=None)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        r = run_incident(a.service, mode=a.mode, act=not a.no_act, g=graph(a.graph) if a.graph else None)
    except llm.LLMError as ex:
        sys.exit(f"LLM error: {ex}")
    if a.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print_result(r)


if __name__ == "__main__":
    main()
