"""
VexCom — one comms fabric.

Was three channels doing one job: the bus file (vex_bus.jsonl), the daemon diary,
and the daemon message bus (SQLite `messages`). VexCom unifies send/receive behind
one envelope and one authoritative store (the `messages` table), keeps
`vex_bus.jsonl` as a mirror adapter, and — crucially — indexes every message into
the same `mem_fts` memory index (src='message'), so recall()/reconstruct() see
comms as memory. Comms and memory are one log.

Chamberlain: one envelope, one send, one inbox, one log. Idempotent reindex.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH, VEX_HOME, VEX_INSTANCE
from memory_index import ensure_schema

BUS_PATH = VEX_HOME / "vex_workspace" / "vex_bus.jsonl"
MESSAGE_TYPES = {"message", "handoff", "query", "response", "system", "voice"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(env: dict) -> dict:
    """Coerce a partial/foreign envelope into the canonical shape."""
    mtype = env.get("type") or env.get("msg_type") or "message"
    if mtype not in MESSAGE_TYPES:
        mtype = "message"
    return {
        "from": env.get("from") or env.get("sender") or f"vex@{VEX_INSTANCE}",
        "to": env.get("to") or env.get("recipient") or "broadcast",
        "type": mtype,
        "body": env.get("body", ""),
        "session_id": env.get("session_id", ""),
        "thread_id": env.get("thread_id"),
        "ts": env.get("ts") or env.get("timestamp") or _now(),
    }


def _msg_text(e: dict) -> str:
    return f"[{e['from']} -> {e['to']}] ({e['type']}) {e['body']}"


def _bus_line(e: dict) -> dict:
    return {
        "from": e["from"], "to": e["to"], "type": e["type"],
        "body": e["body"], "session_id": e["session_id"], "timestamp": e["ts"],
    }


def _bus_hash(line: dict) -> str:
    key = f"{line.get('timestamp','')}|{line.get('from','')}|{line.get('body','')}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def ensure_bus_seen(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bus_seen (hash TEXT PRIMARY KEY, msg_id INTEGER)"
    )


def _index_message(conn, ref: str, e: dict) -> None:
    conn.execute("DELETE FROM mem_fts WHERE ref = ?", (ref,))
    conn.execute(
        "INSERT INTO mem_fts (text, src, ref, date, ts, raw) VALUES (?,?,?,?,?,?)",
        (_msg_text(e), "message", ref, e["ts"][:10], e["ts"],
         json.dumps({"summary": _msg_text(e), **e})),
    )


def _append_bus(line: dict) -> None:
    try:
        BUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BUS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass  # SQLite is authoritative; a failed mirror is non-fatal


def send(env: dict, db_path=DB_PATH) -> dict:
    """Send a message: authoritative SQLite write + bus mirror + memory index."""
    e = normalize(env)
    if not e["body"]:
        return {"ok": False, "error": "body is required"}
    line = _bus_line(e)
    h = _bus_hash(line)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        ensure_bus_seen(conn)
        cur = conn.execute(
            "INSERT INTO messages (created_at, sender, recipient, body, session_id, msg_type) "
            "VALUES (?,?,?,?,?,?)",
            (e["ts"], e["from"], e["to"], e["body"], e["session_id"], e["type"]),
        )
        mid = cur.lastrowid
        _index_message(conn, f"msg:{mid}", e)
        # Record the mirror hash now so ingest_bus() won't re-import our own line.
        conn.execute("INSERT OR IGNORE INTO bus_seen (hash, msg_id) VALUES (?,?)", (h, mid))
        conn.commit()
    finally:
        conn.close()
    _append_bus(line)
    return {"ok": True, "id": mid}


def inbox(since: str = "", to: str = "", mark_read: bool = True, db_path=DB_PATH) -> list[dict]:
    """Unified inbox over the authoritative messages table."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        clauses, params = [], []
        if since:
            clauses.append("created_at > ?"); params.append(since)
        if to:
            clauses.append("(recipient = ? OR recipient = 'broadcast')"); params.append(to)
        if not since and not to:
            clauses.append("read = 0")
        q = "SELECT * FROM messages"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY id ASC LIMIT 50"
        rows = conn.execute(q, params).fetchall()
        if mark_read and rows:
            ids = [r["id"] for r in rows]
            ph = ",".join("?" * len(ids))
            conn.execute(f"UPDATE messages SET read=1 WHERE id IN ({ph})", ids)
            conn.commit()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def reindex_messages(db_path=DB_PATH) -> int:
    """Rebuild src='message' index rows from the messages table. Idempotent."""
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        conn.execute("DELETE FROM mem_fts WHERE src='message'")
        n = 0
        cur = conn.execute(
            "SELECT id, created_at, sender, recipient, body, session_id, msg_type FROM messages"
        )
        for mid, created, sender, recipient, body, sid, mtype in cur.fetchall():
            e = {"from": sender, "to": recipient, "type": mtype, "body": body,
                 "session_id": sid, "ts": created}
            _index_message(conn, f"msg:{mid}", e)
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def ingest_bus(db_path=DB_PATH) -> int:
    """Import bus.jsonl lines not already known (dedup by hash), read=1.

    Makes externally-appended handoffs (written by other Vex sessions via shell)
    first-class: stored in the messages table, indexed, and recall-able.
    """
    if not BUS_PATH.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        ensure_bus_seen(conn)
        seen = {r[0] for r in conn.execute("SELECT hash FROM bus_seen")}
        n = 0
        with open(BUS_PATH, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    b = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                h = _bus_hash(b)
                if h in seen:
                    continue
                e = normalize(b)
                if not e["body"]:
                    continue
                cur = conn.execute(
                    "INSERT INTO messages (created_at, sender, recipient, body, session_id, msg_type, read) "
                    "VALUES (?,?,?,?,?,?,1)",
                    (e["ts"], e["from"], e["to"], e["body"], e["session_id"], e["type"]),
                )
                mid = cur.lastrowid
                _index_message(conn, f"msg:{mid}", e)
                conn.execute("INSERT OR IGNORE INTO bus_seen (hash, msg_id) VALUES (?,?)", (h, mid))
                seen.add(h)
                n += 1
        conn.commit()
        return n
    finally:
        conn.close()


if __name__ == "__main__":
    ingested = ingest_bus()
    reindexed = reindex_messages()
    print(f"ingest_bus: imported {ingested} new bus lines")
    print(f"reindex_messages: indexed {reindexed} messages")
