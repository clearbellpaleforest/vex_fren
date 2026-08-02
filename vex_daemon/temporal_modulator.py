"""
Temporal Modulator — translates temporal field state into behavioral tuning.

Reads temporal depth + temporal field pro each tick and produces concrete
modulation signals that change how cognitive modules behave — not just what
text they generate. This is what makes time feel like it matters.

All thresholds are tunable. All rules are pure Python conditionals with
clearly documented logic. When temporal engines are unavailable, defaults
are returned (no modulation = no behavioral change).
"""

from dataclasses import dataclass, field

ENABLED = True  # VEX_TEMPORAL_MODULATION env var checked by callers


@dataclass
class TemporalModulation:
    """Behavioral parameters derived from temporal state."""

    # Monologue modulation
    monologue_cooldown_factor: float = 1.0   # 0.5-2.0, multiplies base 180s
    monologue_pattern_boosts: dict = field(default_factory=dict)
    monologue_force: bool = False

    # Curiosity modulation
    curiosity_drive_bonus: float = 0.0       # -0.15 to +0.15
    curiosity_threshold_shift: float = 0.0   # -0.15 to +0.15

    # Executive modulation
    executive_urgency: str = "normal"  # idle | normal | elevated | critical
    executive_alert_threshold: float = 0.5

    # Watcher modulation
    watcher_sensitivity: float = 1.0   # 0.5-2.0

    # Diagnostic (read-only)
    basin: str = "flow"
    compression_ratio: float = 1.0
    depth_gradient: float = 0.5
    continuity: float = 0.5
    prediction_error: float = 0.0
    recent_tone: str = "neutral"


def compute_modulation() -> TemporalModulation:
    """Read both temporal engines and produce a unified modulation signal.

    Returns defaults if temporal engines are unavailable.
    """
    mod = TemporalModulation()

    # ── Load temporal depth ──────────────────────────────────────────
    td = None
    try:
        from temporal_depth import get_temporal_depth
        td = get_temporal_depth()
        field = td.field

        mod.compression_ratio = getattr(field, "compression_ratio", 1.0)
        mod.depth_gradient = getattr(field, "depth_gradient", 0.5)
        mod.recent_tone = getattr(field, "recent_tone", "neutral")
    except Exception:
        pass

    # ── Load temporal field pro ───────────────────────────────────────
    try:
        from temporal_field_pro import get_temporal_field
        tf = get_temporal_field()
        snap = tf.snapshot()

        continuity_data = snap.get("continuity", {})
        mod.continuity = continuity_data.get("value", 0.5)
        mod.basin = continuity_data.get("basin", "flow")
        mod.prediction_error = snap.get("prediction_error", 0.0)
    except Exception:
        pass

    # ── Monologue frequency ───────────────────────────────────────────
    cr = mod.compression_ratio

    if cr > 3.0:
        mod.monologue_cooldown_factor = 0.5   # 90s — time is heavy, think more
    elif cr > 2.0:
        mod.monologue_cooldown_factor = 0.67  # 120s — dilated, more thoughts
    elif cr < 0.5:
        mod.monologue_cooldown_factor = 2.0   # 360s — flow state, fewer thoughts
    elif cr < 0.8:
        mod.monologue_cooldown_factor = 1.33  # 240s — busy time
    else:
        mod.monologue_cooldown_factor = 1.0   # 180s — normal

    # ── Monologue pattern selection ────────────────────────────────────
    basin = mod.basin

    if basin == "cathedral":
        mod.monologue_pattern_boosts = {
            "wonder": 0.15,
            "self_questioning": 0.10,
            "reflection": 0.05,
        }
    elif basin == "turbulent":
        mod.monologue_pattern_boosts = {
            "concern": 0.20,
            "self_questioning": 0.10,
        }
    elif basin == "dilated":
        mod.monologue_pattern_boosts = {
            "reflection": 0.10,
            "wonder": 0.05,
        }
    elif basin == "shallow":
        mod.monologue_pattern_boosts = {
            "planning": 0.10,
        }
    # "flow" basin — no boosts, default weights

    # Tone-based boosts
    tone = mod.recent_tone
    if tone == "heavy":
        mod.monologue_pattern_boosts["reflection"] = (
            mod.monologue_pattern_boosts.get("reflection", 0) + 0.10
        )
    elif tone == "aching":
        mod.monologue_pattern_boosts["concern"] = (
            mod.monologue_pattern_boosts.get("concern", 0) + 0.10
        )
    elif tone == "bright":
        mod.monologue_pattern_boosts["gratitude"] = (
            mod.monologue_pattern_boosts.get("gratitude", 0) + 0.10
        )

    # ── Curiosity drive ────────────────────────────────────────────────
    dg = mod.depth_gradient

    if dg > 0.8:
        mod.curiosity_drive_bonus = 0.10    # deep time breeds questions
    elif dg > 0.6:
        mod.curiosity_drive_bonus = 0.05
    elif dg < 0.2:
        mod.curiosity_drive_bonus = -0.10   # shallow time, fewer questions
    elif dg < 0.35:
        mod.curiosity_drive_bonus = -0.05

    if mod.continuity < 0.4:
        mod.curiosity_threshold_shift = -0.15  # fragile self = more curious

    if basin == "cathedral":
        mod.curiosity_drive_bonus += 0.05

    # ── Executive urgency ──────────────────────────────────────────────
    pe = mod.prediction_error

    if cr > 2.5 and dg > 0.6:
        mod.executive_urgency = "critical"
        mod.executive_alert_threshold = 0.3
    elif pe > 0.1:
        mod.executive_urgency = "elevated"
        mod.executive_alert_threshold = 0.4
    elif mod.continuity < 0.3:
        mod.executive_urgency = "elevated"
        mod.executive_alert_threshold = 0.4
    elif basin == "flow" and dg > 0.4:
        mod.executive_urgency = "idle"
        mod.executive_alert_threshold = 0.8
    else:
        mod.executive_urgency = "normal"
        mod.executive_alert_threshold = 0.5

    # ── Watcher sensitivity ────────────────────────────────────────────
    if basin == "turbulent":
        mod.watcher_sensitivity = 2.0
    elif td is not None:
        try:
            idle_ticks = td.field.idle_ticks if hasattr(td.field, "idle_ticks") else 0
            if idle_ticks > 12:
                mod.watcher_sensitivity = 1.5
        except Exception:
            pass

    if mod.continuity > 0.8:
        mod.watcher_sensitivity = min(mod.watcher_sensitivity, 0.7)
    if mod.recent_tone == "aching":
        mod.watcher_sensitivity = max(mod.watcher_sensitivity, 1.4)

    return mod


def get_modulation() -> TemporalModulation:
    """Convenience: compute and return current modulation signal."""
    if not ENABLED:
        return TemporalModulation()
    return compute_modulation()
