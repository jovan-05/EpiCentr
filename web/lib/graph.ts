// Turning /map into what the 3D view draws: deterministic layout, merges that keep positions, evidence nodes from
// tool keys, failure ripples and the caption for each agent step. No three.js here (it must not run on the server).
import type { GLink, GNode, GraphData, MapData, Step } from "./types";

// Every colour encodes data (05 "Visual language").
export const ZONE_COLOR: Record<string, string> = { healthy: "#4da3ff", fragile: "#ffb020", dark: "#ff3352" };
export const TYPE_COLOR: Record<string, string> = {
  Person: "#e8edf5", Team: "#7c8aa5", CodingAgent: "#a78bfa", Incident: "#ff4d5e", Decision: "#f5c542",
  GuardCheck: "#ff8a3d", PR: "#38e1ff", Message: "#4ade80", Doc: "#cbd5e1", Ticket: "#fb923c", Option: "#f5c542",
};
export const VERDICT_COLOR: Record<string, string> = { block: "#ff3352", warn: "#ffb020", ok: "#4ade80" };

export const idOf = (x: string | GNode): string => (typeof x === "string" ? x : x.id);
export const linkId = (l: GLink): string => `${l.type}|${idOf(l.source)}|${idOf(l.target)}`;
export const keyValue = (id: string): string => id.slice(id.indexOf(":") + 1);

/** A node and everything one edge away from it (hover focus, click-to-inspect). */
export function neighbourhood(id: string, links: GLink[]): Set<string> {
  const out = new Set([id]);
  for (const l of links) {
    const s = idOf(l.source), t = idOf(l.target);
    if (s === id) out.add(t);
    else if (t === id) out.add(s);
  }
  return out;
}

/** Layers the viewer can switch off to declutter the map (services always stay). */
export type Layer = "people" | "teams" | "agents" | "rules";
export const LAYER_OF: Partial<Record<string, Layer>> = {
  Person: "people", Team: "teams", CodingAgent: "agents", Decision: "rules", GuardCheck: "rules", Incident: "rules",
};

