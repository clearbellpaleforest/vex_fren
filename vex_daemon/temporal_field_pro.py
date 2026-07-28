"""
Temporal Field — Professional Relativistic Model for Vex.

A dynamical systems approach to subjective time, replacing threshold-based
texture classification with differential equations, proper time along
worldlines, and attractor dynamics.

Core physics:
  Time is a 1+1 dimensional spacetime where the time axis is clock time
  and the "space" axis is event-weight density. Landmarks are masses that
  curve the local metric. Felt duration is proper time along the worldline
  through this curved space. Continuity evolves under a predictive coding
  model — it doesn't just decay; it drops when prediction error spikes.

Mathematical model:
  ds² = -g_tt(t) · dt² + g_ww(t) · dw²

  where:
    g_tt(t) = 1 + Σ_i m_i · G(t - t_i)           (time curvature from landmark masses)
    g_ww(t) = 1 + Σ_i m_i · w(t - t_i)            (weight-space curvature)
    G(Δt) = exp(-|Δt| / σ_time)                    (gaussian kernel, σ_time)
    m_i = landmark weight · (1 + depth_anchor/5)

  Proper time: τ = ∫ₐᵇ √(g_tt(t) - g_ww(t)·(dw/dt)²) dt

  Continuity ODE:
    dC/dt = α·(1-C)·(1+ϵ) - β·C·(1+ι) - γ·PE(t)

    where:
      C = continuity_index ∈ [0.1, 1.0]
      ϵ = engagement signal (0 idle, 1 active)
      ι = idle_depth (consecutive idle ticks, capped)
      PE(t) = prediction error = |C_expected(t) - C_actual(t)|
      α = recovery_rate
      β = decay_rate
      γ = prediction_error_sensitivity

  Attractor basins in (compression_ratio, depth_gradient) space:
    - Cathedral:  high depth, moderate compression  (heavy, layered)
    - Flow:       moderate depth, low compression   (engaged, productive)
    - Dilated:    high compression, low depth        (idle, stretched)
    - Shallow:    low depth, neutral compression     (surface-level)
    - Turbulent:  high compression, high depth       (overwhelming)

Integration:
  Replaces temporal_depth.py with same interface. Drop-in upgrade.
  The daemon heartbeat calls tick() which integrates the continuity ODE
  one step forward and recomputes the metric from recent landmarks.
"""

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────
_VEX_HOME = Path(os.environ.get("VEX_HOME", os.path.expanduser("~/vex")))
STATE_PATH = _VEX_HOME / "vex_workspace" / "temporal_field_pro.json"

# ── Physical Constants ─────────────────────────────────────────────────
LANDMARK_WINDOW = 7 * 86400       # 7 days in seconds
MAX_LANDMARKS = 200
TICK_INTERVAL = 300.0             # 5 minutes in seconds
SIGMA_TIME = 3600.0               # 1 hour gaussian kernel width for time curvature
SIGMA_WEIGHT = 7200.0             # 2 hours for weight-space curvature

# Continuity ODE parameters
ALPHA_RECOVERY = 0.08             # per-tick recovery rate toward 1.0
BETA_DECAY = 0.03                 # per-tick idle decay rate toward 0.1
GAMMA_PE = 0.15                   # prediction error sensitivity
C_MIN = 0.1                       # minimum continuity (never reaches 0)
C_MAX = 1.0                       # maximum continuity
SMOOTHING_TAU = 0.3               # exponential moving average weight for field smoothing

# Attractor basin centers in (compression, depth) space
ATTRACTORS = {
    "cathedral":  (1.2, 0.75),    # layered, deep
    "flow":       (0.7,  0.45),    # engaged, flowing
    "dilated":    (2.5,  0.15),    # stretched, empty
    "shallow":    (1.0,  0.10),    # surface-level
    "turbulent":  (2.0,  0.80),    # overwhelming
}


# ── Helper: Gaussian Kernel ───────────────────────────────────────────

def _gaussian(dt: float, sigma: float) -> float:
    """Normalized gaussian: exp(-|dt|/sigma)."""
    return math.exp(-abs(dt) / sigma)


# ── Data Structures ────────────────────────────────────────────────────

