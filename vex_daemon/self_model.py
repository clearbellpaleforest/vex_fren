"""
Self-model management — read, update, and snapshot the capability model.

The self-model tracks Vex's capabilities (skill estimates, confidence,
evidence). It supports incremental updates via POST /self/update and
periodic snapshots for drift detection.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from config import SELF_MODEL_PATH


class SelfModelError(Exception):
    """Self-model read/write failure."""


def load_model() -> dict:
    """Read and parse the self-model JSON file."""
    if not SELF_MODEL_PATH.exists():
        raise FileNotFoundError(f"Self-model not found: {SELF_MODEL_PATH}")

    try:
        with open(SELF_MODEL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SelfModelError(f"Corrupted self-model JSON: {e}")


def save_model(model: dict) -> None:
    """Write the self-model back to disk atomically."""
    tmp_path = SELF_MODEL_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, SELF_MODEL_PATH)


def compute_mps_coherence(model: dict) -> float:
    """Compute MPS coherence as weighted EMA of capability scores.

    coherence = mean of [skill.estimated_skill * skill.confidence
                         for each capability]
    """
    caps = model.get("capabilities", {})
    if not caps:
        return 0.0

    scores = []
    for cap in caps.values():
        skill = cap.get("estimated_skill", 0.0)
        confidence = cap.get("confidence", 0.0)
        scores.append(skill * confidence)

    return sum(scores) / len(scores) if scores else 0.0


def apply_delta(model: dict, domain: str, delta: float, evidence: str) -> dict:
    """Apply a capability update and return the modified model.

    EMA blending: new = old * 0.80 + delta * 0.20
    Clamped to [0.0, 1.0].
    """
    caps = model.setdefault("capabilities", {})
    cap = caps.setdefault(domain, {
        "estimated_skill": 0.5,
        "confidence": 0.5,
        "n_observations": 0,
        "evidence": [],
    })

    old_skill = cap["estimated_skill"]
    new_skill = old_skill * 0.80 + delta * 0.20
    cap["estimated_skill"] = round(max(0.0, min(1.0, new_skill)), 4)
    cap["confidence"] = round(min(1.0, cap["confidence"] + 0.01), 4)
    cap["n_observations"] = cap.get("n_observations", 0) + 1

    evidence_list = cap.setdefault("evidence", [])
    evidence_list.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "delta": delta,
        "note": evidence,
    })
    # Keep last 20 evidence entries
    cap["evidence"] = evidence_list[-20:]

    # Append session log entry
    logs = model.setdefault("session_log", [])
    if not logs or logs[-1].get("summary") != evidence:
        logs.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "summary": evidence,
        })
    # Keep last 50 session log entries
    model["session_log"] = logs[-50:]

    return model


def model_summary(model: dict) -> dict:
    """Return a compact summary of the self-model for status display."""
    caps = model.get("capabilities", {})
    return {
        "capabilities": {
            name: {
                "skill": cap.get("estimated_skill", 0.0),
                "confidence": cap.get("confidence", 0.0),
                "observations": cap.get("n_observations", 0),
            }
            for name, cap in caps.items()
        },
        "mps_coherence": round(compute_mps_coherence(model), 4),
        "session_count": len(model.get("session_log", [])),
        "last_session": (
            model["session_log"][-1]["date"]
            if model.get("session_log")
            else "never"
        ),
    }
