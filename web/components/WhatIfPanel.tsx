"use client";
// What-if simulator (Q14). Nothing is written; every answer is a read query on FalkorDB.
//   People leave:      services whose experts drop to 0 or 1 change colour on the map (before → after), with the
//                      company-wide zone diff and the best learners for each.
//   Service goes down: merged blast radius of the chosen services, ranked by PageRank (how much depends on each),
//                      red failure particles on the map, and who to call.
//   Most dangerous:    single points of failure (people who are the only or one-of-two experts), next to G2 betweenness.

import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "@/lib/api";
import { ZONE_COLOR } from "@/lib/graph";
import type { ToolResult } from "@/lib/types";
import type { Ops } from "./ops";
import { Button, Dot, Section, Spinner } from "./ui";

type Tab = "people" | "down" | "danger";
type View = "before" | "after";
const TABS: { id: Tab; label: string }[] = [
  { id: "people", label: "People leave" },
  { id: "down", label: "Service goes down" },
  { id: "danger", label: "Most dangerous to lose" },
];
const ZONES = ["dark", "fragile", "healthy"] as const;

export default function WhatIfPanel({ ops, active }: { ops: Ops; active: boolean }) {
  const opsRef = useRef(ops);
  useEffect(() => { opsRef.current = ops; });
  const [tab, setTab] = useState<Tab>("people");
  const [gone, setGone] = useState<string[]>([]);
  const [res, setRes] = useState<ToolResult | null>(null);
  const [view, setView] = useState<View>("after");
  const [down, setDown] = useState<string[]>([]);
  const [downRes, setDownRes] = useState<ToolResult | null>(null);
  const [ranking, setRanking] = useState<ToolResult | null>(null);
  const [busy, setBusy] = useState(false);
  const flip = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Results render under the pickers, often below the fold: scroll them into view when a new answer arrives.
  const peopleTop = useRef<HTMLDivElement>(null);
  const downTop = useRef<HTMLDivElement>(null);
  useEffect(() => { if (res) peopleTop.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [res]);
  useEffect(() => { if (downRes) downTop.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [downRes]);

  const nodes = ops.graph?.nodes ?? [];
  const people = nodes.filter((n) => n.type === "Person" && n.status === "active").sort((a, b) => a.label.localeCompare(b.label));
  const services = nodes.filter((n) => n.type === "Service").sort((a, b) => a.tier - b.tier || a.label.localeCompare(b.label));
  const bridges = ["marco", "dana", "noor", "kenji", "lena"].filter((h) => people.some((p) => p.label === h));

  // ---------------------------------------------------------------- people leave
  const showPeople = useCallback((r: ToolResult, who: string[], v: View) => {
    const o = opsRef.current;
    const zones: Record<string, string> = {};
    if (v === "after") for (const row of r.rows) zones[`Service:${row.service}`] = row.new_zone;
    o.setWhatIf(zones, v === "after" ? who.map((h) => `Person:${h}`) : []);
    const ids = [...who.map((h) => `Person:${h}`), ...r.rows.map((x) => `Service:${x.service}`),
      ...r.rows.flatMap((x) => x.learners.map((l: { learner: string }) => `Person:${l.learner}`))];
    o.highlight(ids);
    const names = who.map((h) => "@" + h).join(", ");
    const first = r.rows[0];
    o.caption(v === "before"
      ? `Today: ${r.rows.map((x) => `${x.service} has ${x.before.length} expert${x.before.length === 1 ? "" : "s"}`).join(", ") || "nothing at risk"}`
      : r.rows.length === 0
        ? `If ${names} left, no service would lose its last experts.`
        : `If ${names} left: ${r.rows.map((x) => `${x.service} goes ${x.new_zone.toUpperCase()}`).join(", ")}`
          + (first?.learners?.[0] ? ` · best learner: @${first.learners[0].learner}` : ""));
    return ids;
  }, []);

  const runPeople = async (who: string[]) => {
    if (who.length === 0) return;
    setBusy(true);
    try {
      const r = await postJSON<ToolResult>("/whatif", { gone: who });
      const o = opsRef.current;
      o.log(`what_if(${who.join(", ")})`, r);
      o.clearDown();
      setRes(r); setGone(who); setTab("people");
      // Play the change: today's map first, then the map after they leave.
      setView("before");
      const ids = showPeople(r, who, "before");
      o.flyTo(ids);
      if (flip.current) clearTimeout(flip.current);
      flip.current = setTimeout(() => { setView("after"); showPeople(r, who, "after"); }, 1600);
    } catch (e) {
      opsRef.current.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleView = (v: View) => { if (res) { setView(v); showPeople(res, gone, v); } };

  // ---------------------------------------------------------------- services down
  const showDown = useCallback((r: ToolResult, which: string[]) => {
    const o = opsRef.current;
    o.setWhatIf({}, []);
    o.clearDown();
    for (const s of which) o.markDown(s);
    const call = (r.summary.call as { person: string | null }[]).filter((c) => c.person).map((c) => `Person:${c.person}`);
    const ids = [...which.map((s) => `Service:${s}`), ...r.rows.map((x) => `Service:${x.service}`), ...call];
    o.highlight(ids);
    o.caption(`If ${which.join(" + ")} went down: ${r.summary.impacted} services break (${r.summary.tier1} tier 1), `
      + `${Math.round(r.summary.share * 100)}% of the company's dependency weight`);
    return ids;
  }, []);

  const runDown = async (which: string[]) => {
    if (which.length === 0) return;
    setBusy(true);
    try {
      const r = await postJSON<ToolResult>("/whatif/down", { down: which });
      opsRef.current.log(`services_down(${which.join(", ")})`, r);
      setDownRes(r); setDown(which); setTab("down");
      opsRef.current.flyTo(showDown(r, which), 1500);
    } catch (e) {
      opsRef.current.setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // ---------------------------------------------------------------- ranking
  const loadRanking = useCallback(async () => {
    try {
      const r = await getJSON<ToolResult>("/whatif/ranking");
      opsRef.current.log("danger_ranking()", r);
      setRanking(r);
      return r;
    } catch (e) {
      opsRef.current.setError((e as Error).message);
      return null;
    }
  }, []);

  const showRanking = useCallback((r: ToolResult | null) => {
    const o = opsRef.current;
    o.setWhatIf({}, []); o.clearDown();
    if (!r) return;
    const ids = r.rows.slice(0, 5).flatMap((x) => [`Person:${x.person}`, ...x.dark.map((d: { service: string }) => `Service:${d.service}`)]);
    o.highlight(ids);
    o.flyTo(ids, 1500);
    o.caption(r.rows[0] ? `Most dangerous to lose: @${r.rows[0].person}, the only expert on ${r.rows[0].dark.map((d: { service: string }) => d.service).join(", ") || "…"}` : null);
  }, []);

  const openTab = async (t: Tab) => {
    setTab(t);
    if (t === "people" && res) showPeople(res, gone, view);
    if (t === "down") {
      if (downRes) showDown(downRes, down);
      else { opsRef.current.setWhatIf({}, []); opsRef.current.highlight([]); opsRef.current.caption(null); }
    }
    if (t === "danger") {
      if (flip.current) clearTimeout(flip.current);
      showRanking(ranking ?? await loadRanking());
    }
  };

  // Leaving the What-if mode removes the overlay; coming back restores the current tab's picture.
  useEffect(() => {
    const o = opsRef.current;
    if (!active) {
      if (flip.current) clearTimeout(flip.current);
      o.setWhatIf({}, []);
      if (downRes) o.clearDown();
      return;
    }
    if (tab === "people" && res) showPeople(res, gone, view);
    if (tab === "down" && downRes) showDown(downRes, down);
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => { if (flip.current) clearTimeout(flip.current); }, []);

  const clearPeople = () => {
    if (flip.current) clearTimeout(flip.current);
    setRes(null); setGone([]);
    opsRef.current.setWhatIf({}, []); opsRef.current.highlight([]); opsRef.current.caption(null);
  };
  const clearDown = () => {
    setDownRes(null); setDown([]);
    opsRef.current.clearDown(); opsRef.current.highlight([]); opsRef.current.caption(null);
  };
  const togglePerson = (h: string) => setGone((g) => (g.includes(h) ? g.filter((x) => x !== h) : [...g, h]));
  const toggleService = (s: string) => setDown((d) => (d.includes(s) ? d.filter((x) => x !== s) : [...d, s]));

  return (
    <div>
      <h2 className="mb-1 text-xl font-semibold">What-if simulator</h2>
      <p className="mb-4 text-sm text-slate-400">
        See the damage before it happens, and who should learn what. Read-only: nothing in the graph changes.
      </p>

      <div className="mb-5 grid grid-cols-3 gap-1 rounded-xl border border-white/10 bg-black/30 p-1">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => openTab(t.id)}
            className={`rounded-lg px-2 py-1.5 text-[12px] leading-tight transition ${tab === t.id ? "bg-white/15 text-white" : "text-slate-400 hover:text-slate-200"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "people" && (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            {bridges.map((h) => (
              <Button key={h} tone="danger" onClick={() => runPeople([h])} disabled={busy}>What if @{h} leaves?</Button>
            ))}
          </div>

          <Section title="Or pick people" right={gone.length ? <button className="text-xs text-slate-400 hover:text-white" onClick={clearPeople}>clear</button> : undefined}>
            <div className="mb-3 flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
              {people.map((p) => (
                <button key={p.id} onClick={() => togglePerson(p.label)}
                  className={`rounded-full border px-2 py-0.5 text-[12px] ${gone.includes(p.label) ? "border-rose-400/60 bg-rose-500/20 text-rose-100" : "border-white/10 text-slate-300 hover:bg-white/5"}`}>
                  @{p.label}
                </button>
              ))}
            </div>
            <Button tone="primary" onClick={() => runPeople(gone)} disabled={busy || gone.length === 0}>
              {busy ? <span className="flex items-center gap-2"><Spinner /> asking the graph</span> : `Simulate ${gone.length || ""} leaving`}
            </Button>
          </Section>

          {res && (
            <>
              <div ref={peopleTop} className="scroll-mt-4" />
              <Section title="Before → after (all services)"
                right={
                  <div className="flex gap-1 rounded-lg border border-white/10 p-0.5 text-[11px]">
                    {(["before", "after"] as View[]).map((v) => (
                      <button key={v} onClick={() => toggleView(v)}
                        className={`rounded px-2 py-0.5 ${view === v ? "bg-white/15 text-white" : "text-slate-400"}`}>{v === "before" ? "Today" : "After"}</button>
                    ))}
                  </div>
                }>
                <ZoneDiff before={res.summary.before} after={res.summary.after} total={res.summary.services} />
              </Section>

              <Section title={`Impact (${res.ms.toFixed(1)} ms in FalkorDB)`}>
                {res.rows.length === 0 && <p className="text-sm text-emerald-300">No service loses its last experts.</p>}
                {res.rows.map((r) => (
                  <div key={r.service} className="mb-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
                    <div className="flex items-center gap-2">
                      <Dot color={ZONE_COLOR[r.new_zone]} />
                      <span className="font-semibold">{r.service}</span>
                      <span className="text-xs text-slate-500">tier {r.tier}</span>
                      <span className="ml-auto text-xs">
                        <span style={{ color: ZONE_COLOR[r.before.length >= 2 ? "healthy" : "fragile"] }}>{r.before.length >= 2 ? "healthy" : "fragile"}</span>
                        <span className="text-slate-500"> → </span>
                        <span style={{ color: ZONE_COLOR[r.new_zone] }}>{r.new_zone}</span>
                      </span>
                    </div>
                    <div className="mt-1 text-[12px] text-slate-400">
                      experts now: {r.before.map((h: string) => "@" + h).join(", ")} · after: {r.after.length ? r.after.map((h: string) => "@" + h).join(", ") : "nobody"}
                    </div>
                    {r.learners.length > 0 && (
                      <div className="mt-2 rounded-lg bg-emerald-400/[0.07] px-2 py-1.5 text-[13px]">
                        <span className="text-emerald-300">Knowledge transfer:</span>{" "}
                        {r.learners.map((l: { learner: string; score: number; shared: number }, i: number) => (
                          <span key={l.learner}>
                            {i > 0 && ", "}<b>@{l.learner}</b>{" "}
                            <span className="text-slate-400">(score {Number(l.score).toFixed(1)}, {l.shared} PRs with the leavers)</span>
                          </span>
                        ))}
                        {r.before[0] && gone.includes(r.before[0]) && <span className="text-slate-400"> should pair with @{r.before[0]} now.</span>}
                      </div>
                    )}
                  </div>
                ))}
              </Section>
            </>
          )}
        </>
      )}

      {tab === "down" && (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            <Button tone="danger" onClick={() => runDown(["payments-api"])} disabled={busy}>payments-api down</Button>
            <Button tone="danger" onClick={() => runDown(["config-svc"])} disabled={busy}>config-svc down</Button>
            <Button tone="danger" onClick={() => runDown(["config-svc", "event-bus"])} disabled={busy}>config-svc + event-bus</Button>
          </div>
          <Section title="Or pick services" right={down.length ? <button className="text-xs text-slate-400 hover:text-white" onClick={clearDown}>clear</button> : undefined}>
            <div className="mb-3 flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
              {services.map((s) => (
                <button key={s.id} onClick={() => toggleService(s.label)}
                  className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px] ${down.includes(s.label) ? "border-rose-400/60 bg-rose-500/20 text-rose-100" : "border-white/10 text-slate-300 hover:bg-white/5"}`}>
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: ZONE_COLOR[s.zone] }} />{s.label}
                  <span className="text-slate-500">t{s.tier}</span>
                </button>
              ))}
            </div>
            <Button tone="primary" onClick={() => runDown(down)} disabled={busy || down.length === 0}>
              {busy ? <span className="flex items-center gap-2"><Spinner /> asking the graph</span> : `Take ${down.length || ""} down`}
            </Button>
          </Section>

          {downRes && (
            <>
              <div ref={downTop} className="scroll-mt-4" />
              <div className="mb-5 grid grid-cols-3 gap-2 text-center">
                <Stat value={String(downRes.summary.impacted)} label="services break" color="#ff3352" />
                <Stat value={String(downRes.summary.tier1)} label="of them tier 1" color="#ffb020" />
                <Stat value={`${Math.round(downRes.summary.share * 100)}%`} label="of dependency weight (PageRank)" color="#a78bfa" />
              </div>
              <Section title="Call first">
                {(downRes.summary.call as { service: string; person: string | null; score: number | null }[]).map((c) => (
                  <div key={c.service} className="text-sm">
                    {c.service}: {c.person ? <><b>@{c.person}</b> <span className="text-slate-400">(understanding {Number(c.score).toFixed(1)})</span></>
                      : <span className="text-rose-300">nobody active understands it</span>}
                  </div>
                ))}
              </Section>
              <Section title={`What breaks, by how much depends on it (${downRes.ms.toFixed(1)} ms)`}>
                {downRes.rows.length === 0 && <p className="text-sm text-emerald-300">Nothing depends on it.</p>}
                {downRes.rows.map((r) => {
                  const max = downRes.rows[0]?.share || 1;
                  return (
                    <div key={r.service} className="mb-1.5 flex items-center gap-2 text-[13px]"
                      title={r.path.join(" ← ")}>
                      <span className="w-28 truncate font-medium">{r.service}</span>
                      <span className="w-6 text-[11px] text-slate-500">t{r.tier}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded bg-white/10">
                        <div className="h-full rounded bg-rose-400" style={{ width: `${Math.max(4, (r.share / max) * 100)}%` }} />
                      </div>
                      <span className="w-20 text-right text-[11px] text-slate-400">{r.hops} hop{r.hops === 1 ? "" : "s"} · {r.via}</span>
                    </div>
                  );
                })}
                <p className="mt-2 text-[11px] text-slate-500">Bar = PageRank share: how much of the company depends on that service. Hover a row for the failure path.</p>
              </Section>
            </>
          )}
        </>
      )}

      {tab === "danger" && (
        <>
          <p className="mb-3 text-sm text-slate-400">
            People who are the <span className="text-rose-300">only</span> expert on a service (it goes dark if they leave) or one of
            two (<span className="text-amber-300">fragile</span>). Compared with betweenness: who connects the company.
          </p>
          {!ranking && <div className="flex items-center gap-2 text-sm text-slate-400"><Spinner /> asking the graph…</div>}
          {ranking && (
            <>
              <div className="mb-3">
                <Button tone="danger" disabled={busy}
                  onClick={() => runPeople(ranking.rows.slice(0, 3).map((r) => r.person))}>
                  What if the top 3 left?
                </Button>
              </div>
              <Section title={`Single points of failure (${ranking.ms.toFixed(1)} ms)`}>
                {ranking.rows.map((r, i) => {
                  const max = ranking.rows[0]?.risk || 1;
                  return (
                    <button key={r.person} onClick={() => runPeople([r.person])}
                      className="mb-1.5 w-full rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-2 text-left hover:bg-white/[0.07]">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="w-5 text-slate-500">{i + 1}</span>
                        <span className="font-semibold">@{r.person}</span>
                        <span className="text-xs text-slate-500">{r.team}</span>
                        <span className="ml-auto text-[11px] text-slate-400" title="G2 betweenness on collaboration">
                          bridge #{r.bridge_rank ?? "–"}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded bg-white/10">
                          <div className="h-full rounded bg-gradient-to-r from-amber-400 to-rose-500" style={{ width: `${(r.risk / max) * 100}%` }} />
                        </div>
                        <span className="w-12 text-right text-[11px] tabular-nums text-slate-300">risk {r.risk}</span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1 text-[11px]">
                        {r.dark.map((d: { service: string; tier: number }) => (
                          <span key={d.service} className="rounded border border-rose-400/40 px-1.5 text-rose-200">{d.service} → dark</span>
                        ))}
                        {r.fragile.map((d: { service: string; tier: number }) => (
                          <span key={d.service} className="rounded border border-amber-400/40 px-1.5 text-amber-200">{d.service} → fragile</span>
                        ))}
                      </div>
                    </button>
                  );
                })}
                <p className="mt-2 text-[11px] text-slate-500">
                  Risk = 2 × tier weight per service they&apos;d turn dark + tier weight per service they&apos;d turn fragile (tier 1 = 3, 2 = 2, 3 = 1). Click a person to simulate it.
                </p>
              </Section>
            </>
          )}
        </>
      )}
    </div>
  );
}

