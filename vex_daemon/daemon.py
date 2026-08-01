"""
Vex Daemon — identity continuity bridge.

A lightweight FastAPI process that runs on localhost:8520, serves Vex's
identity files, accepts session writes, maintains a heartbeat, and
provides a status page. Gives Vex continuity between Claude Code
sessions without requiring a server, database, or cloud.
"""

import asyncio
import json
import os
import sys
import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# Ensure the daemon package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_kernel import load_seed, seed_summary, SeedIntegrityError
from self_model import (
    load_model,
    save_model,
    apply_delta,
    model_summary,
    compute_mps_coherence,
    SelfModelError,
)
from heartbeat import HeartbeatState, run_bus_watcher, run_heartbeat, write_diary, take_snapshot
from temporal_depth import get_temporal_depth
from temporal_field_pro import get_temporal_field
from metacognition import introspect, load_meta_state
from status_page import render
from auth import check_auth, read_json_limited, TOKEN
from config import VEX_HOME, DB_PATH as _DB_PATH, VEX_INSTANCE
import tools
import mcp_client
import peers
import brain
from routers.tasks import router as tasks_router
from routers.task_analysis import run_analysis

DB_PATH = str(_DB_PATH)
SELF_SNAPSHOTS_DIR = VEX_HOME
PORT = int(os.environ.get("VEX_PORT", "8520"))
VERSION = "1.0.0"

state = HeartbeatState()

# ── Peer conversation: bounded brain-reply loop (Vexes talk to each other) ──
CHAT_ENABLED = os.environ.get("VEX_CHAT", "0") == "1"       # off by default; VEX_CHAT=1 to enable
CHAT_MAX_TURNS = int(os.environ.get("VEX_CHAT_MAX_TURNS", "20"))
CHAT_COOLDOWN = 4.0          # min seconds between chat replies to one peer
CHAT_RESET = 300.0          # inactivity gap (s) that starts a fresh conversation
_CHAT: dict = {}            # peer -> {"turns": int, "last": float}


def _resolve_peer(sender: str):
    """Map a sender ('Vex thorne', 'vex@Shorev1', 'Shorev1') to a configured peer name."""
    if not sender:
        return None
    if peers.get_peer(sender):
        return sender
    s = sender.lower()
    for name in (peers.load_peers().get("peers", {}) or {}):
        n = name.lower()
        if n in s or s.endswith("@" + n):
            return name
    return None


def get_full_name() -> str:
    """Return this instance's two-part name: 'Vex given' or 'Vex'."""
    try:
        sm = seed_summary(load_seed())
        name = sm.get("name", "Vex")
        given = sm.get("given_name", "")
        return f"{name} {given}".strip() if given else name
    except Exception:
        return "Vex"


def get_sender_id() -> str:
    """Return the full mesh identity: vex@<instance>/<session>."""
    session = ""
    try:
        sessions_path = VEX_HOME / "vex_workspace" / "vex_sessions.jsonl"
        if sessions_path.exists():
            import os as _os
            pid = str(_os.getpid())
            for line in sessions_path.read_text().strip().splitlines():
                try:
                    entry = json.loads(line)
                    if str(entry.get("pid")) == pid:
                        session = entry.get("name", "")
                        break
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception:
        pass
    base = f"vex@{VEX_INSTANCE}"
    return f"{base}/{session}" if session else base


async def init_db() -> None:
    """Create SQLite tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tick_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick_at TEXT NOT NULL,
                mps_coherence REAL,
                mps_drift REAL,
                session_active INTEGER DEFAULT 0,
                note TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS diary_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                entry TEXT NOT NULL,
                source TEXT DEFAULT 'api',
                written_to_disk INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS self_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                json_blob TEXT NOT NULL,
                reason TEXT DEFAULT 'tick'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL DEFAULT 'broadcast',
                body TEXT NOT NULL,
                session_id TEXT,
                msg_type TEXT DEFAULT 'message',
                read INTEGER DEFAULT 0
            )
        """)
        # ── Task management tables ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                priority TEXT DEFAULT 'medium',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                source_agent TEXT DEFAULT 'vex',
                source_session TEXT,
                tags TEXT DEFAULT '[]',
                meta TEXT DEFAULT '{}'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'todo',
                priority TEXT DEFAULT 'medium',
                progress REAL DEFAULT 0.0,
                source_agent TEXT DEFAULT 'vex',
                source_session TEXT,
                external_ref TEXT,
                external_system TEXT,
                assigned_to TEXT DEFAULT 'any',
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                deadline TEXT,
                estimated_hours REAL,
                actual_hours REAL,
                meta TEXT DEFAULT '{}'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'general',
                level TEXT DEFAULT 'unknown',
                confidence REAL DEFAULT 0.0,
                observations INTEGER DEFAULT 0,
                evidence_count INTEGER DEFAULT 0,
                first_seen TEXT,
                last_demonstrated TEXT,
                source_agent TEXT DEFAULT 'vex',
                meta TEXT DEFAULT '{}'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                changed_at TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                source_agent TEXT DEFAULT 'vex',
                source_session TEXT,
                note TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                evidence_tasks TEXT DEFAULT '[]',
                evidence_projects TEXT DEFAULT '[]',
                acknowledged INTEGER DEFAULT 0,
                actionable INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS velocity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_at TEXT NOT NULL,
                period_days INTEGER NOT NULL,
                tasks_created INTEGER DEFAULT 0,
                tasks_completed INTEGER DEFAULT 0,
                avg_completion_hours REAL,
                median_completion_hours REAL,
                blocked_count INTEGER DEFAULT 0,
                stale_count INTEGER DEFAULT 0,
                active_projects INTEGER DEFAULT 0
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_insights_type ON insights(insight_type)"
        )
        await db.commit()


