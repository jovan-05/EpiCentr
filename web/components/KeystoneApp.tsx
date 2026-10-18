"use client";
// Keystone UI shell: one full-screen 3D universe + a side panel per mode (Map, Incident, What-if),
// a caption line for the agent's hops, and a collapsible Cypher drawer showing every query that ran.

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMap, postJSON } from "@/lib/api";
import { buildDisplay, fmtClock, mergeDisplay, upstream, withEvidence, withEvidenceLinks, type EdgeRow, type Layer } from "@/lib/graph";
import type { CypherEntry, GraphData, GNode, MapData, ToolResult, Visual } from "@/lib/types";
import CypherDrawer from "./CypherDrawer";
import type { GraphHandle } from "./Graph3D";
import IncidentPanel from "./IncidentPanel";
import MapPanel from "./MapPanel";
import MapToolbar, { PanPad } from "./MapToolbar";
import type { Ops } from "./ops";
import SearchBox from "./SearchBox";
import { Button } from "./ui";
import WhatIfPanel from "./WhatIfPanel";

const Graph3D = dynamic(() => import("./Graph3D"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-slate-500">Loading the graph…</div>,
});

type Mode = "map" | "incident" | "whatif";
const PANEL_W = 450;
const MODES: { id: Mode; label: string }[] = [
  { id: "map", label: "Comprehension map" },
  { id: "incident", label: "Incident" },
  { id: "whatif", label: "What-if" },
];

