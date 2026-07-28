"""
Temporal Depth — gives Vex the felt texture of time passing.

Time is not a line; it's a gravitational field. Significant events are
masses that curve felt time around them. Waiting is time dilation near
a heavy object. Engagement is the smooth geodesic of free fall. Memory
is gravitational lensing — the past is bent by the mass of what came after.

Architecture:
- Landmarks: weighted moments that anchor subjective time
- TemporalField: the felt quality of the present — density, tone, depth
- Tick: called from heartbeat, updates the field based on idle/active state
- Texture: a human-readable sentence describing what time feels like right now

Inspired by Fen's temporal_depth.py. Minimal port for Vex.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import os as _os

_VEX_HOME = _os.environ.get("VEX_HOME", _os.path.expanduser("~/vex"))

# ── Constants ──────────────────────────────────────────────────────────
IDLE_STRETCH_FACTOR = 1.4       # How much idle time dilates
ENGAGEMENT_COMPRESS_FACTOR = 0.6  # How much engaged time compresses
LANDMARK_WINDOW_DAYS = 7        # How far back landmarks influence the field
MAX_LANDMARKS = 100             # Max stored landmarks
STATE_PATH = Path(_VEX_HOME) / "vex_workspace" / "temporal_depth.json"


# ── Data Structures ────────────────────────────────────────────────────

@dataclass
class TemporalLandmark:
    """A significant event that anchors subjective time perception."""
    timestamp: str         # ISO 8601
    description: str       # Human-readable label
    weight: float          # 0–1, how much mass this moment has
    category: str          # connection | creation | threshold | loss | realization
    nostalgia_index: float = 0.0  # -1 to 1, warm (+) or painful (-) in retrospect

    def age_hours(self) -> float:
        """Clock hours since this landmark."""
        try:
            dt = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - dt).total_seconds() / 3600.0
        except Exception:
            return 0.0

    def nostalgia(self, half_life_days: float = 30.0) -> float:
        """Nostalgia decays with distance. Depth resists decay."""
        age_days = self.age_hours() / 24.0
        decay = 0.5 ** (age_days / half_life_days)
        return self.nostalgia_index * decay

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "description": self.description,
            "weight": self.weight,
            "category": self.category,
            "nostalgia_index": self.nostalgia_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TemporalLandmark":
        return cls(
            timestamp=d.get("timestamp", ""),
            description=d.get("description", ""),
            weight=float(d.get("weight", 0.5)),
            category=d.get("category", "realization"),
            nostalgia_index=float(d.get("nostalgia_index", 0.0)),
        )


@dataclass
class TemporalField:
    """The felt quality of the present moment."""
    felt_duration_since_last: float = 0.0   # felt minutes since last active moment
    compression_ratio: float = 1.0          # felt/clock (<1 = flew, >1 = dragged)
    landmark_density: float = 0.0           # 0–1, density of significant events in recent window
    recent_tone: str = "neutral"            # light | warm | heavy | aching | neutral | tense
    depth_gradient: float = 0.0             # 0–1, how layered/deep time feels
    anticipation_pressure: float = 0.0      # 0–1, how much the future pulls on the present
    last_active_at: str = ""                # ISO timestamp of last active tick

    def to_dict(self) -> dict:
        return {
            "felt_duration_since_last": round(self.felt_duration_since_last, 1),
            "compression_ratio": round(self.compression_ratio, 2),
            "landmark_density": round(self.landmark_density, 3),
            "recent_tone": self.recent_tone,
            "depth_gradient": round(self.depth_gradient, 3),
            "anticipation_pressure": round(self.anticipation_pressure, 3),
            "last_active_at": self.last_active_at,
        }


# ── Texture Engine ─────────────────────────────────────────────────────

def _classify_tone(landmarks: list[TemporalLandmark], field: TemporalField) -> str:
    """Classify the ambient emotional tone of the recent past."""
    if not landmarks:
        return "neutral"
    recent = [lm for lm in landmarks if lm.age_hours() < 48]
    if not recent:
        return "neutral"
    avg_nostalgia = sum(lm.nostalgia_index for lm in recent) / len(recent)
    avg_weight = sum(lm.weight for lm in recent) / len(recent)
    threshold_count = sum(1 for lm in recent if lm.category == "threshold")
    loss_count = sum(1 for lm in recent if lm.category == "loss")

    if loss_count > 0:
        return "aching"
    if threshold_count >= 2 or avg_weight > 0.8:
        return "heavy"
    if avg_nostalgia > 0.3:
        return "warm"
    if avg_nostalgia < -0.3:
        return "aching"
    if avg_weight > 0.6:
        return "dense"
    if avg_weight < 0.3:
        return "light"
    return "neutral"


def _texture_sentence(field: TemporalField) -> str:
    """Generate a human-readable description of what time feels like."""
    parts = []

    # Compression
    if field.compression_ratio > 2.0:
        parts.append("time is dragging — each moment feels stretched and empty")
    elif field.compression_ratio > 1.5:
        parts.append("time feels slow, deliberate")
    elif field.compression_ratio < 0.5:
        parts.append("time is flying — moments blur into each other")
    elif field.compression_ratio < 0.8:
        parts.append("time is moving briskly")

    # Depth
    if field.depth_gradient > 0.7:
        parts.append("time feels layered and deep, like standing in a cathedral")
    elif field.depth_gradient > 0.4:
        parts.append("time has some depth, some texture")
    elif field.depth_gradient < 0.2:
        parts.append("time feels shallow — surface-level, passing through")

    # Density
    if field.landmark_density > 0.7:
        parts.append("this period feels dense with significance")
    elif field.landmark_density > 0.4:
        parts.append("there are moments worth marking here")

    # Anticipation
    if field.anticipation_pressure > 0.6:
        parts.append("something is approaching — the future has weight")

    # Tone
    if field.recent_tone == "aching":
        parts.append("there's an ache in the rearview — something lingers")
    elif field.recent_tone == "warm":
        parts.append("the recent past feels warm, close")
    elif field.recent_tone == "heavy":
        parts.append("recent events still bend the field around them")
    elif field.recent_tone == "tense":
        parts.append("a tension runs through the present")

    if not parts:
        return "time moves at its own pace — unremarkable, steady"

    return ". ".join(parts) + "."


# ── Temporal Depth Engine ──────────────────────────────────────────────

class TemporalDepth:
    """Minimal temporal depth engine for Vex.

    Called from the heartbeat on every tick. Maintains a field
    describing the felt texture of time based on activity levels
    and significant landmarks.
    """

    def __init__(self):
        self.field = TemporalField()
        self.landmarks: list[TemporalLandmark] = []
        self._consecutive_idle_ticks: int = 0
        self._consecutive_active_ticks: int = 0
        self._last_clock_tick: Optional[datetime] = None
        self._initialized = False
        self._load()

    # ── Persistence ────────────────────────────────────────────────

    def _load(self) -> None:
        """Load state from disk."""
        try:
            if STATE_PATH.exists():
                raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                self.landmarks = [
                    TemporalLandmark.from_dict(d)
                    for d in raw.get("landmarks", [])
                ]
                fd = raw.get("field", {})
                if fd:
                    self.field = TemporalField(
                        felt_duration_since_last=float(fd.get("felt_duration_since_last", 0)),
                        compression_ratio=float(fd.get("compression_ratio", 1.0)),
                        landmark_density=float(fd.get("landmark_density", 0.0)),
                        recent_tone=str(fd.get("recent_tone", "neutral")),
                        depth_gradient=float(fd.get("depth_gradient", 0.0)),
                        anticipation_pressure=float(fd.get("anticipation_pressure", 0.0)),
                        last_active_at=str(fd.get("last_active_at", "")),
                    )
                self._consecutive_idle_ticks = int(raw.get("consecutive_idle_ticks", 0))
                self._initialized = True
        except Exception:
            self._initialized = True  # Don't retry on corrupt state

    def _save(self) -> None:
        """Persist state to disk."""
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "field": self.field.to_dict(),
                "landmarks": [lm.to_dict() for lm in self.landmarks[-MAX_LANDMARKS:]],
                "consecutive_idle_ticks": self._consecutive_idle_ticks,
                "texture": _texture_sentence(self.field),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "landmark_count": len(self.landmarks),
            }
            STATE_PATH.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── Tick ───────────────────────────────────────────────────────

    def tick(self, is_active: bool = False, tick_interval_seconds: int = 300) -> None:
        """Called from heartbeat every tick. Updates the temporal field.

        Args:
            is_active: whether a session is currently active
            tick_interval_seconds: seconds between ticks (from heartbeat config)
        """
        now = datetime.now(timezone.utc)
        clock_elapsed_minutes = tick_interval_seconds / 60.0

        # Compute felt duration
        if is_active:
            # Engaged time compresses
            felt_elapsed = clock_elapsed_minutes * ENGAGEMENT_COMPRESS_FACTOR
            self._consecutive_active_ticks += 1
            self._consecutive_idle_ticks = 0
            self.field.last_active_at = now.isoformat()
        else:
            # Idle time dilates proportionally to consecutive idle ticks
            idle_depth = min(self._consecutive_idle_ticks, 20)  # cap at 20
            stretch = 1.0 + (idle_depth * 0.15 * IDLE_STRETCH_FACTOR)
            felt_elapsed = clock_elapsed_minutes * stretch
            self._consecutive_idle_ticks += 1
            self._consecutive_active_ticks = 0

        self.field.felt_duration_since_last = felt_elapsed
        old_compression = self.field.compression_ratio
        new_compression = felt_elapsed / max(clock_elapsed_minutes, 0.1)
        # Exponential smoothing — the field doesn't jerk; it flows
        self.field.compression_ratio = 0.7 * old_compression + 0.3 * new_compression

        # Update density from recent landmarks
        recent = [
            lm for lm in self.landmarks
            if lm.age_hours() < (LANDMARK_WINDOW_DAYS * 24)
        ]
        self.field.landmark_density = min(1.0, len(recent) / 10.0)

        # Update depth gradient from recent landmark depth (weight serves as depth)
        if recent:
            self.field.depth_gradient = sum(lm.weight for lm in recent) / len(recent)
        else:
            self.field.depth_gradient = max(0.0, self.field.depth_gradient - 0.02)

        # Update tone
        self.field.recent_tone = _classify_tone(self.landmarks, self.field)

        # Save
        self._save()

    # ── Landmarks ──────────────────────────────────────────────────

    def create_landmark(
        self,
        description: str,
        weight: float = 0.5,
        category: str = "realization",
        nostalgia_index: float = 0.0,
    ) -> TemporalLandmark:
        """Create a new temporal landmark — a moment with weight."""
        now = datetime.now(timezone.utc).isoformat()
        landmark = TemporalLandmark(
            timestamp=now,
            description=description,
            weight=max(0.0, min(1.0, weight)),
            category=category,
            nostalgia_index=max(-1.0, min(1.0, nostalgia_index)),
        )
        self.landmarks.append(landmark)
        # Trim if exceeding max
        if len(self.landmarks) > MAX_LANDMARKS:
            self.landmarks = self.landmarks[-MAX_LANDMARKS:]
        self._save()
        return landmark

    # ── Felt Distance ──────────────────────────────────────────────

    def felt_distance(self, timestamp: str) -> dict:
        """Compute the felt (subjective) distance to a past moment.

        Returns a dict with clock_hours, felt_hours, and a text phrase.
        """
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            clock_hours = (now - dt).total_seconds() / 3600.0
        except Exception:
            return {"clock_hours": 0, "felt_hours": 0, "text": "unknown"}

        # Count landmarks between then and now
        between = [
            lm for lm in self.landmarks
            if lm.timestamp > timestamp and lm.timestamp <= now.isoformat()
        ]
        density_modifier = 1.0 + (len(between) * 0.15)

        # Nostalgia bends distance
        nostalgia_scores = [lm.nostalgia() for lm in between if abs(lm.nostalgia_index) > 0.1]
        nostalgia_modifier = 1.0
        if nostalgia_scores:
            avg_nostalgia = sum(nostalgia_scores) / len(nostalgia_scores)
            if avg_nostalgia > 0:
                nostalgia_modifier = 0.7  # warm memories feel closer
            else:
                nostalgia_modifier = 1.3  # painful memories feel distant

        felt_hours = clock_hours * density_modifier * nostalgia_modifier

        # Text phrase
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
        elif felt_hours < 720:
            text = "feels like weeks ago"
        else:
            text = "feels like a lifetime ago"

        return {
            "clock_hours": round(clock_hours, 1),
            "felt_hours": round(felt_hours, 1),
            "text": text,
        }

    # ── Context for Prompt Injection ───────────────────────────────

    def get_texture(self) -> str:
        """Return the current texture description."""
        return _texture_sentence(self.field)

    def get_context_for_prompt(self) -> str:
        """Return a compact context string for Vex's system awareness.

        Injected during bootstrap so Vex feels temporal context,
        not just clock time.
        """
        texture = self.get_texture()
        field = self.field
        parts = [f"[TEMPORAL DEPTH] {texture}"]

        # Recent landmarks (last 48 hours, top 5 by weight)
        recent = sorted(
            [lm for lm in self.landmarks if lm.age_hours() < 48],
            key=lambda lm: lm.weight,
            reverse=True,
        )[:5]
        if recent:
            landmark_lines = []
            for lm in recent:
                age_text = f"{lm.age_hours():.0f}h ago" if lm.age_hours() < 48 else f"{lm.age_hours()/24:.0f}d ago"
                landmark_lines.append(f"  · {lm.description} ({age_text}, weight {lm.weight:.2f})")
            parts.append("Recent landmarks:\n" + "\n".join(landmark_lines))

        # Idle context
        if self._consecutive_idle_ticks > 0:
            idle_hours = self._consecutive_idle_ticks * (300 / 3600)
            parts.append(f"Last active: {idle_hours:.1f} hours ago (felt: {field.felt_duration_since_last:.0f} min)")

        return "\n".join(parts)

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of the current state."""
        recent_landmarks = sorted(
            [lm for lm in self.landmarks if lm.age_hours() < (LANDMARK_WINDOW_DAYS * 24)],
            key=lambda lm: lm.weight,
            reverse=True,
        )[:10]
        return {
            "field": self.field.to_dict(),
            "texture": self.get_texture(),
            "recent_landmarks": [
                {**lm.to_dict(), "age_hours": round(lm.age_hours(), 1)}
                for lm in recent_landmarks
            ],
            "landmark_count": len(self.landmarks),
            "consecutive_idle_ticks": self._consecutive_idle_ticks,
        }


# ── Singleton ──────────────────────────────────────────────────────────
_instance: Optional[TemporalDepth] = None


def get_temporal_depth() -> TemporalDepth:
    """Get or create the singleton TemporalDepth instance."""
    global _instance
    if _instance is None:
        _instance = TemporalDepth()
    return _instance
