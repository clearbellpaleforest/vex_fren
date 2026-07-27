"""
Task management router for the Vex Daemon.

Projects, tasks (with hierarchy), skills, insights, and cross-agent linking.
All mutating endpoints require bearer token auth. Read endpoints are open.
"""

import json
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auth import check_auth
from config import DB_PATH, VEX_HOME

router = APIRouter(prefix="/tasks", tags=["tasks"])

BUS_PATH = VEX_HOME / "vex_workspace" / "vex_bus.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj):
    """Handle non-serializable types."""
    return str(obj)


async def _broadcast_task_event(task_id: int, event: str, title: str, detail: str = ""):
    """Write a task event to the message bus so other Vex instances see it."""
    try:
        body = json.dumps({
            "task_id": task_id,
            "event": event,
            "title": title,
            "detail": detail,
        })
        now = _now()
        # Write to messages table
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO messages (created_at, sender, recipient, body, msg_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, "vex@fedora/quatre", "broadcast", body, "task_event"),
            )
            await db.commit()
        # Append to bus file for inter-instance sync
        bus_entry = {
            "from": "vex@fedora",
            "to": "broadcast",
            "type": "task_event",
            "body": body,
            "session_id": "quatre",
            "timestamp": now,
        }
        BUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BUS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(bus_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Best-effort broadcast — don't fail the request


def _parse_meta(raw: str | None) -> dict:
    """Parse a JSON meta blob, returning {} on failure."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_tags(raw: str | None) -> list:
    """Parse a JSON tags array, returning [] on failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ── Projects ─────────────────────────────────────────────────────

@router.get("/projects")
async def list_projects(status: str = ""):
    """List projects with task counts."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if status:
                cursor = await db.execute(
                    "SELECT * FROM projects WHERE status = ? ORDER BY priority DESC, name ASC",
                    (status,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM projects ORDER BY priority DESC, name ASC"
                )
            rows = await cursor.fetchall()

            projects = []
            for r in rows:
                p = dict(r)
                # Get task counts
                tc = await db.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as done "
                    "FROM tasks WHERE project_id = ?",
                    (p["id"],),
                )
                tcr = await tc.fetchone()
                p["task_count"] = tcr[0] if tcr else 0
                p["completed_count"] = tcr[1] if tcr else 0
                p["tags"] = _parse_tags(p.get("tags"))
                p["meta"] = _parse_meta(p.get("meta"))
                projects.append(p)

            return JSONResponse(projects)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/projects")
async def create_project(request: Request):
    """Create a project."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)

        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "INSERT INTO projects (name, description, status, priority, source_agent, "
                "source_session, tags, meta, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    body.get("description", ""),
                    body.get("status", "active"),
                    body.get("priority", "medium"),
                    body.get("source_agent", "vex"),
                    body.get("source_session", ""),
                    json.dumps(body.get("tags", [])),
                    json.dumps(body.get("meta", {})),
                    now,
                    now,
                ),
            )
            await db.commit()
            pid = cursor.lastrowid

        return JSONResponse({"ok": True, "project": {"id": pid, "name": name}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.get("/projects/{project_id}")
async def get_project(project_id: int):
    """Get a project with its full task tree."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = await cursor.fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

            project = dict(row)
            project["tags"] = _parse_tags(project.get("tags"))
            project["meta"] = _parse_meta(project.get("meta"))

            # Get all tasks for this project
            tcursor = await db.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY priority DESC, created_at ASC",
                (project_id,),
            )
            tasks = [dict(t) for t in await tcursor.fetchall()]
            for t in tasks:
                t["tags"] = _parse_tags(t.get("tags"))
                t["meta"] = _parse_meta(t.get("meta"))

            project["tasks"] = _build_tree(tasks)

        return JSONResponse(project)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.patch("/projects/{project_id}")
async def update_project(project_id: int, request: Request):
    """Update project fields."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        fields = {}
        for key in ("name", "description", "status", "priority", "source_agent", "source_session"):
            if key in body:
                fields[key] = body[key]
        if "tags" in body:
            fields["tags"] = json.dumps(body["tags"])
        if "meta" in body:
            fields["meta"] = json.dumps(body["meta"])

        if not fields:
            return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)

        fields["updated_at"] = _now()
        if fields.get("status") == "completed":
            fields["completed_at"] = _now()

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [project_id]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                f"UPDATE projects SET {set_clause} WHERE id = ?", values
            )
            await db.commit()

        return JSONResponse({"ok": True, "updated": list(fields.keys())})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, request: Request, cascade: bool = False):
    """Delete a project. Without cascade, orphans its tasks."""
    if (err := check_auth(request)):
        return err
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if cascade:
                await db.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
                orphaned = 0
            else:
                cursor = await db.execute(
                    "UPDATE tasks SET project_id = NULL WHERE project_id = ?", (project_id,)
                )
                orphaned = cursor.rowcount
            await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await db.commit()

        return JSONResponse({"ok": True, "orphaned_tasks": orphaned})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Tasks ─────────────────────────────────────────────────────────

