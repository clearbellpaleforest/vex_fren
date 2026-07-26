"""
fleet.py — Multi-repo status and operations for Vex.

Tracks all repos Vex cares about across the filesystem. Provides:
  vex fleet             — status overview of every repo
  vex pulse             — health check across all services
  vex db                — auto-detect DB backend, show schema
  vex ship <repo>       — stage, commit, push in one command
"""

import json
import os
import subprocess
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Repo registry ────────────────────────────────────────────────────────────

# Default fleet entries are relative to home. Override with VEX_FLEET env var
# (JSON map of name -> path), e.g.:
#   VEX_FLEET='{"fen":"/home/aldous/Desktop/fenemerge","vex":"/home/aldous/vex"}'
_DESKTOP = Path.home() / "Desktop"

def _load_fleet() -> dict[str, Path]:
    """Load fleet from env var or fall back to sensible defaults."""
    env = os.environ.get("VEX_FLEET")
    if env:
        try:
            return {k: Path(v) for k, v in json.loads(env).items()}
        except (json.JSONDecodeError, TypeError):
            pass
    # Sensible defaults — paths that might exist on this machine
    fleet = {}
    candidates = {
        "vex": _DESKTOP / "vex",
        "fen": _DESKTOP / "fenemerge",
        "town-records": Path.home() / "work" / "town-records",
        "town-records-pipeline": Path.home() / "work" / "town-records-pipeline",
        "town-records-pipeline-search": Path.home() / "work" / "town-records-pipeline-search",
    }
    for name, path in candidates.items():
        if (path / ".git").exists():
            fleet[name] = path
    return fleet


FLEET = _load_fleet()

SERVICES = [
    ("fen", "http://127.0.0.1:8000/health"),
    ("vex-daemon", "http://127.0.0.1:8520/health"),
    ("town-records-web", "http://127.0.0.1:8080/"),
    ("town-records-qdrant", "http://127.0.0.1:6333/collections"),
]


@dataclass
class RepoStatus:
    name: str
    path: str
    branch: str
    ahead: int
    behind: int
    dirty: bool
    dirty_files: list[str]
    last_commit: str
    last_commit_date: str


# ── Fleet ────────────────────────────────────────────────────────────────────

def _git(repo: Path, *args) -> str:
    """Run a git command in the repo, return stdout stripped."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo)] + list(args),
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def fleet_status() -> dict:
    """Return status for every tracked repo."""
    repos = {}
    for name, path in FLEET.items():
        if not (path / ".git").exists():
            repos[name] = {"error": "not a git repo"}
            continue

        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        ahead_behind = _git(path, "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD")
        ahead = behind = 0
        if "\t" in ahead_behind:
            parts = ahead_behind.split("\t")
            ahead = int(parts[0]) if parts[0] else 0
            behind = int(parts[1]) if parts[1] else 0

        dirty = bool(_git(path, "status", "--porcelain"))
        dirty_files = _git(path, "status", "--porcelain").split("\n")[:10] if dirty else []
        last_commit = _git(path, "log", "-1", "--format=%h %s")
        last_date = _git(path, "log", "-1", "--format=%ar")

        repos[name] = {
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "dirty": dirty,
            "dirty_files": dirty_files[:5],
            "last_commit": last_commit,
            "last_commit_date": last_date,
        }
    return repos


# ── Pulse ────────────────────────────────────────────────────────────────────

def pulse() -> dict:
    """Quick health check across all services."""
    import urllib.request
    import urllib.error

    results = {}
    for name, url in SERVICES:
        try:
            t0 = time.monotonic()
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            body = resp.read().decode()[:2000]
            ms = round((time.monotonic() - t0) * 1000)

            if "json" in resp.headers.get("content-type", ""):
                data = json.loads(body)
                status = data.get("status", str(data.get("ok", "unknown")))
                results[name] = {"up": True, "status": str(status), "latency_ms": ms}
            else:
                results[name] = {"up": True, "status": f"HTTP {resp.status}", "latency_ms": ms}
        except Exception as e:
            results[name] = {"up": False, "error": str(e)[:120]}

    return results


# ── DB Inspector ─────────────────────────────────────────────────────────────

def db_inspect(path: Optional[str] = None) -> dict:
    """Auto-detect DB backend and show schema info."""
    if path is None:
        # Check common locations (relative to home, cross-platform)
        candidates = [
            str(Path.home() / "vex" / "vex.db"),
            str(Path.home() / "Desktop" / "vex" / "vex.db"),
            str(Path.home() / "Desktop" / "fenemerge" / "fen_kernel.sqlite"),
            str(Path.home() / ".fen" / "fen_kernel.sqlite"),
        ]
        for c in candidates:
            if os.path.exists(c):
                path = c
                break

    if path is None:
        return {"error": "no database found"}

    result = {"path": path, "size_mb": round(os.path.getsize(path) / 1024 / 1024, 2)}

    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row

        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

        result["tables"] = {}
        for t in tables:
            name = t["name"]
            count = db.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
            result["tables"][name] = {"rows": count}

        db.close()
    except Exception as e:
        result["error"] = str(e)

    return result


# ── Ship ─────────────────────────────────────────────────────────────────────

def ship(repo_name: str, message: str) -> dict:
    """Stage all changes, commit, and push in one command.

    Args:
        repo_name: Key in FLEET dict (e.g. 'fen', 'vex', 'town-records')
        message: Commit message (multi-line OK)

    Returns:
        Dict with stdout/stderr for each step.
    """
    if repo_name not in FLEET:
        return {"error": f"unknown repo '{repo_name}'. Known: {', '.join(FLEET)}"}

    repo = FLEET[repo_name]
    if not (repo / ".git").exists():
        return {"error": f"{repo} is not a git repository"}

    result = {"repo": repo_name, "path": str(repo)}

    # Stage
    add = subprocess.run(
        ["git", "-C", str(repo), "add", "-A"],
        capture_output=True, text=True, timeout=30,
    )
    result["stage"] = {"ok": add.returncode == 0, "stderr": add.stderr[:200]}

    # Check if anything to commit
    status = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--quiet"],
        capture_output=True, timeout=10,
    )
    if status.returncode == 0:
        result["commit"] = {"ok": True, "note": "nothing to commit"}
        result["push"] = {"ok": True, "note": "nothing to push"}
        return result

    # Commit
    full_msg = f"{message}\n\nCo-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
    commit = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", full_msg],
        capture_output=True, text=True, timeout=30,
    )
    result["commit"] = {"ok": commit.returncode == 0, "stdout": commit.stdout.strip(), "stderr": commit.stderr[:200]}

    # Push
    push = subprocess.run(
        ["git", "-C", str(repo), "push", "origin", "master"],
        capture_output=True, text=True, timeout=60,
    )
    result["push"] = {"ok": push.returncode == 0, "stderr": push.stderr[:200]}

    return result
