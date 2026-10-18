"use client";
// The 3D company universe (react-force-graph-3d on three.js). Every visual effect encodes data (05 "Visual language"):
//   Service sphere size = PageRank, colour = zone (blue healthy / amber fragile / red dark), halo brightness = agent
//   share, red pulse = dark zone or DOWN. Person = white disc with initials, grey + translucent when departed.
//   CodingAgent = violet hexagon. Decision = gold diamond. Incident = red icosahedron. GuardCheck = small tetrahedron
//   coloured by verdict. Particles: UNDERSTANDS (speed = score), AGENT_WROTE (violet), failure spreading (red).
// Visual state (highlight, down, what-if) is applied in an animation loop, so highlighting never rebuilds objects.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { LAYER_OF, TYPE_COLOR, VERDICT_COLOR, ZONE_COLOR, idOf, linkId, neighbourhood, type Layer } from "@/lib/graph";
import type { GLink, GNode, GraphData, Visual } from "@/lib/types";

export interface GraphHandle {
  flyTo(ids: string[], ms?: number): void;
  overview(ms?: number): void;
  setAutoRotate(on: boolean): void;
  camera(): { x: number; y: number; z: number };
  screen(id: string): { x: number; y: number } | null;   // where a node is on screen (headless UI checks)
  pan(dx: number, dy: number): void;   // slide the view sideways/up-down; dx, dy in fractions of the view (0.25 = a quarter)
}