@router.get("")
async def list_tasks(
    status: str = "",
    priority: str = "",
    assigned_to: str = "",
    tags: str = "",
    project_id: int | None = None,
    sort: str = "priority",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
):
    """List tasks with optional filters."""
    try:
        where = []
        params = []

        if status:
            statuses = [s.strip() for s in status.split(",")]
            placeholders = ",".join("?" * len(statuses))
            where.append(f"t.status IN ({placeholders})")
            params.extend(statuses)
        if priority:
            priorities = [p.strip() for p in priority.split(",")]
            placeholders = ",".join("?" * len(priorities))
            where.append(f"t.priority IN ({placeholders})")
            params.extend(priorities)
        if assigned_to:
            where.append("t.assigned_to = ?")
            params.append(assigned_to)
        if tags:
            tag_list = [t.strip() for t in tags.split(",")]
            for tag in tag_list:
                where.append("t.tags LIKE ?")
                params.append(f'%"{tag}"%')
        if project_id is not None:
            where.append("t.project_id = ?")
            params.append(project_id)

        where_clause = ("WHERE " + " AND ".join(where)) if where else ""

        # Validate sort column
        valid_sorts = {"priority", "status", "created_at", "updated_at", "title"}
        sort_col = sort if sort in valid_sorts else "priority"
        order_dir = "DESC" if order.lower() == "desc" else "ASC"

        # Priority ordering: critical > high > medium > low
        if sort_col == "priority":
            order_expr = f"CASE t.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END {order_dir}"
        else:
            order_expr = f"t.{sort_col} {order_dir}"

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            sql = (
                f"SELECT t.*, p.name as project_name FROM tasks t "
                f"LEFT JOIN projects p ON t.project_id = p.id "
                f"{where_clause} ORDER BY {order_expr} LIMIT ? OFFSET ?"
            )
            cursor = await db.execute(sql, params + [limit, offset])
            rows = await cursor.fetchall()

            tasks = []
            for r in rows:
                t = dict(r)
                t["tags"] = _parse_tags(t.get("tags"))
                t["meta"] = _parse_meta(t.get("meta"))
                # Compute depth from parent hierarchy
                t["depth"] = await _task_depth(db, t["id"])
                tasks.append(t)

        return JSONResponse(tasks)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def _task_depth(db, task_id: int) -> int:
    """Compute depth of a task in the hierarchy."""
    depth = 0
    current = task_id
    for _ in range(10):  # Safety limit
        cursor = await db.execute("SELECT parent_id FROM tasks WHERE id = ?", (current,))
        row = await cursor.fetchone()
        if not row or not row[0]:
            break
        depth += 1
        current = row[0]
    return depth


