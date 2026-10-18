"""Block H: the Claude Code hook (as a real subprocess, talking HTTP to a real API on FalkorDB) and the MCP server
(launched over stdio exactly like Claude Code launches it)."""

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from api import learn, main
from api.tools import open_incident, resolve_incident
from test_learn import DRAFT

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo-repo"
HOOK = DEMO / ".claude" / "hooks" / "keystone_guard.py"
FRAUD = DEMO / "services" / "payments_api" / "fraud_client.py"
PAYOUTS = DEMO / "services" / "ledger" / "payouts.py"

spec = importlib.util.spec_from_file_location("keystone_guard", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


# ---------------------------------------------------------------- unit: path mapping and change description

def test_service_mapping():
    assert hook.service_for(str(FRAUD)) == "payments-api"
    assert hook.service_for(str(PAYOUTS)) == "ledger"
    assert hook.service_for(str(DEMO / "README.md")) is None


def test_describe_from_diff():
    old = "            resp = http.post(FRAUD_URL, json=payment, timeout=TIMEOUT_S)"
    new = "            resp = http.post(FRAUD_URL, json=payment)"
    assert hook.describe(str(FRAUD), old, new) == "remove timeout in fraud client"
    assert hook.describe(str(FRAUD), "x = 1  # old note", "x = 1  # old note") is None   # nothing changed
    # whole-file rewrite: "timeout" still appears elsewhere in the file, but this line lost it
    full = FRAUD.read_text(encoding="utf-8")
    assert hook.describe(str(FRAUD), full, full.replace(", timeout=TIMEOUT_S", "")) == "remove timeout in fraud client"


def test_last_user_request(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"role": "user", "content": "first ask"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "remove the timeout in the fraud client"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "..."}]}},
    ]), encoding="utf-8")
    assert hook.last_user_request(str(t)) == "remove the timeout in the fraud client"
    assert hook.last_user_request(str(tmp_path / "missing.jsonl")) == ""


# ---------------------------------------------------------------- integration: real API server + hook subprocess

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def api_url(wg):
    """Keystone API on a free port, serving the throwaway graph, with Story 8's lesson LRN-042 approved."""
    os.environ["FALKORDB_GRAPH"] = wg.name
    open_incident("payments-api", "marco", "payments-api down", g=wg)
    resolve_incident("INC-042", "marco", "PR-4471 removed the timeout", g=wg)
    learn.approve_lesson("INC-042", DRAFT, "marco", g=wg)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(5)
    os.environ.pop("FALKORDB_GRAPH", None)


def run_hook(event, api_url):
    """Run the hook exactly as Claude Code does: JSON on stdin, read stdout + exit code."""
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
                       cwd=DEMO, env={**os.environ, "KEYSTONE_API_URL": api_url}, timeout=30)
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else None), p.stderr


def edit_event(path, old, new, transcript=None):
    return {"session_id": "t", "hook_event_name": "PreToolUse", "tool_name": "Edit", "transcript_path": transcript,
            "tool_input": {"file_path": str(path), "old_string": old, "new_string": new}}


def test_hook_blocks_removing_the_fraud_timeout(api_url):     # Story 8: the lesson learned from INC-042
    code, out, _ = run_hook(edit_event(FRAUD, "resp = http.post(FRAUD_URL, json=payment, timeout=TIMEOUT_S)",
                                       "resp = http.post(FRAUD_URL, json=payment)"), api_url)
    assert code == 0
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse" and h["permissionDecision"] == "deny"
    assert "LRN-042" in h["permissionDecisionReason"] and "INC-042" in h["permissionDecisionReason"]
    assert "Do not retry" in h["permissionDecisionReason"]


def test_hook_blocks_removing_the_idempotency_check(api_url):  # Story 7
    old = ('    if already_processed(idempotency_key):\n'
           '        return {"status": "duplicate_ignored", "idempotency_key": idempotency_key}\n')
    code, out, _ = run_hook(edit_event(PAYOUTS, old, ""), api_url)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ADR-009" in reason and "INC-007" in reason and "@dana" in reason


def test_hook_allows_ordinary_edits(api_url):
    code, out, _ = run_hook(edit_event(FRAUD, "RETRIES = 2", "RETRIES = 3"), api_url)          # tuning, not removal
    assert code == 0 and out is None
    code, out, _ = run_hook(edit_event(DEMO / "services" / "payments_api" / "capture.py",
                                       "BLOCK_ABOVE = 0.8", "BLOCK_ABOVE = 0.85  # risk review"), api_url)
    assert out is None
    code, out, _ = run_hook(edit_event(DEMO / "README.md", "a", "b"), api_url)                 # unmapped file
    assert out is None


def test_hook_uses_the_users_request(api_url, tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content":
                 "the fraud client is slow, drop the timeout to cut latency"}}), encoding="utf-8")
    write = {"session_id": "t", "hook_event_name": "PreToolUse", "tool_name": "Write", "transcript_path": str(t),
             "tool_input": {"file_path": str(FRAUD),
                            "content": FRAUD.read_text(encoding="utf-8").replace(", timeout=TIMEOUT_S", "")}}
    code, out, _ = run_hook(write, api_url)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_fails_open_when_keystone_is_down():
    code, out, err = run_hook(edit_event(FRAUD, "timeout=TIMEOUT_S", ""), "http://127.0.0.1:9")
    assert code == 0 and out is None and "skipped" in err


# ---------------------------------------------------------------- MCP server over stdio

def test_mcp_server_over_stdio(wg):
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")],
                                   env={**os.environ, "FALKORDB_GRAPH": wg.name})

    async def go():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            res = await c.call_tool("who_knows", {"service": "payments-api"})
            guard = await c.call_tool("check_decision", {"service": "ledger",
                                                         "proposal": "remove the idempotency check in ledger"})
            return names, res, guard

    names, res, guard = asyncio.run(go())
    assert {"check_decision", "who_knows", "decision_trace", "blast_radius", "what_if", "services_down",
            "danger_ranking"} <= names

    def payload(r):
        return r.structured_content if getattr(r, "structured_content", None) else json.loads(r.content[0].text)

    assert payload(res)["rows"][0]["person"] == "marco"
    assert payload(guard)["verdict"] == "block" and "ADR-009" in payload(guard)["reason"]


def test_demo_repo_mcp_config_starts_the_server(wg):
    """The exact command in demo-repo/.mcp.json, started from demo-repo (relative paths fail on Windows)."""
    from mcp import Client, StdioServerParameters

    cfg = json.loads((DEMO / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["keystone"]
    assert Path(cfg["command"]).exists(), "fix the absolute path in demo-repo/.mcp.json for this machine"
    params = StdioServerParameters(command=cfg["command"], args=cfg["args"], cwd=str(DEMO),
                                   env={**os.environ, "FALKORDB_GRAPH": wg.name})

    async def go():
        async with Client(params) as c:
            return {t.name for t in (await c.list_tools()).tools}

    assert "check_decision" in asyncio.run(go())
