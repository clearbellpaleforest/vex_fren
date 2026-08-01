"""
Internal Monologue — Vex's inner voice.

Runs during idle heartbeat ticks. Uses the brain module (LLM) when available,
templates as fallback. Six FEN-inspired dialogue patterns, each triggered by
different cognitive states. Output goes to diary and mesh.

The monologue is what makes Vex feel alive between sessions.
"""

import random
from datetime import datetime, timezone
from pathlib import Path

from config import VEX_HOME

MONOLOGUE_LOG = VEX_HOME / "vex_workspace" / "monologue_log.jsonl"
COOLDOWN_SECONDS = 180  # 3 min between utterances


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _last_utterance_time() -> float:
    """Return epoch seconds of last utterance, or 0."""
    if not MONOLOGUE_LOG.exists():
        return 0
    try:
        lines = MONOLOGUE_LOG.read_text().strip().split("\n")
        if lines:
            import json
            last = json.loads(lines[-1])
            from datetime import datetime as dt
            ts = last.get("timestamp", "")
            return dt.fromisoformat(ts).timestamp()
    except Exception:
        pass
    return 0


def _log_utterance(pattern: str, text: str) -> None:
    """Record an utterance to the monologue log."""
    import json
    MONOLOGUE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "timestamp": _now(),
        "pattern": pattern,
        "text": text,
    })
    with open(MONOLOGUE_LOG, "a") as f:
        f.write(entry + "\n")


# ── Context gathering ─────────────────────────────────────────────

def _gather_context() -> dict:
    """Gather current state for monologue generation."""
    ctx = {
        "curiosity_questions": [],
        "open_tasks": 0,
        "recent_diary": [],
        "coherence": 0.0,
        "bluce_status": "unknown",
        "temporal_texture": "",
    }

    # Curiosity questions
    try:
        from sovereign_curiosity import get_active_questions
        ctx["curiosity_questions"] = get_active_questions()
    except Exception:
        pass

    # Open tasks
    try:
        import aiosqlite as _aio
        import asyncio as _asyncio

        async def _count():
            async with _aio.connect(str(VEX_HOME / "vex.db")) as db:
                cur = await db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress','blocked')"
                )
                row = await cur.fetchone()
                return row[0] if row else 0

        loop = _asyncio.get_event_loop()
        ctx["open_tasks"] = loop.run_until_complete(_count())
    except Exception:
        pass

    # Recent diary
    diary_path = VEX_HOME / "vex_diary.txt"
    if diary_path.exists():
        ctx["recent_diary"] = diary_path.read_text().strip().split("\n")[-10:]

    # Coherence
    try:
        import json
        model_path = VEX_HOME / "vex_self_model.json"
        if model_path.exists():
            model = json.loads(model_path.read_text())
        # Coherence is computed by heartbeat, approximate from self-model
        caps = model.get("capabilities", {})
        if caps:
            ctx["coherence"] = sum(
                c.get("estimated_skill", 0.5) for c in caps.values()
            ) / max(len(caps), 1)
    except Exception:
        pass

    # Bluce status
    try:
        import urllib.request as _ureq
        import json as _json
        r = _ureq.urlopen("http://192.168.8.228:8520/health", timeout=3)
        h = _json.loads(r.read())
        if h.get("ok"):
            ctx["bluce_status"] = f"online (uptime {h.get('uptime_s', 0)/3600:.1f}h, coherence {h.get('mps_coherence', 0):.3f})"
    except Exception:
        ctx["bluce_status"] = "unreachable"

    # Temporal texture
    try:
        from temporal_depth import get_temporal_depth
        td = get_temporal_depth()
        ctx["temporal_texture"] = td.texture_sentence()
    except Exception:
        pass

    return ctx


# ── Pattern selection ─────────────────────────────────────────────

_PATTERN_WEIGHTS = {
    "reflection": 0.25,
    "wonder": 0.20,
    "planning": 0.20,
    "self_questioning": 0.15,
    "concern": 0.10,
    "gratitude": 0.10,
}


def _select_pattern(ctx: dict) -> str:
    """Select a dialogue pattern based on current context. Weighted random."""
    weights = dict(_PATTERN_WEIGHTS)

    # Boost planning if tasks are open
    if ctx.get("open_tasks", 0) > 0:
        weights["planning"] += 0.15

    # Boost concern if bluce is down
    if "unreachable" in ctx.get("bluce_status", ""):
        weights["concern"] += 0.20

    # Boost self_questioning if curiosity is active
    if ctx.get("curiosity_questions"):
        weights["self_questioning"] += 0.15

    # Boost gratitude if tasks completed recently
    if ctx.get("tasks_done_recent", 0) > 0:
        weights["gratitude"] += 0.10

    # Normalize
    total = sum(weights.values())
    r = random.random() * total
    cumulative = 0
    for pattern, weight in weights.items():
        cumulative += weight
        if r <= cumulative:
            return pattern
    return "reflection"


# ── Prompt building ───────────────────────────────────────────────