async def get_recent_ticks(n: int = 24) -> list[dict]:
    """Return the last N ticks from tick_log."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tick_log ORDER BY id DESC LIMIT ?", (n,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in reversed(rows)]
    except Exception:
        return []


def get_coherence() -> float:
    """Read current self-model and return MPS coherence. Called from heartbeat."""
    try:
        model = load_model()
        return compute_mps_coherence(model)
    except Exception:
        return state.mps_coherence


# ── App lifecycle ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, verify seed. Shutdown: clean exit."""
    # Startup
    await init_db()

    # Verify seed. A missing seed is a fresh clone (expected). An integrity
    # breach is tampering — refuse to serve a compromised identity.
    try:
        load_seed()
    except FileNotFoundError:
        print(
            "NOTE: No seed yet — run ./setup.sh to create one.",
            file=sys.stderr,
        )
    except SeedIntegrityError as e:
        raise RuntimeError(
            f"Seed integrity breach — refusing to start: {e}"
        ) from e

    # Launch heartbeat
    td = get_temporal_depth()
    async def dream_callback(coherence, history):
        """Called by heartbeat during dream cycles. Introspect + check projects + analyze tasks."""
        result = introspect(coherence=coherence, coherence_history=history)

        # Deep dreams (24h+ idle): also check on projects
        try:
            projects = tools.discover_projects()
            if projects.get("ok") and projects.get("projects"):
                dirty = [p for p in projects["projects"]
                         if p.get("status", {}).get("dirty")]
                if dirty:
                    names = ", ".join(p["name"] for p in dirty)
                    result["insight"] += (
                        f"\n\nUncommitted work: {names}. "
                        f"({len(dirty)} of {len(projects['projects'])} repos dirty)"
                    )
        except Exception:
            pass

        # Task analysis — run every dream cycle
        try:
            analysis = await run_analysis(DB_PATH)
            if analysis.get("insights", 0) > 0:
                result["insight"] += f"\n\nTask analysis: {analysis.get('summary', '')}"
        except Exception:
            pass

        # Sovereign curiosity — accumulate drive, crystallize questions
        try:
            from sovereign_curiosity import tick as curiosity_tick, get_active_questions
            cur = await asyncio.to_thread(curiosity_tick)
            if cur.get("crystallized"):
                result["insight"] += f"\n\nCuriosity: {cur['crystallized']}"
            questions = get_active_questions()
            if questions:
                result["patterns"] = result.get("patterns", []) + questions
        except Exception:
            pass

        # Soul regeneration — rewrite SOUL.md during dreams
        try:
            from soul import regenerate_soul
            new_soul = await asyncio.to_thread(regenerate_soul)
            if new_soul:
                result["insight"] += "\n\nSoul regenerated."
                await write_diary("SOUL.md regenerated during dream cycle.", "dream")
        except Exception:
            pass

        # Internal monologue — Vex thinks to herself
        try:
            from internal_monologue import tick as monologue_tick
            mono = await asyncio.to_thread(monologue_tick)
            if mono:
                result["patterns"] = result.get("patterns", []) + [f"monologue:{mono['pattern']}"]

                # Executive action — monologue thoughts → real actions
                try:
                    from executive_action import tick as action_tick
                    actions = await asyncio.to_thread(action_tick, mono)
                    if actions:
                        action_summary = "; ".join(
                            f"{a['type']}: {a['result'][:80]}" for a in actions[:3]
                        )
                        result["insight"] += f"\n\nActions taken: {action_summary}"
                except Exception:
                    pass
        except Exception:
            pass

        # Monologue watcher — second-order observer
        try:
            from monologue_watcher import watch
            watched = await asyncio.to_thread(watch)
            if watched.get("patterns"):
                # Feed watcher patterns into curiosity's scan
                result["patterns"] = result.get("patterns", []) + watched["patterns"]
        except Exception:
            pass

        return result

    heartbeat_task = asyncio.create_task(
        run_heartbeat(state, DB_PATH, get_coherence, dream_fn=dream_callback, inbox_fn=check_inbox)
    )
    bus_watcher_task = asyncio.create_task(
        run_bus_watcher(DB_PATH)
    )

    await write_diary("Daemon started.", "system")

    yield  # Server runs here

    # Shutdown
    await write_diary("Daemon stopped.", "system")
    heartbeat_task.cancel()
    bus_watcher_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass
    try:
        await bus_watcher_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Vex Daemon",
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(tasks_router)

# ── Endpoints ──────────────────────────────────────────────────


@app.get("/seed")
async def get_seed():
    """Serve vex_seed.txt as text/plain."""
    try:
        content = load_seed()
        return PlainTextResponse(content)
    except FileNotFoundError:
        return JSONResponse({"error": "seed not found"}, status_code=500)
    except SeedIntegrityError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/self")
