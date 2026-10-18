"use client";
// Bottom-right of the 3D view: a pan pad (slide the view without rotating), layer switches to declutter,
// auto-rotate on/off, and how to move around.

import type { Layer } from "@/lib/graph";

const LAYERS: { id: Layer; label: string; color: string }[] = [
  { id: "people", label: "People", color: "#e8edf5" },
  { id: "teams", label: "Teams", color: "#7c8aa5" },
  { id: "agents", label: "AI agents", color: "#a78bfa" },
  { id: "rules", label: "Rules & incidents", color: "#f5c542" },
];

const STEP = 0.2;   // a fifth of the view per click

/** Pan pad, top-left under the logo: arrows move the view that way (to see more of the right side, press ▶). */
export function PanPad({ onPan, onRecenter }: { onPan: (dx: number, dy: number) => void; onRecenter: () => void }) {
  const padBtn = "grid h-8 w-8 place-items-center rounded-md text-[13px] text-slate-200 hover:bg-white/15 active:bg-white/25";
  return (
    <div className="absolute left-4 top-[84px] z-10 flex flex-col items-center gap-1">
      <div className="grid grid-cols-3 gap-0.5 rounded-xl border border-white/10 bg-black/50 p-1 backdrop-blur">
        <span />
        <button className={padBtn} title="Look up (↑ / W)" onClick={() => onPan(0, STEP)}>▲</button>
        <span />
        <button className={padBtn} title="Look further left (← / A)" onClick={() => onPan(-STEP, 0)}>◀</button>
        <button className={`${padBtn} text-[11px]`} title="Recenter: fit the whole map" onClick={onRecenter}>◎</button>
        <button className={padBtn} title="Look further right (→ / D)" onClick={() => onPan(STEP, 0)}>▶</button>
        <span />
        <button className={padBtn} title="Look down (↓ / S)" onClick={() => onPan(0, -STEP)}>▼</button>
        <span />
      </div>
      <span className="text-[10px] text-slate-500">move · arrow keys</span>
    </div>
  );
}

export default function MapToolbar({ hidden, onToggle, rotating, onRotate, right }: {
  hidden: Set<Layer>; onToggle: (l: Layer) => void; rotating: boolean; onRotate: () => void; right: number;
}) {
  return (
    <div className="absolute bottom-4 flex items-end gap-2" style={{ right }}>
      <div className="flex flex-col items-end gap-1.5">
        <div className="flex items-center gap-1 rounded-xl border border-white/10 bg-black/50 p-1 backdrop-blur">
          {LAYERS.map((l) => {
            const on = !hidden.has(l.id);
            return (
              <button key={l.id} onClick={() => onToggle(l.id)} title={on ? `Hide ${l.label.toLowerCase()}` : `Show ${l.label.toLowerCase()}`}
                className={`flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] transition ${on ? "text-slate-100 hover:bg-white/10" : "text-slate-500 line-through hover:bg-white/5"}`}>
                <span className="h-2 w-2 rounded-full" style={{ background: on ? l.color : "#3a4152" }} />
                {l.label}
              </button>
            );
          })}
          <span className="mx-1 h-4 w-px bg-white/10" />
          <button onClick={onRotate} title="Auto-rotate"
            className={`rounded-lg px-2 py-1 text-[12px] transition hover:bg-white/10 ${rotating ? "text-sky-200" : "text-slate-400"}`}>
            {rotating ? "⟳ Rotating" : "⟳ Rotate"}
          </button>
        </div>
        <div className="rounded-lg bg-black/40 px-2.5 py-1 text-[11px] text-slate-400 backdrop-blur">
          Drag: rotate · Right-drag or Shift+drag: move · Arrow keys: move · Scroll: zoom · Click: open · Esc: back
        </div>
      </div>
    </div>
  );
}
