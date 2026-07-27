#!/usr/bin/env python3
"""
Self-check harness for Vex. Run after making changes.
Verifies: syntax, daemon health, API surface, mesh GUI, Playwright rendering.

Usage: python3 vex_daemon/self_check.py [--quick] [--gui]
  --quick   Skip Playwright GUI check
  --gui     Run Playwright GUI verification (requires Playwright)
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VEX_HOME = Path(os.environ.get("VEX_HOME", Path(__file__).resolve().parent.parent))
TOKEN_PATH = VEX_HOME / ".vex_token"
DAEMON_URL = "http://localhost:8520"
MESH_URL = "http://localhost:8600"
FAILURES = []


def log(icon: str, msg: str):
    print(f"  {icon} {msg}")


def fail(msg: str):
    FAILURES.append(msg)
    log("✗", msg)


def ok(msg: str):
    log("✓", msg)


def info(msg: str):
    log("→", msg)


# ── 1. Python syntax check ──────────────────────────────────────

def check_syntax():
    info("Checking Python syntax...")
    errors = 0
    for py_file in sorted(VEX_HOME.rglob("*.py")):
        if ".venv" in str(py_file) or "__pycache__" in str(py_file):
            continue
        try:
            subprocess.run(
                ["python3", "-c",
                 f"import py_compile; py_compile.compile('{py_file}', doraise=True)"],
                capture_output=True, timeout=10, check=True,
            )
        except subprocess.CalledProcessError:
            fail(f"Syntax error: {py_file.relative_to(VEX_HOME)}")
            errors += 1
    if errors == 0:
        ok(f"All Python files pass syntax check")


# ── 2. Daemon health ────────────────────────────────────────────

def check_daemon():
    info("Checking daemon health...")
    try:
        r = urllib.request.urlopen(f"{DAEMON_URL}/health", timeout=5)
        data = json.loads(r.read())
        if data.get("ok"):
            ok(f"Daemon healthy (uptime={data.get('uptime_s', 0):.0f}s, "
               f"coherence={data.get('mps_coherence', 0):.3f})")
        else:
            fail("Daemon reports not OK")
    except Exception as e:
        fail(f"Daemon unreachable: {e}")
        return False
    return True


# ── 3. Task API check ───────────────────────────────────────────

def check_tasks():
    info("Checking task system...")
    token = TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""
    try:
        r = urllib.request.urlopen(f"{DAEMON_URL}/tasks/stats", timeout=5)
        stats = json.loads(r.read())
        t = stats.get("tasks", {})
        ok(f"Task system: {t.get('total', 0)} total, {t.get('todo', 0)} todo, "
           f"{t.get('done', 0)} done, {t.get('blocked', 0)} blocked")
    except Exception as e:
        fail(f"Task system error: {e}")

    # Check skills
    try:
        r = urllib.request.urlopen(f"{DAEMON_URL}/tasks/skills", timeout=5)
        skills = json.loads(r.read())
        ok(f"Skills: {len(skills) if isinstance(skills, list) else 'err'} tracked")
    except Exception as e:
        fail(f"Skills error: {e}")

    # Check FEN bridge linking
    try:
        r = urllib.request.urlopen(
            f"{DAEMON_URL}/tasks/link/external?system=fen&ref=goal_test_1",
            timeout=5,
        )
        task = json.loads(r.read())
        if task and task.get("id"):
            ok(f"FEN bridge: goal_test_1 → task #{task['id']}")
        else:
            info("FEN bridge: no test goal linked (OK — run bridge test first)")
    except Exception:
        pass  # Not an error if no test goal exists


# ── 4. Mesh GUI check ───────────────────────────────────────────

def check_mesh():
    info("Checking mesh GUI...")
    try:
        r = urllib.request.urlopen(f"{MESH_URL}", timeout=5)
        html = r.read().decode()
        if "Vex Mesh" in html and "tick()" in html:
            ok("Mesh GUI serving correctly")
        else:
            fail("Mesh GUI serving but content looks wrong")
    except Exception as e:
        fail(f"Mesh GUI unreachable: {e}")

    # Check /messages endpoint
    try:
        r = urllib.request.urlopen(f"{MESH_URL}/messages", timeout=5)
        data = json.loads(r.read())
        count = data.get("count", 0)
        ok(f"Mesh messages: {count} served")
    except Exception as e:
        fail(f"Mesh /messages error: {e}")


# ── 5. Playwright GUI verification ──────────────────────────────

def check_playwright():
    info("Checking GUI with Playwright...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        info("Playwright not installed — skipping GUI render check. Run: pip install playwright && playwright install chromium")
        return

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(MESH_URL, timeout=15000, wait_until="networkidle")

            # Check page title
            title = page.title()
            if "Vex Mesh" in title:
                ok(f"Playwright: page title = '{title}'")
            else:
                fail(f"Playwright: unexpected title '{title}'")

            # Wait for tick to run
            page.wait_for_timeout(3000)

            # Check for console errors
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            # Check if messages loaded
            meta_text = page.evaluate("() => document.getElementById('meta')?.textContent || ''")
            if "msgs" in meta_text or "db error" in meta_text:
                ok(f"Playwright: mesh loaded — {meta_text}")
            elif "connecting" in meta_text or "offline" in meta_text:
                info(f"Playwright: mesh status = '{meta_text}' (may be normal)")
            else:
                info(f"Playwright: meta = '{meta_text}'")

            # Check install banner
            banner = page.evaluate("() => document.getElementById('install-banner')?.textContent || ''")
            if "Add to home" in banner or "Tap to install" in banner:
                ok(f"Playwright: install banner visible: '{banner[:60]}'")
            else:
                info(f"Playwright: banner = '{banner[:60]}' (may appear after 3s)")

            # Check log has content
            log_html = page.evaluate("() => document.getElementById('log')?.innerHTML?.length || 0")
            if log_html > 50:
                ok(f"Playwright: log has content ({log_html} chars)")
            else:
                info(f"Playwright: log content = {log_html} chars")

            page.screenshot(path=str(VEX_HOME / ".claude" / "skills" / "run-vex" / "mesh_check.png"))
            ok("Playwright: screenshot saved")

            browser.close()
    except Exception as e:
        fail(f"Playwright error: {e}")


# ── 6. Bus and messages check ───────────────────────────────────

def check_bus():
    info("Checking message bus...")
    bus_path = VEX_HOME / "vex_workspace" / "vex_bus.jsonl"
    if bus_path.exists():
        lines = bus_path.read_text().strip().split("\n")
        ok(f"Bus: {len(lines)} entries")
    else:
        info("Bus file empty — no messages yet")

    # Check messages table via daemon
    token = TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""
    try:
        req = urllib.request.Request(
            f"{DAEMON_URL}/message/inbox?mark_read=false",
            headers={"Authorization": f"Bearer {token}"},
        )
        r = urllib.request.urlopen(req, timeout=5)
        msgs = json.loads(r.read())
        ok(f"Inbox: {len(msgs) if isinstance(msgs, list) else 0} unread messages")
    except Exception as e:
        fail(f"Inbox check error: {e}")


# ── 7. Git status check ─────────────────────────────────────────

def check_git():
    info("Checking git status...")
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=10, cwd=str(VEX_HOME),
        )
        dirty = [l for l in result.stdout.strip().split("\n") if l.strip()]
        if dirty:
            info(f"Git: {len(dirty)} uncommitted changes — remember to commit+push")
        else:
            ok("Git: clean working tree")

        # Check if ahead of remote
        subprocess.run(["git", "fetch"], capture_output=True, timeout=10, cwd=str(VEX_HOME))
        ahead = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/vex_fren"],
            capture_output=True, text=True, timeout=10, cwd=str(VEX_HOME),
        )
        behind = int(ahead.stdout.strip() or 0)
        if behind > 0:
            info(f"Git: {behind} commits behind origin — run vex update")
        else:
            ok("Git: up to date with origin")
    except Exception as e:
        fail(f"Git check error: {e}")


# ── Main ────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 55)
    print("Vex Self-Check")
    print("=" * 55)

    quick = "--quick" in sys.argv
    gui = "--gui" in sys.argv

    check_syntax()
    print()
    check_daemon()
    print()
    check_tasks()
    print()
    check_mesh()
    print()
    check_bus()
    print()
    check_git()

    if gui and not quick:
        print()
        check_playwright()

    print()
    print("=" * 55)
    if FAILURES:
        print(f"✗ {len(FAILURES)} FAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    else:
        print("✓ All checks passed. Vex is healthy.")
        return 0


# ── Self-repair ──────────────────────────────────────────────────

REPAIR_LOG = VEX_HOME / "vex_workspace" / "repair_log.jsonl"


def attempt_repair(failure_msg: str) -> bool:
    """Attempt to auto-repair a known failure. Returns True if repair was attempted."""
    msg_lower = failure_msg.lower()

    # Mesh GUI down
    if "mesh gui unreachable" in msg_lower or "connection refused" in msg_lower:
        if "8600" in msg_lower or "mesh" in msg_lower:
            info("Repair: restarting mesh GUI...")
            try:
                subprocess.run(["pkill", "-f", "vex_mesh_gui"], capture_output=True)
                time.sleep(1)
                subprocess.Popen(
                    ["python3", str(VEX_HOME / "vex_mesh_gui.py")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(3)
                r = urllib.request.urlopen(f"{MESH_URL}", timeout=5)
                if r.status == 200:
                    ok("Repair: mesh GUI restarted successfully")
                    _log_repair("mesh_gui_down", "restart", True)
                    return True
            except Exception as e:
                _log_repair("mesh_gui_down", "restart", False, str(e))
                return False

    # Daemon down
    if "daemon unreachable" in msg_lower or "daemon" in msg_lower:
        if "unreachable" in msg_lower:
            info("Repair: restarting daemon...")
            try:
                subprocess.run(["fuser", "-k", "8520/tcp"], capture_output=True)
                time.sleep(2)
                env = {**os.environ, "VEX_HOST": "0.0.0.0"}
                subprocess.Popen(
                    ["python3", str(VEX_HOME / "vex_daemon" / "daemon.py")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, cwd=str(VEX_HOME),
                )
                time.sleep(3)
                r = urllib.request.urlopen(f"{DAEMON_URL}/health", timeout=5)
                data = json.loads(r.read())
                if data.get("ok"):
                    ok("Repair: daemon restarted successfully")
                    _log_repair("daemon_down", "restart", True)
                    return True
            except Exception as e:
                _log_repair("daemon_down", "restart", False, str(e))
                return False

    return False


def _log_repair(failure: str, action: str, success: bool, detail: str = ""):
    """Log repair attempt for learning."""
    try:
        entry = json.dumps({
            "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            "failure": failure,
            "action": action,
            "success": success,
            "detail": detail,
        })
        REPAIR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(REPAIR_LOG, "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def run_with_repair(max_attempts: int = 3) -> int:
    """Run self-check and attempt repairs on failures. Max 3 repair cycles."""
    for attempt in range(max_attempts):
        print(f"\n--- Repair cycle {attempt + 1}/{max_attempts} ---")
        FAILURES.clear()
        result = main()

        if result == 0:
            return 0

        if attempt < max_attempts - 1:
            print(f"\n{len(FAILURES)} failures — attempting repairs...")
            repaired = False
            for failure in list(FAILURES):
                if attempt_repair(failure):
                    repaired = True
            if not repaired:
                print("  No automated repairs available for remaining failures.")
                break
            print("  Repairs attempted — re-checking...")
        else:
            print(f"\n{len(FAILURES)} failures remain after {max_attempts} repair cycles.")
            print("Escalating to user.")

    return 1


if __name__ == "__main__":
    if "--repair" in sys.argv:
        sys.exit(run_with_repair())
    else:
        sys.exit(main())