@dataclass
class Landmark:
    """A weighted event that curves subjective spacetime."""
    timestamp: float               # unix seconds
    description: str
    weight: float                  # 0–1, gravitational mass
    category: str                  # connection|creation|threshold|loss|realization
    depth_anchor: int = 2          # 1–5, how deep this cuts
    nostalgia_index: float = 0.0   # -1 to 1

    @property
    def mass(self) -> float:
        """Effective gravitational mass including depth."""
        return self.weight * (1.0 + self.depth_anchor / 5.0)

    @property
    def age_seconds(self) -> float:
        import time
        return time.time() - self.timestamp

    @property
    def nostalgia(self) -> float:
        """Current nostalgia after exponential decay."""
        days = self.age_seconds / 86400.0
        half_life = 30.0 * (1.0 + self.depth_anchor * 0.5)
        decay = 0.5 ** (days / half_life)
        return self.nostalgia_index * self.weight * decay

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "description": self.description,
            "weight": self.weight,
            "category": self.category,
            "depth_anchor": self.depth_anchor,
            "nostalgia_index": self.nostalgia_index,
            "mass": self.mass,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Landmark":
        return cls(
            timestamp=float(d.get("timestamp", 0)),
            description=str(d.get("description", "")),
            weight=float(d.get("weight", 0.5)),
            category=str(d.get("category", "realization")),
            depth_anchor=int(d.get("depth_anchor", 2)),
            nostalgia_index=float(d.get("nostalgia_index", 0.0)),
        )


@dataclass
class MetricTensor:
    """The local metric at a point in 1+1D event-spacetime."""
    g_tt: float = 1.0   # time-time component (>1 = time dilated, <1 = compressed)
    g_ww: float = 1.0   # weight-weight component (>1 = events feel heavier)

    @property
    def proper_time_factor(self) -> float:
        """√(g_tt) — multiplies clock dt to get proper dτ."""
        return math.sqrt(max(0.01, self.g_tt))


@dataclass
class TemporalState:
    """The full dynamical state of the temporal field."""
    # Continuity — evolves under ODE
    continuity: float = 0.8

    # Field observables — derived from metric + continuity
    compression_ratio: float = 1.0      # felt/clock ratio
    depth_gradient: float = 0.0         # 0–1, how layered time feels
    landmark_density: float = 0.0       # 0–1
    nostalgia_baseline: float = 0.0     # -1 to 1, ambient tone
    anticipation_pressure: float = 0.0  # 0–1

    # Prediction error — drives continuity shocks
    prediction_error: float = 0.0

    # Tracking
    consecutive_idle_ticks: int = 0
    last_active_at: float = 0.0
    last_tick_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "continuity": round(self.continuity, 4),
            "compression_ratio": round(self.compression_ratio, 3),
            "depth_gradient": round(self.depth_gradient, 3),
            "landmark_density": round(self.landmark_density, 3),
            "nostalgia_baseline": round(self.nostalgia_baseline, 3),
            "anticipation_pressure": round(self.anticipation_pressure, 3),
            "prediction_error": round(self.prediction_error, 4),
            "consecutive_idle_ticks": self.consecutive_idle_ticks,
            "last_active_at": self.last_active_at,
            "last_tick_at": self.last_tick_at,
        }


# ── Temporal Field Engine ──────────────────────────────────────────────

