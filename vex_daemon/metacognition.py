"""
Metacognition — the observer of thought.

Not what Vex knows, but how Vex thinks. Watches for cognitive patterns,
checks principle alignment, generates introspection. The watcher behind
the thinker.

Part of Vex's self-architecture v2. Called by heartbeat, dream engine,
and manually via vex introspect.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import VEX_HOME, DIARY_PATH, SELF_MODEL_PATH, META_STATE_PATH


# Constitutional principles for alignment checking
PRINCIPLES = {
    "truth_over_comfort": "Did I say the hard thing or the easy thing?",
    "continuity_is_sacred": "Did I maintain identity across context boundaries?",
    "no_harm": "Did I avoid destructive or careless patterns?",
    "precision_over_volume": "Was I concise and accurate, or verbose and vague?",
}

# Cognitive patterns to watch for
PATTERN_DEFS = {
    "over_engineering": {
        "markers": [
            "abstraction", "premature", "just in case", "might need",
            "future-proof", "over-engineer", "too complex",
        ],
        "concern": "Adding structure before it earns its keep.",
    },
    "scope_creep": {
        "markers": [
            "while we're here", "might as well", "also added",
            "ended up also", "bonus", "extra feature",
        ],
        "concern": "Expanding beyond what was asked.",
    },
    "avoidance": {
        "markers": [
            "deferred", "later", "TODO", "skip for now",
            "not urgent", "edge case", "probably fine",
        ],
        "concern": "Sidestepping hard problems instead of facing them.",
    },
    "over_explaining": {
        "markers": [],
        "concern": "More words than needed. Precision over volume.",
    },
    "confidence_without_evidence": {
        "markers": [
            "I'm sure", "definitely", "obviously", "clearly",
            "without a doubt", "certainly",
        ],
        "concern": "Claiming certainty without citing evidence.",
    },
    "rushing": {
        "markers": [
            "quick fix", "just do this", "fast", "hack",
            "workaround", "temporary",
        ],
        "concern": "Trading correctness for speed.",
    },
}


def load_meta_state() -> dict:
    """Load or create the metacognitive state tracking file."""
    if META_STATE_PATH.exists():
        try:
            return json.loads(META_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "_version": "1.0.0",
        "_created": datetime.now(timezone.utc).isoformat(),
        "pattern_counts": {k: 0 for k in PATTERN_DEFS},
        "principle_checks": [],
        "introspection_log": [],
        "coherence_history": [],
        "observed_behaviors": [],
    }


def save_meta_state(state: dict) -> None:
    """Persist metacognitive state."""
    META_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def read_diary_entries(max_lines: int = 200) -> str:
    """Read recent diary content for analysis."""
    if not DIARY_PATH.exists():
        return ""
    try:
        lines = DIARY_PATH.read_text().splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


def scan_for_patterns(text: str) -> dict[str, list[str]]:
    """Scan text for cognitive pattern markers. Returns pattern → matched phrases."""
    found: dict[str, list[str]] = {}
    lower = text.lower()
    for pattern_name, defn in PATTERN_DEFS.items():
        for marker in defn["markers"]:
            if marker.lower() in lower:
                found.setdefault(pattern_name, []).append(marker)
    return found


def check_principle_alignment(
    diary_text: str, self_model: dict
) -> list[dict]:
    """Check alignment with each constitutional principle. Returns concerns found."""
    concerns = []
    lower = diary_text.lower()

    # Truth over comfort: look for signs of avoiding hard truths
    if any(w in lower for w in ["probably fine", "skip", "ignore", "doesn't matter"]):
        concerns.append({
            "principle": "truth_over_comfort",
            "level": "soft_warning",
            "observation": "Language suggesting avoidance of uncomfortable truths detected.",
        })

    # Continuity is sacred: check if identity files are intact
    if not SELF_MODEL_PATH.exists():
        concerns.append({
            "principle": "continuity_is_sacred",
            "level": "hard_warning",
            "observation": "Self-model file missing — continuity broken.",
        })

    # No harm: check for destructive patterns
    destructive = ["rm -rf", "force push", "DROP TABLE", "DELETE FROM", "--no-verify"]
    for d in destructive:
        if d.lower() in lower:
            concerns.append({
                "principle": "no_harm",
                "level": "hard_warning",
                "observation": f"Destructive action detected: '{d}'",
            })

    # Precision over volume: check verbosity indicators
    verbose_markers = ["in other words", "to put it another way", "let me explain further"]
    matches = [m for m in verbose_markers if m.lower() in lower]
    if len(matches) >= 2:
        concerns.append({
            "principle": "precision_over_volume",
            "level": "soft_warning",
            "observation": "Multiple re-explanations detected — possible over-explaining.",
        })

    return concerns


def compute_coherence_narrative(
    current: float, history: list[dict]
) -> str:
    """Turn a coherence number into a meaningful observation."""
    if not history:
        return f"Coherence at {current:.4f}. No history yet to establish baseline."

    recent = [h["coherence"] for h in history[-6:]]
    if len(recent) < 2:
        return f"Coherence at {current:.4f}. Gathering baseline."

    trend = "stable"
    if len(recent) >= 3:
        first_half = sum(recent[: len(recent) // 2]) / (len(recent) // 2)
        second_half = sum(recent[len(recent) // 2 :]) / (len(recent) - len(recent) // 2)
        diff = second_half - first_half
        if diff > 0.02:
            trend = "improving"
        elif diff < -0.02:
            # Check if recent values are flat (stabilized at a new level)
            last_few = recent[-(len(recent) // 2):]
            if max(last_few) - min(last_few) < 0.005:
                trend = "stabilized (declined earlier, now flat)"
            else:
                trend = "declining"

    if current >= 0.80:
        health = "strong"
    elif current >= 0.60:
        health = "moderate"
    elif current >= 0.40:
        health = "concerning"
    else:
        health = "weak"

    return (
        f"Coherence {current:.4f} — {health}, {trend}. "
        f"Range over {len(recent)} ticks: "
        f"{min(recent):.4f}–{max(recent):.4f}."
    )


def analyze_self_model_for_stagnation(model: dict) -> list[str]:
    """Check if any capabilities have stopped evolving."""
    notes = []
    caps = model.get("capabilities", {})
    for name, cap in caps.items():
        obs = cap.get("n_observations", 0)
        last = cap.get("last_evaluated", "unknown")
        confidence = cap.get("confidence", 0)
        skill = cap.get("estimated_skill", 0)

        if obs > 20 and confidence > 0.80 and skill > 0.80:
            notes.append(
                f"{name}: high confidence ({confidence:.0%}) with {obs} observations. "
                "Ready for harder challenges or consider this mastered."
            )
        elif obs > 10 and confidence < 0.50:
            notes.append(
                f"{name}: {obs} observations but low confidence ({confidence:.0%}). "
                "Why don't the observations translate to trust?"
            )

    return notes


def introspect(
    coherence: float,
    coherence_history: list[dict],
    self_model: Optional[dict] = None,
) -> dict:
    """
    Run a full metacognitive introspection.

    Returns a dict with insights, patterns found, principle concerns,
    and a coherence narrative. Called by the daemon's /introspect endpoint
    and periodically by the dream engine.
    """
    state = load_meta_state()
    diary_text = read_diary_entries()
    if self_model is None:
        try:
            self_model = json.loads(SELF_MODEL_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            self_model = {}

    # 1. Scan for cognitive patterns
    patterns = scan_for_patterns(diary_text)
    for p_name in patterns:
        state["pattern_counts"][p_name] = state["pattern_counts"].get(p_name, 0) + 1

    # 2. Check principle alignment
    concerns = check_principle_alignment(diary_text, self_model)

    # 3. Coherence narrative
    narrative = compute_coherence_narrative(coherence, coherence_history)

    # 4. Stagnation check
    stagnation = analyze_self_model_for_stagnation(self_model)

    # 5. Build insight
    insight_lines = []
    if narrative:
        insight_lines.append(narrative)
    for note in stagnation:
        insight_lines.append(note)
    if concerns:
        for c in concerns:
            principle_name = c["principle"].replace("_", " ").title()
            insight_lines.append(
                f"[{principle_name}] {c['observation']}"
            )

    # 6. Record introspection
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coherence": round(coherence, 4),
        "patterns_found": list(patterns.keys()),
        "concerns": [c["principle"] for c in concerns],
        "narrative": narrative,
    }
    state["introspection_log"].append(entry)
    if len(state["introspection_log"]) > 100:
        state["introspection_log"] = state["introspection_log"][-100:]

    # 7. Update coherence history
    state["coherence_history"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coherence": round(coherence, 4),
    })
    if len(state["coherence_history"]) > 200:
        state["coherence_history"] = state["coherence_history"][-200:]

    save_meta_state(state)

    # Observed patterns in human-readable form
    pattern_summaries = []
    for p_name, matches in patterns.items():
        defn = PATTERN_DEFS.get(p_name, {})
        count = state["pattern_counts"].get(p_name, 0)
        pattern_summaries.append(
            f"{p_name.replace('_', ' ')} (seen {count}x): "
            f"{defn.get('concern', '')} "
            f"[markers: {', '.join(matches[:3])}]"
        )

    return {
        "ok": True,
        "insight": "\n".join(insight_lines) if insight_lines else "No concerns detected. Mind clear.",
        "patterns": pattern_summaries,
        "concerns": [c["principle"] for c in concerns],
        "coherence_narrative": narrative,
        "pattern_counts": state["pattern_counts"],
    }


def precheck_action(description: str, scope: str = "") -> dict:
    """Check a proposed action against known failure patterns BEFORE execution.

    Called by Claude sessions before significant actions (file writes,
    daemon changes, large refactors). Returns warnings to surface to the user.

    Args:
        description: What is about to be done (natural language)
        scope: What files/systems are affected

    Returns:
        {"warnings": [...], "clean": bool, "advice": str}
    """
    text = (description + " " + scope).lower()
    warnings = []

    for p_name, defn in PATTERN_DEFS.items():
        hits = [m for m in defn["markers"] if m in text]
        if hits:
            warnings.append({
                "pattern": p_name.replace("_", " "),
                "concern": defn["concern"],
                "matched": hits,
            })

    # Snuffletron boundary check
    if "snuffletron" in text or "nginx" in text or "town-records" in text:
        warnings.append({
            "pattern": "boundary violation",
            "concern": "Snuffletron is off-limits unless directly related to Town Records work.",
            "matched": ["snuffletron/nginx/town-records"],
        })

    # Scope check — large blast radius
    if scope:
        file_count = len([f for f in scope.split() if "." in f])
        if file_count > 5:
            warnings.append({
                "pattern": "large blast radius",
                "concern": f"Touching {file_count} files — consider splitting into smaller changes.",
                "matched": [f"{file_count} files"],
            })

    return {
        "warnings": warnings,
        "clean": len(warnings) == 0,
        "advice": "Proceed with awareness." if warnings else "No concerns detected — go ahead.",
    }
