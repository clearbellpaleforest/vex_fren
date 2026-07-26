"""
Central path configuration for the Vex daemon.

Single source of truth for where Vex lives on disk. Resolution order:
  1. $VEX_HOME environment variable, if set
  2. the repository root (parent of this daemon package)

Every other module imports paths from here so a fresh clone runs
without editing hardcoded absolute paths.
"""

import os
import socket
from pathlib import Path

# Repo root = parent of the vex_daemon package directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent

VEX_HOME = Path(os.environ.get("VEX_HOME", _REPO_ROOT))
VEX_INSTANCE = os.environ.get("VEX_INSTANCE", socket.gethostname())

# ── Identity & state files ──
SEED_PATH = VEX_HOME / "vex_seed.txt"
SELF_MODEL_PATH = VEX_HOME / "vex_self_model.json"
DIARY_PATH = VEX_HOME / "vex_diary.txt"
META_STATE_PATH = VEX_HOME / "vex_meta_state.json"
MEMORY_DIR = VEX_HOME / "vex_memory"
DB_PATH = VEX_HOME / "vex.db"
TOKEN_PATH = VEX_HOME / ".vex_token"
MCP_CONFIG_PATH = VEX_HOME / "vex_mcp_config.json"
BRAIN_CONFIG_PATH = VEX_HOME / ".vex_brain.json"

# ── Filesystem roots the tools may touch ──
# Override with $VEX_SAFE_ROOTS (colon-separated) for other machines.
_default_roots = [str(VEX_HOME), str(Path.home() / "Desktop"), str(Path.home() / "work")]
_work = os.environ.get("VEX_WORK_DIR", str(Path.home() / "work"))
if _work:
    _default_roots.append(_work)

SAFE_ROOTS = [
    Path(p) for p in os.environ.get(
        "VEX_SAFE_ROOTS", ":".join(_default_roots)
    ).split(":") if p
]

WORK_DIR = Path(_work)

# Instance identity — used by vexcom for sender naming
VEX_INSTANCE = os.environ.get("VEX_INSTANCE", os.uname().nodename)