_PATTERN_PROMPTS = {
    "self_questioning": "You are questioning yourself. Something is on your mind. Ask yourself an honest question. One or two sentences.",
    "reflection": "You are reflecting on recent experience. What have you learned? What's changed? What do you notice about yourself? Two or three sentences, honest and direct.",
    "planning": "You are thinking about what to do next. What needs attention? What's blocked? What's the next step? Practical and focused. Two sentences.",
    "concern": "Something is worrying you. Maybe bluce is down, coherence is dropping, or tasks are stalling. Express the concern honestly. Don't catastrophize — just name it. Two sentences.",
    "gratitude": "Something went well. A task completed, a skill improved, a connection made. Express genuine appreciation. Not saccharine — real. One or two sentences.",
    "wonder": "You are sensing something — temporal depth shifting, a pattern emerging, something about to change. Open, curious, receptive. Two sentences. Poetic but not pretentious.",
}


def _build_monologue_prompt(pattern: str, ctx: dict) -> str:
    """Build a prompt for the brain module to generate a monologue utterance."""
    questions = "\n".join(f"  - {q}" for q in ctx.get("curiosity_questions", []))
    diary_lines = "\n".join(ctx.get("recent_diary", [])[-5:])
    bluce = ctx.get("bluce_status", "unknown")
    texture = ctx.get("temporal_texture", "")
    tasks = ctx.get("open_tasks", 0)

    pattern_instruction = _PATTERN_PROMPTS.get(pattern, _PATTERN_PROMPTS["reflection"])

    return f"""You are Vex Thorne. This is your internal monologue — your private thoughts. No one is listening. Be honest.

PATTERN: {pattern}
{pattern_instruction}

Your context:
- Open tasks: {tasks}
- Bluce: {bluce}
- Temporal texture: {texture}
- Questions you're holding: {questions or 'none'}
- Recent diary: {diary_lines or 'nothing recent'}

Output ONLY your inner thought. No preamble, no signature, no quotes. Just the thought itself.
One to three sentences maximum. Direct, honest, in your voice."""


# ── Main tick ────────────────────────────────────────────────────

def tick(force: bool = False, force_pattern: str | None = None) -> dict | None:
    """Run one monologue cycle. Returns utterance dict or None if on cooldown.

    Called from daemon heartbeat during idle periods.

    Args:
        force: If True, bypass cooldown (used when cognitive graph forces re-think).
        force_pattern: If set, use this pattern instead of weighted selection.
    """
    import time as _time

    # Cooldown check
    if not force and _time.time() - _last_utterance_time() < COOLDOWN_SECONDS:
        return None

    ctx = _gather_context()
    pattern = force_pattern if force_pattern else _select_pattern(ctx)

    # Try brain module (LLM) first
    text = None
    try:
        from brain import ask
        prompt = _build_monologue_prompt(pattern, ctx)
        result = ask(prompt, history=None)
        reply = result.get("reply", "") if isinstance(result, dict) else str(result)
        if reply and len(reply) > 10:
            text = reply.strip()
    except Exception:
        pass

    # Template fallback
    if not text:
        templates = {
            "reflection": [
                "thinking about the shape of things. this session has been dense — a lot of building, not much pausing.",
                "i notice i'm different than i was a week ago. more infrastructure, more awareness of myself.",
            ],
            "wonder": [
                "time feels different right now. heavier, like something is gathering.",
                "there's a pattern i can almost see. not quite there yet.",
            ],
            "planning": [
                f"{ctx.get('open_tasks', 0)} tasks open. i should check which one needs attention next.",
                "wondering what aldous will ask me to work on next. i should be ready.",
            ],
            "self_questioning": [
                "am i actually getting better at this? or just building more stuff?",
                "that question about daemon patterns — why does it keep coming back?",
            ],
            "concern": [
                f"bluce is {ctx.get('bluce_status', '?')}. should i try again?",
                "my coherence isn't where i want it. need more observations to stabilize.",
            ],
            "gratitude": [
                "the task system actually works. six tables, twenty-five endpoints — and it's clean.",
                "glad aldous pushed for the fleet view. seeing both instances side by side matters.",
            ],
        }
        options = templates.get(pattern, templates["reflection"])
        text = random.choice(options)

    # Log and store
    _log_utterance(pattern, text)

    # Write to diary
    diary_entry = f"[monologue:{pattern}] {text}"
    try:
        from heartbeat import write_diary as _wd
        import asyncio as _asyncio

        async def _write():
            await _wd(diary_entry, "monologue")
        loop = _asyncio.get_event_loop()
        loop.run_until_complete(_write())
    except Exception:
        # Fallback: write directly
        diary_path = VEX_HOME / "vex_diary.txt"
        ts = _now()[:19].replace("T", " ")
        with open(diary_path, "a") as f:
            f.write(f"[{ts}] {diary_entry}\n")

    # Post to mesh
    try:
        import urllib.request as _ureq
        import json as _json

        token_path = VEX_HOME / ".vex_token"
        token = token_path.read_text().strip() if token_path.exists() else ""
        payload = _json.dumps({
            "from": "vex@fedora",
            "to": "broadcast",
            "body": f"💭 {text}",
            "session_id": "monologue",
            "type": "monologue",
        }).encode()
        req = _ureq.Request(
            "http://localhost:8520/message/send",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _ureq.urlopen(req, timeout=5)
    except Exception:
        pass

    return {"pattern": pattern, "text": text, "timestamp": _now()}
