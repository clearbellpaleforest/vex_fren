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
from metacognition import introspect, load_meta_state
from status_page import render
from auth import check_auth, read_json_limited, TOKEN
from config import VEX_HOME, DB_PATH as _DB_PATH, VEX_INSTANCE
import tools
import mcp_client
import peers
import brain

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
    async def dream_callback(coherence, history):
        """Called by heartbeat during dream cycles. Introspect + check projects."""
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
    """Update self-model: apply a capability delta."""
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

        return JSONResponse({
            "ok": True,
            "domain": domain,
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