def _build_tree(tasks: list[dict]) -> list[dict]:
    """Build a nested tree from a flat task list using parent_id."""
    task_map = {t["id"]: t for t in tasks}
    for t in tasks:
        t.setdefault("children", [])
    roots = []
    for t in tasks:
        parent_id = t.get("parent_id")
        if parent_id and parent_id in task_map:
            task_map[parent_id].setdefault("children", []).append(t)
        else:
            roots.append(t)
    return roots


@router.post("")
async def create_task(request: Request):
    """Create a task. Supports parent_id for hierarchy."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        title = (body.get("title") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "error": "title is required"}, status_code=400)

        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "INSERT INTO tasks (project_id, parent_id, title, description, status, "
                "priority, progress, source_agent, source_session, external_ref, "
                "external_system, assigned_to, tags, deadline, estimated_hours, "
                "meta, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    body.get("project_id"),
                    body.get("parent_id"),
                    title,
                    body.get("description", ""),
                    body.get("status", "todo"),
                    body.get("priority", "medium"),
                    body.get("progress", 0.0),
                    body.get("source_agent", "vex"),
                    body.get("source_session", ""),
                    body.get("external_ref", ""),
                    body.get("external_system", ""),
                    body.get("assigned_to", "any"),
                    json.dumps(body.get("tags", [])),
                    body.get("deadline", ""),
                    body.get("estimated_hours"),
                    json.dumps(body.get("meta", {})),
                    now,
                    now,
                ),
            )
            await db.commit()
            task_id = cursor.lastrowid

            # Record creation in history
            await db.execute(
                "INSERT INTO task_history (task_id, changed_at, field, new_value, "
                "source_agent, source_session) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, now, "created", title, body.get("source_agent", "vex"),
                 body.get("source_session", "")),
            )
            await db.commit()

        return JSONResponse({"ok": True, "task": {"id": task_id, "title": title}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Skills ────────────────────────────────────────────────────────

@router.get("/skills")
async def list_skills(category: str = "", sort: str = "confidence", order: str = "desc"):
    """List tracked skills."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            if category:
                cursor = await db.execute(
                    "SELECT * FROM skills WHERE category = ? ORDER BY confidence DESC",
                    (category,),
                )
            else:
                valid_sorts = {"confidence", "name", "level", "observations"}
                sort_col = sort if sort in valid_sorts else "confidence"
                dir_keyword = "DESC" if order.lower() == "desc" else "ASC"
                cursor = await db.execute(
                    f"SELECT * FROM skills ORDER BY {sort_col} {dir_keyword}"
                )
            rows = await cursor.fetchall()
        return JSONResponse([dict(r) for r in rows])
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/skills")
async def create_skill(request: Request):
    """Register a new skill."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)

        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "INSERT INTO skills (name, description, category, level, confidence, "
                "observations, evidence_count, first_seen, last_demonstrated, source_agent, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    body.get("description", ""),
                    body.get("category", "general"),
                    body.get("level", "unknown"),
                    body.get("confidence", 0.0),
                    0,
                    0,
                    now,
                    None,
                    body.get("source_agent", "vex"),
                    json.dumps(body.get("meta", {})),
                ),
            )
            await db.commit()
            sid = cursor.lastrowid

        return JSONResponse({"ok": True, "skill": {"id": sid, "name": name}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/skills/{skill_id}/observe")
async def observe_skill(skill_id: int, request: Request):
    """Record a skill observation, recalibrating confidence via EMA."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        task_id = body.get("task_id")
        evidence = body.get("evidence", "")
        now = _now()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
            skill = await cur.fetchone()
            if not skill:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            skill = dict(skill)

            # EMA smoothing: alpha = 0.1, with prior 0.5
            alpha = 0.1
            old_confidence = skill["confidence"]
            new_confidence = old_confidence + alpha * (1.0 - old_confidence)
            new_observations = skill["observations"] + 1
            new_evidence_count = skill["evidence_count"] + (1 if evidence else 0)

            await db.execute(
                "UPDATE skills SET confidence = ?, observations = ?, evidence_count = ?, "
                "last_demonstrated = ?, meta = ? WHERE id = ?",
                (
                    new_confidence,
                    new_observations,
                    new_evidence_count,
                    now,
                    json.dumps(body.get("meta", {})),
                    skill_id,
                ),
            )
            await db.commit()

        return JSONResponse({
            "ok": True,
            "skill": {"id": skill_id, "name": skill["name"],
                      "old_confidence": old_confidence, "new_confidence": new_confidence},
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.get("/skills/gaps")
async def skill_gaps():
    """Identify skills with low confidence and high task demand."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Get all task tags
            cur = await db.execute("SELECT tags FROM tasks WHERE tags != '[]'")
            tag_rows = await cur.fetchall()

            tag_counts = {}
            for r in tag_rows:
                for tag in _parse_tags(r["tags"]):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

            # Get all skills
            cur = await db.execute("SELECT * FROM skills")
            skills = {s["name"]: dict(s) for s in await cur.fetchall()}

            gaps = []
            for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
                if count < 3:
                    continue
                if tag not in skills:
                    gaps.append({
                        "name": tag, "category": "unknown", "confidence": 0.0,
                        "demand": count,
                        "rationale": f"{count} tasks tagged '{tag}' but no skill tracked",
                    })
                elif skills[tag]["confidence"] < 0.4:
                    gaps.append({
                        "name": tag, "category": skills[tag].get("category", ""),
                        "confidence": skills[tag]["confidence"],
                        "demand": count,
                        "rationale": f"Skill at {skills[tag]['confidence']:.0%} confidence with {count} open tasks",
                    })

        return JSONResponse(gaps)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Stats & Insights ──────────────────────────────────────────────

@router.get("/stats")
async def get_stats(period: int = 30):
    """Get aggregate stats for the given period (days)."""
    try:
        period = max(1, min(int(period), 365))
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Task counts
            cur = await db.execute(
                "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
            )
            task_counts = {r["status"]: r["count"] for r in await cur.fetchall()}

            # Project counts
            cur = await db.execute(
                "SELECT status, COUNT(*) as count FROM projects GROUP BY status"
            )
            proj_counts = {r["status"]: r["count"] for r in await cur.fetchall()}

            # Velocity: tasks completed in period
            cutoff = datetime.now(timezone.utc).isoformat()[:19]  # YYYY-MM-DDTHH:MM:SS
            cur = await db.execute(
                "SELECT COUNT(*) as done FROM tasks WHERE status = 'done' "
                "AND completed_at >= date('now', ?)",
                (f'-{period} days',),
            )
            done_period = (await cur.fetchone())[0] or 0

            cur = await db.execute(
                "SELECT COUNT(*) as created FROM tasks "
                "WHERE created_at >= date('now', ?)",
                (f'-{period} days',),
            )
            created_period = (await cur.fetchone())[0] or 0

            # Blocked count
            cur = await db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'blocked'")
            blocked = (await cur.fetchone())[0] or 0

            # Stale count (>14 days untouched, not done)
            cur = await db.execute(
                "SELECT COUNT(*) FROM tasks WHERE status != 'done' "
                "AND updated_at < date('now', '-14 days')"
            )
            stale = (await cur.fetchone())[0] or 0

        return JSONResponse({
            "tasks": {
                "total": sum(task_counts.values()),
                "todo": task_counts.get("todo", 0),
                "in_progress": task_counts.get("in_progress", 0),
                "blocked": task_counts.get("blocked", 0),
                "done": task_counts.get("done", 0),
                "cancelled": task_counts.get("cancelled", 0),
            },
            "projects": {
                "total": sum(proj_counts.values()),
                "active": proj_counts.get("active", 0),
                "completed": proj_counts.get("completed", 0),
                "archived": proj_counts.get("archived", 0),
            },
            "velocity": {
                "created_period": created_period,
                "completed_period": done_period,
                "created_per_week": round(created_period / (period / 7), 1) if period >= 7 else created_period,
                "completed_per_week": round(done_period / (period / 7), 1) if period >= 7 else done_period,
            },
            "bottlenecks": {
                "blocked": blocked,
                "stale": stale,
            },
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/insights")
async def list_insights(insight_type: str = "", acknowledged: bool | None = None, limit: int = 20):
    """List generated insights."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            where = []
            params = []
            if insight_type:
                where.append("insight_type = ?")
                params.append(insight_type)
            if acknowledged is not None:
                where.append("acknowledged = ?")
                params.append(1 if acknowledged else 0)

            where_clause = ("WHERE " + " AND ".join(where)) if where else ""
            cursor = await db.execute(
                f"SELECT * FROM insights {where_clause} ORDER BY id DESC LIMIT ?",
                params + [limit],
            )
            rows = await cursor.fetchall()

        insights = []
        for r in rows:
            i = dict(r)
            i["evidence_tasks"] = _parse_meta(i.get("evidence_tasks"))
            i["evidence_projects"] = _parse_meta(i.get("evidence_projects"))
            insights.append(i)

        return JSONResponse(insights)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/insights/{insight_id}/acknowledge")
async def acknowledge_insight(insight_id: int, request: Request):
    """Mark an insight as acknowledged."""
    if (err := check_auth(request)):
        return err
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE insights SET acknowledged = 1 WHERE id = ?", (insight_id,)
            )
            await db.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/insights/analyze")
async def trigger_analysis(request: Request):
    """Manually trigger a task analysis cycle."""
    if (err := check_auth(request)):
        return err
    try:
        from routers.task_analysis import run_analysis
        result = await run_analysis(DB_PATH)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Cross-Agent Linking ───────────────────────────────────────────

@router.get("/link/external")
async def find_by_external(system: str = "", ref: str = ""):
    """Find a task by external system reference."""
    if not system or not ref:
        return JSONResponse({"ok": False, "error": "system and ref required"}, status_code=400)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT t.*, p.name as project_name FROM tasks t "
                "LEFT JOIN projects p ON t.project_id = p.id "
                "WHERE t.external_system = ? AND t.external_ref = ?",
                (system, ref),
            )
            row = await cursor.fetchone()
            if row:
                task = dict(row)
                task["tags"] = _parse_tags(task.get("tags"))
                task["meta"] = _parse_meta(task.get("meta"))
                return JSONResponse(task)
        return JSONResponse(None)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/link/external")
