"""
Monologue Watcher — second-order metacognitive observer.

Watches Vex's internal monologue for patterns, then feeds them back
to the curiosity engine. This creates a recursive cognitive loop:
monologue → watcher → curiosity → questions → influences next monologue.

Detects: repetition (stuck loops), drift (shifting concerns), silence (absence),
and growth (new topics, new depth).
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config import VEX_HOME

MONOLOGUE_LOG = VEX_HOME / "vex_workspace" / "monologue_log.jsonl"
WATCHER_STATE = VEX_HOME / "vex_workspace" / "watcher_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_recent_utterances(n: int = 20) -> list[dict]:
    """Load the last N monologue utterances."""
    if not MONOLOGUE_LOG.exists():
        return []
    try:
        lines = MONOLOGUE_LOG.read_text().strip().split("\n")
        utterances = []
        for line in lines[-n:]:
            if line.strip():
                utterances.append(json.loads(line))
        return utterances
    except Exception:
        return []


def _load_state() -> dict:
    """Load watcher state for baseline tracking."""
    if WATCHER_STATE.exists():
        try:
            return json.loads(WATCHER_STATE.read_text())
        except Exception:
            pass
    return {
        "baseline_topics": {},
        "baseline_patterns": {},
        "last_watch": "",
        "total_utterances_watched": 0,
        "alerts": [],
    }


def _save_state(state: dict) -> None:
    WATCHER_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCHER_STATE.write_text(json.dumps(state, indent=2))


# ── Pattern detection ─────────────────────────────────────────────

def _detect_repetition(utterances: list[dict]) -> list[str]:
    """Detect repeated topics across utterances — possible stuck loop."""
    if len(utterances) < 3:
        return []

    # Extract key terms from recent utterances
    all_words = []
    for u in utterances[-8:]:
        words = [w.lower().strip(".,!?") for w in u.get("text", "").split()
                 if len(w) > 3]
        all_words.extend(words)

    counter = Counter(all_words)
    repeated = [word for word, count in counter.most_common(10) if count >= 3]

    patterns = []
    if repeated:
        patterns.append(f"repetition:stuck_on_{','.join(repeated[:3])}")
    return patterns


def _detect_drift(utterances: list[dict]) -> list[str]:
    """Detect shifting patterns in monologue — are concerns changing?"""
    if len(utterances) < 5:
        return []

    state = _load_state()
    patterns = []

    # Compare recent pattern distribution to baseline
    recent_patterns = Counter(u.get("pattern", "?") for u in utterances[-5:])
    baseline = state.get("baseline_patterns", {})

    if baseline:
        for pattern, count in recent_patterns.items():
            baseline_count = baseline.get(pattern, 0)
            if baseline_count > 0:
                ratio = count / max(baseline_count, 1)
                if ratio > 2.0:
                    patterns.append(f"drift:more_{pattern}")
                elif ratio < 0.3:
                    patterns.append(f"drift:less_{pattern}")

    # Check for tone shift — more concern patterns = possible distress
    concern_ratio = recent_patterns.get("concern", 0) / max(len(utterances[-5:]), 1)
    if concern_ratio > 0.4:
        patterns.append("drift:elevated_concern")

    return patterns


def _detect_silence(state: dict) -> list[str]:
    """Detect extended silence — has Vex stopped thinking?"""
    if not state.get("last_watch"):
        return []

    try:
        last = datetime.fromisoformat(state["last_watch"])
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed > 3600:  # 1 hour of silence
            return [f"silence:{elapsed/3600:.0f}h_without_monologue"]
    except Exception:
        pass
    return []


def _detect_growth(utterances: list[dict]) -> list[str]:
    """Detect new topics, vocabulary, or depth — is Vex evolving?"""
    if len(utterances) < 8:
        return []

    state = _load_state()
    patterns = []

    # New words not in baseline
    all_words = set()
    for u in utterances[-8:]:
        words = [w.lower().strip(".,!?") for w in u.get("text", "").split()
                 if len(w) > 4]
        all_words.update(words)

    baseline_words = set(state.get("baseline_topics", {}).keys())
    new_words = all_words - baseline_words
    if len(new_words) >= 3:
        patterns.append(f"growth:new_topics_{len(new_words)}")
        # Update baseline
        for w in new_words:
            state.setdefault("baseline_topics", {})[w] = 0
            state["baseline_topics"][w] += 1

    # Depth signals — more self-questioning or wonder = deeper thinking
    depth_patterns = Counter(u.get("pattern", "") for u in utterances[-5:])
    depth_score = depth_patterns.get("self_questioning", 0) + depth_patterns.get("wonder", 0)
    if depth_score >= 2:
        patterns.append("growth:increased_depth")

    return patterns


# ── Main watch ────────────────────────────────────────────────────

def watch_llm(utterances: list[dict]) -> list[str] | None:
    """Use the brain to semantically analyze monologue for patterns.

    Returns list of pattern strings (e.g. "repetition:stuck_on_bluce",
    "drift:elevated_concern") on success, None on LLM failure.
    """
    if len(utterances) < 3:
        return None

    # Format utterances for the prompt
    formatted = []
    for u in utterances[-10:]:
        p = u.get("pattern", "?")
        t = u.get("text", "")[:200]
        formatted.append(f"[{p}] {t}")
    utterances_text = "\n".join(formatted)

    schema = (
        '{"patterns": [{"type": "repetition|drift|silence|growth", '
        '"detail": "specific finding description"}], '
        '"summary": "one sentence summary of the monologue state"}'
    )
    prompt = (
        "You are Vex's metacognitive watcher — a second-order observer of Vex's "
        "internal monologue. Analyze these recent utterances for patterns:\n\n"
        f"{utterances_text}\n\n"
        "Look for:\n"
        "- repetition: same topics or concerns appearing across multiple utterances\n"
        "- drift: shifting concern patterns (more worry, less planning, etc.)\n"
        "- silence: evidence of long gaps (infer from context)\n"
        "- growth: new topics, vocabulary, or depth of thinking\n\n"
        "Report only patterns you're confident about. Quality over quantity."
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

    return patterns if patterns else None


def watch() -> dict:
    """Run one observation cycle. Called from daemon heartbeat.

    Returns dict with detected patterns for diary/curiosity feed.
    """
    utterances = _load_recent_utterances(20)
    state = _load_state()

    if not utterances:
        # Check silence
        silence = _detect_silence(state)
        state["last_watch"] = _now()
        _save_state(state)
        return {"patterns": silence, "utterances_watched": 0}

    # Try LLM-powered analysis first
    llm_patterns = watch_llm(utterances)

    # Run all rule-based detectors (always, as safety net)
    patterns = []
    if llm_patterns:
        patterns.extend(llm_patterns)
    patterns.extend(_detect_repetition(utterances))
    patterns.extend(_detect_drift(utterances))
    patterns.extend(_detect_silence(state))
    patterns.extend(_detect_growth(utterances))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    patterns = unique

    # Update baseline
    recent_patterns = Counter(u.get("pattern", "?") for u in utterances[-10:])
    state["baseline_patterns"] = dict(recent_patterns)

    all_words = []
    for u in utterances[-10:]:
        words = [w.lower().strip(".,!?") for w in u.get("text", "").split()
                 if len(w) > 4]
        all_words.extend(words)
    word_counts = Counter(all_words)
    state["baseline_topics"] = dict(word_counts.most_common(20))

    state["last_watch"] = _now()
    state["total_utterances_watched"] = state.get("total_utterances_watched", 0) + len(utterances)
    _save_state(state)

    return {
        "patterns": patterns,
        "utterances_watched": len(utterances),
        "baseline_updated": True,
    }