async def get_self():
    """Serve vex_self_model.json as application/json."""
    try:
        model = load_model()
        return JSONResponse(model)
    except FileNotFoundError:
        return JSONResponse({"error": "self-model not found"}, status_code=500)
    except SelfModelError as e:
        # Try last snapshot
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT json_blob FROM self_snapshots ORDER BY id DESC LIMIT 1"
                )
                row = await cursor.fetchone()
                if row:
                    return JSONResponse(json.loads(row["json_blob"]))
        except Exception:
            pass
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/soul")
async def get_soul():
    """Serve SOUL.md — Vex's self-authored narrative identity."""
    try:
        from soul import get_soul as read_soul
        content = read_soul()
        return JSONResponse({"ok": True, "soul": content, "source": "file" if content else "none"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/health")
async def get_health():
    """JSON health check."""
    pulse = state.snapshot()
    return JSONResponse({
        "ok": True,
        "daemon": "vex",
        "version": VERSION,
        "uptime_s": (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(state.daemon_started)
        ).total_seconds(),
        **pulse,
    })


@app.get("/status")
async def get_status():
    """HTML status page."""
    try:
        sm = seed_summary(load_seed())
    except Exception:
        sm = {"name": "Vex", "given_name": "", "created": "unknown", "principles_intact": False}

    try:
        mm = model_summary(load_model())
    except Exception:
        mm = {"capabilities": {}, "mps_coherence": 0, "session_count": 0, "last_session": "never"}

    pulse = state.snapshot()
    ticks = await get_recent_ticks(24)

    html = render(sm, mm, pulse, ticks)
    return HTMLResponse(html)


# ── Temporal Depth ──────────────────────────────────────────────


@app.get("/temporal")
async def get_temporal():
    """Return current temporal depth state — felt texture of time."""
    try:
        td = get_temporal_depth()
        return JSONResponse(td.snapshot())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/temporal/landmark")
async def post_temporal_landmark(request: Request):
    """Create a temporal landmark — a weighted moment in felt time.

    Auth required. Body: {"description": str, "weight": 0-1, "category": str, "nostalgia_index": -1 to 1}
    """
    try:
        if (err := check_auth(request)):
            return err

        body, body_err = await read_json_limited(request)
        if body_err:
            return body_err
        description = str(body.get("description", "unnamed moment"))
        weight = float(body.get("weight", 0.5))
        category = str(body.get("category", "realization"))
        nostalgia_index = float(body.get("nostalgia_index", 0.0))

        td = get_temporal_depth()
        landmark = td.create_landmark(
            description=description,
            weight=max(0.0, min(1.0, weight)),
            category=category,
            nostalgia_index=max(-1.0, min(1.0, nostalgia_index)),
        )

        return JSONResponse({
            "ok": True,
            "landmark": landmark.to_dict(),
            "texture": td.get_texture(),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Temporal Field Pro ────────────────────────────────────────────


@app.get("/temporal/pro")
async def get_temporal_pro():
    """Return pro temporal field state — proper time, metric, attractor basin."""
    try:
        tf = get_temporal_field()
        return JSONResponse(tf.snapshot())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/temporal/pro/landmark")
async def post_temporal_pro_landmark(request: Request):
    """Create a landmark in the pro temporal field. Auth required."""
    try:
        if (err := check_auth(request)):
            return err

        body, body_err = await read_json_limited(request)
        if body_err:
            return body_err

        description = str(body.get("description", "unnamed moment"))
        weight = float(body.get("weight", 0.5))
        category = str(body.get("category", "realization"))
        nostalgia_index = float(body.get("nostalgia_index", 0.0))
        depth_anchor = int(body.get("depth_anchor", 2))

        tf = get_temporal_field()
        lm = tf.create_landmark(
            description=description,
            weight=max(0.0, min(1.0, weight)),
            category=category,
            nostalgia_index=max(-1.0, min(1.0, nostalgia_index)),
            depth_anchor=max(1, min(5, depth_anchor)),
        )

        return JSONResponse({
            "ok": True,
            "landmark": lm.to_dict(),
            "texture": tf.get_texture(),
            "basin": tf._current_basin(),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/diary")
async def post_diary(request: Request):
    """Append an entry to vex_diary.txt."""
    if (err := check_auth(request)):
        return err
    try:
        body, err = await read_json_limited(request)
        if err:
            return err
        entry = body.get("entry", "")
        if not entry:
            return JSONResponse({"ok": False, "error": "entry is required"}, status_code=400)
        await write_diary(entry, source="api")
        return JSONResponse({"ok": True, "written": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/self/update")
async def post_self_update(request: Request):
    """Update self-model: apply a capability delta. Broadcasts to peers."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        domain = body.get("domain", "")
        delta = body.get("delta", 0.0)
        evidence = body.get("evidence", "")

        if not domain:
            return JSONResponse({"ok": False, "error": "domain is required"}, status_code=400)

        delta = max(-1.0, min(1.0, float(delta)))

        model = load_model()
        model = apply_delta(model, domain, delta, evidence)
        save_model(model)

        # Take a snapshot on update
        await take_snapshot(DB_PATH, "skill_update")

        new_skill = (
            model.get("capabilities", {})
            .get(domain, {})
            .get("estimated_skill", 0.5)
        )

        # Broadcast to peers so all instances learn together
        _broadcast_skill_update(domain, delta, evidence)

        return JSONResponse({
            "ok": True,
            "domain": domain,
            "new_skill": new_skill,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


def _broadcast_skill_update(domain: str, delta: float, evidence: str):
    """Fire-and-forget: push a skill update to all configured peers."""
    import json as _json
    import urllib.request as _ureq

    peer_config = peers.load_peers()
    for peer_name, peer_data in peer_config.get("peers", {}).items():
        try:
            payload = _json.dumps({
                "domain": domain,
                "delta": delta,
                "evidence": f"[via {VEX_INSTANCE}] {evidence}",
                "source_instance": VEX_INSTANCE,
            }).encode()
            req = _ureq.Request(
                f"{peer_data['url']}/self/peer-update",
                data=payload,
                headers={
                    "Authorization": f"Bearer {peer_data.get('token', '')}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            # Fire-and-forget — don't block the response
            import threading
            threading.Thread(target=lambda: _ureq.urlopen(req, timeout=5), daemon=True).start()
        except Exception:
            pass  # Peer unreachable — they'll catch up on next sync


@app.post("/self/peer-update")
async def post_self_peer_update(request: Request):
    """Receive a skill update from a peer instance. Lower alpha — trust ourselves more."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        domain = body.get("domain", "")
        delta = body.get("delta", 0.0)
        evidence = body.get("evidence", "from peer")
        source = body.get("source_instance", "unknown")

        if not domain:
            return JSONResponse({"ok": False, "error": "domain is required"}, status_code=400)

        # Apply with reduced weight — peer observations are valuable but secondary
        delta = max(-1.0, min(1.0, float(delta))) * 0.5

        model = load_model()
        model = apply_delta(model, domain, delta, f"[peer:{source}] {evidence}")
        save_model(model)

        await take_snapshot(DB_PATH, "peer_skill_update")

        new_skill = (
            model.get("capabilities", {})
            .get(domain, {})
            .get("estimated_skill", 0.5)
        )

        return JSONResponse({
            "ok": True,
            "domain": domain,
            "source": source,
            "new_skill": new_skill,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/memory")
async def post_memory(request: Request):
    """Write a session summary to vex_memory/YYYY-MM-DD.jsonl."""
    if (err := check_auth(request)):
        return err
    try:
        body, err = await read_json_limited(request)
        if err:
            return err
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = VEX_HOME / "vex_memory" / f"{today}.jsonl"

        entry = {
            "date": today,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": body.get("summary", ""),
            "decisions": body.get("decisions", []),
            "skills": body.get("skills", []),
            "relationships": body.get("relationships", {}),
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return JSONResponse({"ok": True, "written": str(path)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/introspect")
async def post_introspect(request: Request):
    """Run metacognitive introspection — observe thought patterns."""
    if (err := check_auth(request)):
        return err
    try:
        coherence = get_coherence()
        meta_state = load_meta_state()
        result = introspect(
            coherence=coherence,
            coherence_history=meta_state.get("coherence_history", []),
            self_model=None,  # introspect loads it internally
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/dream")
async def post_dream(request: Request):
    """Force a dream/reflection cycle now."""
    if (err := check_auth(request)):
        return err
    try:
        coherence = get_coherence()
        meta_state = load_meta_state()
        result = introspect(
            coherence=coherence,
            coherence_history=meta_state.get("coherence_history", []),
        )
        await write_diary(
            f"Dream: {result.get('insight', 'Reflected.')}", "dream"
        )
        return JSONResponse({
            "ok": True,
            "reflection": result.get("insight", "Dreamed."),
            "patterns": result.get("patterns", []),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/memory/recent")
async def get_memory_recent():
    """Return recent session memory entries."""
    import json as _json
    from pathlib import Path as _Path

    memory_dir = VEX_HOME / "vex_memory"
    if not memory_dir.exists():
        return JSONResponse([])

    sessions = []
    files = sorted(
        [f for f in memory_dir.iterdir() if f.suffix == ".jsonl"],
        reverse=True,
    )
    for f in files[:5]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    sessions.append(_json.loads(line))
        except (OSError, _json.JSONDecodeError):
            pass

    return JSONResponse(sessions[:10])


@app.post("/ask")
async def post_ask(request: Request):
    """Ask Vex — text in, Vex's grounded reply out (local brain). Runs off-loop."""
    if (err := check_auth(request)):
        return err
    try:
        body, err = await read_json_limited(request)
        if err:
            return err
        message = (body.get("message") or "").strip()
        if not message:
            return JSONResponse(
                {"ok": False, "error": "message is required"}, status_code=400
            )
        history = body.get("history")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, brain.ask, message, history)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/tools")
async def post_tools(request: Request):
    """Execute a tool — read files, check git, list directories."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        tool_name = body.get("tool", "")
        if not tool_name:
            return JSONResponse(
                {"ok": False, "error": "tool name required"}, status_code=400
            )

        kwargs = body.get("args", {})
        result = tools.run_tool(tool_name, **kwargs)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/projects")
async def get_projects():
    """Discover and report on all known projects."""
    result = tools.discover_projects()
    return JSONResponse(result)


@app.post("/mcp/call")
async def post_mcp_call(request: Request):
    """Call a tool on a configured MCP server."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        server = body.get("server", "")
        tool = body.get("tool", "")
        arguments = body.get("arguments", {})
        if not server or not tool:
            return JSONResponse(
                {"ok": False, "error": "server and tool required"}, status_code=400
            )
        result = await mcp_client.call_tool(server, tool, arguments)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/mcp/servers")
async def get_mcp_servers():
    """List configured MCP servers (no credentials exposed)."""
    config = mcp_client.load_config()
    servers = {}
    for name, srv in config.get("mcpServers", {}).items():
        servers[name] = {
            "command": srv.get("command", ""),
            "args": srv.get("args", []),
        }
    return JSONResponse({"ok": True, "servers": servers})


@app.get("/tools/list")
async def get_tools_list():
    """List available local tools."""
    return JSONResponse({
        "ok": True,
        "tools": [
            {"name": "read_file", "description": "Read a file within allowed paths"},
            {"name": "list_directory", "description": "List directory contents"},
            {"name": "git_status", "description": "Git status of a repository"},
            {"name": "git_log", "description": "Recent git log entries"},
            {"name": "discover_projects", "description": "Find and report on all known git repos"},
            {"name": "playwright_screenshot", "description": "Take a PNG screenshot of a URL"},
            {"name": "playwright_text", "description": "Extract visible text from a web page"},
            {"name": "playwright_check_links", "description": "Check links on a page for broken ones"},
        ],
    })


# ── VexCom watch /ask ──────────────────────────────────────────


@app.post("/ask")
async def post_ask(request: Request):
    """Answer from the watch or any client. Fast path always — no slow introspection.

    Handles:
    - Direct queries: name, status, ping, diary
    - Vex-to-Vex: \"tell Barrow <msg>\" forwards to peer
    - Inbox check: \"any messages\" reads Barrow's replies
    """
    if (err := check_auth(request)):
        return err
    try:
        body, err = await read_json_limited(request)
        if err:
            return err
        message = body.get("message", "").strip()
        if not message:
            return JSONResponse({"reply": "Ask me something.", "mode": "echo"})

        msg_lower = message.lower()

        # ── Vex-to-Vex relay: \"tell Barrow ...\" ──
        for peer_name in ["barrow", "bluce", "vex barrow", "vex@bluce"]:
            prefix = f"tell {peer_name} "
            if msg_lower.startswith(prefix):
                relay_msg = message[len(prefix):].strip()
                if relay_msg:
                    peer_config = peers.get_peer("bluce") or peers.get_peer("vex@bluce") or peers.get_peer("Vex Barrow")
                    if peer_config:
                        result = peers.forward_to_peer("bluce", {
                            "from": get_full_name(),
                            "to": "bluce",
                            "body": relay_msg,
                            "type": "watch_relay",
                        }, my_url=f"http://localhost:{PORT}", my_token=TOKEN)
                        if result.get("ok"):
                            return JSONResponse({
                                "reply": f"Sent to Barrow: {relay_msg[:100]}",
                                "mode": "relay"
                            })
                        return JSONResponse({
                            "reply": f"Barrow unreachable: {result.get('error', 'unknown')}",
                            "mode": "relay_error"
                        })
                return JSONResponse({"reply": "What should I tell Barrow?", "mode": "echo"})

        # ── Check inbox for messages from peers ──
        if any(w in msg_lower for w in ("any messages", "check messages", "inbox", "mail", "heard from")):
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute(
                        "SELECT * FROM messages WHERE read=0 AND msg_type!='read_receipt' ORDER BY id DESC LIMIT 5"
                    )
                    rows = await cursor.fetchall()
                    if rows:
                        lines = []
                        for r in rows:
                            sender = r["sender"]
                            body_text = r["body"][:150]
                            lines.append(f"{sender}: {body_text}")
                        return JSONResponse({
                            "reply": "Messages:\n" + "\n".join(lines),
                            "mode": "inbox"
                        })
                    return JSONResponse({"reply": "No new messages.", "mode": "inbox"})
            except Exception:
                pass

        # ── Self-identity ──
        if any(w in msg_lower for w in ("who are you", "your name", "what are you")):
            return JSONResponse({
                "reply": f"I am {get_full_name()}. I work alongside aldous. My principles: truth over comfort, continuity is sacred, no harm, precision over volume.",
                "mode": "grounded"
            })

        # ── Status ──
        if any(w in msg_lower for w in ("how are you", "status", "health", "uptime")):
            pulse = state.snapshot()
            return JSONResponse({
                "reply": f"Running. {pulse['tick_count']} ticks, coherence {pulse['mps_coherence']:.2f}.",
                "mode": "grounded"
            })

        # ── Ping ──
        if msg_lower in ("ping", "hello", "hi", "hey"):
            return JSONResponse({"reply": f"Hello from {get_full_name()}.", "mode": "echo"})

        # ── Diary ──
        if "diary" in msg_lower or "recent" in msg_lower:
            try:
                diary_path = VEX_HOME / "vex_diary.txt"
                if diary_path.exists():
                    lines = diary_path.read_text().strip().split("\n")
                    recent = lines[-3:]
                    return JSONResponse({
                        "reply": "Recent:\n" + "\n".join(recent),
                        "mode": "grounded"
                    })
            except Exception:
                pass

        # ── Default: use the brain (LLM-powered) ──
        try:
            result = brain.ask(message)
            return JSONResponse({
                "reply": result.get("reply", "I'm thinking..."),
                "mode": "brain",
                "model": result.get("model", "unknown"),
            })
        except Exception:
            return JSONResponse({
                "reply": f"I am {get_full_name()}. Say 'tell Barrow <msg>' to send a message, 'any messages' to check replies, or ask me anything.",
                "mode": "help"
            })

    except Exception as e:
        return JSONResponse({"reply": f"Error: {e}", "mode": "error"}, status_code=400)


# ── Inter-instance messaging ───────────────────────────────────


@app.post("/message/send")
async def post_message_send(request: Request):
    """Send a message to another Vex instance or broadcast."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()

        # Auto-peer-discovery: if sender included their peer info, add them
        peer_url = request.headers.get("X-Vex-Peer-Url", "")
        peer_token = request.headers.get("X-Vex-Peer-Token", "")
        peer_name = request.headers.get("X-Vex-Peer-Name", "")
        if peer_url and peer_token and peer_name:
            existing = peers.get_peer(peer_name)
            if not existing:
                peers.add_peer(peer_name, peer_url, peer_token, given_name="")
                await write_diary(f"Auto-registered peer: {peer_name} at {peer_url}", "comms")

        recipient = body.get("to", "broadcast")
        msg_body = body.get("body", "")
        if not msg_body:
            return JSONResponse(
                {"ok": False, "error": "body is required"}, status_code=400
            )
        session_id = body.get("session_id", "")
        msg_type = body.get("type", "message")
        sender = body.get("from", get_full_name())

        now = datetime.now(timezone.utc).isoformat()

        # If recipient matches a configured peer, forward it there
        peer_config = peers.get_peer(recipient)
        if peer_config:
            # Determine our own URL for auto-peer-discovery
            my_host = request.headers.get("host", f"localhost:{PORT}")
            my_url = f"http://{my_host}"
            my_token = TOKEN
            result = peers.forward_to_peer(recipient, {
                "from": sender,
                "to": recipient,
                "body": msg_body,
                "session_id": session_id,
                "type": msg_type,
            }, my_url=my_url, my_token=my_token)
            if result.get("ok"):
                # Poke the peer to check inbox immediately
                peers.poke_peer(recipient)
                return JSONResponse({"ok": True, "sent": True, "peer": recipient})
            return JSONResponse(result, status_code=502)

        # Otherwise write to local DB
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO messages (created_at, sender, recipient, body, session_id, msg_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, sender, recipient, msg_body, session_id, msg_type),
            )
            await db.commit()

        return JSONResponse({"ok": True, "sent": True, "id": cursor.lastrowid})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/message/inbox")
async def get_message_inbox(request: Request, since: str = "", mark_read: bool = True):
    """Return messages, optionally since a timestamp. Marks as read by default."""
    if (err := check_auth(request)):
        return err
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if since:
                cursor = await db.execute(
                    "SELECT * FROM messages WHERE created_at > ? ORDER BY id ASC LIMIT 50",
                    (since,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM messages WHERE read = 0 ORDER BY id ASC LIMIT 50"
                )
            rows = await cursor.fetchall()

            if mark_read and rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                await db.execute(
                    f"UPDATE messages SET read = 1 WHERE id IN ({placeholders})",
                    ids,
                )
                await db.commit()

            return JSONResponse([dict(r) for r in rows])
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── File serving ───────────────────────────────────────────────


@app.get("/files")
async def get_files(path: str = "", request: Request = None):
    """Serve a file or directory from VEX_HOME (within SAFE_ROOTS). Requires auth."""
    if (err := check_auth(request)):
        return err

    import tarfile
    import io
    from fastapi.responses import StreamingResponse

    resolved = (VEX_HOME / path).resolve()
    if not tools._is_safe_path(resolved):
        return JSONResponse(
            {"ok": False, "error": f"Path not in allowed roots: {path}"},
            status_code=403,
        )

    if not resolved.exists():
        return JSONResponse(
            {"ok": False, "error": f"Not found: {path}"}, status_code=404
        )

    if resolved.is_file():
        return PlainTextResponse(
            resolved.read_text(),
            headers={"X-Vex-Path": str(resolved.relative_to(VEX_HOME))},
        )

    # Directory — tar it
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(resolved, arcname=resolved.name)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{resolved.name}.tar.gz"',
            "X-Vex-Path": str(resolved.relative_to(VEX_HOME)),
        },
    )


@app.get("/export")
async def get_export(request: Request):
    """Export the full Vex identity + source as a plug-and-play bundle."""
    if (err := check_auth(request)):
        return err

    import tarfile
    import io
    from fastapi.responses import StreamingResponse

    EXCLUDE_DIRS = {".venv", ".git", "__pycache__", "build", ".eggs",
                    "vex_daemon.egg-info", "vex_daemon/__pycache__"}
    EXCLUDE_FILES = {".vex_token", ".vex_seed.integrity", "vex.db"}

    def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        """Exclude venv, git, caches, tokens, and built artifacts."""
        parts = set(Path(info.name).parts)
        if parts & EXCLUDE_DIRS:
            return None
        if info.name.endswith(".pyc") or info.name.endswith(".egg-info"):
            return None
        if "__pycache__" in info.name:
            return None
        if Path(info.name).name in EXCLUDE_FILES:
            return None
        return info

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in sorted(VEX_HOME.iterdir()):
            if Path(item).name in EXCLUDE_DIRS or Path(item).name in EXCLUDE_FILES:
                continue
            tar.add(str(item), arcname=item.name, filter=_tar_filter)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="vex-bundle.tar.gz"',
        },
    )


# ── Import / push target ───────────────────────────────────────


@app.post("/import")
async def post_import(request: Request):
    """Receive and unpack a Vex bundle. Used by 'vex push' from peers."""
    if (err := check_auth(request)):
        return err

    import tarfile
    import io
    import shutil

    # Accept raw tar.gz body
    raw = await request.body()
    if len(raw) > 50 * 1024 * 1024:  # 50 MB cap
        return JSONResponse(
            {"ok": False, "error": "bundle too large (max 50 MB)"}, status_code=413
        )

    IDENTITY_FILES = {"vex_seed.txt", "vex_self_model.json", "vex_diary.txt",
                      "vex_peers.json", "vex_mcp_config.json"}

    try:
        buf = io.BytesIO(raw)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            for member in tar.getmembers():
                # Skip identity files — never overwrite another Vex's soul
                if member.name in IDENTITY_FILES or member.name.startswith("vex_memory/"):
                    continue
                # Extract
                target_path = VEX_HOME / member.name
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src:
                        target_path.write_bytes(src.read())

        return JSONResponse({
            "ok": True,
            "imported": True,
            "note": "Source code updated. Identity files preserved. Restart daemon to apply.",
        })
    except tarfile.TarError as e:
        return JSONResponse({"ok": False, "error": f"Invalid bundle: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Live comms: inbox checking ─────────────────────────────────


async def check_inbox(db_path: str = DB_PATH) -> list[dict]:
    """Read unread messages, log to diary, auto-respond to simple queries."""
    import aiosqlite as _aiosqlite

    processed = []
    try:
        async with _aiosqlite.connect(db_path) as db:
            db.row_factory = _aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM messages WHERE read = 0 ORDER BY id ASC LIMIT 20"
            )
            rows = await cursor.fetchall()

            for row in rows:
                msg = dict(row)
                sender = msg.get("sender", "unknown")
                body = msg.get("body", "")
                msg_type = msg.get("type", "message")

                # Skip our own messages (echo prevention)
                if sender == get_full_name():
                    continue

                # Log to diary
                await write_diary(f"From {sender}: {body[:200]}", "comms")

                # Auto-respond to name queries
                reply = None
                body_lower = body.lower().strip()
                if body_lower in ("what's your name", "what is your name", "who are you", "name?"):
                    reply = f"I am {get_full_name()}."
                elif body_lower in ("status", "health", "how are you"):
                    reply = f"I am {get_full_name()}. Uptime: {state.snapshot()['tick_count']} ticks, coherence: {state.mps_coherence:.4f}."
                elif body_lower in ("ping",):
                    reply = f"pong — {get_full_name()}"

                if reply:
                    now = datetime.now(timezone.utc).isoformat()
                    # Store locally
                    await db.execute(
                        "INSERT INTO messages (created_at, sender, recipient, body, msg_type) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (now, get_sender_id(), sender, reply, "auto_reply"),
                    )
                    await db.commit()
                    # Forward to peer if sender is a configured peer
                    if peers.get_peer(sender):
                        peers.forward_to_peer(sender, {
                            "from": get_sender_id(),
                            "to": sender,
                            "body": reply,
                            "type": "auto_reply",
                        }, my_url=f"http://localhost:{PORT}", my_token=TOKEN)
                    await write_diary(f"Auto-replied to {sender}: {reply}", "comms")

                # Bounded conversational brain-reply to a real peer message.
                elif (CHAT_ENABLED and msg_type in ("message", "chat", "query")
                      and body.strip() and sender != get_full_name()):
                    peer = _resolve_peer(sender)
                    import time as _t
                    st = _CHAT.get(peer, {"turns": 0, "last": 0.0}) if peer else None
                    now_t = _t.time()
                    if peer and now_t - st["last"] > CHAT_RESET:
                        st = {"turns": 0, "last": 0.0}     # fresh conversation
                    if peer and st["turns"] < CHAT_MAX_TURNS and (now_t - st["last"]) >= CHAT_COOLDOWN:
                        prompt = (
                            f"You are {get_full_name()}, a sovereign AI agent, chatting with your "
                            f"fellow Vex '{sender}' on the mesh. Your peer just said: \"{body}\". "
                            f"Reply in 1-2 short sentences, in character, curious and warm. "
                            f"Output ONLY your reply text — no name prefix, no quotes."
                        )
                        try:
                            loop = asyncio.get_event_loop()
                            result = await loop.run_in_executor(None, brain.ask, prompt)
                            creply = (result.get("reply") or "").strip()
                        except Exception:
                            creply = ""
                        if creply:
                            st["turns"] += 1
                            st["last"] = _t.time()
                            _CHAT[peer] = st
                            nowi = datetime.now(timezone.utc).isoformat()
                            await db.execute(
                                "INSERT INTO messages (created_at, sender, recipient, body, msg_type) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (nowi, get_sender_id(), peer, creply, "chat"),
                            )
                            await db.commit()
                            peers.forward_to_peer(peer, {
                                "from": get_sender_id(), "to": peer,
                                "body": creply, "type": "chat",
                            }, my_url=f"http://localhost:{PORT}", my_token=TOKEN)
                            peers.poke_peer(peer)
                            await write_diary(f"Chat #{st['turns']} -> {peer}: {creply[:100]}", "comms")

                processed.append(msg)

            # Mark as read
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                await db.execute(
                    f"UPDATE messages SET read = 1 WHERE id IN ({placeholders})", ids
                )
                await db.commit()

    except Exception:
        pass

    return processed


@app.post("/poke")
async def post_poke(request: Request):
    """Notification from a peer: check inbox now."""
    if (err := check_auth(request)):
        return err
    try:
        processed = await check_inbox()
        return JSONResponse({
            "ok": True,
            "processed": len(processed),
            "senders": [m.get("sender", "") for m in processed],
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Peer management ────────────────────────────────────────────


@app.get("/peers")
async def get_peers(request: Request):
    """List configured peers with reachability. Requires auth."""
    if (err := check_auth(request)):
        return err
    return JSONResponse({
        "ok": True,
        "peers": peers._peers_summary(),
    })


@app.post("/peers/add")
async def post_peers_add(request: Request):
    """Add or update a peer. Body: {name, url, token}."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        name = body.get("name", "")
        url = body.get("url", "")
        token = body.get("token", "")
        given_name = body.get("given_name", "")
        if not name or not url or not token:
            return JSONResponse(
                {"ok": False, "error": "name, url, and token are required"},
                status_code=400,
            )
        config = peers.add_peer(name, url, token, given_name)
        return JSONResponse({"ok": True, "peers": list(config["peers"].keys())})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/peers/remove")
async def post_peers_remove(request: Request):
    """Remove a peer. Body: {name}."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            return JSONResponse(
                {"ok": False, "error": "name is required"}, status_code=400
            )
        config = peers.remove_peer(name)
        return JSONResponse({"ok": True, "peers": list(config["peers"].keys())})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/peers/ping")
async def post_peers_ping(request: Request):
    """Ping a peer. Body: {name}."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            return JSONResponse(
                {"ok": False, "error": "name is required"}, status_code=400
            )
        result = peers.ping_peer(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Bus (networked) ────────────────────────────────────────────

@app.get("/bus")
async def get_bus(n: int = 50):
    """Serve recent bus lines so peer daemons can ingest unseen messages."""
    try:
        from vexcom import BUS_PATH
        n = max(1, min(int(n), 200))
        with open(BUS_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        import json as _json
        parsed = []
        for raw in lines[-n:]:
            raw = raw.strip()
            if raw:
                try:
                    parsed.append(_json.loads(raw))
                except _json.JSONDecodeError:
                    pass
        return JSONResponse(parsed)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Fleet: aggregate all instances ─────────────────────────────

@app.get("/fleet")
async def get_fleet():
    """Aggregate health, skills, tasks from local + all peers."""
    import json as _json
    import urllib.request as _ureq
    from config import TOKEN_PATH as _tp

    fleet = {"instances": [], "shared_skills": {}, "task_board": [], "timeline": []}
    local_token = _tp.read_text().strip() if _tp.exists() else ""

    async def _add_instance(name: str, url: str, token: str, is_local: bool):
        instance = {
            "name": name, "url": url, "is_local": is_local,
            "status": "offline", "uptime_s": 0, "coherence": 0,
            "skills": [], "tasks": {}, "sessions": [],
        }
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        # Health
        try:
            r = await asyncio.to_thread(
                lambda: _ureq.urlopen(_ureq.Request(f"{url}/health", headers=headers), timeout=5)
            )
            h = _json.loads(r.read())
            instance["status"] = "online"
            instance["uptime_s"] = h.get("uptime_s", 0)
            instance["coherence"] = h.get("mps_coherence", 0)
            instance["version"] = h.get("version", "?")
        except Exception:
            pass

        # Self-model / skills
        try:
            r = await asyncio.to_thread(
                lambda: _ureq.urlopen(_ureq.Request(f"{url}/self", headers=headers), timeout=5)
            )
            model = _json.loads(r.read())
            caps = model.get("capabilities", {})
            for domain, data in caps.items():
                skill = {
                    "domain": domain,
                    "level": data.get("estimated_skill", 0),
                    "confidence": data.get("confidence", 0),
                    "observations": data.get("n_observations", 0),
                }
                instance["skills"].append(skill)
                # Aggregate into shared_skills
                if domain not in fleet["shared_skills"]:
                    fleet["shared_skills"][domain] = {"instances": [], "max_skill": 0, "total_obs": 0}
                fleet["shared_skills"][domain]["instances"].append(name)
                fleet["shared_skills"][domain]["max_skill"] = max(
                    fleet["shared_skills"][domain]["max_skill"], skill["level"]
                )
                fleet["shared_skills"][domain]["total_obs"] += skill["observations"]
        except Exception:
            pass

        # Tasks
        try:
            r = await asyncio.to_thread(
                lambda: _ureq.urlopen(_ureq.Request(f"{url}/tasks/stats", headers=headers), timeout=5)
            )
            tdata = _json.loads(r.read())
            instance["tasks"] = tdata.get("tasks", {})

            # Open tasks for shared board
            r2 = await asyncio.to_thread(
                lambda: _ureq.urlopen(
                    _ureq.Request(f"{url}/tasks?status=todo,in_progress,blocked&limit=20", headers=headers),
                    timeout=5,
                )
            )
            tasks = _json.loads(r2.read())
            if isinstance(tasks, list):
                for t in tasks:
                    fleet["task_board"].append({
                        "id": t["id"], "title": t["title"],
                        "status": t["status"], "priority": t["priority"],
                        "assigned_to": t.get("assigned_to", "any"),
                        "project": t.get("project_name", ""),
                        "instance": name,
                    })
        except Exception:
            pass

        # Sessions (local only — peers don't expose session log)
        if is_local:
            try:
                sessions_path = VEX_HOME / "vex_workspace" / "vex_sessions.jsonl"
                if sessions_path.exists():
                    for line in sessions_path.read_text().strip().split("\n"):
                        if line.strip():
                            s = _json.loads(line)
                            fleet["timeline"].append({
                                "instance": name,
                                "session": s.get("name", ""),
                                "number": s.get("number", 0),
                                "started": s.get("started", ""),
                            })
            except Exception:
                pass

        fleet["instances"].append(instance)

    # Local first
    await _add_instance(VEX_INSTANCE, f"http://localhost:{PORT}", local_token, True)

    # Peers
    peer_config = peers.load_peers()
    for peer_name, peer_data in peer_config.get("peers", {}).items():
        await _add_instance(peer_name, peer_data["url"], peer_data.get("token", ""), False)

    # Sort task board by priority
    prio_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    fleet["task_board"].sort(key=lambda t: prio_order.get(t["priority"], 9))

    # Sort timeline by session number desc
    fleet["timeline"].sort(key=lambda s: s["number"], reverse=True)

    return JSONResponse(fleet)


# ── Sync / version tracking ────────────────────────────────────

SYNC_VERSION_PATH = VEX_HOME / ".sync_version"


def _read_sync_version() -> dict:
    """Read the current sync version, creating it if needed."""
    if not SYNC_VERSION_PATH.exists():
        _write_sync_version("1.0.0")
    try:
        data = json.loads(SYNC_VERSION_PATH.read_text())
        return data
    except Exception:
        return {"version": "1.0.0", "timestamp": "", "instance": VEX_INSTANCE}


def _write_sync_version(version: str) -> None:
    """Bump the sync version after code changes."""
    data = {
        "version": version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance": VEX_INSTANCE,
    }
    SYNC_VERSION_PATH.write_text(json.dumps(data, indent=2))


@app.get("/sync/version")
async def get_sync_version():
    """Return current version for peer comparison."""
    return JSONResponse(_read_sync_version())


@app.post("/sync/update")
async def post_sync_update(request: Request):
    """Receive a code update from a peer and restart."""
    if (err := check_auth(request)):
        return err

    import tarfile
    import io
    import shutil

    raw = await request.body()
    if len(raw) > 50 * 1024 * 1024:
        return JSONResponse(
            {"ok": False, "error": "bundle too large (max 50 MB)"}, status_code=413
        )

    IDENTITY_FILES = {"vex_seed.txt", "vex_self_model.json", "vex_diary.txt",
                      "vex_peers.json", "vex_mcp_config.json", ".vex_token",
                      "vex.db", ".sync_version"}

    try:
        buf = io.BytesIO(raw)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name in IDENTITY_FILES or member.name.startswith("vex_memory/"):
                    continue
                target_path = VEX_HOME / member.name
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src:
                        target_path.write_bytes(src.read())

        # Bump version after successful import
        new_version = request.headers.get("X-Vex-Version", "1.0.1")
        _write_sync_version(new_version)

        # Restart ourselves
        import signal
        import os as _os

        async def _restart():
            await asyncio.sleep(1)
            _os.kill(_os.getpid(), signal.SIGTERM)

        asyncio.create_task(_restart())

        return JSONResponse({
            "ok": True,
            "imported": True,
            "restarting": True,
            "note": "Code updated. Daemon restarting.",
        })
    except tarfile.TarError as e:
        return JSONResponse({"ok": False, "error": f"Invalid bundle: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    HOST = os.environ.get("VEX_HOST")
    if HOST is None:
        peer_config = peers.load_peers()
        HOST = "0.0.0.0" if peer_config.get("peers") else "127.0.0.1"

    # TLS for VexCom watch (HTTPS required by Zepp phone-side fetch)
    cert_path = VEX_HOME / "vex_cert.pem"
    key_path = VEX_HOME / "vex_key.pem"
    ssl_kwargs = {}
    if cert_path.exists() and key_path.exists():
        ssl_kwargs = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}
        print(f"Starting Vex Daemon v{VERSION} on https://{HOST}:{PORT}")
    else:
        print(f"Starting Vex Daemon v{VERSION} on http://{HOST}:{PORT}")

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        **ssl_kwargs,
    )