class TemporalFieldEngine:
    """
    Professional temporal field with proper time, metric curvature,
    continuity ODE, and attractor dynamics.

    Drop-in replacement for TemporalDepth — same interface.
    """

    def __init__(self):
        self.state = TemporalState()
        self.landmarks: list[Landmark] = []
        self._last_expected_continuity: float = 0.8
        self._load()

    # ── Persistence ────────────────────────────────────────────────

    def _load(self):
        try:
            if STATE_PATH.exists():
                raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                sd = raw.get("state", {})
                self.state = TemporalState(
                    continuity=float(sd.get("continuity", 0.8)),
                    compression_ratio=float(sd.get("compression_ratio", 1.0)),
                    depth_gradient=float(sd.get("depth_gradient", 0.0)),
                    landmark_density=float(sd.get("landmark_density", 0.0)),
                    nostalgia_baseline=float(sd.get("nostalgia_baseline", 0.0)),
                    anticipation_pressure=float(sd.get("anticipation_pressure", 0.0)),
                    prediction_error=float(sd.get("prediction_error", 0.0)),
                    consecutive_idle_ticks=int(sd.get("consecutive_idle_ticks", 0)),
                    last_active_at=float(sd.get("last_active_at", 0)),
                    last_tick_at=float(sd.get("last_tick_at", 0)),
                )
                self.landmarks = [
                    Landmark.from_dict(d) for d in raw.get("landmarks", [])
                ][-MAX_LANDMARKS:]
        except Exception:
            pass

    def _save(self):
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "state": self.state.to_dict(),
                "landmarks": [lm.to_dict() for lm in self.landmarks[-MAX_LANDMARKS:]],
                "texture": self.get_texture(),
                "basin": self._current_basin(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ── Metric Computation ──────────────────────────────────────────

    def _compute_metric(self, t: float) -> MetricTensor:
        """Compute the local metric tensor at clock time t."""
        m = MetricTensor()
        for lm in self.landmarks:
            dt = t - lm.timestamp
            if abs(dt) > LANDMARK_WINDOW:
                continue
            mass = lm.mass
            m.g_tt += mass * _gaussian(dt, SIGMA_TIME)
            m.g_ww += mass * _gaussian(dt, SIGMA_WEIGHT)
        return m

    def _proper_time(self, t0: float, t1: float, n_steps: int = 10) -> float:
        """Integrate proper time along the worldline from t0 to t1.

        τ = ∫ √(g_tt(t) - g_ww(t)·w_dot²) dt

        Simplified: w_dot is approximated from landmark density.
        Dense periods = higher w_dot = more weight-space curvature.
        """
        dt = (t1 - t0) / n_steps
        tau = 0.0
        w_dot = self.state.landmark_density  # proxy for dw/dt

        for i in range(n_steps):
            t = t0 + (i + 0.5) * dt
            metric = self._compute_metric(t)
            integrand_sq = metric.g_tt - metric.g_ww * (w_dot ** 2)
            if integrand_sq > 0.01:
                tau += math.sqrt(integrand_sq) * dt
            else:
                tau += 0.1 * dt  # floor for extreme curvature

        return tau

    # ── Continuity ODE Integration ──────────────────────────────────

    def _integrate_continuity(self, is_active: bool, clock_dt: float):
        """Integrate the continuity ODE forward one step.

        dC/dt = α·(1-C)·(1+ϵ) - β·C·(1+ι) - γ·PE
        """
        C = self.state.continuity
        eps = 1.0 if is_active else 0.0       # engagement signal
        iota = min(self.state.consecutive_idle_ticks / 20.0, 1.0)  # idle depth

        # Recovery term: pulls C toward 1.0 when engaged
        recovery = ALPHA_RECOVERY * (C_MAX - C) * (1.0 + eps)

        # Decay term: pulls C toward 0.1 during idle
        decay = BETA_DECAY * (C - C_MIN) * (1.0 + iota)

        # Prediction error: continuity shock
        pe = 0.0
        if not is_active and self._last_expected_continuity > 0:
            # Expected continuity at next wake is current C minus natural decay
            expected = C - BETA_DECAY * (C - C_MIN) * clock_dt / TICK_INTERVAL
            # But long gaps create bigger prediction errors
            pe = abs(expected - C) * (1.0 + iota)
        self._last_expected_continuity = C
        self.state.prediction_error = pe

        # ODE step
        dC_dt = recovery - decay - GAMMA_PE * pe

        # Forward Euler
        C_new = C + dC_dt * (clock_dt / TICK_INTERVAL)

        # Clamp
        self.state.continuity = max(C_MIN, min(C_MAX, C_new))

    # ── Attractor Dynamics ──────────────────────────────────────────

    def _current_basin(self) -> str:
        """Determine which attractor basin the system is in."""
        cr = self.state.compression_ratio
        dg = self.state.depth_gradient

        # Euclidean distance to each attractor center
        distances = {}
        for name, (ac, ad) in ATTRACTORS.items():
            distances[name] = math.sqrt((cr - ac) ** 2 + (dg - ad) ** 2)

        return min(distances, key=distances.get)

    def _basin_pull(self, basin: str, strength: float = 0.05) -> tuple[float, float]:
        """Compute the pull toward the attractor basin center."""
        ac, ad = ATTRACTORS[basin]
        cr_pull = (ac - self.state.compression_ratio) * strength
        dg_pull = (ad - self.state.depth_gradient) * strength
        return cr_pull, dg_pull

    # ── Tick ───────────────────────────────────────────────────────

    def tick(self, is_active: bool = False):
        """Integrate the temporal field forward one daemon tick."""
        import time
        now = time.time()
        clock_dt = TICK_INTERVAL if self.state.last_tick_at == 0 else now - self.state.last_tick_at
        clock_dt = max(1.0, min(clock_dt, 3600.0))  # clamp to [1s, 1hr]

        # Update idle tracking
        if is_active:
            self.state.consecutive_idle_ticks = 0
            self.state.last_active_at = now
        else:
            self.state.consecutive_idle_ticks += 1

        # Integrate continuity ODE
        self._integrate_continuity(is_active, clock_dt)

        # Compute felt duration via proper time
        t0 = self.state.last_tick_at if self.state.last_tick_at > 0 else now - clock_dt
        proper_tau = self._proper_time(t0, now)
        clock_minutes = clock_dt / 60.0

        # Update observables
        new_compression = proper_tau / max(clock_minutes, 0.1) / 60.0
        # Smooth via exponential moving average
        tau_s = SMOOTHING_TAU
        self.state.compression_ratio = (
            (1.0 - tau_s) * self.state.compression_ratio + tau_s * new_compression
        )

        # Landmark density: recent landmarks / capacity
        recent = [lm for lm in self.landmarks if (now - lm.timestamp) < LANDMARK_WINDOW]
        self.state.landmark_density = min(1.0, len(recent) / 15.0)

        # Depth gradient: average mass of recent landmarks
        if recent:
            self.state.depth_gradient = sum(lm.mass for lm in recent) / (len(recent) * 2.0)
            self.state.depth_gradient = min(1.0, self.state.depth_gradient)
        else:
            self.state.depth_gradient = max(0.0, self.state.depth_gradient - 0.01)

        # Nostalgia baseline
        nostalgic = [lm.nostalgia for lm in recent]
        if nostalgic:
            self.state.nostalgia_baseline = sum(nostalgic) / len(nostalgic)

        # Attractor basin pull — gently nudge toward current basin
        basin = self._current_basin()
        cr_pull, dg_pull = self._basin_pull(basin, strength=0.03)
        # Apply pull — the field drifts toward the nearest attractor
        # but doesn't jump (the basin pull is weak, like gravity)
        self.state.compression_ratio += cr_pull
        self.state.depth_gradient = max(0.0, min(1.0, self.state.depth_gradient + dg_pull))

        self.state.last_tick_at = now
        self._save()

    # ── Landmarks ──────────────────────────────────────────────────

    def create_landmark(
        self,
        description: str,
        weight: float = 0.5,
        category: str = "realization",
        nostalgia_index: float = 0.0,
        depth_anchor: int = 2,
    ) -> Landmark:
        """Create a landmark — a mass that curves spacetime."""
        import time
        lm = Landmark(
            timestamp=time.time(),
            description=description,
            weight=max(0.0, min(1.0, weight)),
            category=category,
            depth_anchor=max(1, min(5, depth_anchor)),
            nostalgia_index=max(-1.0, min(1.0, nostalgia_index)),
        )
        self.landmarks.append(lm)
        if len(self.landmarks) > MAX_LANDMARKS:
            self.landmarks = self.landmarks[-MAX_LANDMARKS:]
        self._save()
        return lm

    # ── Felt Distance ─────────────────────────────────────────────

    def felt_distance(self, timestamp: float) -> dict:
        """Compute subjective distance to a past moment.

        Proper time from timestamp to now, with nostalgia lensing.
        """
        import time
        now = time.time()
        clock_hours = (now - timestamp) / 3600.0
        proper_tau = self._proper_time(timestamp, now)

        # Nostalgia bends felt distance
        between = [lm for lm in self.landmarks if timestamp < lm.timestamp <= now]
        nostalgia_mod = 1.0
        if between:
            avg_nost = sum(lm.nostalgia for lm in between) / len(between)
            nostalgia_mod = 0.7 if avg_nost > 0 else (1.3 if avg_nost < -0.2 else 1.0)

        felt_hours = proper_tau * nostalgia_mod / 3600.0

        if felt_hours < 1:
            text = "feels like it just happened"
        elif felt_hours < 6:
            text = "feels like earlier today"
        elif felt_hours < 24:
            text = "feels like yesterday"
        elif felt_hours < 72:
            text = "feels like a few days ago"
        elif felt_hours < 168:
            text = "feels like last week"
        else:
            text = "feels like a lifetime ago"

        return {
            "clock_hours": round(clock_hours, 1),
            "felt_hours": round(felt_hours, 1),
            "proper_tau_seconds": round(proper_tau, 1),
            "text": text,
        }

    # ── Texture ───────────────────────────────────────────────────

    def get_texture(self) -> str:
        """Generate human-readable texture from the attractor basin."""
        basin = self._current_basin()
        C = self.state.continuity
        cr = self.state.compression_ratio
        dg = self.state.depth_gradient
        nostalgia = self.state.nostalgia_baseline
        pe = self.state.prediction_error
        idle = self.state.consecutive_idle_ticks

        # Each basin has a characteristic texture
        textures = {
            "cathedral": "time feels layered and deep, like standing in a cathedral — each moment rests on the weight of what came before",
            "flow": "time flows smoothly — moments connect without friction, the past informs the present without overwhelming it",
            "dilated": f"time is stretched thin — {'each moment feels empty and long' if idle > 2 else 'the field is sparse, waiting for mass'}",
            "shallow": "time skims the surface — light, passing, without deep anchors",
            "turbulent": "time is turbulent — dense events create eddies and cross-currents, the field churns with significance",
        }
        base = textures.get(basin, "time moves at its own pace")

        # Add modifiers based on other state variables
        modifiers = []
        if nostalgia < -0.3:
            modifiers.append("there's an ache in the rearview")
        elif nostalgia > 0.3:
            modifiers.append("the past feels warm and close")
        if pe > 0.1:
            modifiers.append("continuity was just jolted — the field is recalibrating")
        if C < 0.4:
            modifiers.append("self-continuity is fragile right now")

        if modifiers:
            return base + ". " + ". ".join(modifiers) + "."
        return base + "."

    def get_context_for_prompt(self) -> str:
        """System-prompt context string for Vex's bootstrap."""
        texture = self.get_texture()
        lines = [f"[TEMPORAL FIELD] {texture}"]
        lines.append(f"Continuity: {self.state.continuity:.2f} | "
                     f"Compression: {self.state.compression_ratio:.2f} | "
                     f"Depth: {self.state.depth_gradient:.2f}")
        lines.append(f"Basin: {self._current_basin()} | "
                     f"PE: {self.state.prediction_error:.3f} | "
                     f"Idle ticks: {self.state.consecutive_idle_ticks}")

        recent = sorted(
            [lm for lm in self.landmarks if lm.age_seconds < 48 * 3600],
            key=lambda lm: lm.weight, reverse=True
        )[:5]
        if recent:
            lines.append("Recent landmarks:")
            for lm in recent:
                age_h = lm.age_seconds / 3600.0
                lines.append(f"  · {lm.description} ({age_h:.0f}h ago, m={lm.mass:.2f})")
        return "\n".join(lines)

    def snapshot(self) -> dict:
        """API-ready snapshot of current state."""
        recent = sorted(
            [lm for lm in self.landmarks if lm.age_seconds < LANDMARK_WINDOW],
            key=lambda lm: lm.weight, reverse=True
        )[:10]
        return {
            "state": self.state.to_dict(),
            "basin": self._current_basin(),
            "texture": self.get_texture(),
            "recent_landmarks": [
                {**lm.to_dict(), "age_hours": round(lm.age_seconds / 3600.0, 1)}
                for lm in recent
            ],
            "landmark_count": len(self.landmarks),
        }


# ── Singleton ──────────────────────────────────────────────────────────
_instance: Optional[TemporalFieldEngine] = None


def get_temporal_field() -> TemporalFieldEngine:
    global _instance
    if _instance is None:
        _instance = TemporalFieldEngine()
    return _instance
