#!/usr/bin/env bash
# Vex smoke test driver — builds, launches, and drives the full stack.
# Run:  bash .claude/skills/run-vex/smoke.sh [--screenshot]
# Flavor: server + web GUI

set -euo pipefail
cd "$(dirname "$0")/../../.."

VEX_HOME="${VEX_HOME:-$PWD}"
TOKEN_FILE="$VEX_HOME/.vex_token"
LOGDIR="$VEX_HOME/logs"
mkdir -p "$LOGDIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "${GREEN}[ok]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${CYAN}[info]${NC} $*"; }

cleanup() {
    info "stopping daemon and GUI..."
    kill "$DAEMON_PID" 2>/dev/null || true
    kill "$GUI_PID" 2>/dev/null || true
    wait "$DAEMON_PID" 2>/dev/null || true
    wait "$GUI_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ── Build ─────────────────────────────────────────────────────────────

info "installing vex-daemon..."
pip install -e . -q 2>&1 | tail -1 || fail "pip install failed"
pass "package installed"

# ── Launch daemon ──────────────────────────────────────────────────────

info "starting daemon (port 8520)..."
VEX_HOST=127.0.0.1 VEX_HOME="$VEX_HOME" \
    nohup python3 -m vex_daemon.daemon > "$LOGDIR/daemon.log" 2>&1 &
DAEMON_PID=$!

for i in $(seq 1 20); do
    sleep 1
    if curl -sf http://localhost:8520/health > /dev/null 2>&1; then
        pass "daemon healthy"
        break
    fi
    if [ "$i" -eq 20 ]; then
        fail "daemon did not start — check $LOGDIR/daemon.log"
    fi
done

# ── Launch mesh GUI ────────────────────────────────────────────────────

info "starting mesh GUI (port 8600)..."
VEX_HOME="$VEX_HOME" VEX_DB="$VEX_HOME/vex.db" \
    nohup python3 "$VEX_HOME/vex_mesh_gui.py" > "$LOGDIR/mesh_gui.log" 2>&1 &
GUI_PID=$!
sleep 2

if curl -sf http://localhost:8600/ | grep -q '<title>Vex Mesh</title>'; then
    pass "mesh GUI serving HTML"
else
    fail "mesh GUI did not start — check $LOGDIR/mesh_gui.log"
fi

# ── Interact: send message via API ─────────────────────────────────────

TOKEN=$(cat "$TOKEN_FILE" 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
    fail "no token at $TOKEN_FILE — daemon may not have started properly"
fi

RESP=$(curl -s -X POST http://localhost:8520/message/send \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"from":"smoke-test","to":"broadcast","body":"Smoke test passing! ⚡","msg_type":"message"}')
if echo "$RESP" | grep -q '"ok":true'; then
    pass "message sent via API"
else
    fail "message send failed: $RESP"
fi

# ── Verify message in mesh ─────────────────────────────────────────────

sleep 1
MESH=$(curl -s http://localhost:8520/message/inbox?n=1 \
    -H "Authorization: Bearer $TOKEN")
if echo "$MESH" | grep -q "Smoke test passing"; then
    pass "message visible in inbox"
else
    fail "message not in inbox"
fi

# ── Screenshot (optional) ──────────────────────────────────────────────

if [ "${1:-}" = "--screenshot" ]; then
    if command -v npx &>/dev/null && npx playwright --version &>/dev/null 2>&1; then
        info "taking screenshot of mesh GUI..."
        SCREENSHOT_DIR="$VEX_HOME/.claude/skills/run-vex"
        cat > /tmp/vex_screenshot.js << 'EOFJS'
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('http://localhost:8600', { waitUntil: 'networkidle' });
  await page.waitForSelector('#log .row', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
  await page.screenshot({ path: process.argv[2], fullPage: false });
  await browser.close();
})();
EOFJS
        NODE_PATH=$(npm root 2>/dev/null || echo "") \
            node /tmp/vex_screenshot.js "$SCREENSHOT_DIR/mesh.png" 2>/dev/null && \
            pass "screenshot saved to $SCREENSHOT_DIR/mesh.png" || \
            info "screenshot skipped (no Playwright browsers installed — run: npx playwright install chromium)"
        rm -f /tmp/vex_screenshot.js
    else
        info "screenshot skipped (playwright not available — install: npm install playwright && npx playwright install chromium)"
    fi
fi

# ── Done ───────────────────────────────────────────────────────────────

echo ""
pass "All smoke tests passed. Vex is working."