export default function KeystoneApp() {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [clock, setClock] = useState<{ now: number; week: string } | null>(null);
  const [mode, setMode] = useState<Mode>("map");
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [down, setDown] = useState<Set<string>>(new Set());
  const [failingLinks, setFailing] = useState<Set<string>>(new Set());
  const [zoneOverride, setZoneOverride] = useState<Record<string, string>>({});
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [caption, setCaption] = useState<string | null>(null);
  const [cypher, setCypher] = useState<CypherEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyReset, setBusyReset] = useState(false);
  const [hidden, setHidden] = useState<Set<Layer>>(new Set());
  const [rotating, setRotating] = useState(true);
  const handle = useRef<GraphHandle | null>(null);
  const rotate = useCallback((on: boolean) => { handle.current?.setAutoRotate(on); setRotating(on); }, []);
  const graphRef = useRef<GraphData | null>(null);
  // Full-window canvas; the camera's view is shifted by half the panel width so the graph is centred where you can see it.
  const [size, setSize] = useState({ w: 1600, h: 900 });
  useEffect(() => {
    const fit = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  useEffect(() => { graphRef.current = graph; }, [graph]);

  const log = useCallback((title: string, res: { ms: number; cypher: string }) => {
    setCypher((c) => [{ title, ms: res.ms, cypher: res.cypher, at: Date.now() }, ...c].slice(0, 60));
  }, []);

  // No backend reachable (e.g. the public site while the API is down): show a bundled snapshot of the starting graph
  // (what GET /map returns after Reset demo) so visitors still see the map; switch to live data when the API answers.
  const [offline, setOffline] = useState(false);
  const offlineRef = useRef(false);
  const refreshMap = useCallback(async (full = false) => {
    try {
      const m = await getMap();
      setClock(m.clock);
      setGraph((cur) => (!cur || full ? buildDisplay(m) : mergeDisplay(cur, m).graph));
      log("GET /map (services, people, agents, decisions, guard checks)", m);
      setError(null);
      offlineRef.current = false; setOffline(false);
    } catch (e) {
      if (!graphRef.current) {
        try {
          const r = await fetch("/map-snapshot.json");
          if (!r.ok) throw new Error(String(r.status));
          const snap = (await r.json()) as MapData;
          setClock(snap.clock);
          setGraph(buildDisplay(snap));
          offlineRef.current = true; setOffline(true); setError(null);
          return;
        } catch { /* no snapshot either: show the error below */ }
      }
      if (offlineRef.current) return;   // the offline note is already up; keep retrying quietly
      setError(`Can't reach the Keystone API: ${(e as Error).message}. Start it with: .venv\\Scripts\\uvicorn.exe api.main:app --port 8000`);
    }
  }, [log]);

  useEffect(() => { refreshMap(true); }, [refreshMap]);

  // Pick up changes made elsewhere (e.g. Claude Code hitting the guard adds a GuardCheck node) every few seconds.
  const pollPaused = useRef(false);
  useEffect(() => {
    const t = setInterval(() => { if (!pollPaused.current && document.visibilityState === "visible") refreshMap(); }, 6000);
    return () => clearInterval(t);
  }, [refreshMap]);

  const ops: Ops = useMemo(() => ({
    graph,
    highlight: (ids) => setHighlight(new Set(ids)),
    flyTo: (ids, ms) => handle.current?.flyTo(ids, ms),
    overview: () => handle.current?.overview(),
    inject: (keys, linkAlso = []) => {
      // New evidence nodes appear at once (the camera flies to them); their lines are the real edges between them
      // and the other keys, read from FalkorDB a moment later. No line is ever guessed.
      const known = new Set((graphRef.current?.nodes ?? []).map((n) => n.id));
      const focus = [...new Set([...keys.filter((k) => !known.has(k)), ...linkAlso])];
      if (focus.length === 0) return;
      setGraph((cur) => (cur ? withEvidence(cur, keys) ?? cur : cur));
      postJSON<ToolResult>("/graph/edges", { keys, focus })
        .then((r) => setGraph((cur) => (cur ? withEvidenceLinks(cur, r.rows as EdgeRow[]) ?? cur : cur)))
        .catch(() => { /* nodes stay without lines; never draw a guessed one */ });
    },
    clearEvidence: () => setGraph((cur) => cur ? {
      nodes: cur.nodes.filter((n) => !n.evidence),
      links: cur.links.filter((l) => l.type !== "EVIDENCE"),
    } : cur),
    caption: setCaption,
    markDown: (service) => {
      const g = graphRef.current;
      setDown((d) => new Set([...d, `Service:${service}`]));
      // several services can be down at once (what-if): the failure paths add up
      if (g) setFailing((f) => new Set([...f, ...upstream(service, g.links).links]));
    },
    clearDown: () => { setDown(new Set()); setFailing(new Set()); },
    setWhatIf: (zones, people) => { setZoneOverride(zones); setGone(new Set(people)); },
    refreshMap: () => refreshMap(false),
    log,
    select: setSelected,
    setAutoRotate: rotate,
    // While only the snapshot is showing, any API call fails: say why instead of "Failed to fetch".
    setError: (msg) => setError(msg && offlineRef.current
      ? "This needs the live Keystone backend, which is offline right now. The map above is a snapshot; run Keystone locally (see the README) for incidents, what-if and the guard."
      : msg),
  }), [graph, refreshMap, log, rotate]);

  // The incident panel pauses polling while a run streams, so map merges don't interrupt the camera flight.
  const setPollPaused = useCallback((p: boolean) => { pollPaused.current = p; }, []);

  const visual: Visual = useMemo(() => ({ highlight, failingLinks, down, zoneOverride, gone, selected }),
    [highlight, failingLinks, down, zoneOverride, gone, selected]);

  const onReady = useCallback((h: GraphHandle) => { handle.current = h; }, []);
  // Dev-only hook for headless UI checks: node positions and the camera.
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") (window as unknown as Record<string, unknown>).__ks = { graphRef, handle };
  }, []);

  // Any node on the map opens its card (evidence pulled in by the agent is explained in the incident panel instead).
  // During an incident the panel stays put, so clicking around doesn't interrupt the story.
  const onNodeClick = useCallback((n: GNode) => {
    if (n.evidence) return;
    setMode((m) => (m === "incident" ? m : "map"));
    setSelected(n.id);
  }, []);

  const clearSelection = useCallback(() => {
    if (mode !== "map") return;
    setSelected(null);
    setHighlight(new Set());
    setCaption(null);
  }, [mode]);

  // Slide the view (camera + its target together). Stops auto-rotate, which would otherwise swing the view away.
  const pan = useCallback((dx: number, dy: number) => { rotate(false); handle.current?.pan(dx, dy); }, [rotate]);

  // Esc = back to the whole map. Arrow keys (or WASD) slide the view; hold Shift for a bigger step.
  useEffect(() => {
    const PAN: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1], a: [-1, 0], d: [1, 0], w: [0, 1], s: [0, -1],
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "Escape") clearSelection();
      const p = PAN[e.key.length === 1 ? e.key.toLowerCase() : e.key];
      if (p && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        const k = e.shiftKey ? 0.3 : 0.12;
        pan(p[0] * k, p[1] * k);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clearSelection, pan]);

  const switchMode = (m: Mode) => {
    setMode(m);
    setCaption(null);
    rotate(m === "map" && !selected);
  };

  const toggleLayer = (l: Layer) => setHidden((h) => {
    const next = new Set(h);
    if (next.has(l)) next.delete(l); else next.add(l);
    return next;
  });

  const reset = async () => {
    setBusyReset(true);
    try {
      const r = await postJSON<{ nodes: number }>("/reset");
      setHighlight(new Set()); setDown(new Set()); setFailing(new Set()); setZoneOverride({}); setGone(new Set());
      setSelected(null); setCaption(null); setCypher([]);
      setResetKey((k) => k + 1);
      await refreshMap(true);
      setCaption(`Demo graph rebuilt from data/northwind (${r.nodes} nodes)`);
    } catch (e) {
      setError(offlineRef.current ? "Reset needs the live Keystone backend, which is offline right now." : (e as Error).message);
    } finally {
      setBusyReset(false);
    }
  };
  const [resetKey, setResetKey] = useState(0);

  return (
    <div className="fixed inset-0 overflow-hidden bg-[#05070d] text-slate-100">
      <div className="absolute inset-0">
        {graph && <Graph3D data={graph} visual={visual} width={size.w} height={size.h} offsetX={PANEL_W / 2}
          hidden={hidden} onNodeClick={onNodeClick} onBackgroundClick={clearSelection}
          onInteract={() => setRotating(false)} onReady={onReady} />}
      </div>
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_35%_50%,transparent_40%,rgba(0,0,0,0.6)_100%)]" />

      {/* top bar */}
      <header className="absolute left-4 right-4 top-4 flex items-center gap-4">
        <div className="ks-blue flex items-center gap-3 rounded-xl border px-4 py-2 backdrop-blur">
          <div className="h-3 w-3 rotate-45 bg-gradient-to-br from-white to-sky-300 shadow-[0_0_14px_#9ec5ff]" />
          <div>
            <div className="text-[15px] font-semibold tracking-wide">Keystone</div>
            <div className="text-[11px] text-blue-100/75">Northwind Labs · company brain on FalkorDB</div>
          </div>
        </div>
        <nav className="ks-blue flex gap-1 rounded-xl border p-1 backdrop-blur">
          {MODES.map((m) => (
            <button key={m.id} onClick={() => switchMode(m.id)}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${mode === m.id ? "ks-blue-on" : "text-blue-100/75 hover:bg-white/10 hover:text-white"}`}>
              {m.label}
            </button>
          ))}
        </nav>
        {graph && <SearchBox nodes={graph.nodes} onPick={(id) => { setMode("map"); setSelected(id); }} />}
        <div className="ml-auto flex items-center gap-2">
          {clock && (
            <div className="ks-blue rounded-xl border px-3 py-2 text-[12px] backdrop-blur"
              title="All data is synthetic and pinned to this demo clock">
              {fmtClock(clock.now)} · {clock.week}
            </div>
          )}
          <Button onClick={() => {
            handle.current?.overview(); setHighlight(new Set()); setCaption(null);
            if (mode === "map") setSelected(null);
          }} className="ks-blue">Overview</Button>
          <Button onClick={reset} disabled={busyReset} className="ks-blue">{busyReset ? "Resetting…" : "Reset demo"}</Button>
        </div>
      </header>

      {/* side panel */}
      <aside className="absolute bottom-4 right-4 top-[84px] w-[430px] overflow-y-auto rounded-2xl border border-white/10 bg-[#070b16]/80 p-5 shadow-2xl backdrop-blur-md">
        <div hidden={mode !== "map"}><MapPanel key={`m${resetKey}`} ops={ops} selected={selected} /></div>
        <div hidden={mode !== "incident"}>
          <IncidentPanel key={`i${resetKey}`} ops={ops} setPollPaused={setPollPaused} />
        </div>
        <div hidden={mode !== "whatif"}><WhatIfPanel key={`w${resetKey}`} ops={ops} active={mode === "whatif"} /></div>
      </aside>

      {/* caption: one line per agent hop */}
      {caption && (
        <div className="pointer-events-none absolute bottom-[96px] left-6 right-[470px] flex justify-center">
          <div className="max-w-4xl rounded-xl border border-white/10 bg-black/65 px-5 py-3 text-center text-[17px] leading-snug text-white shadow-xl backdrop-blur">
            {caption}
          </div>
        </div>
      )}

      <PanPad onPan={pan} onRecenter={() => handle.current?.overview()} />
      <MapToolbar hidden={hidden} onToggle={toggleLayer} rotating={rotating} onRotate={() => rotate(!rotating)}
        right={PANEL_W + 16} />

      <CypherDrawer entries={cypher} />

      {offline && !error && (
        <div className="pointer-events-none absolute left-1/2 top-[84px] -translate-x-[calc(50%+215px)] rounded-full border border-sky-300/30 bg-[#021449]/85 px-4 py-1.5 text-[12px] text-sky-100 backdrop-blur">
          Live backend offline: showing a snapshot of the starting graph. Run it locally for incidents and what-if (see README).
        </div>
      )}
      {error && (
        <div className="absolute left-1/2 top-24 w-[640px] -translate-x-1/2 rounded-xl border border-rose-400/40 bg-rose-950/80 px-4 py-3 text-sm text-rose-100 backdrop-blur">
          {error}
          <button className="ml-3 text-rose-300 underline" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}
    </div>
  );
}
