"""
Task analysis engine — runs as part of daemon heartbeat.

Detects stale tasks, computes velocity, finds bottlenecks,
identifies skill gaps. All insights are advisory only.
"""

import json
from datetime import datetime, timezone

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_analysis(db_path: str) -> dict:
    """Run full analysis pass. Returns summary for daemon diary."""
    new_insights = []

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # 1. Stale detection
        cur = await db.execute(
            "SELECT id, title, status, updated_at FROM tasks "
            "WHERE status NOT IN ('done', 'cancelled') "
            "AND updated_at < date('now', '-14 days') "
            "ORDER BY updated_at ASC LIMIT 10"
        )
        stale = await cur.fetchall()
        for t in stale:
            days = "14+"
            new_insights.append({
                "type": "stale",
                "title": f"Task #{t['id']} stale: {t['title'][:80]}",
                "body": f"Status '{t['status']}', untouched for {days} days.",
                "confidence": 0.85,
                "evidence": json.dumps([t["id"]]),
                "actionable": 1,
            })

        # 2. Bottlenecks — projects with blocked tasks
        cur = await db.execute(
            "SELECT p.id, p.name, COUNT(t.id) as blocked_count "
            "FROM projects p JOIN tasks t ON t.project_id = p.id "
            "WHERE t.status = 'blocked' GROUP BY p.id HAVING blocked_count >= 2"
        )
        bottlenecks = await cur.fetchall()
        for b in bottlenecks:
            new_insights.append({
                "type": "bottleneck",
                "title": f"Project '{b['name']}' has {b['blocked_count']} blocked tasks",
                "body": f"Review blocked tasks in this project — may indicate a systemic blocker.",
                "confidence": 0.75,
                "evidence": json.dumps([b["id"]]),
                "actionable": 1,
            })

        # 3. Skill gaps — tags with no matching skill
        cur = await db.execute("SELECT tags FROM tasks WHERE tags != '[]' AND status != 'done'")
        tag_rows = await cur.fetchall()
        tag_counts = {}
        for r in tag_rows:
            try:
                for tag in json.loads(r["tags"]):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

        cur = await db.execute("SELECT name FROM skills")
        skill_names = {r["name"] for r in await cur.fetchall()}

        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            if count >= 3 and tag not in skill_names:
                new_insights.append({
                    "type": "skill_gap",
                    "title": f"Skill gap: '{tag}'",
                    "body": f"{count} open tasks tagged '{tag}' but no skill tracked. Register it with: vex tasks skills add {tag}",
                    "confidence": 0.7,
                    "evidence": json.dumps([]),
                    "actionable": 1,
                })

        # 4. Velocity snapshot
        cur = await db.execute(
            "SELECT COUNT(*) as created FROM tasks "
            "WHERE created_at >= date('now', '-7 days')"
        )
        created_7d = (await cur.fetchone())[0] or 0

        cur = await db.execute(
            "SELECT COUNT(*) as done FROM tasks "
            "WHERE status = 'done' AND completed_at >= date('now', '-7 days')"
        )
        done_7d = (await cur.fetchone())[0] or 0

        cur = await db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'blocked'")
        blocked = (await cur.fetchone())[0] or 0

        cur = await db.execute(
            "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done', 'cancelled') "
            "AND updated_at < date('now', '-14 days')"
        )
        stale_count = (await cur.fetchone())[0] or 0

        cur = await db.execute("SELECT COUNT(*) FROM projects WHERE status = 'active'")
        active_projects = (await cur.fetchone())[0] or 0

        await db.execute(
            "INSERT INTO velocity (snapshot_at, period_days, tasks_created, "
            "tasks_completed, blocked_count, stale_count, active_projects) "
            "VALUES (?, 7, ?, ?, ?, ?, ?)",
            (_now(), created_7d, done_7d, blocked, stale_count, active_projects),
        )

        # Write insights
        for ins in new_insights:
            # Skip duplicates — don't re-report the same insight
            cur = await db.execute(
                "SELECT COUNT(*) FROM insights WHERE title = ? AND generated_at >= date('now', '-1 day')",
                (ins["title"],),
            )
            if (await cur.fetchone())[0] > 0:
                continue
            await db.execute(
                "INSERT INTO insights (generated_at, insight_type, title, body, "
                "confidence, evidence_tasks, evidence_projects, acknowledged, actionable) "
                "VALUES (?, ?, ?, ?, ?, ?, '[]', 0, ?)",
                (_now(), ins["type"], ins["title"], ins["body"],
                 ins["confidence"], ins["evidence"], ins["actionable"]),
            )

        await db.commit()

    summary = f"Analysis: {created_7d} created, {done_7d} done this week. "
    summary += f"{blocked} blocked, {stale_count} stale. "
    summary += f"{len(new_insights)} new insights."

    return {
        "ok": True,
        "insights": len(new_insights),
        "new_insights": new_insights,
        "summary": summary,
        "velocity": {
            "created_7d": created_7d,
            "done_7d": done_7d,
            "blocked": blocked,
            "stale": stale_count,
        },
    }