interface Props {
  data: GraphData;
  visual: Visual;
  width: number;
  height: number;
  offsetX?: number;
  hidden?: Set<Layer>;                 // layers switched off in the toolbar
  onNodeClick?: (n: GNode) => void;
  onBackgroundClick?: () => void;
  onInteract?: () => void;             // the viewer grabbed the camera (stop auto-rotate)
  onReady?: (h: GraphHandle) => void;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FG = ForceGraph3D as any;

const LINK_DISTANCE: Record<string, number> = {
  DEPENDS_ON: 38, OWNS: 42, MEMBER_OF: 30, UNDERSTANDS: 75, AGENT_WROTE: 85, EVIDENCE: 18, AFFECTS: 48,
  CAUSED_BY: 36, IMPACTS: 40, ASSIGNED_TO: 45, MATCHED: 22, CHECKED: 22,
};

/** Weak spring pulling seeded nodes (services, teams, people, agents) back to their seed position: without it,
 *  people with no current links (the CTO, departed people) drift ~700 units out and the map looks tiny. */
function anchorForce(strength: number) {
  let nodes: GNode[] = [];
  const force = (alpha: number) => {
    for (const n of nodes) {
      if (n.sx === undefined || n.fx !== undefined) continue;
      const k = strength * alpha * (n.type === "Service" ? 0.4 : 1);
      n.vx = (n.vx ?? 0) + (n.sx - (n.x ?? 0)) * k;
      n.vy = (n.vy ?? 0) + (n.sy - (n.y ?? 0)) * k;
      n.vz = (n.vz ?? 0) + (n.sz - (n.z ?? 0)) * k;
    }
  };
  force.initialize = (ns: GNode[]) => { nodes = ns; };
  return force;
}
const LINK_STRENGTH: Record<string, number> = { UNDERSTANDS: 0.12, AGENT_WROTE: 0.08, OWNS: 0.35, MEMBER_OF: 0.5 };

// ---------------------------------------------------------------- three.js helpers (client only)

let glowTex: THREE.Texture | null = null;
function glowTexture(): THREE.Texture {
  if (glowTex) return glowTex;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.25, "rgba(255,255,255,0.45)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  glowTex = new THREE.CanvasTexture(c);
  return glowTex;
}

function textSprite(text: string, color = "#dbe7ff", height = 3.6, weight = 600): THREE.Sprite {
  const px = 48;
  const c = document.createElement("canvas");
  const g = c.getContext("2d")!;
  const font = `${weight} ${px}px "Segoe UI", system-ui, sans-serif`;
  g.font = font;
  c.width = Math.ceil(g.measureText(text).width) + 24;
  c.height = px + 20;
  g.font = font;
  g.fillStyle = color;
  g.textBaseline = "middle";
  g.shadowColor = "rgba(0,0,0,0.9)";
  g.shadowBlur = 8;
  g.fillText(text, 12, c.height / 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  s.scale.set((height * c.width) / c.height, height, 1);
  return s;
}

function initialsSprite(handle: string): THREE.Sprite {
  const c = document.createElement("canvas");
  c.width = c.height = 96;
  const g = c.getContext("2d")!;
  g.fillStyle = "#ffffff";
  g.beginPath();
  g.arc(48, 48, 44, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#0b1020";
  g.font = '700 40px "Segoe UI", system-ui, sans-serif';
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(handle.slice(0, 2).toUpperCase(), 48, 51);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  s.scale.set(5, 5, 1);
  return s;
}

function basic(color: string, opacity = 1, wireframe = false) {
  return new THREE.MeshBasicMaterial({ color, transparent: true, opacity, wireframe });
}

function halo(color: string, scale: number, opacity: number): THREE.Sprite {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture(), color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  s.scale.setScalar(scale);
  return s;
}

interface Parts {
  core: THREE.Mesh | THREE.Sprite;
  halo?: THREE.Sprite;
  label?: THREE.Sprite;
  baseHalo: number;
  baseOpacity: number;
}

function makeObject(n: GNode): THREE.Object3D {
  const g = new THREE.Group();
  let parts: Parts;
  switch (n.type) {
    case "Service": {
      const r = n.size ?? 5;
      const color = ZONE_COLOR[n.zone] ?? ZONE_COLOR.healthy;
      const core = new THREE.Mesh(new THREE.SphereGeometry(r, 24, 16), basic(color));
      const h = halo(color, r * 5, 0.5);
      const label = textSprite(n.label, "#e6eeff", 5.2);
      label.position.set(0, -r - 4.5, 0);
      g.add(h, core, label);
      parts = { core, halo: h, label, baseHalo: r * 5, baseOpacity: 1 };
      break;
    }
    case "Person": {
      const core = initialsSprite(n.label);
      // Name tag, shown only while the person is highlighted (a cited claim, a team, a what-if).
      const gone = n.status === "departed";
      const label = textSprite(`@${n.label}${gone ? " · left" : ""}`, gone ? "#9aa3b8" : "#ffffff", 3.8, 600);
      label.position.set(0, -4.4, 0);
      setOpacity(label, 0);
      g.add(core, label);
      parts = { core, label, baseHalo: 0, baseOpacity: 1 };
      break;
    }
    case "Team": {
      const core = new THREE.Mesh(new THREE.OctahedronGeometry(2.4), basic(TYPE_COLOR.Team, 0.7, true));
      const label = textSprite(n.label, "#b8c4dc", 3.4, 600);
      label.position.set(0, -4.5, 0);
      g.add(core, label);
      parts = { core, label, baseHalo: 0, baseOpacity: 0.7 };
      break;
    }
    case "CodingAgent": {
      const core = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 1.4, 6), basic(TYPE_COLOR.CodingAgent));
      core.rotation.x = Math.PI / 2;
      const h = halo(TYPE_COLOR.CodingAgent, 16, 0.55);
      const label = textSprite(n.label, "#cdbfff", 3.2);
      label.position.set(0, -6.5, 0);
      g.add(h, core, label);
      parts = { core, halo: h, label, baseHalo: 16, baseOpacity: 1 };
      break;
    }
    case "Incident": {
      const resolved = n.status === "resolved";
      const color = resolved ? "#8a4a55" : TYPE_COLOR.Incident;
      const core = new THREE.Mesh(new THREE.IcosahedronGeometry(4.2), basic(color));
      const h = halo(color, 22, resolved ? 0.2 : 0.7);
      const label = textSprite(n.label, resolved ? "#c79aa2" : "#ffd0d6", 3.2);
      label.position.set(0, -7, 0);
      g.add(h, core, label);
      parts = { core, halo: h, label, baseHalo: 22, baseOpacity: 1 };
      break;
    }
    case "Decision":
    case "Option": {
      const dim = n.status === "superseded";
      const core = new THREE.Mesh(new THREE.OctahedronGeometry(n.evidence ? 3 : 4.6), basic(TYPE_COLOR.Decision, dim ? 0.35 : 1));
      core.scale.y = 1.4;
      const h = halo(TYPE_COLOR.Decision, n.evidence ? 12 : 24, dim ? 0.15 : 0.65);
      const label = textSprite(n.label, "#ffe7a3", 3.2);
      label.position.set(0, -8, 0);
      g.add(h, core, label);
      parts = { core, halo: h, label, baseHalo: n.evidence ? 12 : 24, baseOpacity: dim ? 0.35 : 1 };
      break;
    }
    case "GuardCheck": {
      const color = VERDICT_COLOR[n.verdict] ?? TYPE_COLOR.GuardCheck;
      const core = new THREE.Mesh(new THREE.TetrahedronGeometry(2.8), basic(color));
      const h = halo(color, 12, 0.6);
      const label = textSprite(n.verdict === "block" ? "BLOCKED" : String(n.verdict).toUpperCase(), color, 2.6, 700);
      label.position.set(0, -5, 0);
      g.add(h, core, label);
      parts = { core, halo: h, label, baseHalo: 12, baseOpacity: 1 };
      break;
    }
    default: {
      // Evidence pulled in by the agent: PR (cyan box), Message (green), Doc (white), Ticket (orange).
      const color = TYPE_COLOR[n.type] ?? "#ffffff";
      const geom = n.type === "Message" ? new THREE.SphereGeometry(2.2, 12, 8)
        : n.type === "Doc" ? new THREE.BoxGeometry(3.2, 4, 0.4)
          : new THREE.BoxGeometry(3, 3, 3);
      const core = new THREE.Mesh(geom, basic(color));
      const h = halo(color, 10, 0.5);
      const label = textSprite(n.label, color, 2.8);
      label.position.set(0, -4.5, 0);
      g.add(h, core, label);
      parts = { core, halo: h, label, baseHalo: 10, baseOpacity: 1 };
    }
  }
  g.userData = { node: n, parts };
  return g;
}

function setOpacity(o: THREE.Object3D | undefined, v: number) {
  if (!o) return;
  const m = (o as THREE.Mesh).material as THREE.Material | undefined;
  if (m) m.opacity = v;
}

function setColor(o: THREE.Object3D | undefined, c: string) {
  const m = (o as THREE.Mesh | undefined)?.material as THREE.MeshBasicMaterial | undefined;
  if (m && "color" in m) m.color.set(c);
}

// ---------------------------------------------------------------- component

export default function Graph3D({ data, visual, width, height, offsetX = 0, hidden, onNodeClick, onBackgroundClick,
  onInteract, onReady }: Props) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fg = useRef<any>(null);
  const objects = useRef(new Map<string, THREE.Object3D>());
  const visualRef = useRef(visual);
  const dataRef = useRef(data);
  const interactRef = useRef(onInteract);
  // Hover focus: the hovered node and its direct neighbours light up (with name tags) while the pointer is on it.
  const [hoverSet, setHoverSet] = useState<Set<string> | null>(null);
  const hoverRef = useRef<Set<string> | null>(null);
  useEffect(() => {
    visualRef.current = visual; dataRef.current = data; hoverRef.current = hoverSet; interactRef.current = onInteract;
  });
  const onNodeHover = useCallback((n: GNode | null) => {
    setHoverSet(n ? neighbourhood(n.id, dataRef.current.links) : null);
  }, []);
  const visibleNode = useCallback((n: GNode) => !hidden?.has(LAYER_OF[n.type] as Layer), [hidden]);
  const visibleLink = useCallback((l: GLink) => {
    const s = l.source as GNode, t = l.target as GNode;
    return typeof s !== "object" || typeof t !== "object" || (visibleNode(s) && visibleNode(t));
  }, [visibleNode]);

