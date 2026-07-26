"""
Vex memory index — one queryable index over ALL episodic memory.

Fixes the reconstruction seam: instead of reading only the newest
vex_memory/*.jsonl file, every session summary across every file is indexed
into a single SQLite FTS5 table inside vex.db, so recall() can query the
whole history.

Chamberlain: one store (vex.db), one index (mem_fts), one build function.
No second database, no overlapping stores. Idempotent per source.
"""

import json
import sqlite3
from pathlib import Path

from config import MEMORY_DIR, DB_PATH

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
    text,
    src UNINDEXED,
    ref UNINDEXED,
    date UNINDEXED,
    ts UNINDEXED,
    raw UNINDEXED
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(FTS_SCHEMA)
    conn.commit()


def _text_of(rec: dict) -> str:
    """Flatten a memory record into one searchable blob.

    Handles schema drift across memory files: `summary` is always present;
    decisions/skills/relationships vary in key and shape.
    """
    parts: list[str] = []
    if rec.get("summary"):
        parts.append(str(rec["summary"]))
    for key in ("decisions", "skills", "skills_demonstrated", "channels"):
        val = rec.get(key)
        if isinstance(val, list):
            parts.extend(str(v) for v in val)
    rel = rec.get("relationships")
    if isinstance(rel, dict):
        for v in rel.values():
            if isinstance(v, dict):
                note = v.get("note") or v.get("notes")
                if note:
                    parts.append(str(note))
            elif v:
                parts.append(str(v))
    return "\n".join(parts)


def _iter_memory_docs():
    """Yield (text, ref, date, ts, raw) for every memory entry on disk."""
    if not MEMORY_DIR.exists():
        return
    for path in sorted(MEMORY_DIR.glob("*.jsonl")):
        file_date = path.stem  # YYYY-MM-DD
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = _text_of(rec)
                if not text:
                    continue
                date = rec.get("date") or file_date
                ts = rec.get("timestamp") or f"{date}T00:00:00Z"
                ref = f"vex_memory/{path.name}#{lineno}"
                yield text, ref, date, ts, line


def reindex_source(conn: sqlite3.Connection, src: str, docs) -> int:
    """Replace all rows for `src` with `docs`. Idempotent per source."""
    conn.execute("DELETE FROM mem_fts WHERE src = ?", (src,))
    n = 0
    for text, ref, date, ts, raw in docs:
        conn.execute(
            "INSERT INTO mem_fts (text, src, ref, date, ts, raw) "
            "VALUES (?,?,?,?,?,?)",
            (text, src, ref, date, ts, raw),
        )
        n += 1
    conn.commit()
    return n


def build_index(db_path=DB_PATH) -> dict:
    """(Re)build the memory index from all vex_memory/*.jsonl. Idempotent.

    VexCom messages get their own source at Stage C1; this call only touches
    src='memory' rows, so the two never clobber each other.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        n_mem = reindex_source(conn, "memory", _iter_memory_docs())
        return {"ok": True, "memory": n_mem}
    finally:
        conn.close()


if __name__ == "__main__":
    result = build_index()
    print(f"Indexed {result['memory']} memory entries into {DB_PATH}")
