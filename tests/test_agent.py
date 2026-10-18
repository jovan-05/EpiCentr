"""Agent loop, grounding guard and actions, with a scripted fake LLM (no API key, no network)."""

import json

import pytest

from api import agent, llm
from api.db import rows_of


def tool_call(name, **args):
    return {"id": f"c_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def fake_llm(monkeypatch, turns):
    """Replace llm.chat with a queue of scripted assistant messages; records what it was sent."""
    sent = []

    def chat(messages, tools=None, temperature=0.1):
        sent.append({"messages": list(messages), "tools": tools})
        msg = turns.pop(0)
        if isinstance(msg, Exception):
            raise msg
        return {"model": "fake:free", **msg}

    monkeypatch.setattr(llm, "chat", chat)
    return sent


ANSWER = {
    "summary": "payments-api is down after PR-4471 removed the fraud-svc timeout. Marco owns the fix.",
    "claims": [
        {"text": "PR-4471 by codegen-bot was approved by sam in 38 s", "keys": ["PR:PR-4471", "Person:sam"], "tool": "suspect_changes"},
        {"text": "marco understands payments-api best", "keys": ["Person:marco", "Service:payments-api"], "tool": "who_knows"},
        {"text": "bob from finance approved it", "keys": ["Person:bob"], "tool": "who_knows"},          # invented person
        {"text": "it was probably a DNS issue", "keys": []},                                         # no evidence at all
    ],
    "actions": [{"type": "open_incident", "assignee": "@marco", "title": "payments-api down", "severity": "sev1",
                 "reason": "top who_knows; on-call priya departed"}],
}


def test_grounding_guard_unit():
    kept, dropped = agent.ground(ANSWER, ["PR:PR-4471", "Person:sam", "Person:marco", "Service:payments-api"],
                                 {"suspect_changes", "who_knows"})
    assert [c["text"] for c in kept] == [ANSWER["claims"][0]["text"], ANSWER["claims"][1]["text"]]
    assert [d["dropped_because"] for d in dropped] == ["keys not returned by any tool: ['Person:bob']", "no keys"]
    # a claim attributed to a tool that never ran is dropped even if its keys exist
    kept, dropped = agent.ground({"claims": [ANSWER["claims"][0]]}, ["PR:PR-4471", "Person:sam"], {"who_knows"})
    assert not kept and "was not called" in dropped[0]["dropped_because"]


def test_parse_json_handles_fences_and_prose():
    assert agent.parse_json('Sure!\n```json\n{"summary": "x", "claims": []}\n```') == {"summary": "x", "claims": []}
    assert agent.parse_json("no json here") is None


def test_agent_mode_end_to_end(wg, monkeypatch):
    sent = fake_llm(monkeypatch, [
        {"content": None, "tool_calls": [tool_call("suspect_changes", service="payments-api")]},
        {"content": None, "tool_calls": [tool_call("current_owners", service="payments-api"),
                                         tool_call("who_knows", service="payments-api")]},
        {"content": None, "tool_calls": [tool_call("blast_radius", service="payments-api", bogus_arg=1)]},
        {"content": "```json\n" + json.dumps(ANSWER) + "\n```"},
    ])
    r = agent.run_incident("payments-api", mode="agent", g=wg)

    assert r["mode"] == "agent" and r["llm_calls"] == 4
    assert [s["tool"] for s in r["steps"]] == ["suspect_changes", "current_owners", "who_knows", "blast_radius"]
    assert sent[0]["tools"] and "who_knows" in json.dumps(sent[0]["tools"])
    tool_msgs = [m for m in sent[1]["messages"] if m["role"] == "tool"]
    assert "PR-4471" in tool_msgs[0]["content"]                     # the LLM really saw the graph result
    assert len(r["claims"]) == 2 and len(r["dropped"]) == 2         # bob + DNS dropped by the guard

    act = r["actions"][0]
    assert act["status"] == "done" and act["id"] == "INC-042" and act["assignee"] == "marco"
    assert "checkout-api" in act["downstream"] and act["cited"] == 4
    cites = rows_of(wg.ro_query("MATCH (:Incident {id:'INC-042'})-[c:CITES]->(n) RETURN c.claim AS claim"))
    assert len(cites) == 4 and not any("bob" in c["claim"] for c in cites)   # dropped claims never reach the graph


def test_ungrounded_assignee_is_corrected_to_top_who_knows(wg, monkeypatch):
    # the live free model once proposed @sam (the rubber-stamp reviewer); the graph's answer must win
    bad = {**ANSWER, "actions": [{"type": "open_incident", "assignee": "priya", "title": "x"}]}
    fake_llm(monkeypatch, [
        {"content": None, "tool_calls": [tool_call("who_knows", service="payments-api")]},
        {"content": json.dumps(bad)},
    ])
    r = agent.run_incident("payments-api", mode="agent", act=True, g=wg)
    a = r["actions"][0]
    assert a["status"] == "done" and a["assignee"] == "marco" and a["reassigned_from"] == "priya"
    assert not rows_of(wg.ro_query("MATCH (i:Incident)-[:ASSIGNED_TO]->(:Person {handle:'priya'}) "
                                   "WHERE i.status = 'open' RETURN i.id AS id"))


def test_action_refused_without_who_knows(wg, monkeypatch):
    bad = {**ANSWER, "actions": [{"type": "open_incident", "assignee": "priya", "title": "x"}]}
    fake_llm(monkeypatch, [
        {"content": None, "tool_calls": [tool_call("suspect_changes", service="payments-api")]},
        {"content": json.dumps(bad)},
    ])
    r = agent.run_incident("payments-api", mode="agent", act=True, g=wg)
    assert r["actions"][0]["status"].startswith("refused")


def test_falls_back_to_playbook_when_model_wont_call_tools(wg, monkeypatch):
    sent = fake_llm(monkeypatch, [
        {"content": "I think it's the database."},          # agent mode: no tool call -> fallback
        {"content": json.dumps({**ANSWER, "actions": []})},  # playbook: the single answer call
    ])
    r = agent.run_incident("payments-api", mode="agent", g=wg)
    assert r["mode"] == "playbook (fallback)" and r["llm_calls"] == 1
    assert [s["tool"] for s in r["steps"]] == [n for n, _ in agent.PLAYBOOK]
    assert "Lena" in sent[-1]["messages"][1]["content"] or "lena" in sent[-1]["messages"][1]["content"]
    assert len(r["claims"]) == 2


def test_llm_error_without_key(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    with pytest.raises(llm.LLMError, match="paste your key"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_only_free_models_allowed(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "x")
    monkeypatch.setattr(llm.config, "LLM_MODELS", ["some/paid-model"])
    with pytest.raises(llm.LLMError, match="only free models"):
        llm.chat([{"role": "user", "content": "hi"}])
