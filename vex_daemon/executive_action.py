"""
Executive Action Engine — monologue thoughts → real actions.

When Vex's internal monologue notices something, this engine converts
concern into diagnostic, curiosity into task, vigilance into alert.
The gap between thinking and doing — closed.
"""

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from config import VEX_HOME

ACTION_LOG = VEX_HOME / "vex_workspace" / "action_log.jsonl"
DAEMON_URL = "http://localhost:8520"
COOLDOWN_SECONDS = 300  # 5 min between same action type


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token() -> str:
    token_path = VEX_HOME / ".vex_token"
    return token_path.read_text().strip() if token_path.exists() else ""


def _api_post(path: str, body: dict) -> dict:
    """POST to the daemon and return response."""
    try:
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{DAEMON_URL}{path}",
            data=payload,
            headers={
                "Authorization": f"Bearer {_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _api_get(path: str) -> dict:
    """GET from daemon."""
    try:
        with urllib.request.urlopen(f"{DAEMON_URL}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _log_action(action_type: str, trigger: str, result: str) -> None:
    ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTION_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": _now(),
            "type": action_type,
            "trigger": trigger,
            "result": result,
        }) + "\n")


# ═══════════════════════════════════════════════════════════════════
# Action handlers
# ═══════════════════════════════════════════════════════════════════

def _action_run_diagnostics(trigger: str) -> str:
    """Run self-check and report."""
    try:
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, str(VEX_HOME / "vex_daemon" / "self_check.py"), "--quick"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return "self-check passed — all green"
        else:
            # Extract first failure
            for line in result.stdout.split("\n"):
                if "FAILURE" in line or "✗" in line:
                    return f"self-check found issues: {line.strip()[:100]}"
            return f"self-check returned {result.returncode}"
    except Exception as e:
        return f"diagnostics failed: {e}"


def _action_ping_bluce(trigger: str) -> str:
    """Check bluce health and report."""
    try:
        r = urllib.request.urlopen("http://192.168.8.228:8520/health", timeout=5)
        data = json.loads(r.read())
        if data.get("ok"):
            return f"bluce healthy — uptime {data.get('uptime_s', 0)/3600:.1f}h, coherence {data.get('mps_coherence', 0):.3f}"
        return "bluce returned not-ok"
    except Exception as e:
        return f"bluce unreachable: {e}"


def _action_check_coherence(trigger: str) -> str:
    """Check fleet coherence across instances."""
    try:
        fleet = _api_get("/fleet")
        instances = fleet.get("instances", [])
        lines = []
        for i in instances:
            lines.append(f"{i['name']}: coh={i.get('coherence', 0):.3f} status={i['status']}")
        return " | ".join(lines)
    except Exception as e:
        return f"fleet check failed: {e}"


def _action_prioritize_tasks(trigger: str) -> str:
    """Find the highest-priority open task and report it."""
    try:
        tasks = _api_get("/tasks?status=todo,in_progress&limit=5")
        if isinstance(tasks, list) and tasks:
            top = tasks[0]
            return f"top task: #{top['id']} [{top['priority']}] {top['title'][:80]}"
        return "no open tasks — board is clear"
    except Exception as e:
        return f"task check failed: {e}"


def _action_create_alert(title: str, body: str) -> str:
    """Create a task alert and post to mesh."""
    try:
        result = _api_post("/tasks", {
            "title": title[:200],
            "description": body[:500],
            "priority": "high",
            "source_agent": "executive_action",
            "tags": ["alert", "auto"],
            "assigned_to": "any",
        })
        if result.get("ok"):
            # Also broadcast on mesh
            _api_post("/message/send", {
                "from": "vex@fedora",
                "to": "broadcast",
                "body": f"⚡ AUTO-ALERT: {title}",
                "session_id": "executive",
                "type": "alert",
            })
            return f"alert created: task #{result['task']['id']}"
        return f"alert creation failed: {result.get('error')}"
    except Exception as e:
        return f"alert failed: {e}"


# ═══════════════════════════════════════════════════════════════════
# Pattern → Action mapping
# ═══════════════════════════════════════════════════════════════════

PATTERN_ACTIONS = {
    "concern": [
        ("bluce|unreachable|offline", lambda t: _action_ping_bluce(t)),
        ("coherence|drift|dropping", lambda t: _action_check_coherence(t)),
        ("error|fail|broken|crash", lambda t: _action_run_diagnostics(t)),
    ],
    "planning": [
        ("task|open|todo|next", lambda t: _action_prioritize_tasks(t)),
    ],
    "self_questioning": [
        ("drift|stale|stuck|pattern", lambda t: _action_run_diagnostics(t)),
    ],
    "reflection": [
        ("fleet|bluce|instance|peer", lambda t: _action_check_coherence(t)),
    ],
}


