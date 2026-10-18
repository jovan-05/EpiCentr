// Thin client for the Keystone API. Use 127.0.0.1 (on Windows "localhost" tries IPv6 first and adds ~2 s per call).
import type { MapData, Step } from "./types";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch { /* not JSON */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export async function getJSON<T>(path: string): Promise<T> {
  return handle<T>(await fetch(`${API}${path}`, { cache: "no-store" }));
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  return handle<T>(await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
}

export const getMap = () => getJSON<MapData>("/map");

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface StreamHandlers {
  down?: (e: any) => void;
  step?: (e: Step) => void;
  thinking?: (e: any) => void;
  fallback?: (e: any) => void;
  grounded?: (e: any) => void;
  action?: (e: any) => void;
  result?: (e: any) => void;
  error?: (e: any) => void;
}
/* eslint-enable @typescript-eslint/no-explicit-any */

/** Run an incident and receive the agent's events live (Server-Sent Events). Returns a function that stops it.
 *  The stream is closed on result/error: EventSource would otherwise reconnect and start a second run. */
export function streamIncident(service: string, mode: "playbook" | "agent", h: StreamHandlers): () => void {
  const url = `${API}/incident/simulate/stream?service=${encodeURIComponent(service)}&mode=${mode}&act=true`;
  const es = new EventSource(url);
  const types = ["down", "step", "thinking", "fallback", "grounded", "action", "result", "error"] as const;
  for (const t of types) {
    es.addEventListener(t, (ev) => {
      const data = JSON.parse((ev as MessageEvent).data);
      if (t === "result" || t === "error") es.close();
      h[t]?.(data);
    });
  }
  es.onerror = () => {
    if (es.readyState !== EventSource.CLOSED) {
      es.close();
      h.error?.({ message: "Lost connection to the Keystone API (is uvicorn running on :8000?)" });
    }
  };
  return () => es.close();
}
