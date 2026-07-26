"""
Vex recall — query the whole memory index, never just the newest file.

recall(query, k) ranks matches by relevance, not recency:
  1. term coverage  — how many distinct query terms the entry contains
                      (resists bm25 term-frequency spam)
  2. bm25           — SQLite FTS5 relevance, as the tiebreak within a coverage tier
  3. recency        — PAD ONLY: if fewer than k relevant matches, top up with the
                      most recent entries. Recency never outranks relevance, so the
                      old newest-file bias cannot creep back in.

Empty / no-match query -> most-recent entries (graceful default).

Chamberlain: one recall function. The embedding channel is the planned upgrade
(A2/brain): add it as a third relevance signal and fuse with RRF once a model
exists. Until then, coverage-first lexical ranking is correct and model-free.
"""

import json
import re
import sqlite3

from config import DB_PATH
from memory_index import ensure_schema


def _terms(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1]


def _match(conn, terms, limit=50):
    """Return [(ref, text, bm25)] for entries matching any term, best-bm25 first."""
    expr = " OR ".join(f'"{t}"' for t in terms)
    cur = conn.execute(
        "SELECT ref, text, bm25(mem_fts) AS rank FROM mem_fts "
        "WHERE mem_fts MATCH ? ORDER BY rank LIMIT ?",
        (expr, limit),
    )
    return cur.fetchall()


def _coverage(text: str, terms: list[str]) -> int:
    toks = set(re.findall(r"[a-z0-9]+", text.lower()))
    return sum(1 for t in set(terms) if t in toks)


def _recent_refs(conn, limit=50):
    cur = conn.execute(
        "SELECT ref FROM mem_fts ORDER BY ts DESC LIMIT ?", (limit,)
    )
    return [row[0] for row in cur.fetchall()]


def _hydrate(conn, refs):
    if not refs:
        return {}
    placeholders = ",".join("?" * len(refs))
    cur = conn.execute(
        f"SELECT ref, src, date, ts, raw FROM mem_fts WHERE ref IN ({placeholders})",
        refs,
    )
    out = {}
    for ref, src, date, ts, raw in cur.fetchall():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            rec = {}
        out[ref] = {
            "ref": ref, "src": src, "date": date, "ts": ts,
            "summary": rec.get("summary", ""), "raw": rec,
        }
    return out


def recall(query: str, k: int = 5, db_path=DB_PATH) -> list[dict]:
    """Return up to k memory entries most relevant to `query`, whole-history."""
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        terms = _terms(query)
        cov = {}
        ordered: list[str] = []
        if terms:
            rows = _match(conn, terms)
            ranked = sorted(
                rows, key=lambda r: (-_coverage(r[1], terms), r[2])
            )  # coverage desc, then bm25 asc
            for ref, text, _bm25 in ranked:
                cov[ref] = _coverage(text, terms)
                ordered.append(ref)
        # Pad to k with the most recent, relevance-first.
        if len(ordered) < k:
            for ref in _recent_refs(conn):
                if ref not in cov:
                    ordered.append(ref)
                    cov[ref] = 0
                if len(ordered) >= k:
                    break
        ordered = ordered[:k]
        hydrated = _hydrate(conn, ordered)
        results = []
        for ref in ordered:
            item = hydrated.get(ref)
            if item:
                item["coverage"] = cov.get(ref, 0)
                results.append(item)
        return results
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "metacognition dream engine"
    print(f"recall({q!r}):\n")
    for i, r in enumerate(recall(q), 1):
        print(f"{i}. [{r['date']}] cover={r['coverage']}  {r['ref']}")
        print(f"   {r['summary'][:150]}")
        print()