async def link_external(request: Request):
    """Link an external reference to an existing task."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        system = body.get("system", "")
        ref = body.get("ref", "")
        task_id = body.get("task_id")
        if not system or not ref or not task_id:
            return JSONResponse(
                {"ok": False, "error": "system, ref, and task_id required"},
                status_code=400,
            )

        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE tasks SET external_system = ?, external_ref = ?, updated_at = ? "
                "WHERE id = ?",
                (system, ref, now, task_id),
            )
            await db.commit()

        return JSONResponse({"ok": True, "linked": f"{system}:{ref} -> task #{task_id}"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.get("/{task_id}")
async def get_task(task_id: int):
    """Get a task with its children and full history."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT t.*, p.name as project_name FROM tasks t "
                "LEFT JOIN projects p ON t.project_id = p.id WHERE t.id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

            task = dict(row)
            task["tags"] = _parse_tags(task.get("tags"))
            task["meta"] = _parse_meta(task.get("meta"))

            # Get children
            ccursor = await db.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY priority DESC, created_at ASC",
                (task_id,),
            )
            task["children"] = [dict(c) for c in await ccursor.fetchall()]
            for c in task["children"]:
                c["tags"] = _parse_tags(c.get("tags"))
                c["meta"] = _parse_meta(c.get("meta"))

            # Get history
            hcursor = await db.execute(
                "SELECT * FROM task_history WHERE task_id = ? ORDER BY id DESC LIMIT 50",
                (task_id,),
            )
            task["history"] = [dict(h) for h in await hcursor.fetchall()]

        return JSONResponse(task)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.patch("/{task_id}")
