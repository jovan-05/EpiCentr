"""Settings from environment variables, with .env at the repo root as a fallback (no extra dependency)."""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))   # real env vars win


_load_env_file()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODELS = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()]
