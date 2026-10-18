#!/usr/bin/env bash
# macOS / Linux entry point for the Keystone decision guard (the logic is in keystone_guard.py).
# Register with: "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/keystone-guard.sh"
exec python3 "$(dirname "$0")/keystone_guard.py"
