// Small shared UI pieces.
import type { ReactNode } from "react";

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="mb-5">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Button({ children, onClick, disabled, tone = "default", className = "" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; tone?: "default" | "danger" | "primary" | "gold";
  className?: string;
}) {
  const tones = {
    default: "border-white/15 bg-white/5 hover:bg-white/10 text-slate-100",
    primary: "border-sky-400/40 bg-sky-500/20 hover:bg-sky-500/30 text-sky-100",
    danger: "border-rose-400/50 bg-rose-500/20 hover:bg-rose-500/35 text-rose-100",
    gold: "border-amber-300/50 bg-amber-400/20 hover:bg-amber-400/30 text-amber-100",
  };
  return (
    <button onClick={onClick} disabled={disabled}
      className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${tones[tone]} ${className}`}>
      {children}
    </button>
  );
}

export function Pill({ children, color }: { children: ReactNode; color: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium"
      style={{ borderColor: `${color}66`, color, background: `${color}14` }}>{children}</span>
  );
}

export function Dot({ color }: { color: string }) {
  return <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />;
}

export function Spinner() {
  return <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/20 border-t-white/80" />;
}