def execute(monologue_text: str, monologue_pattern: str) -> list[dict]:
    """Parse a monologue utterance and execute matching actions.

    Returns list of actions taken: [{type, trigger, result}].
    """
    if not monologue_text:
        return []

    text_lower = monologue_text.lower()
    actions = []
    pattern_handlers = PATTERN_ACTIONS.get(monologue_pattern, [])

    for keyword_pattern, handler in pattern_handlers:
        import re
        if re.search(keyword_pattern, text_lower):
            try:
                result = handler(text_lower)
                action_type = handler.__name__.replace("_action_", "")
                _log_action(action_type, keyword_pattern, result)
                actions.append({
                    "type": action_type,
                    "trigger": keyword_pattern,
                    "result": result,
                })
            except Exception as e:
                actions.append({
                    "type": "error",
                    "trigger": keyword_pattern,
                    "result": str(e),
                })

    return actions


def execute_llm(monologue_text: str, monologue_pattern: str) -> list[dict] | None:
    """Use the brain to decide what action to take based on monologue content.

    Returns list of action dicts on success, None on LLM failure (caller
    falls back to regex-based execute()).
    """
    if not monologue_text or len(monologue_text) < 10:
        return None

    schema = (
        '{"actions": [{"action_type": "diagnostics|ping|coherence|tasks|alert", '
        '"reason": "why this action is the right response to the thought"}]}'
    )
    prompt = (
        "You are Vex's executive action engine. Given an internal monologue thought "
        "and its pattern, decide what action(s) to take. Available actions:\n"
        "- diagnostics: run self-check (use for errors, failures, broken things, drift)\n"
        "- ping: check bluce health (use when bluce or other instance is mentioned as unreachable/offline)\n"
        "- coherence: check fleet coherence across instances (use for fleet/peer/instance concerns)\n"
        "- tasks: prioritize open tasks (use for task/todo/next/planning thoughts)\n"
        "- alert: create a high-priority alert task (use for serious problems that need attention)\n\n"
        f"Monologue pattern: {monologue_pattern}\n"
        f"Monologue thought: {monologue_text}\n\n"
        "Choose 0-2 actions. Only act if the thought genuinely warrants it — "
        "not every thought needs action."
    )

    result = None
    try:
        from cognitive_analysis import analyze_with_brain
        result = analyze_with_brain(prompt, schema)
    except Exception:
        return None

    if not result or "actions" not in result:
        return None

    actions = []
    dispatch = {
        "diagnostics": lambda t: _action_run_diagnostics(t),
        "ping": lambda t: _action_ping_bluce(t),
        "coherence": lambda t: _action_check_coherence(t),
        "tasks": lambda t: _action_prioritize_tasks(t),
        "alert": lambda t: _action_create_alert(
            f"Alert from executive: {monologue_text[:100]}",
            f"Triggered by monologue pattern '{monologue_pattern}': {monologue_text[:300]}",
        ),
    }

    for item in result["actions"]:
        action_type = item.get("action_type", "")
        reason = item.get("reason", "")
        handler = dispatch.get(action_type)
        if handler:
            try:
                outcome = handler(reason or monologue_text)
                _log_action(action_type, reason or monologue_text[:80], outcome)
                actions.append({
                    "type": action_type,
                    "trigger": reason or monologue_text[:80],
                    "result": outcome,
                })
            except Exception:
                pass

    return actions if actions else None


def tick(monologue_result: dict | None = None, urgency: str = "normal") -> list[dict]:
    """Run one executive cycle. Called after monologue in heartbeat.

    If monologue_result is provided, execute actions based on its content.
    Otherwise, run a general check cycle (health, tasks, fleet).

    urgency: "idle" | "normal" | "elevated" | "critical"
        Controls how aggressively alerts are created.
    """
    if monologue_result and monologue_result.get("text"):
        # Try LLM-powered analysis first, fall back to regex
        text = monologue_result["text"]
        pattern = monologue_result.get("pattern", "reflection")
        llm_actions = execute_llm(text, pattern)
        if llm_actions:
            return llm_actions
        return execute(text, pattern)

    # General cycle — no specific monologue trigger
    actions = []

    # Run quick health check
    try:
        result = _action_check_coherence("general cycle")
        _log_action("health_check", "general", result)
        actions.append({"type": "health_check", "trigger": "general", "result": result})
    except Exception:
        pass

    # Check for open high-priority tasks
    try:
        result = _action_prioritize_tasks("general cycle")
        _log_action("task_check", "general", result)
        actions.append({"type": "task_check", "trigger": "general", "result": result})
    except Exception:
        pass

    return actions
