// What the side panels can do to the 3D view (implemented in KeystoneApp).
import type { GraphData } from "@/lib/types";

export interface Ops {
  graph: GraphData | null;
  highlight(ids: string[]): void;
  flyTo(ids: string[], ms?: number): void;
  overview(): void;
  // add evidence nodes (PRs, messages, docs...) for keys the map doesn't show, with their real edges to the other keys;
  // `linkAlso` = nodes already on the map whose real edges to `keys` should be drawn too (e.g. an incident's CITES)
  inject(keys: string[], linkAlso?: string[]): void;
  clearEvidence(): void;
  caption(text: string | null): void;
  markDown(service: string): void;       // service DOWN + failure ripple upstream
  clearDown(): void;
  setWhatIf(zones: Record<string, string>, gone: string[]): void;
  refreshMap(): Promise<void>;
  log(title: string, res: { ms: number; cypher: string }): void;
  select(id: string | null): void;
  setAutoRotate(on: boolean): void;
  setError(msg: string | null): void;
}
