"use client";
// Every query the UI caused, with FalkorDB's own execution time. "Show me the Cypher" for judges.
import { useState } from "react";
import type { CypherEntry } from "@/lib/types";

export default function CypherDrawer({ entries }: { entries: CypherEntry[] }) {
  const [open, setOpen] = useState(false);
  return (
    // The wrapper spans the bottom of the screen but must not swallow clicks meant for the map or the pan pad
    // underneath it: only the button and the open drawer take pointer events.
    <div className="pointer-events-none absolute bottom-4 left-4 right-[460px] z-10">
      {open && (
        <div className="pointer-events-auto mb-2 max-h-[46vh] overflow-y-auto rounded-xl border border-white/10 bg-[#060a14]/95 p-3 backdrop-blur">
          {entries.length === 0 && <p className="text-sm text-slate-500">No queries yet.</p>}
          {entries.map((e) => (
            <div key={e.at + e.title} className="mb-3">
              <div className="mb-1 flex items-center gap-2 text-[12px]">
                <span className="font-medium text-sky-300">{e.title}</span>
                <span className="ml-auto tabular-nums text-emerald-300">{e.ms.toFixed(2)} ms</span>
              </div>
              <pre className="overflow-x-auto whitespace-pre rounded-lg bg-black/50 p-2 font-mono text-[11px] leading-relaxed text-slate-300">{e.cypher}</pre>
            </div>
          ))}
        </div>
      )}
      <button onClick={() => setOpen(!open)}
        className="pointer-events-auto rounded-lg border border-white/10 bg-black/55 px-3 py-1.5 font-mono text-[12px] text-slate-300 backdrop-blur hover:text-white">
        {open ? "▾" : "▸"} Cypher ({entries.length}){entries[0] ? ` · last ${entries[0].ms.toFixed(1)} ms` : ""}
      </button>
    </div>
  );
}
