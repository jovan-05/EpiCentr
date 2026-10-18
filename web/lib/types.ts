// Shapes returned by the Keystone API (api/main.py). Node ids are "Label:key", the same strings tools return in `keys`.

export type NodeType =
  | "Service" | "Team" | "Person" | "CodingAgent" | "Incident" | "Decision" | "GuardCheck"
  | "PR" | "Message" | "Doc" | "Ticket" | "Option";

export interface GNode {
  id: string;
  type: NodeType;
  label: string;
  evidence?: boolean; // injected by the UI from tool keys (PRs, messages, docs...), not part of /map
  size?: number;
  x?: number; y?: number; z?: number;
  fx?: number; fy?: number; fz?: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
}

export interface GLink {
  source: string | GNode;
  target: string | GNode;
  type: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
}

export interface GraphData { nodes: GNode[]; links: GLink[] }

export interface MapData extends GraphData {
  clock: { now: number; week: string };
  threshold: number;
  cypher: string;
  ms: number;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface ToolResult { rows: any[]; keys: string[]; cypher: string; params: any; ms: number; [k: string]: any }

export interface Step {
  type: "step"; n: number; tool: string; args: Record<string, unknown>;
  keys: string[]; rows: number; ms: number; cypher: string; data: any[];
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export interface Claim { text: string; keys: string[]; tool?: string }

export interface Action {
  type: string; status: string; assignee?: string; id?: string; title?: string; reason?: string;
  downstream?: string[]; cited?: number; reassigned_from?: string;
}

export interface IncidentResult {
  service: string; mode: string; models: string[]; llm_calls: number; seconds: number;
  summary: string; claims: Claim[]; dropped: (Claim & { dropped_because: string })[];
  actions: Action[]; steps: Step[];
}

export interface Lesson {
  summary: string; chosen_option: string; rejected_option: string; reason: string;
  keywords: string; affects: string[];
}

export interface CypherEntry { title: string; ms: number; cypher: string; at: number }

export interface Visual {
  highlight: Set<string>;     // node ids to light up (everything else dims)
  failingLinks: Set<string>;  // DEPENDS_ON links carrying the failure (red particles)
  down: Set<string>;          // services currently down
  zoneOverride: Record<string, string>; // what-if: service id -> zone it would fall to
  gone: Set<string>;          // what-if: people treated as departed
  selected: string | null;
}
