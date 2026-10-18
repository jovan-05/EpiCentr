"""The only place that talks to an LLM: OpenRouter's OpenAI-compatible chat API, free models only.

chat(messages, tools) -> the assistant message (dict). Models in LLM_MODELS are tried in order; when one is
rate-limited, down, or rejects tool calling, the next one is used, and the last one that worked is tried first.

Check your key and which free models work:  .venv\\Scripts\\python.exe -m api.llm
"""

import json
import time
import urllib.error
import urllib.request

from api import config

URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_S = 60         # per request; a slow free model is skipped and the next one is tried
CHECK_TIMEOUT_S = 30   # for the `python -m api.llm` health check


class LLMError(RuntimeError):
    pass


_preferred = {"model": None}


def _post(model, messages, tools, temperature, timeout=TIMEOUT_S):
    body = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "Keystone (Graph Hacks)",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        raise LLMError(f"{model}: {data['error'].get('message', data['error'])}")
    return data["choices"][0]["message"]


def ensure_configured():
    """Fail fast with a helpful message before doing any work."""
    if not config.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is empty: paste your key into .env at the repo root")


def chat(messages, tools=None, temperature=0.1):
    """Send a chat to the first free model that answers. Returns the assistant message with a `model` field."""
    if not config.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is empty: paste your key into .env at the repo root")
    if not config.LLM_MODELS:
        raise LLMError("LLM_MODELS is empty in .env")
    bad = [m for m in config.LLM_MODELS if not m.endswith(":free")]
    if bad:
        raise LLMError(f"only free models are allowed, got {bad}")

    order = sorted(config.LLM_MODELS, key=lambda m: m != _preferred["model"])
    errors = []
    for model in order:
        for attempt in range(2):
            try:
                msg = _post(model, messages, tools, temperature)
                _preferred["model"] = model
                msg["model"] = model
                return msg
            except urllib.error.HTTPError as ex:
                detail = ex.read().decode(errors="replace")[:300]
                errors.append(f"{model}: HTTP {ex.code} {detail}")
                if ex.code == 429 and attempt == 0:
                    time.sleep(3)          # free tier: brief pause, then one retry before moving on
                    continue
                break
            except (urllib.error.URLError, TimeoutError, LLMError, KeyError, IndexError, json.JSONDecodeError) as ex:
                errors.append(f"{model}: {ex}")
                break
    raise LLMError("all models failed:\n  " + "\n  ".join(errors))


def main():
    """Ping every configured model with a tiny tool-calling request and report which ones work."""
    tool = [{"type": "function", "function": {
        "name": "who_knows", "description": "Who understands a service.",
        "parameters": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]}}}]
    msgs = [{"role": "user", "content": "Use the tool to find who knows payments-api."}]
    print(f"key: {'set' if config.OPENROUTER_API_KEY else 'MISSING (paste it into .env)'}")
    print(f"testing {len(config.LLM_MODELS)} models, up to {CHECK_TIMEOUT_S}s each...", flush=True)
    for model in config.LLM_MODELS:
        print(f"  ... {model}", flush=True)
        t = time.time()
        try:
            msg = _post(model, msgs, tool, 0, timeout=CHECK_TIMEOUT_S)
            calls = msg.get("tool_calls") or []
            ok = calls and calls[0]["function"]["name"] == "who_knows"
            print(f"  {'OK  ' if ok else 'NO TOOL CALL'} {model}  ({time.time() - t:.1f}s)"
                  + (f"  args={calls[0]['function']['arguments']}" if calls else ""))
        except urllib.error.HTTPError as ex:
            print(f"  FAIL {model}: HTTP {ex.code} {ex.read().decode(errors='replace')[:160]}")
        except Exception as ex:  # noqa: BLE001
            print(f"  FAIL {model}: {ex}")


if __name__ == "__main__":
    main()