function ZoneDiff({ before, after, total }: { before: Record<string, number>; after: Record<string, number>; total: number }) {
  return (
    <div className="space-y-2">
      {[["Today", before], ["After", after]].map(([label, z]) => (
        <div key={label as string} className="flex items-center gap-2">
          <span className="w-10 text-[11px] text-slate-400">{label as string}</span>
          <div className="flex h-3 flex-1 overflow-hidden rounded">
            {ZONES.map((k) => (
              <div key={k} style={{ width: `${((z as Record<string, number>)[k] / total) * 100}%`, background: ZONE_COLOR[k] }} title={`${k}: ${(z as Record<string, number>)[k]}`} />
            ))}
          </div>
        </div>
      ))}
      <div className="flex gap-3 text-[12px]">
        {ZONES.map((k) => {
          const d = after[k] - before[k];
          return (
            <span key={k} className="flex items-center gap-1">
              <Dot color={ZONE_COLOR[k]} /> {k} {before[k]} → <b>{after[k]}</b>
              {d !== 0 && <span className={k === "healthy" ? (d > 0 ? "text-emerald-300" : "text-rose-300") : (d > 0 ? "text-rose-300" : "text-emerald-300")}>({d > 0 ? "+" : ""}{d})</span>}
            </span>
          );
        })}
      </div>
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
