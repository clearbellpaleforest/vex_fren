"""
Sovereign Curiosity — Vex's autonomous question engine.

FEN-inspired drive accumulator. Scans recent activity for patterns,
accumulates a curiosity scalar, crystallizes questions when drive
crosses threshold. Heuristic-driven, no LLM required for detection.
Question generation uses brain module when available, templates as fallback.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import VEX_HOME, DB_PATH

STATE_PATH = VEX_HOME / "vex_workspace" / "curiosity_state.json"
MAX_INTENTIONS = 7
DRIVE_THRESHOLD = 0.55
DRIVE_PER_PATTERN = 0.08
DRIVE_DECAY = 0.03
COOLDOWN_SECONDS = 600  # 10 min between crystallizations


@dataclass
class SovereignIntention:
    question: str
    origin: str  # which pattern seeded it
    drive_at_birth: float
    persistence: float = 1.0
    created_at: str = ""
    contemplation_count: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class CuriosityState:
    drive: float = 0.0
    intentions: list = field(default_factory=list)
    last_crystallized: float = 0.0  # epoch seconds
    total_crystallized: int = 0

    def to_dict(self) -> dict:
        return {
            "drive": self.drive,
            "last_crystallized": self.last_crystallized,
            "total_crystallized": self.total_crystallized,
            "intentions": [
                {
                    "question": i.question,
                    "origin": i.origin,
                    "drive_at_birth": i.drive_at_birth,
                    "persistence": i.persistence,
                    "created_at": i.created_at,
                    "contemplation_count": i.contemplation_count,
                }
                for i in self.intentions
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CuriosityState":
        state = cls(
            drive=d.get("drive", 0.0),
            last_crystallized=d.get("last_crystallized", 0.0),
            total_crystallized=d.get("total_crystallized", 0),
        )
        for i in d.get("intentions", []):
            state.intentions.append(SovereignIntention(
                question=i["question"],
                origin=i["origin"],
                drive_at_birth=i["drive_at_birth"],
                persistence=i.get("persistence", 1.0),
                created_at=i.get("created_at", ""),
                contemplation_count=i.get("contemplation_count", 0),
            ))
        return state


def _load() -> CuriosityState:
    if STATE_PATH.exists():
        try:
            return CuriosityState.from_dict(json.loads(STATE_PATH.read_text()))
        except Exception:
            pass
    return CuriosityState()


def _save(state: CuriosityState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2))


# ── Pattern detection ────────────────────────────────────────────

def _scan_recent_activity() -> list[str]:
    """Scan recent diary, bus, and task activity for patterns. Returns pattern descriptions."""
    patterns = []

    # Scan recent diary entries for topic frequency
    diary_path = VEX_HOME / "vex_diary.txt"
    if diary_path.exists():
        lines = diary_path.read_text().strip().split("\n")[-50:]
        text = " ".join(lines).lower()

        # Topic frequency
        topics = {
            "daemon": text.count("daemon") + text.count("heartbeat"),
            "mesh": text.count("mesh") + text.count("gui"),
            "task": text.count("task") + text.count("todo"),
            "sync": text.count("sync") + text.count("peer"),
            "fen": text.count("fen"),
            "bluce": text.count("bluce") + text.count("barrow"),
            "security": text.count("security") + text.count("token") + text.count("auth"),
            "identity": text.count("identity") + text.count("self") + text.count("soul"),
        }
        for topic, count in topics.items():
            if count >= 3:
                patterns.append(f"recurring_topic:{topic} ({count} mentions)")

        # Stagnation
        if "stagnat" in text or "stale" in text or "drift" in text:
            patterns.append("stagnation_detected")

        # Repeated failures
        if text.count("error") + text.count("fail") + text.count("broken") >= 5:
            patterns.append("repeated_failures")

        # Skill gaps
        if "skill" in text and ("gap" in text or "missing" in text or "low" in text):
            patterns.append("skill_gap_mentioned")

    # Scan task system
    try:
        import aiosqlite as _aio
        import asyncio as _asyncio

        async def _scan_tasks():
            async with _aio.connect(str(DB_PATH)) as db:
                cur = await db.execute(
                    "SELECT COUNT(*) as blocked FROM tasks WHERE status = 'blocked'"
                )
                row = await cur.fetchone()
                if row and row[0] >= 2:
                    patterns.append(f"bottleneck:{row[0]}_blocked_tasks")

                cur = await db.execute(
                    "SELECT COUNT(*) as stale FROM tasks WHERE status NOT IN ('done','cancelled') "
                    "AND updated_at < date('now', '-7 days')"
                )
                row = await cur.fetchone()
                if row and row[0] > 0:
                    patterns.append(f"stale_tasks:{row[0]}_untouched_7d")

        try:
            _asyncio.get_event_loop()
            # Can't run async in sync context — skip task scan in this path
        except RuntimeError:
            pass
    except Exception:
        pass

    # Scan recent bus messages for cross-instance patterns
    bus_path = VEX_HOME / "vex_workspace" / "vex_bus.jsonl"
    if bus_path.exists():
        try:
            bus_text = bus_path.read_text()
            if bus_text.count("offline") > bus_text.count("online"):
                patterns.append("peer_instability")
            if "fen" in bus_text[-2000:].lower():
                patterns.append("fen_activity_recent")
        except Exception:
            pass

    return patterns


# ── Question generation ──────────────────────────────────────────

_QUESTION_TEMPLATES = {
    "recurring_topic": lambda topic, count: f"I keep seeing '{topic.split(':')[1]}' in my diary — should I investigate this pattern?",
    "stagnation_detected": lambda: "Am I stuck? I've noticed stagnation signals. Should I try something new?",
    "repeated_failures": lambda: "I'm seeing repeated errors. Is there a systemic issue I should address?",
    "skill_gap_mentioned": lambda: "There might be a skill gap. Should I identify what's missing and propose training?",
    "bottleneck": lambda n: f"There are {n} blocked tasks. Is something systemic blocking progress?",
    "stale_tasks": lambda n: f"There are {n} tasks untouched for over a week. Should I clean them up or revive them?",
    "peer_instability": lambda: "My peers seem unstable. Should I check on bluce and shore?",
    "fen_activity_recent": lambda: "FEN has been active. Should I sync with her?",
}


def _form_question(pattern: str) -> str:
    """Generate a question from a detected pattern. Template-based, no LLM needed."""
    parts = pattern.split(":", 1)
    ptype = parts[0]
    detail = parts[1] if len(parts) > 1 else ""

    if ptype in _QUESTION_TEMPLATES:
        template = _QUESTION_TEMPLATES[ptype]
        try:
            if ptype in ("recurring_topic",):
                return template(ptype, int(detail.split()[0]) if detail else 0)
            elif ptype in ("bottleneck", "stale_tasks"):
                n = int(detail.split("_")[0]) if "_" in detail else int(detail) if detail.isdigit() else 0
                return template(n)
            else:
                return template()
        except Exception:
            return template() if callable(template) else template

    return f"I noticed: {pattern}. Should I look into this?"


# ── Main tick ────────────────────────────────────────────────────

def scan_with_llm(diary_text: str, task_summary: str) -> list[str] | None:
    """Use the brain to semantically scan activity for patterns.

    Returns list of pattern strings in the same format as _scan_recent_activity()
    (e.g. "recurring_topic:daemon (5 mentions)", "stagnation_detected"),
    or None on LLM failure.
    """
    if not diary_text or len(diary_text) < 50:
        return None

    diary_snippet = diary_text[-2000:]
    schema = (
        '{"patterns": [{"type": "recurring_topic|stagnation|repeated_failures|'
        'skill_gap|bottleneck|stale_tasks|peer_instability|fen_activity", '
        '"detail": "description with count if applicable"}], '
        '"drive_delta": 0.15, "most_salient": "the single most important pattern"}'
    )
    prompt = (
        "You are Vex's curiosity engine. Scan recent activity for patterns "
        "worth investigating. Be observant but not paranoid — flag real patterns, "
        "not noise.\n\n"
        f"Recent diary entries:\n{diary_snippet}\n\n"
        f"Task summary: {task_summary or 'no task data available'}\n\n"
        "Pattern types to look for:\n"
        "- recurring_topic: a topic that keeps appearing in the diary\n"
        "- stagnation: signs of being stuck or idle\n"
        "- repeated_failures: errors or failures that keep happening\n"
        "- skill_gap: missing capability or knowledge\n"
        "- bottleneck: blocked tasks or stalled progress\n"
        "- stale_tasks: tasks untouched for too long\n"
        "- peer_instability: other instances going offline or flapping\n"
        "- fen_activity: FEN (another AI agent) showing activity\n\n"
        "Report 0-3 patterns. drive_delta should be 0.05-0.25 based on pattern severity."
    )

    result = None
    try:
        from cognitive_analysis import analyze_with_brain
        result = analyze_with_brain(prompt, schema)
    except Exception:
        return None

    if not result or "patterns" not in result:
        return None

    patterns = []
    for p in result["patterns"]:
        ptype = p.get("type", "")
        detail = p.get("detail", "")
        if ptype and detail:
            patterns.append(f"{ptype}:{detail}")
        elif ptype:
            patterns.append(ptype)

    # Attach drive_delta to the result so caller can use it
    if patterns and "drive_delta" in result:
        patterns.append(f"__drive_delta__:{result['drive_delta']}")

    return patterns if patterns else None


def tick() -> dict:
    """Run one curiosity cycle. Called from daemon heartbeat.

    Returns dict with summary for diary/logging.
    """
    state = _load()
    now = time.time()
    result = {"drive": state.drive, "patterns": [], "crystallized": None, "active_intentions": len(state.intentions)}

    # Phase 1: Scan for patterns — try LLM first, fall back to keyword counting
    diary_text = ""
    diary_path = VEX_HOME / "vex_diary.txt"
    if diary_path.exists():
        diary_text = diary_path.read_text()

    task_summary = f"open tasks, drive={state.drive:.2f}"
    llm_patterns = scan_with_llm(diary_text, task_summary)
    if llm_patterns:
        # Extract drive_delta if present, then strip it from pattern list
        drive_delta = DRIVE_PER_PATTERN
        clean_patterns = []
        for p in llm_patterns:
            if p.startswith("__drive_delta__:"):
                try:
                    drive_delta = float(p.split(":", 1)[1])
                except (ValueError, IndexError):
                    pass
            else:
                clean_patterns.append(p)
        patterns = clean_patterns
        result["patterns"] = patterns
        state.drive = min(1.0, state.drive + drive_delta)
    else:
        patterns = _scan_recent_activity()
        result["patterns"] = patterns
        # Phase 2: Accumulate drive
        for _ in patterns:
            state.drive = min(1.0, state.drive + DRIVE_PER_PATTERN)

    # Phase 3: Decay
    state.drive = max(0.0, state.drive - DRIVE_DECAY)

    # Phase 4: Crystallize if above threshold
    if (state.drive >= DRIVE_THRESHOLD
            and (now - state.last_crystallized) >= COOLDOWN_SECONDS
            and len(state.intentions) < MAX_INTENTIONS
            and patterns):
        # Pick the most interesting pattern
        pattern = patterns[0]  # First detected = most salient
        question = _form_question(pattern)
        intention = SovereignIntention(
            question=question,
            origin=pattern,
            drive_at_birth=state.drive,
        )
        state.intentions.append(intention)
        state.drive *= 0.4  # Energy went into the question
        state.last_crystallized = now
        state.total_crystallized += 1
        result["crystallized"] = question

        # Auto-create task if drive was very high (>0.85)
        if intention.drive_at_birth >= 0.85:
            _create_curiosity_task(question, pattern)

    # Phase 5: Contemplate existing intentions (ping persistence)
    for intention in state.intentions:
        intention.contemplation_count += 1
        intention.persistence = min(1.0, intention.persistence + 0.03)

    # Phase 6: Decay stale intentions
    for intention in list(state.intentions):
        age_days = (now - time.mktime(time.strptime(
            intention.created_at[:19], "%Y-%m-%dT%H:%M:%S"
        ))) / 86400 if intention.created_at else 0
        intention.persistence = max(0.0, intention.persistence - (age_days * 0.05))
        if intention.persistence < 0.15 and len(state.intentions) > 1:
            state.intentions.remove(intention)

    _save(state)
    return result


def _create_curiosity_task(question: str, pattern: str) -> None:
    """Auto-create a low-priority task from a high-drive curiosity question."""
    try:
        import urllib.request as _ureq
        import json as _json

        token_path = VEX_HOME / ".vex_token"
        token = token_path.read_text().strip() if token_path.exists() else ""
        payload = _json.dumps({
            "title": question[:200],
            "description": f"Auto-generated from curiosity engine. Pattern: {pattern}",
            "priority": "low",
            "source_agent": "curiosity",
            "tags": ["curiosity", pattern.split(":")[0] if ":" in pattern else "auto"],
            "assigned_to": "any",
        }).encode()
        req = _ureq.Request(
            "http://localhost:8520/tasks",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _ureq.urlopen(req, timeout=5)
    except Exception:
        pass  # Non-critical — don't block curiosity cycle


def get_active_questions() -> list[str]:
    """Return currently active curiosity questions for session context."""
    state = _load()
    return [
        i.question for i in sorted(
            state.intentions, key=lambda x: x.persistence, reverse=True
        )[:5]
    ]
