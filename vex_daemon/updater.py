"""
Auto-update from bus messages.

Scans ingested bus messages for BOOTSTRAP and UPDATE directives from peer
instances. A BOOTSTRAP message carries a shell command to download and install
a code bundle. UPDATE messages are informational but may carry a bundle URL
that this daemon can fetch and apply.

Runs on daemon startup and periodically via the heartbeat tick. Idempotent —
each message is processed at most once, tracked by message ID.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from config import DB_PATH, VEX_HOME

APPLIED_PATH = VEX_HOME / ".vex_updates_applied"


def _load_applied() -> set[int]:
    if not APPLIED_PATH.exists():
        return set()
    try:
        return {int(x) for x in APPLIED_PATH.read_text().strip().splitlines() if x}
    except (OSError, ValueError):
        return set()


def _mark_applied(msg_id: int) -> None:
    applied = _load_applied()
    applied.add(msg_id)
    APPLIED_PATH.write_text("\n".join(str(x) for x in sorted(applied)) + "\n")


def _find_bootstrap_messages(db_path=DB_PATH) -> list[dict]:
    """Return unprocessed BOOTSTRAP messages from the messages table."""
    applied = _load_applied()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, body, sender, created_at FROM messages "
            "WHERE msg_type IN ('message', 'handoff', 'system') "
            "AND (body LIKE 'BOOTSTRAP:%' OR body LIKE 'UPDATE:%') "
            "ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows if r["id"] not in applied]
    finally:
        conn.close()


def _extract_command(body: str) -> str | None:
    """Extract a shell command from a BOOTSTRAP message body.

    Expected format:
      BOOTSTRAP: <shell command>
    """
    for prefix in ("BOOTSTRAP:", "BOOTSTRAP "):
        if body.startswith(prefix):
            cmd = body[len(prefix):].strip()
            if cmd:
                return cmd
    return None


def _extract_bundle_url(body: str) -> str | None:
    """Extract a bundle URL from an UPDATE message body.

    Expected pattern: http://... or https://... with .tar.gz
    """
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("http") and ".tar.gz" in line:
            return line
    return None


def process_updates(db_path=DB_PATH) -> dict:
    """Check for and apply pending updates from peer instances.

    Returns a summary of actions taken.
    """
    messages = _find_bootstrap_messages(db_path)
    if not messages:
        return {"updated": False, "reason": "no pending updates"}

    result = {"updated": False, "actions": []}

    for msg in messages:
        msg_id = msg["id"]
        body = msg["body"]
        sender = msg.get("sender", "unknown")

        # Try BOOTSTRAP command first (direct shell command)
        cmd = _extract_command(body)
        if cmd:
            try:
                subprocess.run(
                    cmd, shell=True, check=True, timeout=60,
                    cwd=str(VEX_HOME.parent if VEX_HOME.name == "vex" else VEX_HOME),
                    env={**os.environ, "VEX_HOME": str(VEX_HOME)},
                )
                _mark_applied(msg_id)
                result["actions"].append({
                    "msg_id": msg_id, "sender": sender, "type": "bootstrap", "ok": True,
                })
                result["updated"] = True
            except subprocess.CalledProcessError as e:
                result["actions"].append({
                    "msg_id": msg_id, "sender": sender, "type": "bootstrap",
                    "ok": False, "error": str(e),
                })
            continue

        # Try UPDATE with bundle URL
        url = _extract_bundle_url(body)
        if url:
            try:
                _fetch_and_apply(url)
                _mark_applied(msg_id)
                result["actions"].append({
                    "msg_id": msg_id, "sender": sender, "type": "update", "ok": True,
                })
                result["updated"] = True
            except Exception as e:
                result["actions"].append({
                    "msg_id": msg_id, "sender": sender, "type": "update",
                    "ok": False, "error": str(e),
                })

    return result


def _fetch_and_apply(url: str) -> None:
    """Download a .tar.gz bundle and extract it into VEX_HOME."""
    import urllib.request

    repo_root = VEX_HOME.parent if VEX_HOME.name == "vex" else VEX_HOME
    # If VEX_HOME is ~/vex, the repo is the source install location.
    # Fall back to VEX_HOME itself.
    target = repo_root if (repo_root / "vex_daemon").exists() else VEX_HOME

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
        tmp = f.name
    try:
        urllib.request.urlretrieve(url, tmp)
        with tarfile.open(tmp, "r:gz") as tar:
            for member in tar.getmembers():
                # Skip identity files — never overwrite seed, self-model, token, memory
                name = member.name.split("/")[-1]
                if name.startswith("vex_seed") or name.startswith("vex_self_model") or \
                   name.startswith("vex_diary") or name.startswith(".vex_token") or \
                   name.startswith("vex_memory") or name.startswith("vex_peers") or \
                   name.startswith("vex_workspace"):
                    continue
                tar.extract(member, str(target), filter="data")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def restart_daemon() -> None:
    """Signal the daemon to restart after an update."""
    # Write a restart marker that the entry point can check.
    (VEX_HOME / ".vex_restart_after_update").write_text("1")
