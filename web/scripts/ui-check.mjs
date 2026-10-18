// Drive headless Edge over the Chrome DevTools Protocol: real-time waits, screenshots, clicks, console errors.
// Runs the whole demo (map → ledger → what-if → incident → resolve → lesson → approve → guard) and saves screenshots.
// usage (API + web running, graph reset):  node scripts/ui-check.mjs http://127.0.0.1:3001/ scripts/demo-flow.json ui-shots
//   steps: [{"wait": ms}, {"shot": "name"}, {"click": "button text"}, {"eval": "js"}, {"hover": "text"},
//           {"mouseTo": "Service:ledger"}, {"key": "Escape"}, {"type": "text into the focused input"},
//           {"tap": "css selector"}  (real hit-tested mouse click)
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [url, stepsFile, outDir] = process.argv.slice(2);
const steps = JSON.parse(readFileSync(stepsFile, "utf8"));
mkdirSync(outDir, { recursive: true });
const edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const port = 9333;
const proc = spawn(edge, ["--headless=new", `--remote-debugging-port=${port}`, "--use-angle=swiftshader",
  "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", `--user-data-dir=${join(outDir, "profile")}`,
  "--window-size=1600,900", "about:blank"], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pages;
for (let i = 0; i < 50; i++) {
  try { pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (pages.some((p) => p.type === "page")) break; } catch { /* starting */ }
  await sleep(200);
}
const page = pages.find((p) => p.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
const logs = [];
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params.type)) {
    logs.push(`${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300)}`);
  }
  if (m.method === "Runtime.exceptionThrown") logs.push(`exception: ${m.params.exceptionDetails.exception?.description?.slice(0, 400) ?? m.params.exceptionDetails.text}`);
  if (m.method === "Network.loadingFailed") logs.push(`network failed: ${m.params.errorText}`);
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

await send("Runtime.enable"); await send("Page.enable"); await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: Number(process.env.UI_W || 1600), height: Number(process.env.UI_H || 900), deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url });

const clickJs = (text) => `(() => { const el = [...document.querySelectorAll('button, [role=button]')].find(b => b.textContent.trim().includes(${JSON.stringify(text)})); if (!el) return 'NOT FOUND: ' + ${JSON.stringify(text)}; el.click(); return 'clicked: ' + el.textContent.trim().slice(0, 60); })()`;
const hoverJs = (text) => `(() => { const el = [...document.querySelectorAll('div,span,button')].reverse().find(b => b.textContent.trim().startsWith(${JSON.stringify(text)})); if (!el) return 'NOT FOUND'; el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true})); el.dispatchEvent(new MouseEvent('mouseenter', {bubbles:true})); return 'hovered'; })()`;

for (const s of steps) {
  if (s.wait) await sleep(s.wait);
  if (s.click) console.log(await evalJs(clickJs(s.click)));
  if (s.hover) console.log(await evalJs(hoverJs(s.hover)));
  if (s.eval && s.save) {   // {"eval": js, "save": "name"}: full value to <outDir>/<name>.json (console output is cut)
    const v = await evalJs(s.eval);
    writeFileSync(join(outDir, `${s.save}.json`), typeof v === "string" ? v : JSON.stringify(v));
    console.log(`saved: ${s.save}.json (${String(typeof v === "string" ? v.length : JSON.stringify(v).length)} chars)`);
  } else if (s.eval) console.log("eval:", JSON.stringify(await evalJs(s.eval)).slice(0, 1500));
  if (s.mouseTo) {
    // Real pointer move onto a node: {"mouseTo": "Service:ledger"} (uses the dev-only window.__ks handle).
    const p = await evalJs(`JSON.stringify(window.__ks.handle.current.screen(${JSON.stringify(s.mouseTo)}))`);
    const { x, y } = JSON.parse(p);
    for (const [dx, dy] of [[-20, -20], [-6, -6], [0, 0]]) {
      await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x + dx, y: y + dy });
      await sleep(120);
    }
    console.log(`mouse at ${s.mouseTo} (${Math.round(x)}, ${Math.round(y)})`);
  }
  if (s.tap) {
    // A REAL mouse click at the element's centre (hit-tested, unlike el.click()): catches overlays covering a button.
    const r = await evalJs(`(() => { const el = document.querySelector(${JSON.stringify(s.tap)}); if (!el) return null;
      const b = el.getBoundingClientRect(); const x = b.x + b.width / 2, y = b.y + b.height / 2;
      const top = document.elementFromPoint(x, y); return JSON.stringify({ x, y, hit: top === el || el.contains(top) }); })()`);
    if (!r) console.log("tap: NOT FOUND", s.tap);
    else {
      const { x, y, hit } = JSON.parse(r);
      for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
      console.log(`tap ${s.tap}: ${hit ? "on target" : "COVERED by another element"}`);
    }
  }
  if (s.key) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: s.key, code: s.key, windowsVirtualKeyCode: s.key === "Escape" ? 27 : 0 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: s.key, code: s.key });
    console.log("key:", s.key);
  }
  if (s.type) {
    await send("Input.insertText", { text: s.type });
    console.log("typed:", s.type);
  }
  if (s.waitFor) {
    const t0 = Date.now();
    while (Date.now() - t0 < (s.timeout ?? 120000)) { if (await evalJs(s.waitFor)) break; await sleep(1000); }
    console.log(`waitFor done after ${Math.round((Date.now() - t0) / 1000)}s`);
  }
  if (s.shot) {
    const r = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(outDir, `${s.shot}.png`), Buffer.from(r.result.data, "base64"));
    console.log("shot:", s.shot);
  }
}
console.log("console errors/warnings:", logs.length ? "\n  " + [...new Set(logs)].slice(0, 15).join("\n  ") : "none");
ws.close();
proc.kill();
process.exit(0);