async def update_task(task_id: int, request: Request):
    """Update task fields. Auto-records status and assignment changes in history."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        note = body.pop("note", "")

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Fetch current state for history
            cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            current = await cur.fetchone()
            if not current:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            current = dict(current)

        now = _now()
        fields = {}
        history_entries = []

        for key in ("title", "description", "status", "priority", "progress",
                     "assigned_to", "deadline", "estimated_hours", "actual_hours",
                     "external_ref", "external_system", "project_id", "parent_id",
                     "source_agent", "source_session"):
            if key in body:
                new_val = body[key]
                old_val = current.get(key)
                fields[key] = new_val
                if str(new_val) != str(old_val):
                    history_entries.append((key, str(old_val), str(new_val)))

        if "tags" in body:
            fields["tags"] = json.dumps(body["tags"])
            old_tags = _parse_tags(current.get("tags"))
            if body["tags"] != old_tags:
                history_entries.append(("tags", json.dumps(old_tags), json.dumps(body["tags"])))

        if "meta" in body:
            fields["meta"] = json.dumps(body["meta"])

        # Status transitions
        if "status" in body:
            if body["status"] == "in_progress" and not current.get("started_at"):
                fields["started_at"] = now
            if body["status"] == "done":
                fields["completed_at"] = now
                fields["progress"] = 1.0
            if body["status"] == "blocked":
                fields["progress"] = current.get("progress", 0.0)

        fields["updated_at"] = now

        if not fields:
            return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)

            # Write history entries
            for field, old_val, new_val in history_entries:
                await db.execute(
                    "INSERT INTO task_history (task_id, changed_at, field, old_value, "
                    "new_value, source_agent, source_session, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, now, field, old_val[:500] if old_val else "",
                     new_val[:500], body.get("source_agent", "vex"),
                     body.get("source_session", ""), note),
                )

            await db.commit()

        # Broadcast on completion
        if fields.get("status") == "done":
            await _broadcast_task_event(task_id, "done", current["title"],
                                        f"Completed by {body.get('source_agent', 'vex')}")
        elif fields.get("status") == "blocked":
            await _broadcast_task_event(task_id, "blocked", current["title"], note)

        return JSONResponse({"ok": True, "updated": list(fields.keys())})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.delete("/{task_id}")
async def delete_task(task_id: int, request: Request, cascade: bool = False):
    """Delete a task. Without cascade, reparents children upward."""
    if (err := check_auth(request)):
        return err
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Get task to find parent_id for reparenting
            cur = await db.execute("SELECT parent_id FROM tasks WHERE id = ?", (task_id,))
            row = await cur.fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            parent_id = row[0]

            if cascade:
                # Recursively delete children (SQLite doesn't do recursive FK cascade)
                await _cascade_delete(db, task_id)
                await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                reparented = 0
            else:
                # Reparent children to this task's parent
                cursor = await db.execute(
                    "UPDATE tasks SET parent_id = ? WHERE parent_id = ?",
                    (parent_id, task_id),
                )
                reparented = cursor.rowcount
                await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

            # Clean history
            await db.execute("DELETE FROM task_history WHERE task_id = ?", (task_id,))
            await db.commit()

        return JSONResponse({"ok": True, "reparented_children": reparented})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


async def _cascade_delete(db, task_id: int):
    """Recursively delete children of a task."""
    cur = await db.execute("SELECT id FROM tasks WHERE parent_id = ?", (task_id,))
    children = await cur.fetchall()
    for child in children:
        await _cascade_delete(db, child[0])
        await db.execute("DELETE FROM task_history WHERE task_id = ?", (child[0],))
        await db.execute("DELETE FROM tasks WHERE id = ?", (child[0],))


@router.get("/{task_id}/tree")
async def get_task_tree(task_id: int):
    """Get the full recursive hierarchy from this task downward."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Get the root task
            cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = await cur.fetchone()
            if not row:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

            # Get all descendants
            all_tasks = [dict(row)]
            to_process = [task_id]
            while to_process:
                placeholders = ",".join("?" * len(to_process))
                cur = await db.execute(
                    f"SELECT * FROM tasks WHERE parent_id IN ({placeholders}) ORDER BY priority DESC, created_at ASC",
                    to_process,
                )
                children = [dict(r) for r in await cur.fetchall()]
                to_process = [c["id"] for c in children]
                all_tasks.extend(children)

            for t in all_tasks:
                t["tags"] = _parse_tags(t.get("tags"))
                t["meta"] = _parse_meta(t.get("meta"))

        return JSONResponse(_build_tree(all_tasks)[0] if all_tasks else {})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/{task_id}/history")
