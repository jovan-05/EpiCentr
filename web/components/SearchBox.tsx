"use client";
// Find anything on the map by name: services, people (handle or full name), teams, agents, rules.
// Picking a result opens its card and flies the camera there. "/" focuses the box from anywhere.

import { useEffect, useMemo, useRef, useState } from "react";
import { TYPE_COLOR, ZONE_COLOR } from "@/lib/graph";
import type { GNode } from "@/lib/types";

const KIND: Record<string, string> = {
  Service: "service", Person: "person", Team: "team", CodingAgent: "agent", Decision: "rule", Incident: "incident",
};

export default function SearchBox({ nodes, onPick }: { nodes: GNode[]; onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const results = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/^@/, "");
    if (!s) return [];
    const score = (n: GNode) => {
      const label = String(n.label).toLowerCase(), name = String(n.name ?? "").toLowerCase();
      if (label === s) return 0;
      if (label.startsWith(s)) return 1;
      if (name.split(" ").some((w) => w.startsWith(s))) return 2;
      if (label.includes(s) || name.includes(s)) return 3;
      return 9;
    };
    return nodes.filter((n) => KIND[n.type] && !n.evidence)
      .map((n) => ({ n, sc: score(n) })).filter((r) => r.sc < 9)
      .sort((a, b) => a.sc - b.sc || String(a.n.label).localeCompare(String(b.n.label)))
      .slice(0, 8).map((r) => r.n);
  }, [q, nodes]);

  const pick = (n: GNode) => { onPick(n.id); setQ(""); setOpen(false); input.current?.blur(); };

  return (
    <div className="relative">
      <input ref={input} value={q} placeholder="Search services, people, teams…  ( / )"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          if (e.key === "Enter" && results[active]) pick(results[active]);
          if (e.key === "Escape") { setQ(""); input.current?.blur(); }
        }}
        className="ks-blue w-[290px] rounded-xl border px-3 py-2 text-sm backdrop-blur focus:outline-none" />
      {open && results.length > 0 && (
        <div className="absolute left-0 top-full z-20 mt-1 w-[290px] overflow-hidden rounded-xl border border-white/10 bg-[#070b16]/95 py-1 shadow-2xl backdrop-blur">
          {results.map((n, i) => {
            const color = n.type === "Service" ? ZONE_COLOR[n.zone] : TYPE_COLOR[n.type];
            const gone = n.status === "departed";
            return (
              <button key={n.id} onMouseDown={() => pick(n)} onMouseEnter={() => setActive(i)}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${i === active ? "bg-white/10" : ""}`}>
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
                <span className={gone ? "text-slate-500 line-through" : ""}>{n.type === "Person" ? `@${n.label}` : n.label}</span>
                {n.type === "Person" && n.name && <span className="truncate text-xs text-slate-500">{n.name}</span>}
                <span className="ml-auto text-[11px] text-slate-500">{KIND[n.type]}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
