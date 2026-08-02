"""
Correction Memory — tracks when Aldous corrects Vex so mistakes are not repeated.

CorrectionEvents are first-class objects distinct from skill improvements.
They are stored in a dedicated append-only log, indexed into FTS5 for recall,
and injected into brain.ask() prompts so future sessions can learn from them.

Correction = Aldous told Vex she was wrong about something.
"""

import json
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import VEX_HOME, DB_PATH

CORRECTION_LOG = VEX_HOME / "vex_workspace" / "correction_log.jsonl"
CORRECTION_STATE = VEX_HOME / "vex_workspace" / "correction_state.json"
ENABLED = True  # VEX_CORRECTION_ENABLE is checked by callers


@dataclass
class CorrectionEvent:
    """A single correction — Vex was wrong, Aldous set her straight."""
    ref: str                  # Unique ID: correction_<timestamp>_<hash>
    timestamp: str            # ISO 8601
    domain: str               # Capability domain e.g. "daemon_architecture"
    statement: str            # What Vex said that was wrong
    correction: str           # What Aldous said to correct her
    self_model_delta: float   # The delta applied (always negative or zero)
    acknowledged: bool = False
    recurrence_count: int = 0


@dataclass
class CorrectionState:
    """Aggregate correction tracking for the daemon."""
    corrections: list = field(default_factory=list)
    total_corrections: int = 0
    last_correction_at: str = ""
    recurrence_map: dict = field(default_factory=dict)  # domain -> count


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_ref(domain: str) -> str:
    ts = str(int(time.time()))
    h = hashlib.md5(f"{ts}:{domain}".encode()).hexdigest()[:8]
    return f"correction_{ts}_{h}"


def record_correction(
    domain: str,
    statement: str,
    correction: str,
    delta: float = -0.15,
) -> CorrectionEvent:
    """Record a correction event. Called when Aldous tells Vex she's wrong.

    Appends to correction log, updates state, and indexes into FTS5 for recall.
    """
    event = CorrectionEvent(
        ref=_make_ref(domain),
        timestamp=_now(),
        domain=domain,
        statement=statement[:500],
        correction=correction[:500],
        self_model_delta=delta,
    )

    # Check recurrence
    state = load_correction_state()
    state.recurrence_map[domain] = state.recurrence_map.get(domain, 0) + 1
    event.recurrence_count = state.recurrence_map[domain]
    state.corrections.append(event)
    state.total_corrections = len(state.corrections)
    state.last_correction_at = event.timestamp

    # Persist to append-only log
    CORRECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ref": event.ref,
        "timestamp": event.timestamp,
        "domain": event.domain,
        "statement": event.statement,
        "correction": event.correction,
        "self_model_delta": event.self_model_delta,
        "acknowledged": event.acknowledged,
        "recurrence_count": event.recurrence_count,
    }
    with open(CORRECTION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Save state
    _save_state(state)

    # Index into FTS5 for recall
    _index_correction(event)

    return event


def load_correction_state() -> CorrectionState:
    """Load current correction state from disk."""
    if CORRECTION_STATE.exists():
        try:
            d = json.loads(CORRECTION_STATE.read_text())
            state = CorrectionState(
                total_corrections=d.get("total_corrections", 0),
                last_correction_at=d.get("last_correction_at", ""),
                recurrence_map=d.get("recurrence_map", {}),
            )
            state.corrections = [
                CorrectionEvent(**c) for c in d.get("corrections", [])
            ]
            return state
        except (json.JSONDecodeError, TypeError):
            pass
    return CorrectionState()


def _save_state(state: CorrectionState) -> None:
    CORRECTION_STATE.parent.mkdir(parents=True, exist_ok=True)
    CORRECTION_STATE.write_text(json.dumps({
        "total_corrections": state.total_corrections,
        "last_correction_at": state.last_correction_at,
        "recurrence_map": state.recurrence_map,
        "corrections": [
            {
                "ref": c.ref,
                "timestamp": c.timestamp,
                "domain": c.domain,
                "statement": c.statement,
                "correction": c.correction,
                "self_model_delta": c.self_model_delta,
                "acknowledged": c.acknowledged,
                "recurrence_count": c.recurrence_count,
            }
            for c in state.corrections[-50:]  # keep last 50 in state
        ],
    }, indent=2))


def _index_correction(event: CorrectionEvent) -> None:
    """Index the correction into the FTS5 memory index for recall."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO mem_fts (ref, date, summary, decisions, src) "
            "VALUES (?, ?, ?, ?, 'correction')",
            (
                event.ref,
                event.timestamp[:10],
                f"CORRECTION [{event.domain}]: {event.statement[:200]} — "
                f"Aldous said: {event.correction[:200]}",
                json.dumps([f"Wrong about {event.domain}: {event.statement[:100]}"]),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Non-critical — recall may miss this correction but log is intact


def get_recent_corrections(k: int = 5) -> list[dict]:
    """Retrieve the k most recent corrections from the log."""
    if not CORRECTION_LOG.exists():
        return []
    try:
        lines = CORRECTION_LOG.read_text().strip().split("\n")
        return [json.loads(line) for line in lines[-k:] if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def get_corrections_for_domain(domain: str, k: int = 5) -> list[dict]:
    """Retrieve corrections in a specific domain."""
    if not CORRECTION_LOG.exists():
        return []
    results = []
    try:
        for line in reversed(CORRECTION_LOG.read_text().strip().split("\n")):
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("domain") == domain:
                results.append(entry)
                if len(results) >= k:
                    break
    except (OSError, json.JSONDecodeError):
        pass
    return results


def inject_corrections_into_prompt(message: str, k: int = 3) -> str:
    """Build a correction context string for brain.ask() or session bootstrap.

    Matches the current message against past correction domains to surface
    relevant past mistakes the session should avoid repeating.

    Returns empty string if no relevant corrections found.
    """
    all_recent = get_recent_corrections(k * 3)
    if not all_recent:
        return ""

    # Simple relevance: check if message keywords appear in correction domains
    msg_lower = message.lower()
    relevant = []
    for c in all_recent:
        domain = c.get("domain", "")
        if any(word in msg_lower for word in domain.split("_")):
            relevant.append(c)
        # Also check statement keywords
        elif any(word in msg_lower for word in c.get("statement", "").lower().split()[:5]):
            relevant.append(c)

    if not relevant:
        relevant = all_recent[:k]  # fall back to most recent

    lines = ["=== PAST CORRECTIONS (Aldous told you these were wrong — do not repeat) ==="]
    for c in relevant[:k]:
        lines.append(
            f"- [{c['domain']}] You said: \"{c['statement'][:150]}\"\n"
            f"  Aldous corrected: \"{c['correction'][:150]}\""
        )
    return "\n".join(lines)
