"use client";
// Incident (hero 1) + learning loop. "Simulate outage" streams the agent's steps (SSE); each step is played as a
// camera hop with a caption while the LLM writes the brief. Then: grounded claims (hover a chip to re-fly to its
// evidence), action cards, Resolve → lesson card (editable) → Approve → the guard now blocks the mistake.

import { useCallback, useEffect, useRef, useState } from "react";
import { postJSON, streamIncident } from "@/lib/api";
import { captionFor, idOf } from "@/lib/graph";
import type { Action, IncidentResult, Lesson, Step, ToolResult } from "@/lib/types";
import type { Ops } from "./ops";
import { Button, Pill, Section, Spinner } from "./ui";

const HOP_MS = 3200;
const GUARD_PROPOSAL = "remove the timeout in the fraud client, it's adding latency";

type Phase = "idle" | "running" | "done" | "error";
type LessonPhase = "none" | "drafting" | "draft" | "approving" | "approved";

export default function IncidentPanel({ ops, setPollPaused }: {
  ops: Ops; setPollPaused: (p: boolean) => void;
}) {
  const opsRef = useRef(ops);
  useEffect(() => { opsRef.current = ops; });

  const [service, setService] = useState("payments-api");
  const [agentMode, setAgentMode] = useState<"playbook" | "agent">("playbook");
  const [phase, setPhase] = useState<Phase>("idle");
  const [steps, setSteps] = useState<Step[]>([]);
  const [played, setPlayed] = useState(0);
  const [thinking, setThinking] = useState(false);
  const [result, setResult] = useState<IncidentResult | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [resolver, setResolver] = useState("");
  const [rootCause, setRootCause] = useState("");
  const [resolved, setResolved] = useState<{ before: number; after: number; person: string; service: string } | null>(null);
  const [lessonPhase, setLessonPhase] = useState<LessonPhase>("none");
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [decisionId, setDecisionId] = useState("");
  const [guard, setGuard] = useState<ToolResult | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const queue = useRef<Step[]>([]);
  const finalized = useRef(false);
  const briefRef = useRef<HTMLDivElement>(null);
  const lessonRef = useRef<HTMLDivElement>(null);
  const guardRef = useRef<HTMLDivElement>(null);
  // Bring each new result into view in the side panel as it appears.
  const reveal = (r: React.RefObject<HTMLDivElement | null>) =>
    setTimeout(() => r.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);

  const services = (ops.graph?.nodes ?? []).filter((n) => n.type === "Service" && n.tier === 1).map((n) => n.label).sort();
  const action = result?.actions.find((a) => a.type === "open_incident");
  const incidentId = action?.status === "done" ? action.id : undefined;

  // Elapsed timer while running (the LLM part takes ~40-90 s on free models).
  useEffect(() => {
    if (phase !== "running") return;
    const t = setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 500);
    return () => clearInterval(t);
  }, [phase, startedAt]);

  const finalize = useCallback(async (r: IncidentResult) => {
    const o = opsRef.current;
    finalized.current = true;
    await o.refreshMap();
    const a = r.actions.find((x) => x.type === "open_incident");
    const ids = [`Service:${r.service}`, ...(a?.assignee ? [`Person:${a.assignee}`] : []), ...(a?.id ? [`Incident:${a.id}`] : [])];
    setTimeout(() => {
      o.highlight(ids);
      o.flyTo(ids, 1600);
      o.caption(a?.status === "done"
        ? `${a.id} opened · assigned to @${a.assignee} · ${r.claims.length} claims, every one backed by the graph`
        : `Investigation done · ${r.claims.length} grounded claims`);
    }, 700);
    setResolver(a?.assignee ?? "");
    setPhase("done");
    setPollPaused(false);
    reveal(briefRef);
  }, [setPollPaused]);

  // Play queued steps one hop at a time; finish once the result is in and every step has been shown.
  useEffect(() => {
    if (phase !== "running") return;
    const t = setInterval(() => {
      const o = opsRef.current;
      const s = queue.current.shift();
      if (s) {
        o.inject(s.keys);
        setTimeout(() => {
          o.highlight(s.keys);
          o.flyTo(s.keys);
          o.caption(`${s.n} · ${captionFor(s)}`);
        }, 150);
        setPlayed((p) => p + 1);
        if (s.tool === "suspect_changes") {
          const r0 = s.data[0];
          if (r0) setRootCause(`${r0.pr} "${r0.title}" removed the timeout on the fraud-svc call`);
        }
      } else if (result && !finalized.current) {
        finalize(result);
      } else if (thinking && !result) {
        o.caption(`Keystone's LLM is writing the brief from these ${played} graph results… every sentence must cite graph keys`);
      }
    }, HOP_MS);
    return () => clearInterval(t);
  }, [phase, result, thinking, played, finalize]);

  const start = () => {
    const o = opsRef.current;
    stop.current?.();
    queue.current = [];
    finalized.current = false;
    setSteps([]); setPlayed(0); setThinking(false); setResult(null); setResolved(null);
    setLesson(null); setLessonPhase("none"); setGuard(null); setRootCause("");
    o.clearEvidence(); o.clearDown(); o.highlight([]); o.select(null); o.setAutoRotate(false);
    setPollPaused(true);
    setPhase("running");
    setStartedAt(Date.now()); setElapsed(0);
    stop.current = streamIncident(service, agentMode, {
      down: () => {
        o.markDown(service);
        o.highlight([`Service:${service}`]);
        o.flyTo([`Service:${service}`], 1200);
        o.caption(`ALERT · ${service} is DOWN (error rate 100%). The failure spreads to everything that depends on it.`);
      },
      step: (s) => {
        queue.current.push(s);
        setSteps((prev) => [...prev, s]);
        o.log(`step ${s.n}: ${s.tool}(${Object.values(s.args).join(", ")})`, s);
      },
      thinking: () => setThinking(true),
      result: (e) => { setThinking(false); setResult(e.result as IncidentResult); },
      error: (e) => {
        setPhase("error"); setThinking(false); setPollPaused(false);
        o.setError(`Incident run failed: ${e.message}`);
      },
    });
  };

  useEffect(() => () => stop.current?.(), []);

  // A cited Team also lights up its current members (MEMBER_OF edges on the map), so "the payments team" is visible.
  const links = ops.graph?.links ?? [];
  const expandTeams = (keys: string[]) => {
    const teams = new Set(keys.filter((k) => k.startsWith("Team:")));
    const members = links.filter((l) => l.type === "MEMBER_OF" && teams.has(idOf(l.target))).map((l) => idOf(l.source));
    return [...new Set([...keys, ...members])];
  };

  const [hovered, setHovered] = useState<number | null>(null);
  const hoverClaim = (keys: string[], i: number | null = null) => {
    const o = opsRef.current;
    const ids = expandTeams(keys);
    setHovered(i);
    o.inject(ids);
    setTimeout(() => { o.highlight(ids); o.flyTo(ids, 900); }, 100);
  };

  // Hovering the action card shows what Keystone wrote: the incident, who it's assigned to, the services it impacts,
  // and every node it CITES (the kept claims' keys), with the real CITES lines read from the graph.
  const hoverAction = (a: Action) => {
    if (!a.id || !result) return;
    const o = opsRef.current;
    const inc = `Incident:${a.id}`;
    const cited = [...new Set(result.claims.flatMap((c) => c.keys))];
    const ids = [...new Set([inc, ...(a.assignee ? [`Person:${a.assignee}`] : []), `Service:${result.service}`,
      ...(a.downstream ?? []).map((s) => `Service:${s}`), ...cited])];
    setHovered(null);
    o.inject(ids, [inc]);
    setTimeout(() => {
      o.highlight(ids);
      o.flyTo(ids, 900);
      o.caption(`${a.id}: assigned to @${a.assignee}, impacts ${a.downstream?.length ?? 0} downstream services, cites ${cited.length} pieces of evidence`);
    }, 100);
  };

  // The entities a claim cites, as chips under it: what lights up in the graph, in words.
  const nodeById = new Map((ops.graph?.nodes ?? []).map((n) => [n.id, n]));
  const citedChips = (keys: string[]) => {
    const ids = expandTeams(keys);
    const order = ["Service", "Team", "Person", "Incident", "Decision", "PR", "Ticket", "Message", "Doc"];
    return ids
      .map((id) => ({ id, type: id.split(":")[0], name: id.slice(id.indexOf(":") + 1), node: nodeById.get(id) }))
      .sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type));
  };

  const resolve = async () => {
    if (!incidentId || !result) return;
    const o = opsRef.current;
    try {
      const r = await postJSON<ToolResult & { understanding: { before: number; after: number; person: string; service: string } }>(
        `/incident/${incidentId}/resolve`, { resolver, root_cause: rootCause });
      o.log(`resolve_incident(${incidentId})`, r);
      setResolved(r.understanding);
      o.clearDown();
      await o.refreshMap();
      const ids = [`Person:${resolver}`, `Service:${result.service}`, `Incident:${incidentId}`];
      o.highlight(ids);
      o.flyTo(ids);
      o.caption(`@${resolver} resolved ${incidentId}. Their understanding of ${result.service}: ${r.understanding.before.toFixed(1)} → ${r.understanding.after.toFixed(1)}`);
      setLessonPhase("drafting");
      const d = await postJSON<{ decision_id: string; lesson: Lesson; model: string }>(`/incident/${incidentId}/lesson/draft`);
      setLesson(d.lesson);
      setDecisionId(d.decision_id);
      setLessonPhase("draft");
      reveal(lessonRef);
      o.caption(`Keystone drafted a lesson from ${incidentId}. Nothing is written until a human approves it.`);
    } catch (e) {
      setLessonPhase((p) => (p === "drafting" ? "none" : p));
      o.setError((e as Error).message);
    }
  };

  const approve = async () => {
    if (!incidentId || !lesson) return;
    const o = opsRef.current;
    setLessonPhase("approving");
    try {
      const r = await postJSON<ToolResult>("/lesson/approve", { incident_id: incidentId, approver: resolver, lesson });
      o.log(`approve_lesson(${incidentId}) → ${decisionId}`, r);
      setLessonPhase("approved");
      await o.refreshMap();
      const ids = [`Decision:${decisionId}`, `Incident:${incidentId}`, `Person:${resolver}`, ...lesson.affects.map((s) => `Service:${s}`)];
      setTimeout(() => {
        o.highlight(ids);
        o.flyTo(ids, 1500);
        o.caption(`New rule ${decisionId}: "${lesson.summary}" Every coding agent must now follow it.`);
      }, 600);
    } catch (e) {
      setLessonPhase("draft");
      o.setError((e as Error).message);
    }
  };

  const testGuard = async () => {
    if (!result) return;
    const o = opsRef.current;
    try {
      const r = await postJSON<ToolResult>("/guard", { service: result.service, proposal: GUARD_PROPOSAL, caller: "keystone-ui" });
      o.log(`check_decision(${result.service}) → ${r.verdict}`, r);
      setGuard(r);
      reveal(guardRef);
      await o.refreshMap();
      const ids = [`GuardCheck:${r.guard_check}`, ...r.keys];
      setTimeout(() => {
        o.highlight(ids);
        o.flyTo(ids, 1200);
        o.caption(r.verdict === "block" ? r.reason : `Guard: ${r.verdict}`);
      }, 500);
    } catch (e) {
      o.setError((e as Error).message);
    }
  };

  const shown = steps.slice(0, played);

  return (
    <div>
      <h2 className="mb-1 text-xl font-semibold">Incident commander</h2>
      <p className="mb-4 text-sm text-slate-400">
        Take a service down. Keystone&apos;s agent walks the graph, writes a brief where every sentence is backed by a
        graph path, and acts.
      </p>

      <div className="mb-5 flex flex-wrap items-center gap-2">
        <select value={service} onChange={(e) => setService(e.target.value)} disabled={phase === "running"}
          className="rounded-lg border border-white/15 bg-black/40 px-2 py-1.5 text-sm">
          {(services.length ? services : ["payments-api"]).map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={agentMode} onChange={(e) => setAgentMode(e.target.value as "playbook" | "agent")} disabled={phase === "running"}
          className="rounded-lg border border-white/15 bg-black/40 px-2 py-1.5 text-sm"
          title="playbook: fixed tool order, 1 LLM call · agent: the LLM picks the tools itself">
          <option value="playbook">playbook · 1 LLM call</option>
          <option value="agent">agent · LLM picks tools</option>
        </select>
        <Button tone="danger" onClick={start} disabled={phase === "running"}>
          {phase === "running" ? <span className="flex items-center gap-2"><Spinner /> {elapsed}s</span> : "Simulate outage"}
        </Button>
      </div>

      {(shown.length > 0 || phase === "running") && (
        <Section title="Agent steps (each one Cypher on FalkorDB)"
          right={steps.length > played ? <span className="text-[11px] text-slate-500">+{steps.length - played} queued</span> : undefined}>
          <ol className="space-y-1.5">
            {shown.map((s) => (
              <li key={s.n}>
                <button onClick={() => hoverClaim(s.keys)}
                  className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-2 text-left hover:bg-white/[0.07]">
                  <div className="flex items-center gap-2 text-[12px]">
                    <span className="font-mono text-sky-300">{s.n}. {s.tool}</span>
                    <span className="ml-auto tabular-nums text-slate-500">{s.ms.toFixed(1)} ms · {s.keys.length} keys</span>
                  </div>
                  <div className="mt-0.5 text-[13px] leading-snug text-slate-200">{captionFor(s)}</div>
                </button>
              </li>
            ))}
          </ol>
          {thinking && !result && (
            <div className="mt-2 flex items-center gap-2 text-sm text-slate-400"><Spinner /> LLM writing the brief (free model, ~1 min)…</div>
          )}
        </Section>
      )}

      {result && phase === "done" && (
        <>
          <div ref={briefRef} />
          <Section title="Brief" right={<span className="text-[11px] text-slate-500">{result.mode} · {result.llm_calls} LLM call(s) · {result.seconds}s</span>}>
            <p className="mb-3 text-[14px] leading-relaxed text-slate-100">{result.summary}</p>
            <div className="space-y-1.5">
              {result.claims.map((c, i) => (
                <div key={i} onMouseEnter={() => hoverClaim(c.keys, i)}
                  className={`flex cursor-pointer gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-white/5 ${hovered === i ? "bg-white/[0.06]" : ""}`}>
                  <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-cyan-300 shadow-[0_0_8px_#67e8f9]" />
                  <div>
                    <span>{c.text}</span>
                    {hovered === i && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {citedChips(c.keys).map((e) => {
                          const gone = e.node?.status === "departed";
                          return (
                            <span key={e.id} title={e.id}
                              className={`rounded-md border px-1.5 py-0.5 text-[11px] ${gone
                                ? "border-slate-500/40 text-slate-400 line-through decoration-slate-500"
                                : e.type === "Team" ? "border-sky-300/40 bg-sky-400/10 text-sky-100"
                                  : "border-white/15 bg-white/[0.04] text-slate-200"}`}>
                              {e.type === "Person" ? `@${e.name}` : `${e.type} ${e.name}`}{gone ? " (left)" : ""}
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-slate-500">
              Grounding guard: {result.claims.length} claims kept, {result.dropped.length} dropped (claims citing anything a tool
              didn&apos;t return are deleted). Hover a claim to fly to its evidence.
            </p>
            {result.dropped.length > 0 && (
              <details className="mt-1 text-[12px] text-slate-400">
                <summary className="cursor-pointer">Dropped claims</summary>
                {result.dropped.map((d, i) => <div key={i} className="mt-1">✗ {d.text} <span className="text-rose-300">({d.dropped_because})</span></div>)}
              </details>
            )}
          </Section>

          <Section title="Actions taken (written to the graph)">
            {result.actions.length === 0 && <p className="text-sm text-slate-500">No action proposed.</p>}
            {result.actions.map((a, i) => (
              <div key={i} onMouseEnter={() => hoverAction(a)}
                className={`rounded-xl border border-rose-400/30 bg-rose-500/10 p-3 text-sm ${a.id ? "cursor-pointer transition hover:border-rose-300/70 hover:bg-rose-500/20" : ""}`}>
                {a.status === "done" ? (
                  <>
                    <div className="font-semibold text-rose-100">{a.id} opened · assigned @{a.assignee}</div>
                    <div className="mt-0.5 text-[12px] text-rose-200/80">
                      {a.downstream?.length ?? 0} downstream services · {a.cited} citation edges (CITES) to the evidence
                    </div>
                    <div className="mt-1 text-[11px] text-rose-200/50">Hover to see it in the graph</div>
                    {a.reassigned_from && (
                      <div className="mt-1 text-[12px] text-amber-200">Model proposed @{a.reassigned_from}; not backed by who_knows → corrected.</div>
                    )}
                  </>
                ) : <div className="text-slate-300">{a.type}: {a.status}</div>}
              </div>
            ))}
          </Section>

          {incidentId && (
            <Section title="Learning loop">
              {!resolved && (
                <div className="space-y-2">
                  <input value={rootCause} onChange={(e) => setRootCause(e.target.value)} placeholder="root cause"
                    className="w-full rounded-lg border border-white/15 bg-black/40 px-2 py-1.5 text-sm" />
                  <Button tone="primary" onClick={resolve} disabled={lessonPhase === "drafting"}>
                    Resolve {incidentId} as @{resolver}
                  </Button>
                </div>
              )}
              {resolved && (
                <p className="mb-3 text-sm text-emerald-300">
                  ✓ Resolved by @{resolved.person}. Understanding of {resolved.service}: {resolved.before.toFixed(1)} → <b>{resolved.after.toFixed(1)}</b>
                </p>
              )}
              {lessonPhase === "drafting" && <div className="flex items-center gap-2 text-sm text-slate-400"><Spinner /> drafting the lesson…</div>}
              {lesson && lessonPhase !== "drafting" && (
                <div ref={lessonRef} className="rounded-xl border border-amber-300/40 bg-amber-400/[0.07] p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-amber-200">◆</span>
                    <span className="font-semibold text-amber-100">Lesson {decisionId}</span>
                    {lessonPhase === "approved" ? <Pill color="#4ade80">approved · active rule</Pill> : <Pill color="#f5c542">draft · not written</Pill>}
                  </div>
                  <Field label="Rule" value={lesson.summary} onChange={(v) => setLesson({ ...lesson, summary: v })} locked={lessonPhase === "approved"} />
                  <Field label="Always" value={lesson.chosen_option} onChange={(v) => setLesson({ ...lesson, chosen_option: v })} locked={lessonPhase === "approved"} />
                  <Field label="Never again (rejected)" value={lesson.rejected_option} onChange={(v) => setLesson({ ...lesson, rejected_option: v })} locked={lessonPhase === "approved"} />
                  <Field label="Because" value={lesson.reason} onChange={(v) => setLesson({ ...lesson, reason: v })} locked={lessonPhase === "approved"} />
                  <div className="mb-2 text-[12px] text-slate-400">Protects: {lesson.affects.join(", ")} · keywords: {lesson.keywords}</div>
                  {lessonPhase !== "approved" && (
                    <Button tone="gold" onClick={approve} disabled={lessonPhase === "approving"}>
                      {lessonPhase === "approving" ? "Writing…" : `Approve as @${resolver}`}
                    </Button>
                  )}
                </div>
              )}
              {lessonPhase === "approved" && (
                <div className="mt-3 space-y-2">
                  {/* <p className="text-[13px] text-slate-300">
                    Now ask Claude Code in <span className="font-mono">demo-repo</span>: <i>&quot;{GUARD_PROPOSAL}&quot;</i>, or test it here:
                  </p> */}
                  <Button tone="danger" onClick={testGuard}>Test the guard</Button>
                  {guard && (
                    <div ref={guardRef} className={`rounded-xl border p-3 text-sm ${guard.verdict === "block" ? "border-rose-400/40 bg-rose-500/10 text-rose-100" : "border-white/15 text-slate-200"}`}>
                      <div className="mb-1 font-semibold">{guard.verdict === "block" ? "BLOCKED" : guard.verdict.toUpperCase()}</div>
                      <div className="text-[13px]">{guard.reason}</div>
                    </div>
                  )}
                </div>
              )}
            </Section>
          )}
        </>
      )}

      {phase === "idle" && (
        <Section title="What happens">
          <ol className="list-decimal space-y-1 pl-4 text-[13px] text-slate-300">
            <li>The service goes red; red particles show the failure spreading upstream.</li>
            <li>The camera follows the agent: suspect change → blast radius → official owner → who really knows → why it&apos;s built this way → who warned us.</li>
            <li>The brief appears; every sentence links to the graph nodes that prove it.</li>
            <li>The incident is opened and assigned, then you resolve it and approve the lesson.</li>
          </ol>
          <p className="mt-2 text-[11px] text-slate-500">Graph steps take milliseconds; the free LLM takes about a minute to write the brief.</p>
        </Section>
      )}
    </div>
  );
}

function Field({ label, value, onChange, locked }: { label: string; value: string; onChange: (v: string) => void; locked: boolean }) {
  return (
    <label className="mb-2 block">
      <span className="text-[11px] uppercase tracking-wider text-amber-200/70">{label}</span>
      <textarea value={value} onChange={(e) => onChange(e.target.value)} readOnly={locked} rows={2}
        className="mt-0.5 w-full resize-none rounded-lg border border-white/10 bg-black/30 px-2 py-1 text-[13px] text-slate-100" />
    </label>
  );
}