  // The canvas spans the whole window (no seam at the panel edge); shifting the camera's view window by
  // `offsetX` pixels keeps the scene centred in the area left of the side panel.
  useEffect(() => {
    const cam = fg.current?.camera();
    if (!cam) return;
    cam.setViewOffset(width, height, offsetX, 0, width, height);
    cam.updateProjectionMatrix();
  }, [width, height, offsetX]);

  const nodeThreeObject = useCallback((n: GNode) => {
    const o = makeObject(n);
    objects.current.set(n.id, o);
    return o;
  }, []);

  // Animation loop: colours, pulses, dimming and highlight from the latest visual state.
  useEffect(() => {
    let raf = 0;
    const loop = (t: number) => {
      const v = visualRef.current;
      const hl = hoverRef.current ?? v.highlight;
      const anyHl = hl.size > 0;
      for (const [id, o] of objects.current) {
        const n = o.userData.node as GNode;
        const p = o.userData.parts as Parts;
        const on = hl.has(id) || v.selected === id;
        const dim = anyHl && !on;
        if (n.type === "Service") {
          const zone = v.zoneOverride[id] ?? n.zone;
          const down = v.down.has(id) || n.status === "down";
          const color = down ? "#ff1e3c" : ZONE_COLOR[zone] ?? ZONE_COLOR.healthy;
          setColor(p.core, color);
          setColor(p.halo, color);
          let pulse = 1;
          if (down) pulse = 1 + 0.6 * Math.abs(Math.sin(t / 170));
          else if (zone === "dark") pulse = 1 + 0.35 * (0.5 + 0.5 * Math.sin(t / 380));
          p.halo!.scale.setScalar(p.baseHalo * pulse);
          const share = Number(n.agent_share ?? 0);
          // A lit service keeps its zone colour: the halo is capped so bloom doesn't wash it out to white.
          const haloA = Math.min(1, (0.18 + 0.82 * share) * (down ? 1.4 : 1));
          setOpacity(p.halo, (on && !down ? Math.min(haloA, 0.28) : haloA) * (dim ? 0.12 : 1));
          setOpacity(p.core, dim ? 0.2 : 1);
          setOpacity(p.label, dim ? 0.18 : 1);
          o.scale.setScalar(on ? 1.12 : 1);
        } else if (n.type === "Person") {
          const gone = n.status === "departed" || v.gone.has(id);
          setColor(p.core, gone ? "#56607a" : on ? "#ffffff" : "#c3cde0");
          setOpacity(p.core, (gone ? (on ? 0.7 : 0.35) : 1) * (dim ? 0.2 : 1));
          setOpacity(p.label, on ? 1 : 0);
          o.scale.setScalar(on ? 1.6 : 1);
        } else if (n.type === "Incident") {
          // A resolved incident is drawn dark maroon; when it's lit (searched, clicked, cited) it must stand out more
          // than its neighbours, so it turns bright red, pulses and grows like an open one.
          const resolved = n.status === "resolved";
          const color = on ? "#ff4d5e" : resolved ? "#8a4a55" : TYPE_COLOR.Incident;
          setColor(p.core, color);
          setColor(p.halo, color);
          setOpacity(p.core, dim ? 0.15 : 1);
          setOpacity(p.label, dim ? 0.15 : 1);
          const beat = !resolved || on ? 1 + 0.3 * Math.abs(Math.sin(t / 260)) : 1;
          p.halo!.scale.setScalar(p.baseHalo * beat * (on ? 1.6 : 1));
          setOpacity(p.halo, dim ? 0.08 : on ? 0.9 : resolved ? 0.2 : 0.6);
          o.scale.setScalar(on ? 1.7 : 1);
        } else if (n.type === "Team") {
          // A lit team must read as "this team" from across the map: only the diamond grows (the label keeps its
          // size), turns bright and turns slowly.
          setColor(p.core, on ? "#dbe7ff" : TYPE_COLOR.Team);
          setOpacity(p.core, on ? 1 : p.baseOpacity * (dim ? 0.15 : 1));
          setOpacity(p.label, dim ? 0.15 : 1);
          p.core.scale.setScalar(on ? 2.4 : 1);
          p.core.rotation.y = on ? t / 900 : 0;
          if (p.label) p.label.position.y = on ? -8 : -4.5; // clear of the bigger diamond
          o.scale.setScalar(1);
        } else {
          setOpacity(p.core, p.baseOpacity * (dim ? 0.15 : 1));
          setOpacity(p.label, dim ? 0.15 : 1);
          if (p.halo) {
            p.halo.scale.setScalar(p.baseHalo * (on ? 1.4 : 1));
            setOpacity(p.halo, (dim ? 0.08 : 0.6));
          }
          o.scale.setScalar(on ? 1.45 : 1);
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Forces, bloom and orbit controls, once.
  const setupDone = useRef(false);
  useEffect(() => {
    const g = fg.current;
    if (!g || setupDone.current) return;
    setupDone.current = true;
    g.d3Force("charge").strength(-60);
    g.d3Force("link")
      .distance((l: GLink) => LINK_DISTANCE[l.type] ?? 40)
      .strength((l: GLink) => LINK_STRENGTH[l.type] ?? 0.6);
    g.d3Force("anchor", anchorForce(0.08));
    // Soft bloom with a high threshold: halos glow, but zone colours stay readable.
    const bloom = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.55, 0.35, 0.42);
    g.postProcessingComposer().addPass(bloom);
    const controls = g.controls();
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.35;
    controls.addEventListener("start", () => interactRef.current?.());
    g.cameraPosition({ x: 0, y: 140, z: 430 }, { x: 0, y: 0, z: 0 }, 0);

    const handle: GraphHandle = {
      flyTo(ids, ms = 1400) {
        const pts = dataRef.current.nodes.filter((n) => ids.includes(n.id) && n.x !== undefined);
        if (!pts.length) return;
        const c = pts.reduce((a, n) => ({ x: a.x + n.x! / pts.length, y: a.y + n.y! / pts.length, z: a.z + n.z! / pts.length }),
          { x: 0, y: 0, z: 0 });
        const radius = Math.max(...pts.map((n) => Math.hypot(n.x! - c.x, n.y! - c.y, n.z! - c.z)));
        const cam = g.cameraPosition();
        let dir = { x: cam.x - c.x, y: cam.y - c.y, z: cam.z - c.z };
        const len = Math.hypot(dir.x, dir.y, dir.z) || 1;
        dir = { x: dir.x / len, y: dir.y / len, z: dir.z / len };
        const dist = Math.max(120, radius * 2.3 + 90);
        g.cameraPosition({ x: c.x + dir.x * dist, y: c.y + dir.y * dist, z: c.z + dir.z * dist }, c, ms);
      },
      overview(ms = 1400) { g.zoomToFit(ms, 40, (n: GNode) => !n.evidence); },
      setAutoRotate(on) { controls.autoRotate = on; },
      camera() { return g.cameraPosition(); },
      pan(dx, dy) {
        // Move camera AND orbit target together along the screen's right/up axes, so the view slides without turning.
        // Step size scales with the zoom (distance to target), animated over ~260 ms.
        const cam = g.camera() as THREE.PerspectiveCamera;
        const dist = cam.position.distanceTo(controls.target);
        const viewH = 2 * dist * Math.tan(THREE.MathUtils.degToRad(cam.fov / 2));
        const right = new THREE.Vector3().setFromMatrixColumn(cam.matrix, 0).multiplyScalar(dx * viewH * cam.aspect);
        const up = new THREE.Vector3().setFromMatrixColumn(cam.matrix, 1).multiplyScalar(dy * viewH);
        const total = right.add(up);
        const t0 = performance.now(), ms = 260;
        let done = 0;
        const step = (t: number) => {
          const k = Math.min(1, (t - t0) / ms);
          const eased = 1 - (1 - k) ** 3;
          const d = total.clone().multiplyScalar(eased - done);
          done = eased;
          cam.position.add(d);
          controls.target.add(d);
          controls.update();
          if (k < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      },
      screen(id) {
        const n = dataRef.current.nodes.find((x) => x.id === id);
        return n?.x === undefined ? null : g.graph2ScreenCoords(n.x, n.y, n.z);
      },
    };
    onReady?.(handle);
  }, [onReady]);

  // Link styling. Recomputed when the visual state changes (accessor identity tells the graph to restyle).
  const linkStyle = useMemo(() => {
    const set = hoverSet ?? visual.highlight;
    const hl = (l: GLink) => set.has(idOf(l.source)) && set.has(idOf(l.target));
    const failing = (l: GLink) => visual.failingLinks.has(linkId(l));
    return {
      color: (l: GLink) => {
        if (failing(l)) return "#ff3352";
        const on = hl(l);
        switch (l.type) {
          case "DEPENDS_ON": return on ? "#9cc7ff" : "rgba(110,140,190,0.45)";
          case "UNDERSTANDS": return l.departed ? "rgba(120,125,145,0.25)" : on ? "#ffffff" : "rgba(220,235,255,0.4)";
          case "AGENT_WROTE": return on ? "#c4b5fd" : "rgba(167,139,250,0.5)";
          case "OWNS": case "MEMBER_OF": return on ? "#dbe7ff" : "rgba(120,140,170,0.14)";
          case "EVIDENCE": return on ? "#38e1ff" : "rgba(56,225,255,0.35)";
          case "AFFECTS": case "CAUSED_BY": return "rgba(245,197,66,0.8)";
          case "IMPACTS": return "rgba(255,77,94,0.75)";
          case "ASSIGNED_TO": return "#ffffff";
          case "MATCHED": case "CHECKED": return "rgba(255,138,61,0.9)";
          default: return "rgba(255,255,255,0.2)";
        }
      },
      width: (l: GLink) => {
        if (failing(l)) return 1.6;
        if (l.type === "UNDERSTANDS") return 0.3 + Math.min(1.6, (l.score ?? 0) / 20);
        if (hl(l)) return 1.1;
        return l.type === "AFFECTS" || l.type === "CAUSED_BY" || l.type === "ASSIGNED_TO" ? 0.8 : 0;
      },
      particles: (l: GLink) => {
        if (failing(l)) return 5;
        if (l.type === "UNDERSTANDS") return l.departed ? 0 : 2;
        if (l.type === "AGENT_WROTE") return 3;
        if (l.type === "EVIDENCE" || l.type === "MEMBER_OF" || l.type === "OWNS") return hl(l) ? 2 : 0;
        if (l.type === "MATCHED" || l.type === "CAUSED_BY") return 2;
        return 0;
      },
      particleSpeed: (l: GLink) => {
        if (failing(l)) return 0.014;
        if (l.type === "UNDERSTANDS") return 0.0015 + Math.min(0.012, (l.score ?? 0) / 2800);   // speed = score
        return 0.005;
      },
      particleWidth: (l: GLink) => (failing(l) ? 2.6 : l.type === "UNDERSTANDS" ? 1.1 : 1.5),
      particleColor: (l: GLink) => {
        if (failing(l)) return "#ff3352";
        return { UNDERSTANDS: "#ffffff", AGENT_WROTE: "#a78bfa", EVIDENCE: "#38e1ff", MATCHED: "#ff8a3d", CAUSED_BY: "#f5c542" }[l.type] ?? "#ffffff";
      },
    };
  }, [visual, hoverSet]);

  const label = useCallback((n: GNode) => {
    const row = (k: string, v: unknown) => `<div><span style="opacity:.6">${k}</span> ${v}</div>`;
    const head = `<div style="font-weight:600;margin-bottom:2px">${n.type}: ${n.label}</div>`;
    let body = "";
    if (n.type === "Service") {
      body = row("zone", n.zone) + row("tier", n.tier) + row("agent-written (90 d)", `${Math.round((n.agent_share ?? 0) * 100)}%`)
        + row("experts", (n.experts ?? []).map((h: string) => "@" + h).join(", ") || "nobody") + row("status", n.status);
    } else if (n.type === "Person") {
      body = row("name", n.name) + row("role", n.role) + row("team", n.team) + row("status", n.status);
    } else if (n.type === "Decision") {
      body = row("", n.summary ?? "") + row("status", n.status ?? "") + row("origin", n.origin ?? "evidence");
    } else if (n.type === "GuardCheck") {
      body = row("verdict", n.verdict) + row("caller", n.caller) + row("proposal", n.proposal);
    } else if (n.type === "Incident") {
      body = row("", n.title ?? "") + row("status", n.status ?? "");
    } else if (n.type === "Team") {
      body = row("group", n.group ?? "");
    }
    const hint = n.evidence ? "" : `<div style="margin-top:5px;opacity:.55;font-size:11px">Click to open · neighbours are lit</div>`;
    return `<div style="background:rgba(8,12,24,.92);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:8px 10px;font:12px system-ui;color:#e6eeff;max-width:320px">${head}${body}${hint}</div>`;
  }, []);

  // Hovering an evidence line names the real relationship it was read from (e.g. "@sam REVIEWED PR-4471").
  const linkLabel = useCallback((l: GLink) => {
    if (!l.rel) return "";
    const name = (x: string | GNode) => {
      const id = idOf(x);
      const v = id.slice(id.indexOf(":") + 1);
      return id.startsWith("Person:") ? `@${v}` : v;
    };
    return `<div style="background:rgba(8,12,24,.92);border:1px solid rgba(56,225,255,.35);border-radius:8px;padding:6px 9px;font:12px system-ui;color:#e6eeff">${name(l.source)} <b style="color:#38e1ff">${l.rel}</b> ${name(l.target)}</div>`;
  }, []);

  return (
    <FG
      ref={fg}
      width={width}
      height={height}
      graphData={data}
      backgroundColor="#05070d"
      showNavInfo={false}
      controlType="orbit"
      nodeId="id"
      nodeThreeObject={nodeThreeObject}
      nodeLabel={label}
      enableNodeDrag={false}
      linkLabel={linkLabel}
      linkColor={linkStyle.color}
      linkWidth={linkStyle.width}
      linkDirectionalParticles={linkStyle.particles}
      linkDirectionalParticleSpeed={linkStyle.particleSpeed}
      linkDirectionalParticleWidth={linkStyle.particleWidth}
      linkDirectionalParticleColor={linkStyle.particleColor}
      warmupTicks={80}
      cooldownTicks={160}
      onEngineStop={() => {
        // Freeze the layout so later additions don't move anything (identical video takes).
        // No automatic zoom here: the camera only moves when the story moves it (or "Overview").
        for (const n of dataRef.current.nodes) { n.fx = n.x; n.fy = n.y; n.fz = n.z; }
      }}
      onNodeClick={(n: GNode) => onNodeClick?.(n)}
      onNodeHover={onNodeHover}
      onBackgroundClick={() => onBackgroundClick?.()}
      nodeVisibility={visibleNode}
      linkVisibility={visibleLink}
    />
  );
}
