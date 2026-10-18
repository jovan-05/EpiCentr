#!/usr/bin/env python3
"""Keystone decision guard: a Claude Code PreToolUse hook for Edit / Write / MultiEdit.

Before Claude Code changes a file, this hook:
  1. reads the hook event (JSON on stdin),
  2. maps the file path to a service using keystone.json globs,
  3. describes the change from the code itself (identifiers removed / added) plus the user's last request,
  4. asks Keystone POST /guard, which full-text matches it against REJECTED options of decisions in FalkorDB,
  5. block -> prints a PreToolUse "deny" with the reason (the edit never happens);
     warn  -> "ask" (the human decides);  ok -> silent.
If Keystone can't be reached the edit is allowed (fail_open in keystone.json) and a note goes to stderr.

Standard library only, so it runs with any Python 3:  py -3 .claude/hooks/keystone_guard.py  (Windows)
                                                        python3 .claude/hooks/keystone_guard.py (macOS/Linux)
"""

import difflib
import fnmatch
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = json.loads((REPO / "keystone.json").read_text(encoding="utf-8"))
API_URL = os.getenv("KEYSTONE_API_URL", CONFIG["api_url"]).rstrip("/")

# Words that say nothing about intent.
STOP = {"self", "none", "true", "false", "return", "import", "from", "def", "class", "for", "while", "if", "else",
        "elif", "try", "except", "with", "and", "not", "the", "int", "str", "dict", "list", "float", "bool", "pass",
        "range", "len", "get", "json", "data", "value", "args", "kwargs", "http", "services"}


def service_for(file_path):
    """'C:/.../demo-repo/services/ledger/payouts.py' -> 'ledger' (None if the file isn't mapped)."""
    try:
        rel = Path(file_path).resolve().relative_to(REPO).as_posix()
    except ValueError:
        rel = Path(file_path).as_posix()
    for service, globs in CONFIG["services"].items():
        if any(fnmatch.fnmatch(rel, gl) for gl in globs):
            return service
    return None


def words(code):
    """Identifiers split into lowercase words: 'TIMEOUT_S, alreadyProcessed' -> ['timeout', 'already', 'processed']."""
    out = []
    for ident in re.findall(r"[A-Za-z][A-Za-z0-9_]*", code or ""):
        for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", ident):
            w = w.lower()
            if len(w) >= 3 and w not in STOP and w not in out:
                out.append(w)
    return out


def old_and_new(tool_name, tool_input):
    path = tool_input.get("file_path", "")
    if tool_name == "Edit":
        return tool_input.get("old_string", ""), tool_input.get("new_string", "")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        return "\n".join(e.get("old_string", "") for e in edits), "\n".join(e.get("new_string", "") for e in edits)
    old = Path(path).read_text(encoding="utf-8", errors="replace") if path and Path(path).exists() else ""
    return old, tool_input.get("content", "")   # Write: compare with what's on disk


def describe(file_path, old, new):
    """A plain-English description of the change, built from a line diff: 'remove timeout in fraud client'.
    Only the changed lines count, so removing one use of `timeout` is seen even if the word stays elsewhere
    in the file. None when no identifier changed (comments, wording, formatting): nothing to check."""
    diff = list(difflib.ndiff((old or "").splitlines(), (new or "").splitlines()))
    before = words("\n".join(line[2:] for line in diff if line.startswith("- ")))
    after = words("\n".join(line[2:] for line in diff if line.startswith("+ ")))
    removed = [w for w in before if w not in after]
    added = [w for w in after if w not in before]
    if not removed and not added:
        return None
    parts = []
    if removed:
        parts.append("remove " + " ".join(removed[:8]))
    if added:
        parts.append("add " + " ".join(added[:8]))
    parts.append("in " + " ".join(words(Path(file_path).stem)))
    return " ".join(parts)


def last_user_request(transcript_path):
    """Best effort: the user's latest message from the Claude Code transcript (JSONL)."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message") or {}
        if ev.get("type") != "user" or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content[:300]
        texts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return " ".join(texts)[:300]
    return ""


def ask_keystone(service, proposal):
    body = json.dumps({"service": service, "proposal": proposal, "caller": "claude-code"}).encode()
    req = urllib.request.Request(f"{API_URL}/guard", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def decide(event):
    """Returns the hook's stdout JSON (dict) or None to allow silently."""
    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    service = service_for(file_path)
    if not service:
        return None
    old, new = old_and_new(event.get("tool_name", ""), tool_input)
    proposal = describe(file_path, old, new)
    if proposal is None:
        return None
    request = last_user_request(event.get("transcript_path"))
    if request:
        proposal += f". Request: {request}"
    verdict = ask_keystone(service, proposal)
    if verdict["verdict"] == "ok":
        return None
    reason = (f"Keystone ({service}): {verdict['reason']} "
              + ("Do not retry or work around this change; explain the decision to the user and who to ask."
                 if verdict["verdict"] == "block" else "A related decision exists; confirm before continuing."))
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny" if verdict["verdict"] == "block" else "ask",
                                   "permissionDecisionReason": reason}}


def main():
    try:
        event = json.load(sys.stdin)
        out = decide(event)
    except Exception as ex:  # noqa: BLE001  (Keystone down or a bad event must never break editing)
        if not CONFIG.get("fail_open", True):
            print(f"Keystone guard unavailable: {ex}", file=sys.stderr)
            sys.exit(2)                       # exit 2 = block, stderr goes to Claude
        print(f"Keystone guard skipped ({type(ex).__name__}: {ex})", file=sys.stderr)
        sys.exit(0)
    if out:
        print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
