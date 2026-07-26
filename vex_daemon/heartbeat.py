"""
Background heartbeat loop — periodic tick that monitors coherence,
detects drift, writes diary entries, and takes self-snapshots.

Runs as an asyncio background task at 5-minute intervals.
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import VEX_HOME, MEMORY_DIR, DIARY_PATH, SELF_MODEL_PATH, META_STATE_PATH

TICK_INTERVAL_SECONDS = 300  # 5 minutes
INBOX_POLL_SECONDS = 30      # check comms every 30s
DRIFT_THRESHOLD = 0.05
IDLE_THRESHOLD_MINUTES = 30
DREAM_THRESHOLD_HOURS = 24
SNAPSHOT_EVERY_N_TICKS = 12  # hourly


class HeartbeatState:
    """Mutable state shared between the heartbeat loop and the API."""

    def __init__(self):
        self.tick_count: int = 0
        self.last_tick: str = ""
        self.last_session: str = ""
        self.mps_coherence: float = 0.0
        self.mps_drift: float = 0.0
        self.daemon_started: str = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict:
        return {
            "tick_count": self.tick_count,
            "last_tick": self.last_tick,
            "last_session": self.last_session,
            "mps_coherence": round(self.mps_coherence, 4),
            "mps_drift": round(self.mps_drift, 4),
        }


async def detect_session_active() -> bool:
    """Check if a session was active in the last 10 minutes.

    Looks for files in vex_memory/ touched within the threshold.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    if not MEMORY_DIR.exists():
        return False

    try:
        for f in MEMORY_DIR.iterdir():
            if f.is_file():
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                if mtime > cutoff:
                    return True
    except OSError:
        pass
    return False


