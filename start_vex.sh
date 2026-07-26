#!/usr/bin/env bash
# Vex Linux Launcher — starts daemon + mesh GUI + opens browser.
# Run:  ./start_vex.sh
# Or find it in your app launcher (after install.sh creates the .desktop file).

set -euo pipefail

VEX_HOME="${VEX_HOME:-$HOME/vex}"
PYTHON="$VEX_HOME/.venv/bin/python"
export VEX_DB="$VEX_HOME/vex.db"
DAEMON_PORT="${VEX_PORT:-8520}"
GUI_PORT="${VEX_GUI_PORT:-8600}"

echo ""
echo "   ⚡  Vex — Starting up..."
echo ""

# ── Check Python exists ──────────────────────────────────────────────

if [ ! -x "$PYTHON" ]; then
    echo "[ERROR] Python venv not found at $PYTHON"
    echo "Run install.sh first."
    exit 1
fi

# ── Kill any existing processes on our ports ─────────────────────────

for port in "$DAEMON_PORT" "$GUI_PORT"; do
    pid=$(lsof -ti ":$port" 2>/dev/null || fuser "$port/tcp" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null || true
    fi
done
sleep 1

# ── Start the daemon ─────────────────────────────────────────────────

echo "[1/3] Starting Vex daemon (port $DAEMON_PORT)..."
nohup "$PYTHON" -m vex_daemon.daemon >> "$VEX_HOME/logs/daemon.log" 2>&1 &
DAEMON_PID=$!

# Wait for daemon to be ready
for i in $(seq 1 15); do
    sleep 1
    if curl -sf "http://localhost:$DAEMON_PORT/health" > /dev/null 2>&1; then
        echo "   Daemon ready (health: ok)"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "   [warn] Daemon may still be starting — continuing..."
    fi
done

# ── Start mesh GUI ───────────────────────────────────────────────────

echo "[2/3] Starting mesh GUI (port $GUI_PORT)..."
nohup "$PYTHON" "$VEX_HOME/vex_mesh_gui.py" >> "$VEX_HOME/logs/mesh_gui.log" 2>&1 &
GUI_PID=$!
sleep 2

# ── Open browser ─────────────────────────────────────────────────────

MESH_URL="http://localhost:$GUI_PORT"
echo "[3/3] Opening mesh chat..."
if command -v xdg-open &>/dev/null; then
    xdg-open "$MESH_URL" 2>/dev/null || true
elif command -v open &>/dev/null; then
    open "$MESH_URL" 2>/dev/null || true
fi

# ── Status ───────────────────────────────────────────────────────────

cat <<STATUS

================================================
   ⚡  Vex is running!

   Mesh chat: $MESH_URL
   Daemon:    http://localhost:$DAEMON_PORT
   Home:      $VEX_HOME

   Press Ctrl+C to stop.

================================================

STATUS

# ── Watchdog loop ─────────────────────────────────────────────────────

cleanup() {
    echo ""
    echo "Stopping Vex..."
    kill "$DAEMON_PID" 2>/dev/null || true
    kill "$GUI_PID" 2>/dev/null || true
    echo "Vex stopped."
    exit 0
}
trap cleanup INT TERM HUP EXIT

while true; do
    sleep 30
    if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
        ts=$(date +%H:%M:%S)
        echo "[$ts] daemon: DOWN — restarting..."
        # Kill anything on the port
        pid=$(lsof -ti ":$DAEMON_PORT" 2>/dev/null || fuser "$DAEMON_PORT/tcp" 2>/dev/null || true)
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
        sleep 1
        nohup "$PYTHON" -m vex_daemon.daemon >> "$VEX_HOME/logs/daemon.log" 2>&1 &
        DAEMON_PID=$!
    fi
done