async def get_task_history(task_id: int):
    """Get the full change history for a task."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM task_history WHERE task_id = ? ORDER BY id DESC",
                (task_id,),
            )
            rows = await cursor.fetchall()
        return JSONResponse([dict(r) for r in rows])
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/{task_id}/block")
async def block_task(task_id: int, request: Request):
    """Block a task with a reason."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        reason = body.get("reason", "")
        now = _now()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Get current task
            cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = await cur.fetchone()
            if not task:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            task = dict(task)

            old_status = task["status"]
            await db.execute(
                "UPDATE tasks SET status = 'blocked', updated_at = ? WHERE id = ?",
                (now, task_id),
            )
            await db.execute(
                "INSERT INTO task_history (task_id, changed_at, field, old_value, "
                "new_value, source_agent, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, now, "status", old_status, "blocked",
                 body.get("source_agent", "vex"), reason),
            )
            await db.commit()

        await _broadcast_task_event(task_id, "blocked", task["title"], reason)
        return JSONResponse({"ok": True, "task": {"id": task_id, "status": "blocked"}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/{task_id}/unblock")
async def unblock_task(task_id: int, request: Request):
    """Unblock a task, restoring its previous status."""
    if (err := check_auth(request)):
        return err
    try:
        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # Find the last non-blocked status from history
            cur = await db.execute(
                "SELECT old_value FROM task_history WHERE task_id = ? AND field = 'status' "
                "AND old_value != 'blocked' ORDER BY id DESC LIMIT 1",
                (task_id,),
            )
            row = await cur.fetchone()
            prev_status = row[0] if row else "todo"

            await db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (prev_status, now, task_id),
            )
            await db.execute(
                "INSERT INTO task_history (task_id, changed_at, field, old_value, "
                "new_value, source_agent, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, now, "status", "blocked", prev_status,
                 body.get("source_agent", "vex"), "unblocked"),
            )
            await db.commit()

        return JSONResponse({"ok": True, "task": {"id": task_id, "status": prev_status}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/{task_id}/done")
async def complete_task(task_id: int, request: Request):
    """Mark a task as done."""
    if (err := check_auth(request)):
        return err
    try:
        body = await request.json()
        actual_hours = body.get("actual_hours")
        note = body.get("note", "")
        now = _now()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            task = await cur.fetchone()
            if not task:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            task = dict(task)

            updates = {"status": "done", "progress": 1.0, "completed_at": now, "updated_at": now}
            if actual_hours is not None:
                updates["actual_hours"] = float(actual_hours)

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [task_id]
            await db.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)

            await db.execute(
                "INSERT INTO task_history (task_id, changed_at, field, old_value, "
                "new_value, source_agent, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, now, "status", task["status"], "done",
                 body.get("source_agent", "vex"), note),
            )
            await db.commit()

        await _broadcast_task_event(task_id, "done", task["title"],
                                    note or f"Completed by {body.get('source_agent', 'vex')}")
        return JSONResponse({"ok": True, "task": {"id": task_id, "status": "done"}})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