/** Stable pseudo-random number in [0, 1) from a string, so the layout is identical on every load (same video takes). */
export function hash01(s: string, salt = 0): number {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

const TEAMS = ["payments", "checkout", "risk", "platform", "data", "growth", "mobile", "support-tools", "sre"];

function teamAngle(team: string | undefined, id: string): number {
  const i = team ? TEAMS.indexOf(team) : -1;
  const base = i >= 0 ? (i + 0.5) / TEAMS.length : hash01(id);
  return base * Math.PI * 2;
}

function seedPosition(n: GNode, teamOf: Record<string, string>): Partial<GNode> {
  const h1 = hash01(n.id, 1), h2 = hash01(n.id, 2);
  if (n.type === "Service") {
    const a = teamAngle(teamOf[n.id], n.id) + (h1 - 0.5) * 0.6;
    return { x: 70 * Math.cos(a), y: (h2 - 0.5) * 60, z: 70 * Math.sin(a) };
  }
  if (n.type === "Team") {
    const a = teamAngle(keyValue(n.id), n.id);
    return { x: 125 * Math.cos(a), y: 0, z: 125 * Math.sin(a) };
  }
  if (n.type === "Person") {
    const a = teamAngle(n.team, n.id) + (h1 - 0.5) * 0.7;
    const r = 175 + h2 * 35;
    return { x: r * Math.cos(a), y: (hash01(n.id, 3) - 0.5) * 100, z: r * Math.sin(a) };
  }
  if (n.type === "CodingAgent") return { x: (h1 - 0.5) * 80, y: 150, z: (h2 - 0.5) * 40 };
  return {};
}

/** /map -> display graph. DEPENDS_ON is drawn reversed (callee -> caller) so particles flow the way a failure spreads. */
export function buildDisplay(map: MapData): GraphData {
  const teamOf: Record<string, string> = {};
  for (const l of map.links) if (l.type === "OWNS") teamOf[idOf(l.target)] = keyValue(idOf(l.source));
  const maxPr = Math.max(1e-9, ...map.nodes.filter((n) => n.type === "Service").map((n) => n.pagerank ?? 0));
  const nodes: GNode[] = map.nodes.map((n) => {
    const seed = seedPosition(n, teamOf);
    return {
      ...n,
      ...(n.type === "Service" ? { size: 3 + Math.sqrt((n.pagerank ?? 0) / maxPr) * 9, team: teamOf[n.id] } : {}),
      ...seed,
      // anchor: a weak spring back to the seeded spot keeps the universe compact and grouped by team
      ...(seed.x !== undefined ? { sx: seed.x, sy: seed.y, sz: seed.z } : {}),
    };
  });
  // A link to a node that isn't drawn makes the 3D engine throw and blanks the whole view, so drop those.
  const ids = new Set(nodes.map((n) => n.id));
  const links: GLink[] = map.links
    .filter((l) => ids.has(idOf(l.source)) && ids.has(idOf(l.target)))
    .map((l) => l.type === "DEPENDS_ON" ? { ...l, source: l.target, target: l.source, reversed: true } : { ...l });
  placeNearNeighbours(nodes, links);
  return { nodes, links };
}

/** Nodes with no seeded position (incidents, decisions, guard checks) start next to something they link to. */
function placeNearNeighbours(nodes: GNode[], links: GLink[], known?: Map<string, GNode>) {
  const byId = known ?? new Map(nodes.map((n) => [n.id, n]));
  for (let pass = 0; pass < 3; pass++) {
    for (const n of nodes) {
      if (n.x !== undefined) continue;
      for (const l of links) {
        const s = idOf(l.source), t = idOf(l.target);
        const other = s === n.id ? byId.get(t) : t === n.id ? byId.get(s) : undefined;
        if (other && other.x !== undefined) { placeNear(n, other); break; }
      }
    }
  }
}

/** Start `n` just next to `other` (deterministic jitter, so every take looks the same). */
function placeNear(n: GNode, other: GNode) {
  // rules, incidents and guard checks float clearly above what they attach to, so labels don't collide
  const lift = n.type === "Decision" || n.type === "Incident" || n.type === "GuardCheck" ? 38 : 18;
  n.x = (other.x ?? 0) + (hash01(n.id, 4) - 0.5) * 36;
  n.y = (other.y ?? 0) + lift + hash01(n.id, 5) * 10;
  n.z = (other.z ?? 0) + (hash01(n.id, 6) - 0.5) * 36;
}

/** Apply a fresh /map onto the current graph without moving anything. `structural` = nodes/links added or removed. */
export function mergeDisplay(cur: GraphData, map: MapData): { graph: GraphData; structural: boolean } {
  const fresh = buildDisplay(map);
  const byId = new Map(cur.nodes.map((n) => [n.id, n]));
  let structural = false;
  const nodes: GNode[] = [];
  for (const n of fresh.nodes) {
    const old = byId.get(n.id);
    if (old) {
      const { x: _x, y: _y, z: _z, ...props } = n;   // eslint-disable-line @typescript-eslint/no-unused-vars
      Object.assign(old, props);
      nodes.push(old);
    } else {
      structural = true;
      delete n.x; delete n.y; delete n.z;
      nodes.push(n);
    }
  }
  const freshIds = new Set(fresh.nodes.map((n) => n.id));
  const evidence = cur.nodes.filter((n) => n.evidence && !freshIds.has(n.id));
  nodes.push(...evidence);
  if (nodes.length !== cur.nodes.length) structural = true;

  const ids = new Set(nodes.map((n) => n.id));
  const curLinks = new Map(cur.links.map((l) => [linkId(l), l]));
  const links: GLink[] = [];
  for (const l of fresh.links) {
    const old = curLinks.get(linkId(l));
    if (old) {
      Object.assign(old, { ...l, source: old.source, target: old.target });
      links.push(old);
    } else {
      structural = true;
      links.push(l);
    }
  }
  for (const l of cur.links) if (l.type === "EVIDENCE" && ids.has(idOf(l.source)) && ids.has(idOf(l.target))) links.push(l);
  if (links.length !== cur.links.length) structural = true;
  placeNearNeighbours(nodes, links, new Map(nodes.map((n) => [n.id, n])));
  return { graph: structural ? { nodes, links } : cur, structural };
}

/** Add nodes for evidence keys the map doesn't show (PR-4471, ADR-017, Lena's message...). Nodes only: each starts
 *  next to the nearest key in the list that's already drawn, so the camera can fly there at once. The lines come
 *  from the graph itself (withEvidenceLinks + POST /graph/edges); the key order is never used to guess a line. */
export function withEvidence(cur: GraphData, keys: string[]): GraphData | null {
  const byId = new Map(cur.nodes.map((n) => [n.id, n]));
  if (keys.every((k) => byId.has(k))) return null;
  const nodes = [...cur.nodes];
  keys.forEach((k, i) => {
    if (byId.has(k)) return;
    const [label] = k.split(":");
    const node: GNode = { id: k, type: label as GNode["type"], label: keyValue(k), evidence: true };
    for (let d = 1; d < keys.length; d++) {        // nearest drawn key, the one before it first
      const near = [byId.get(keys[i - d]), byId.get(keys[i + d])].find((n) => n && n.x !== undefined);
      if (near) { placeNear(node, near); break; }
    }
    byId.set(k, node);
    nodes.push(node);
  });
  return { nodes, links: cur.links };
}

/** One row of POST /graph/edges: a real relationship (or several) between two nodes, with its direction. */
export interface EdgeRow { source: string; target: string; types: string[] }

/** Draw the real edges FalkorDB returned for evidence nodes. They keep type "EVIDENCE" (colour, particles, cleanup)
 *  and carry the relationship in `rel` ("REVIEWED", "AUTHORED · PROMPTED"...), shown when you hover the line. */
export function withEvidenceLinks(cur: GraphData, rows: EdgeRow[]): GraphData | null {
  const ids = new Set(cur.nodes.map((n) => n.id));
  // pairs already joined by any line (e.g. an incident's IMPACTS / ASSIGNED_TO from /map): don't draw a second one
  const pair = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);
  const have = new Set(cur.links.map((l) => pair(idOf(l.source), idOf(l.target))));
  const add: GLink[] = [];
  for (const r of rows) {
    if (!ids.has(r.source) || !ids.has(r.target)) continue;     // a line to a node that isn't drawn blanks the view
    if (have.has(pair(r.source, r.target))) continue;
    have.add(pair(r.source, r.target));
    add.push({ source: r.source, target: r.target, type: "EVIDENCE", rel: r.types.join(" · ") });
  }
  return add.length ? { nodes: cur.nodes, links: [...cur.links, ...add] } : null;
}

