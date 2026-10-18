"use client";
// Comprehension map (hero 2). Nothing selected: legend (every visual = data), the dark/fragile zones and the live
// guard log. A service selected: "70% agent-written · 0 people understand it · N approvals under 90 s", who really
// knows it, the stamp chain, the pairing suggestion, and the "File risk" action (A5).

import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "@/lib/api";
import { TYPE_COLOR, VERDICT_COLOR, ZONE_COLOR, idOf, keyValue, neighbourhood } from "@/lib/graph";
import type { GNode, GraphData, ToolResult } from "@/lib/types";
import type { Ops } from "./ops";
import { Button, Dot, Pill, Section, Spinner } from "./ui";

interface Detail {
  service: string;
  owners: ToolResult; who_knows: ToolResult; stamp_chains: ToolResult; knowledge_spread: ToolResult;
  decisions: ToolResult; blast_radius: ToolResult;
}

export default function MapPanel({ ops, selected }: { ops: Ops; selected: string | null }) {
  const opsRef = useRef(ops);
  useEffect(() => { opsRef.current = ops; });
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [risk, setRisk] = useState<string | null>(null);

  const services = (ops.graph?.nodes ?? []).filter((n) => n.type === "Service");
  const node = services.find((n) => n.id === selected);
  const other = !node && selected ? ops.graph?.nodes.find((n) => n.id === selected) : undefined;

  // Breadcrumb: clicking through cards (person → service → team…) can be walked back.
  const [trail, setTrail] = useState<string[]>([]);
  const go = (id: string) => { if (selected) setTrail((t) => [...t, selected]); ops.select(id); };
  const back = () => { const t = [...trail]; const prev = t.pop(); setTrail(t); ops.select(prev ?? null); };
  const home = () => { setTrail([]); ops.select(null); };

  useEffect(() => {
    const o = opsRef.current;
    setRisk(null);
    if (!selected) { setDetail(null); setTrail([]); o.highlight([]); o.setAutoRotate(true); return; }
    o.setAutoRotate(false);
    if (!selected.startsWith("Service:")) {
      // Not a service: light up the node and its direct neighbours from the map itself (no extra query).
      setDetail(null);
      const ids = [...neighbourhood(selected, o.graph?.links ?? [])];
      o.highlight(ids);
      o.flyTo(ids);
      return;
    }
    setLoading(true);
    getJSON<Detail>(`/service/${encodeURIComponent(keyValue(selected))}`)
      .then((d) => {
        setDetail(d);
        for (const k of ["who_knows", "stamp_chains", "knowledge_spread", "owners", "decisions"] as const) o.log(`${k}(${d.service})`, d[k]);
        const people = d.who_knows.rows.slice(0, 3).map((r) => `Person:${r.person}`);
        const approvers = d.stamp_chains.rows.map((r) => `Person:${r.approver}`);
        // The owning team too (from the map's OWNS links): "the org chart says Payments owns it" must be visible
        // next to who actually understands it.
        const owners = (o.graph?.links ?? []).filter((l) => l.type === "OWNS" && idOf(l.target) === selected).map((l) => idOf(l.source));
        const ids = [selected, ...owners, ...people, ...approvers, "CodingAgent:codegen-bot"];
        o.highlight(ids);
        o.flyTo(ids);
      })
      .catch((e) => o.setError(e.message))
      .finally(() => setLoading(false));
  }, [selected]);

  if (selected && node) {
    const share = Math.round((node.agent_share ?? 0) * 100);
    const experts = (node.experts ?? []) as string[];
    const sum = detail?.stamp_chains.summary;
    const pair = detail?.knowledge_spread.rows[0];
    const owner = detail?.owners.rows[0];
    return (
      <div>
        <NavBar trail={trail} onBack={back} onHome={home} />
        <div className="mb-1 flex items-center gap-2">
          <Dot color={ZONE_COLOR[node.zone]} />
          <h2 className="text-xl font-semibold">{node.label}</h2>
          <Pill color={ZONE_COLOR[node.zone]}>{node.zone}</Pill>
          <span className="text-xs text-slate-500">tier {node.tier}</span>
        </div>
        <p className="mb-4 text-sm text-slate-400">{node.description}</p>

        <div className="mb-5 grid grid-cols-3 gap-2 text-center">
          <Stat value={`${share}%`} label="agent-written (90 d)" color="#a78bfa" />
          <Stat value={String(experts.length)} label="people understand it" color={ZONE_COLOR[node.zone]} />
          <Stat value={sum ? String(sum.under_90s) : "…"} label="approvals under 90 s" color="#ff8a3d" />
        </div>

        {loading && <div className="mb-4 flex items-center gap-2 text-sm text-slate-400"><Spinner /> asking the graph…</div>}

        {detail && (
          <>
            <Section title="Who actually understands it">
              {detail.who_knows.rows.length === 0 && <p className="text-sm text-rose-300">No active person has any evidence on it.</p>}
              {detail.who_knows.rows.map((r) => (
                <div key={r.person} className="mb-1.5 flex items-center gap-2 text-sm">
                  <button className="w-16 text-left font-medium hover:text-sky-300" onClick={() => go(`Person:${r.person}`)}>@{r.person}</button>
                  <div className="h-1.5 flex-1 overflow-hidden rounded bg-white/10">
                    <div className="h-full rounded" style={{
                      width: `${Math.min(100, (r.score / Math.max(5, detail.who_knows.rows[0].score)) * 100)}%`,
                      background: r.score >= 5 ? ZONE_COLOR.healthy : "#64748b",
                    }} />
                  </div>
                  <span className="w-10 text-right tabular-nums text-slate-300">{Number(r.score).toFixed(1)}</span>
                </div>
              ))}
              <p className="mt-1 text-[11px] text-slate-500">Score = authored ×3, deep review ×2, incident ×4, rubber stamp ×0.2, weighted by recency. Expert ≥ 5.</p>
              {owner && (
                <p className="mt-2 text-xs text-slate-400">
                  Org chart says: team{" "}
                  <button className="font-bold hover:text-sky-300" onClick={() => go(`Team:${owner.team}`)}>{owner.team}</button>,
                  on call <b>@{owner.on_call}</b>
                  {owner.on_call_departed && <span className="text-rose-300"> (left the company)</span>}
                </p>
              )}
            </Section>

            {detail.stamp_chains.rows.length > 0 && (
              <Section title="Stamp chain: agent wrote it, a human approved it unread">
                <div className="space-y-1">
                  {detail.stamp_chains.rows.slice(0, 6).map((r, i) => (
                    // one PR can be stamped by two approvers, so the PR id alone isn't unique
                    <div key={`${r.pr}-${r.approver}-${i}`} className="flex items-center gap-2 text-[13px]">
                      <span className="w-[68px] font-mono text-cyan-300">{r.pr}</span>
                      <span className="text-violet-300">{r.agent}</span>
                      <span className="text-slate-500">→</span>
                      <span>@{r.approver}</span>
                      <span className="ml-auto tabular-nums text-orange-300">{r.secs} s · 0 comments</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {pair && (
              <Section title="Spread the knowledge">
                <p className="text-sm">
                  Pair <b>@{pair.mentor}</b> <span className="text-slate-400">(best remaining, {Number(pair.mentor_score).toFixed(1)})</span> with{" "}
                  <b>@{pair.learner}</b> <span className="text-slate-400">(touched it {pair.shallow_touches}× without understanding it)</span>.
                </p>
              </Section>
            )}

            {node.zone !== "healthy" && (
              <Section title="Act">
                <div className="flex items-center gap-3">
                  <Button tone="danger" onClick={async () => {
                    try {
                      const r = await postJSON<ToolResult>("/risk", { service: node.label, kind: node.zone === "dark" ? "dark_zone" : "bus_factor", score: node.agent_share });
                      ops.log(`file_risk(${node.label})`, r);
                      setRisk(r.rows[0].id);
                    } catch (e) { ops.setError((e as Error).message); }
                  }}>File risk</Button>
                  {risk && <span className="text-sm text-emerald-300">✓ {risk} written to the graph</span>}
                </div>
              </Section>
            )}

            {detail.decisions.rows.length > 0 && (
              <Section title="Why it's built this way">
                {detail.decisions.rows.slice(0, 4).map((d, i) => (
                  <div key={`${d.decision}-${i}`} className="mb-1.5 text-[13px]">
                    <span className="font-mono text-amber-300">{d.decision}</span>{" "}
                    <span className={d.status === "superseded" ? "text-slate-500 line-through" : "text-slate-200"}>{d.summary}</span>
                  </div>
                ))}
              </Section>
            )}
          </>
        )}
      </div>
    );
  }

  if (selected && other) {
    return (
      <div>
        <NavBar trail={trail} onBack={back} onHome={home} />
        <NodeCard node={other} graph={ops.graph} onGo={go} />
      </div>
    );
  }

  const zones = (z: string) => services.filter((s) => s.zone === z).sort((a, b) => a.tier - b.tier);
  const guards = (ops.graph?.nodes ?? []).filter((n) => n.type === "GuardCheck").sort((a, b) => (b.at ?? 0) - (a.at ?? 0));
  const pick = (n: GNode) => ops.select(n.id);

  return (
    <div>
      <h2 className="mb-1 text-xl font-semibold">Who actually understands your systems?</h2>
      <p className="mb-5 text-sm text-slate-400">
        Computed from every PR, review, incident and chat message in the graph, not from the org chart.
      </p>

      <details open className="mb-5 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
        <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">How to read the map</summary>
        <ul className="mt-2 space-y-1.5 text-[13px] text-slate-300">
          <li className="flex items-center gap-1.5"><Dot color={ZONE_COLOR.healthy} /> healthy <Dot color={ZONE_COLOR.fragile} /> fragile <Dot color={ZONE_COLOR.dark} /> dark zone (pulses): spheres are services</li>
          <li>Bigger sphere = more of the company depends on it (PageRank)</li>
          <li>Brighter halo = more of its recent code written by AI agents</li>
          <li>White discs = people (initials); grey and faded = left the company</li>
          <li><span className="text-violet-300">Violet hexagons</span> = AI coding agents; violet particles = code they wrote</li>
          <li>White particles = a person understands a service (faster = deeper)</li>
          <li><span className="text-slate-300">Wire diamonds</span> = teams · <span className="text-amber-300">gold diamonds</span> = decisions and learned rules</li>
          <li className="text-slate-400">Hover anything to light up what it&apos;s connected to; click to open it. Search with <kbd className="rounded bg-white/10 px-1">/</kbd>.</li>
        </ul>
      </details>

      <Section title="Dark zones: nobody left understands them">
        <ZoneList list={zones("dark")} onPick={pick} />
      </Section>
      <Section title="Fragile: one person away from dark">
        <ZoneList list={zones("fragile")} onPick={pick} />
      </Section>

      <Section title="Decision guard (live)" right={<span className="text-[11px] text-slate-500">Claude Code → hook → Keystone</span>}>
        {guards.length === 0 && <p className="text-sm text-slate-500">No guard calls yet. Edits from Claude Code in demo-repo appear here.</p>}
        {guards.map((gc) => (
          <div key={gc.id} className="mb-2 rounded-lg border border-white/10 bg-white/[0.03] p-2 text-[13px]">
            <div className="flex items-center gap-2">
              <Pill color={VERDICT_COLOR[gc.verdict] ?? "#fff"}>{gc.verdict === "block" ? "BLOCKED" : gc.verdict}</Pill>
              <span className="text-slate-400">{gc.caller} · {gc.service}</span>
            </div>
            <div className="mt-1 line-clamp-2 text-slate-300">{gc.proposal}</div>
          </div>
        ))}
      </Section>
    </div>
  );
}

function Stat({ value, label, color }: { value: string; label: string; color: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-2 py-3">
      <div className="text-2xl font-semibold tabular-nums" style={{ color }}>{value}</div>
      <div className="text-[11px] leading-tight text-slate-400">{label}</div>
    </div>
  );
}

function NavBar({ trail, onBack, onHome }: { trail: string[]; onBack: () => void; onHome: () => void }) {
  return (
    <div className="mb-3 flex items-center gap-3 text-xs text-slate-400">
      {trail.length > 0 && (
        <button className="hover:text-slate-200" onClick={onBack}>← back to {keyValue(trail[trail.length - 1])}</button>
      )}
      <button className="hover:text-slate-200" onClick={onHome}>{trail.length ? "· whole map" : "← whole map"}</button>
      <span className="ml-auto text-slate-600">Esc</span>
    </div>
  );
}

/** A clickable row pointing at another node. */
function Go({ id, graph, onGo, right, faded }: {
  id: string; graph: GraphData | null; onGo: (id: string) => void; right?: React.ReactNode; faded?: boolean;
}) {
  const n = graph?.nodes.find((x) => x.id === id);
  const type = id.split(":")[0];
  const color = n?.type === "Service" ? ZONE_COLOR[n.zone] : TYPE_COLOR[type] ?? "#fff";
  const gone = faded || n?.status === "departed";
  return (
    <button onClick={() => onGo(id)}
      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-white/5">
      <Dot color={gone ? "#56607a" : color} />
      <span className={gone ? "text-slate-500 line-through" : "font-medium"}>{type === "Person" ? `@${keyValue(id)}` : keyValue(id)}</span>
      {type === "Person" && n?.role && <span className="truncate text-xs text-slate-500">{n.role}</span>}
      {type === "Service" && n && <span className="text-xs text-slate-500">{n.zone} · tier {n.tier}</span>}
      <span className="ml-auto text-xs text-slate-400">{right}</span>
    </button>
  );
}

/** Card for anything that isn't a service: built from the map's own edges, so it's instant. */
function NodeCard({ node, graph, onGo }: { node: GNode; graph: GraphData | null; onGo: (id: string) => void }) {
  const links = graph?.links ?? [];
  const out = (type: string) => links.filter((l) => l.type === type && idOf(l.source) === node.id);
  const inc = (type: string) => links.filter((l) => l.type === type && idOf(l.target) === node.id);
  const color = node.type === "GuardCheck" ? VERDICT_COLOR[node.verdict] ?? "#fff" : TYPE_COLOR[node.type] ?? "#fff";
  const kind = { Person: "Person", Team: "Team", CodingAgent: "AI coding agent", Decision: "Decision / rule",
    Incident: "Incident", GuardCheck: "Decision-guard check" }[node.type as string] ?? node.type;

  const header = (
    <div className="mb-4">
      <div className="text-[11px] uppercase tracking-[0.14em]" style={{ color }}>{kind}</div>
      <h2 className="text-xl font-semibold">{node.type === "Person" ? `@${node.label}` : node.label}</h2>
      {node.type === "Person" && (
        <p className="text-sm text-slate-400">
          {node.name} · {node.role}
          {node.status === "departed" && <span className="text-rose-300"> · left the company</span>}
        </p>
      )}
      {node.summary && <p className="mt-1 text-sm text-slate-300">{node.summary}</p>}
      {node.title && <p className="mt-1 text-sm text-slate-300">{node.title}</p>}
    </div>
  );

  if (node.type === "Person") {
    const knows = out("UNDERSTANDS").sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const team = out("MEMBER_OF")[0];
    const assigned = inc("ASSIGNED_TO");
    return (
      <div>
        {header}
        <Section title={`Understands (expert score ≥ 5)`}>
          {knows.length === 0 && <p className="text-sm text-slate-500">No service where the evidence makes them an expert.</p>}
          {knows.map((l) => <Go key={idOf(l.target)} id={idOf(l.target)} graph={graph} onGo={onGo} right={Number(l.score).toFixed(1)} />)}
          {node.status === "departed" && knows.length > 0 && (
            <p className="mt-1 text-xs text-rose-300">This knowledge left with them: these services lost an expert.</p>
          )}
        </Section>
        {team && <Section title="Team (org chart)"><Go id={idOf(team.target)} graph={graph} onGo={onGo} /></Section>}
        {assigned.length > 0 && (
          <Section title="Assigned incidents">{assigned.map((l) => <Go key={idOf(l.source)} id={idOf(l.source)} graph={graph} onGo={onGo} />)}</Section>
        )}
      </div>
    );
  }

  if (node.type === "Team") {
    const members = inc("MEMBER_OF");
    const owns = out("OWNS");
    const departed = (graph?.nodes ?? []).filter((n) => n.type === "Person" && n.status === "departed" && n.team === keyValue(node.id));
    return (
      <div>
        {header}
        <Section title={`Owns (${owns.length})`}>
          {owns.map((l) => <Go key={idOf(l.target)} id={idOf(l.target)} graph={graph} onGo={onGo} />)}
        </Section>
        <Section title={`Current members (${members.length})`}>
          {members.map((l) => <Go key={idOf(l.source)} id={idOf(l.source)} graph={graph} onGo={onGo} />)}
        </Section>
        {departed.length > 0 && (
          <Section title="Former members">
            {departed.map((n) => <Go key={n.id} id={n.id} graph={graph} onGo={onGo} right="left" />)}
          </Section>
        )}
      </div>
    );
  }

  if (node.type === "CodingAgent") {
    const wrote = out("AGENT_WROTE").sort((a, b) => (b.prs ?? 0) - (a.prs ?? 0));
    return (
      <div>
        {header}
        <Section title="Code it merged in the last 90 days">
          {wrote.map((l) => <Go key={idOf(l.target)} id={idOf(l.target)} graph={graph} onGo={onGo}
            right={`${l.prs} PRs · ${Number(l.lines).toLocaleString()} lines`} />)}
        </Section>
      </div>
    );
  }

  // Decisions, incidents, guard checks: what they touch.
  const related = [...neighbourhood(node.id, links)].filter((id) => id !== node.id);
  return (
    <div>
      {header}
      {node.type === "GuardCheck" && (
        <Section title="Proposal checked">
          <div className="flex items-center gap-2 text-sm">
            <Pill color={color}>{node.verdict === "block" ? "BLOCKED" : node.verdict}</Pill>
            <span className="text-slate-400">from {node.caller} · {node.service}</span>
          </div>
          <p className="mt-2 text-sm text-slate-300">{node.proposal}</p>
        </Section>
      )}
      {(node.status || node.origin) && node.type !== "GuardCheck" && (
        <p className="mb-4 text-xs text-slate-400">status {node.status}{node.origin ? ` · ${node.origin}` : ""}</p>
      )}
      <Section title="Connected to">
        {related.map((id) => <Go key={id} id={id} graph={graph} onGo={onGo} />)}
      </Section>
    </div>
  );
}

function ZoneList({ list, onPick }: { list: GNode[]; onPick: (n: GNode) => void }) {
  if (list.length === 0) return <p className="text-sm text-slate-500">None</p>;
  return (
    <div className="space-y-1">
      {list.map((s) => (
        <button key={s.id} onClick={() => onPick(s)}
          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-white/5">
          <Dot color={ZONE_COLOR[s.zone]} />
          <span className="font-medium">{s.label}</span>
          <span className="text-xs text-slate-500">tier {s.tier}</span>
          <span className="ml-auto text-xs text-violet-300">{Math.round((s.agent_share ?? 0) * 100)}% agent</span>
          <span className="w-20 text-right text-xs text-slate-400">{(s.experts ?? []).length} expert{(s.experts ?? []).length === 1 ? "" : "s"}</span>
        </button>
      ))}
    </div>
  );
}
