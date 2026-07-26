"""
Vex Tools — hands and capabilities for the daemon.

Gives Vex the ability to read files, check git repos, run safe commands,
and interact with the world beyond localhost:8520.

Used by the dream engine for project check-ins, by metacognition for
evidence gathering, and via vex ask for manual queries.
"""

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import SAFE_ROOTS, WORK_DIR
import playwright_tools

# Cap a single file read so a giant file can't OOM the daemon.
_MAX_READ_BYTES = 1_000_000  # 1 MB


def _is_safe_path(path: Path) -> bool:
    """Check if path is within allowed roots.

    Uses path-component containment, not string prefix — otherwise
    a root like ~/work would also match ~/work-secrets.
    Resolves symlinks before the check, so a link pointing outside
    the roots is rejected.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for r in SAFE_ROOTS:
        try:
            root = r.resolve()
        except OSError:
            continue
        if resolved == root or root in resolved.parents:
            return True
    return False


def read_file(path: str, max_lines: int = 200) -> dict:
    """Read a file within allowed paths. Returns {ok, content, error}."""
    max_lines = max(1, min(int(max_lines), 10_000))
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return {"ok": False, "error": f"Path not in allowed roots: {path}"}
    if not p.exists():
        return {"ok": False, "error": f"File not found: {path}"}
    if p.is_dir():
        return {"ok": False, "error": f"Path is a directory: {path}"}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read(_MAX_READ_BYTES + 1)
        size_capped = len(raw) > _MAX_READ_BYTES
        raw = raw[:_MAX_READ_BYTES]
        lines = raw.splitlines()
        truncated = size_capped or len(lines) > max_lines
        return {
            "ok": True,
            "path": str(p),
            "lines": len(lines),
            "truncated": truncated,
            "content": "\n".join(lines[:max_lines]),
        }
    except OSError as e:
        return {"ok": False, "error": str(e)}


def list_directory(path: str) -> dict:
    """List directory contents within allowed paths."""
    p = Path(path).expanduser()
    if not _is_safe_path(p):
        return {"ok": False, "error": f"Path not in allowed roots: {path}"}
    if not p.exists():
        return {"ok": False, "error": f"Directory not found: {path}"}
    if not p.is_dir():
        return {"ok": False, "error": f"Not a directory: {path}"}
    try:
        entries = []
        for child in sorted(p.iterdir()):
            entry = {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
            }
            if child.is_file():
                try:
                    entry["size"] = child.stat().st_size
                except OSError:
                    pass
            entries.append(entry)
        return {"ok": True, "path": str(p), "entries": entries}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def git_status(repo_path: str) -> dict:
    """Run git status in a repository. Returns parsed status info."""
    p = Path(repo_path).expanduser()
    if not _is_safe_path(p):
        return {"ok": False, "error": f"Path not in allowed roots: {repo_path}"}

    try:
        result = subprocess.run(
            ["git", "-C", str(p), "status", "--porcelain", "-b"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip()}

        lines = result.stdout.splitlines()
        branch = ""
        if lines and lines[0].startswith("## "):
            branch = lines[0][3:].split("...")[0].strip()
            lines = lines[1:]

        staged = [l for l in lines if l[0] != " " and l[1] != "?"]
        unstaged = [l for l in lines if l[1] != " " and l[0] != "?"]
        untracked = [l for l in lines if l.startswith("??")]

        return {
            "ok": True,
            "path": str(p),
            "branch": branch,
            "staged": len(staged),
            "unstaged": len(unstaged),
            "untracked": len(untracked),
            "dirty": len(lines) > 0,
            "sample": lines[:10] if lines else [],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git status timed out"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def git_log(repo_path: str, n: int = 5) -> dict:
    """Get recent git log entries."""
    p = Path(repo_path).expanduser()
    if not _is_safe_path(p):
        return {"ok": False, "error": f"Path not in allowed roots: {repo_path}"}

    try:
        result = subprocess.run(
            ["git", "-C", str(p), "log", f"-{n}", "--oneline", "--decorate"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip()}

        return {
            "ok": True,
            "path": str(p),
            "commits": [l.strip() for l in result.stdout.splitlines() if l.strip()],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git log timed out"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def discover_projects() -> dict:
    """Find all git repositories under the work dir and report their status."""
    work = WORK_DIR
    projects = []

    if not work.exists():
        return {"ok": False, "error": f"work dir not found: {work}"}

    for child in sorted(work.iterdir()):
        if not child.is_dir():
            continue
        git_dir = child / ".git"
        if git_dir.exists():
            status = git_status(str(child))
            projects.append({
                "name": child.name,
                "path": str(child),
                "status": status,
            })

    return {
        "ok": True,
        "project_count": len(projects),
        "projects": projects,
    }


def run_tool(tool_name: str, **kwargs) -> dict:
    """Dispatch a tool call by name. Safe entry point for API/dream engine."""
    tools = {
        "read_file": read_file,
        "list_directory": list_directory,
        "git_status": git_status,
        "git_log": git_log,
        "discover_projects": discover_projects,
        "playwright_screenshot": playwright_tools.screenshot,
        "playwright_text": playwright_tools.get_text,
        "playwright_check_links": playwright_tools.check_links,
    }

    if tool_name not in tools:
        return {"ok": False, "error": f"Unknown tool: {tool_name}. Available: {list(tools)}"}

    return tools[tool_name](**kwargs)
