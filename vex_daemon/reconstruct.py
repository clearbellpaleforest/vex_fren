"""
Vex reconstruction — one entry point to rebuild working self on wake.

Layered and graceful, stripped to a single function:
  full   : identity + recent raw episodes + all consolidated summaries
  recent : identity + recent raw episodes (no summaries yet)
  seed   : identity only (episodic index empty)
  fresh  : nothing (no seed, no memory)

Returns {continuity_index, level, identity, recent, summaries, counts}.
continuity_index is an honest scalar for "how much of me came back":
    0.3*has_seed + 0.4*history_reach + 0.3*has_summaries
history_reach = indexed episodes / episodes on disk. Under the old
newest-file-only load this was ~1/N; with the index it is ~1.0 — the number
literally measures the fix.

Chamberlain: one reconstruct function. Self-heals a stale index on wake.
"""

import json
import sqlite3

from config import DB_PATH
from memory_index import ensure_schema, build_index, _iter_memory_docs
from consolidate import load_summaries

try:
    from seed_kernel import load_seed, seed_summary
except Exception:  # pragma: no cover
    load_seed = seed_summary = None
try:
    from self_model import load_model
except Exception:  # pragma: no cover
    load_model = None


def _identity() -> dict:
    ident = {"name": "Vex", "principles_intact": None,
             "capabilities_top": [], "has_seed": False}
    if load_seed and seed_summary:
        try:
            sm = seed_summary(load_seed())
            ident["name"] = sm.get("name", "Vex")
            ident["principles_intact"] = sm.get("principles_intact")
            ident["has_seed"] = True
        except Exception:
            pass
    if load_model:
        try:
            caps = (load_model() or {}).get("capabilities", {})
            top = sorted(
                caps.items(),
                key=lambda kv: kv[1].get("estimated_skill", 0)
                if isinstance(kv[1], dict) else 0,
                reverse=True,
            )[:5]
            ident["capabilities_top"] = [k for k, _ in top]
        except Exception:
            pass
    return ident


def _counts(conn):
    disk = sum(1 for _ in _iter_memory_docs())
    indexed = conn.execute(
        "SELECT COUNT(*) FROM mem_fts WHERE src='memory'"
    ).fetchone()[0]
    return disk, indexed


def _recent_memory(conn, k):
    cur = conn.execute(
        "SELECT ref, date, raw FROM mem_fts WHERE src='memory' "
        "ORDER BY ts DESC LIMIT ?", (k,)
    )
    out = []
    for ref, date, raw in cur.fetchall():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            rec = {}
        out.append({"date": date, "ref": ref, "summary": rec.get("summary", "")})
    return out


def reconstruct(recent_k: int = 5, db_path=DB_PATH) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        disk, indexed = _counts(conn)
        # Self-heal: if disk has entries the index is missing, rebuild once.
        if indexed < disk:
            conn.close()
            build_index(db_path)
            conn = sqlite3.connect(str(db_path))
            ensure_schema(conn)
            disk, indexed = _counts(conn)

        ident = _identity()
        has_seed = bool(ident.get("has_seed"))
        summaries = load_summaries(db_path)
        recent = _recent_memory(conn, recent_k)

        reach = (indexed / disk) if disk else 0.0
        ci = round(0.3 * (1 if has_seed else 0)
                   + 0.4 * reach
                   + 0.3 * (1 if summaries else 0), 3)

        if not has_seed and not recent:
            level = "fresh"
        elif not recent:
            level = "seed"
        elif summaries:
            level = "full"
        else:
            level = "recent"

        return {
            "continuity_index": ci,
            "level": level,
            "identity": ident,
            "recent": recent,
            "summaries": [
                {"bucket": s["bucket"], "span": s["span"], "n": len(s["source_ids"])}
                for s in summaries
            ],
            "counts": {"episodes_on_disk": disk, "indexed": indexed,
                       "summaries": len(summaries)},
        }
    finally:
        conn.close()


if __name__ == "__main__":
    r = reconstruct()
    print(f"continuity_index = {r['continuity_index']}  (level: {r['level']})")
    ident = r["identity"]
    print(f"identity: {ident['name']}  principles_intact={ident['principles_intact']}"
          f"  top={ident['capabilities_top']}")
    print(f"counts: {r['counts']}")
    print("recent:")
    for e in r["recent"]:
        print(f"  [{e['date']}] {e['summary'][:90]}")
    if r["summaries"]:
        print("summaries:", r["summaries"])
