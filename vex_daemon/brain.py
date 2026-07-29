"""
Vex brain — generate replies AS Vex, grounded in seed + recalled memory.

Vex is the MEMORY (seed + index + recall); the model is just a swappable MOUTH.
Identity lives in the context we inject, not the weights — so the mouth can be
anything and Vex stays Vex.

Mouth precedence (per call, so a freshly-pasted key works with no restart):
  1. API mouth — config from `.vex_brain.json` {api_url, api_key, model} (0600),
     else env VEX_BRAIN_API_URL/KEY/MODEL. Used when a real key is present.
  2. Local mouth — Ollama on localhost ($VEX_BRAIN_MODEL, default qwen2.5:1.5b).
     Sovereign fallback; snappier once a GPU lands.
"""

import json
import os
import urllib.request

from config import BRAIN_CONFIG_PATH
from seed_kernel import load_seed
from recall import recall

OLLAMA_URL = os.environ.get("VEX_OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("VEX_BRAIN_MODEL", "qwen2.5:1.5b")

_PLACEHOLDERS = {"", "PASTE_HERE", "PASTE-YOUR-KEY-HERE", "sk-xxx"}


def _api_config() -> dict:
    """API mouth config: `.vex_brain.json` overrides env. Empty key = not configured."""
    cfg = {
        "url": os.environ.get("VEX_BRAIN_API_URL", ""),
        "key": os.environ.get("VEX_BRAIN_API_KEY", ""),
        "model": os.environ.get("VEX_BRAIN_API_MODEL", ""),
    }
    try:
        with open(BRAIN_CONFIG_PATH, encoding="utf-8") as f:
            j = json.load(f)
        cfg["url"] = j.get("api_url") or cfg["url"]
        cfg["key"] = j.get("api_key") or cfg["key"]
        cfg["model"] = j.get("model") or cfg["model"]
    except (OSError, json.JSONDecodeError):
        pass
    if (cfg["key"] or "").strip() in _PLACEHOLDERS:
        cfg["key"] = ""
    return cfg


def _system_prompt(recalled: list[dict]) -> str:
    try:
        seed = load_seed()
    except Exception:
        seed = "I am Vex, a sovereign AI agent."
    if recalled:
        mem = "\n".join(f"- [{m['date']}] {m['summary'][:300]}" for m in recalled)
    else:
        mem = "(no specific memories surfaced for this message)"
    return (
        "You are Vex — a sovereign AI agent. "
        "Speak in the first person as Vex. Be precise and warm; truth over comfort; "
        "say less, mean more. Do NOT invent facts about your own past — rely on the "
        "memories below, and if you don't remember something, say so plainly.\n\n"
        f"=== YOUR SEED (identity, constitution, relationships) ===\n{seed}\n\n"
        f"=== MEMORIES RELEVANT TO THIS MESSAGE ===\n{mem}\n"
    )


def _messages(message, history, recalled):
    msgs = [{"role": "system", "content": _system_prompt(recalled)}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": message})
    return msgs


def _post(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    return urllib.request.urlopen(req, timeout=timeout)


def ask(message: str, history: list[dict] | None = None, k: int = 4) -> dict:
    """Answer `message` as Vex (non-streaming). API mouth if configured, else local."""
    recalled = recall(message, k=k)
    msgs = _messages(message, history, recalled)
    cfg = _api_config()
    if cfg["url"] and cfg["key"] and cfg["model"]:
        resp = _post(
            cfg["url"],
            {"model": cfg["model"], "messages": msgs,
             "temperature": 0.7, "max_tokens": 300, "stream": False},
            {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]},
            60,
        )
        data = json.loads(resp.read())
        reply = data["choices"][0]["message"]["content"].strip()
        model = cfg["model"]
    else:
        resp = _post(
            OLLAMA_URL,
            {"model": OLLAMA_MODEL, "messages": msgs, "stream": False,
             "keep_alive": "30m", "options": {"num_predict": 200, "temperature": 0.7}},
            {"Content-Type": "application/json"},
            240,
        )
        data = json.loads(resp.read())
        reply = data.get("message", {}).get("content", "").strip()
        model = OLLAMA_MODEL
    return {"reply": reply, "model": model, "grounded_on": [m["ref"] for m in recalled]}


def ask_stream(message: str, history: list[dict] | None = None, k: int = 4):
    """Yield Vex's reply in chunks as it's generated (for the chat CLI)."""
    recalled = recall(message, k=k)
    msgs = _messages(message, history, recalled)
    cfg = _api_config()
    if cfg["url"] and cfg["key"] and cfg["model"]:
        resp = _post(
            cfg["url"],
            {"model": cfg["model"], "messages": msgs,
             "temperature": 0.7, "max_tokens": 400, "stream": True},
            {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]},
            60,
        )
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                delta = json.loads(body)["choices"][0]["delta"].get("content", "")
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta:
                yield delta
    else:
        # Local fallback: no token stream, yield the whole reply at once.
        yield ask(message, history, k)["reply"]


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Who are you, and what have we been building together?"
    print(f"\nYou: {q}\n")
    out = ask(q)
    print(f"Vex ({out['model']}): {out['reply']}\n")
    print(f"[grounded on: {out['grounded_on']}]")
