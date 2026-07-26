"""
Vex memory consolidation — fold old episodes into durable monthly digests.

Each digest is an extractive summary carrying the source refs it folded, so
reconstruct() can load a compact digest for old months instead of every raw
episode — nothing falls off the edge as history grows.

Model-free by design: the digest is deterministic extraction (dates, first-line
summaries, decisions). Abstractive (LLM) consolidation is a clean later upgrade
once the brain exists — it can rewrite `text` without changing the record shape
or any caller.

Chamberlain: one consolidation job, one summaries table, idempotent by bucket.
"""

import json
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH
from memory_index import ensure_schema, _iter_memory_docs

SUMMARIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket TEXT UNIQUE,
    span_start TEXT,
    span_end TEXT,
    source_ids TEXT,
    text TEXT,
    ts TEXT
)
"""


def ensure_summaries(conn: sqlite3.Connection) -> None:
    conn.execute(SUMMARIES_SCHEMA)
    conn.commit()


def _episodes_by_month():
    """Group memory docs by YYYY-MM -> [(ref, date, raw), ...]."""
    buckets: dict[str, list] = {}
    for text, ref, date, ts, raw in _iter_memory_docs():
        month = (date or ts)[:7]
        buckets.setdefault(month, []).append((ref, date, raw))
    return buckets


def _digest(month: str, episodes: list) -> tuple[str, list[str]]:
    """Deterministic extractive digest of a month's episodes."""
    lines = [f"Digest for {month} ({len(episodes)} sessions):"]
    refs = []
    for ref, date, raw in episodes:
        refs.append(ref)
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            rec = {}
        summary = str(rec.get("summary", "")).strip()
        first = summary.split(". ")[0][:200] if summary else ""
        lines.append(f"- [{date}] {first}")
        for d in (rec.get("decisions") or [])[:3]:
            lines.append(f"    - {d}")
    return "\n".join(lines), refs


def consolidate(force_all: bool = False, db_path=DB_PATH) -> list[dict]:
    """Build/refresh monthly digests for months older than the current one.

    Production default leaves the current month as raw episodes. force_all=True
    also consolidates the current month (for demonstration/tests). Idempotent:
    re-running refreshes each bucket in place (bucket is UNIQUE).
    """
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        ensure_summaries(conn)
        done = []
        for month, episodes in sorted(_episodes_by_month().items()):
            if month >= current_month and not force_all:
                continue
            text, refs = _digest(month, episodes)
            span_start = min(e[1] for e in episodes)
            span_end = max(e[1] for e in episodes)
            now = datetime.now(timezone.utc).isoformat()

            conn.execute("DELETE FROM summaries WHERE bucket = ?", (month,))
            conn.execute(
                "INSERT INTO summaries (bucket, span_start, span_end, source_ids, text, ts) "
                "VALUES (?,?,?,?,?,?)",
                (month, span_start, span_end, json.dumps(refs), text, now),
            )
            # Index the digest so recall() can surface it (src='summary').
            sref = f"summary:{month}"
            conn.execute("DELETE FROM mem_fts WHERE ref = ?", (sref,))
            conn.execute(
                "INSERT INTO mem_fts (text, src, ref, date, ts, raw) VALUES (?,?,?,?,?,?)",
                (text, "summary", sref, span_end, now,
                 json.dumps({"summary": f"[digest {month}]", "source_ids": refs})),
            )
            conn.commit()
            done.append({"bucket": month, "sessions": len(episodes), "source_ids": refs})
        return done
    finally:
        conn.close()


def load_summaries(db_path=DB_PATH) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_summaries(conn)
        cur = conn.execute(
            "SELECT bucket, span_start, span_end, source_ids, text, ts "
            "FROM summaries ORDER BY bucket"
        )
        out = []
        for bucket, s0, s1, sids, text, ts in cur.fetchall():
            out.append({
                "bucket": bucket, "span": [s0, s1],
                "source_ids": json.loads(sids), "text": text, "ts": ts,
            })
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    force = "--all" in sys.argv
    done = consolidate(force_all=force)
    if not done:
        print("Nothing to consolidate (no months older than current). Use --all to force.")
    for d in done:
        print(f"Consolidated {d['bucket']}: {d['sessions']} sessions, "
              f"source_ids={d['source_ids']}")