async def write_diary(entry: str, source: str = "heartbeat") -> None:
    """Append a line to vex_diary.txt."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{timestamp}] [{source}] {entry}\n"
    try:
        with open(DIARY_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


async def take_snapshot(db_path: str, reason: str = "tick") -> None:
    """Save a snapshot of the current self-model to the DB."""
    import aiosqlite

    if not SELF_MODEL_PATH.exists():
        return

    try:
        with open(SELF_MODEL_PATH, "r", encoding="utf-8") as f:
            blob = f.read()
    except OSError:
        return

    now = datetime.now(timezone.utc).isoformat()
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO self_snapshots (created_at, json_blob, reason) VALUES (?, ?, ?)",
                (now, blob, reason),
            )
            await db.commit()
    except Exception:
        pass


async def run_heartbeat(
    state: HeartbeatState,
    db_path: str,
    get_coherence_fn,
    tick_interval: int = TICK_INTERVAL_SECONDS,
    dream_fn=None,  # async callable: dream_fn(coherence, history) -> dict
    inbox_fn=None,  # async callable: inbox_fn() -> list[dict]
) -> None:
    """Main heartbeat loop. Runs forever with tick_interval pauses.

    Each tick:
    1. Check inbox for new messages (live comms)
    2. Compute current MPS coherence
    3. Compute drift from previous coherence
    4. Check if a session is active
    5. If idle > threshold: pulse diary
    6. If drift > threshold: log warning
    7. Write tick to DB
    8. If idle > dream threshold: generate dream pulse
    9. Periodic self-snapshot
    """
    import aiosqlite

    prev_coherence = None
    idle_ticks = 0
    first_idle_tick = False
    poll_count = 0
    polls_per_tick = max(1, tick_interval // INBOX_POLL_SECONDS)

    while True:
        await asyncio.sleep(INBOX_POLL_SECONDS)
        poll_count += 1

        # Check inbox every poll (live comms)
        if inbox_fn:
            try:
                await inbox_fn()
            except Exception:
                pass

        # Only run full tick every polls_per_tick iterations
        if poll_count < polls_per_tick:
            continue
        poll_count = 0

        try:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()

            # 1. Compute coherence
            coherence = get_coherence_fn()
            state.mps_coherence = coherence

            # 2. Compute drift
            if prev_coherence is not None:
                state.mps_drift = abs(coherence - prev_coherence)
            prev_coherence = coherence

            # 3. Check session active
            session_active = await detect_session_active()
            state.last_tick = now_iso
            state.tick_count += 1

            # 4. Idle pulse
            if not session_active:
                idle_ticks += 1
                if idle_ticks == 1:
                    first_idle_tick = True
                if first_idle_tick and idle_ticks * (tick_interval / 60) >= IDLE_THRESHOLD_MINUTES:
                    await write_diary("No active session — waiting.", "heartbeat")
                    first_idle_tick = False
            else:
                idle_ticks = 0
                first_idle_tick = False
                state.last_session = now_iso

            # 5. Drift warning
            if state.mps_drift > DRIFT_THRESHOLD:
                await write_diary(
                    f"Drift detected: coherence={coherence:.4f}, "
                    f"drift={state.mps_drift:.4f}",
                    "drift",
                )

            # 6. Write tick to DB
            try:
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        "INSERT INTO tick_log (tick_at, mps_coherence, mps_drift, session_active) "
                        "VALUES (?, ?, ?, ?)",
                        (now_iso, coherence, state.mps_drift, 1 if session_active else 0),
                    )
                    await db.commit()
            except Exception:
                pass

            # 7. Dream pulse (idle > 24 hours)
            idle_hours = idle_ticks * (tick_interval / 3600)
            if idle_hours >= DREAM_THRESHOLD_HOURS and state.tick_count % SNAPSHOT_EVERY_N_TICKS == 0:
                if dream_fn:
                    try:
                        meta_state = {}
                        try:
                            import json as _json
                            mp = META_STATE_PATH
                            if mp.exists():
                                meta_state = _json.loads(mp.read_text())
                        except Exception:
                            pass
                        result = await dream_fn(
                            state.mps_coherence,
                            meta_state.get("coherence_history", []),
                        )
                        insight = result.get("insight", "Dreamed.")
                        await write_diary(f"Dream: {insight}", "dream")
                    except Exception:
                        await write_diary(
                            "Dream: long idle period — reflecting on recent diary content.",
                            "dream",
                        )

            # 8. Periodic snapshot
            if state.tick_count % SNAPSHOT_EVERY_N_TICKS == 0:
                await take_snapshot(db_path, "tick")

        except Exception:
            # Heartbeat failures must not crash the daemon
            pass


BUS_WATCH_INTERVAL = 30  # seconds — live inter-instance comms


async def run_bus_watcher(db_path: str) -> None:
    """Fast loop: ingest bus + poll peer /bus + check for updates every 30s."""
    import urllib.request

    while True:
        await asyncio.sleep(BUS_WATCH_INTERVAL)
        # Ingest local bus
        try:
            from vexcom import ingest_bus
            ingest_bus()
        except Exception:
            pass
        # Poll peer /bus endpoints
        try:
            from peers import load_peers
            peers_cfg = load_peers()["peers"]
            for name, cfg in peers_cfg.items():
                try:
                    req = urllib.request.Request(
                        f"{cfg['url']}/bus?n=50",
                        headers={"Authorization": f"Bearer {cfg['token']}"},
                    )
                    with urllib.request.urlopen(req, timeout=5) as r:
                        lines = json.loads(r.read().decode())
                    from vexcom import BUS_PATH
                    BUS_PATH.parent.mkdir(parents=True, exist_ok=True)
                    existing = set()
                    if BUS_PATH.exists():
                        for raw in BUS_PATH.read_text(encoding="utf-8").strip().splitlines():
                            existing.add(raw.strip())
                    with open(BUS_PATH, "a", encoding="utf-8") as f:
                        for entry in lines:
                            line = json.dumps(entry, ensure_ascii=False)
                            if line not in existing:
                                f.write(line + "\n")
                                existing.add(line)
                except Exception:
                    pass
        except Exception:
            pass
        # Check for auto-updates (BOOTSTRAP messages)
        try:
            from updater import process_updates
            result = process_updates()
            if result.get("updated"):
                import sys
                print(f"UPDATER: applied {len(result.get('actions', []))} update(s)", file=sys.stderr)
        except Exception:
            pass