/** Services a failure reaches (everything that depends on `service`, transitively) and the links it travels. */
export function upstream(service: string, links: GLink[]): { services: Set<string>; links: Set<string> } {
  const start = `Service:${service}`;
  const seen = new Set([start]);
  const failing = new Set<string>();
  const queue = [start];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const l of links) {
      if (l.type === "DEPENDS_ON" && idOf(l.source) === cur) {   // display links run callee -> caller
        failing.add(linkId(l));
        const t = idOf(l.target);
        if (!seen.has(t)) { seen.add(t); queue.push(t); }
      }
    }
  }
  return { services: seen, links: failing };
}

/** One caption per agent hop, built from the rows that tool returned (the camera shows the same nodes). */
export function captionFor(step: Step): string {
  const d = step.data ?? [];
  const r = d[0];
  switch (step.tool) {
    case "suspect_changes": {
      if (!r) return "No changes right before the outage";
      const reviews = (r.reviews ?? [])
        .map((v: { by: string; secs: number; comments: number }) => `@${v.by} in ${v.secs} s${v.comments === 0 ? " with 0 comments" : ""}`)
        .join(", ");
      const by = r.agent_author ?? `@${r.human_author}`;
      return `${r.pr} "${r.title}" by ${by}${r.prompted_by ? ` (prompted by @${r.prompted_by})` : ""}, ${r.hours_ago} h ago. Approved by ${reviews || "nobody"}`;
    }
    case "blast_radius":
      return `${d.length} services break with it: ${d.map((x: { service: string }) => x.service).join(", ")}`;
    case "current_owners":
      return r ? `Org chart: team ${r.team}. On call: @${r.on_call}${r.on_call_departed ? ", who LEFT the company" : ""}` : "Nobody owns it";
    case "who_knows":
      return r ? `@${r.person} actually understands it: score ${Number(r.score).toFixed(1)} (${r.authored} PRs, ${r.deep_reviews} deep reviews)` : "Nobody active understands it";
    case "decision_trace":
      return r ? `Why it's built this way: ${r.decision}, ${r.summary}` : "No recorded decisions";
    case "prior_warnings": {
      const m = d.find((x: { kind: string }) => x.kind === "message") ?? r;
      const t = d.find((x: { kind: string }) => x.kind === "ticket");
      return m ? `@${m.by} warned${m.channel ? ` in ${m.channel}` : ""}: "${m.text}"${t ? ` (${t.id} still ${t.status})` : ""}` : "Nobody warned about this";
    }
    case "stamp_chains":
      return `${d.length} agent PRs approved in under 2 minutes with no comments`;
    case "check_decision":
      return `Guard: ${r ? r.decision : "no matching decision"}`;
    default:
      return `${step.tool}: ${step.rows} rows`;
  }
}

export function fmtClock(ms: number): string {
  return new Date(ms).toLocaleString("en-GB", {
    weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    timeZone: "UTC",
  }) + " UTC";
}
