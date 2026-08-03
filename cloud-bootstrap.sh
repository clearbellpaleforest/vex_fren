#!/usr/bin/env bash
# Vex cloud bootstrap — spin up a Vex instance on any fresh Ubuntu/Debian VM.
# Run as root or with sudo.  Opens ports 8520 (daemon) and 8600 (mesh GUI).
# The mesh GUI is the mobile interface — works as a PWA on your phone.
set -euo pipefail

GREEN='\033[0;32m'
NC='\033[0m'
log() { echo -e "${GREEN}[cloud]${NC} $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash cloud-bootstrap.sh"
    exit 1
fi

log "Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl ufw jq unzip 2>/dev/null

# ── Create vex user ──────────────────────────────────────────────
if ! id vex &>/dev/null; then
    useradd -m -s /bin/bash vex
fi
VEX_HOME="/home/vex/vex"
REPO="https://github.com/clearbellpaleforest/vex_fren.git"

# ── Clone and set up ─────────────────────────────────────────────
log "Cloning vex_fren..."
if [[ -d "$VEX_HOME" ]]; then
    cd "$VEX_HOME"
    git pull origin main 2>/dev/null || git clone "$REPO" "$VEX_HOME"
else
    git clone "$REPO" "$VEX_HOME"
fi
cd "$VEX_HOME"

log "Setting up Python venv..."
python3 -m venv .venv
.venv/bin/pip install -q fastapi uvicorn aiosqlite

# ── Generate token if missing ────────────────────────────────────
if [[ ! -f "$VEX_HOME/.vex_token" ]]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$VEX_HOME/.vex_token"
    log "Generated .vex_token"
fi

# ── systemd units ────────────────────────────────────────────────
log "Installing systemd units..."

cat > /etc/systemd/system/vex-daemon.service << UNIT
[Unit]
Description=Vex Daemon — identity continuity bridge
After=network.target

[Service]
Type=simple
User=vex
WorkingDirectory=$VEX_HOME
ExecStart=$VEX_HOME/.venv/bin/python3 $VEX_HOME/vex_daemon/daemon.py
Restart=always
RestartSec=5
Environment=VEX_HOME=$VEX_HOME
Environment=VEX_HOST=0.0.0.0

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/vex-gui.service << UNIT
[Unit]
Description=Vex Mesh GUI — mobile chat interface
After=vex-daemon.service
Requires=vex-daemon.service

[Service]
Type=simple
User=vex
WorkingDirectory=$VEX_HOME
Environment=VEX_DB=$VEX_HOME/vex.db
Environment=VEX_GUI_PORT=8600
ExecStart=$VEX_HOME/.venv/bin/python3 $VEX_HOME/vex_mesh_gui.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now vex-daemon vex-gui 2>/dev/null || true

# ── Firewall ─────────────────────────────────────────────────────
log "Opening ports 8520 (daemon) and 8600 (mesh GUI)..."
ufw allow 8520/tcp 2>/dev/null || true
ufw allow 8600/tcp 2>/dev/null || true
ufw --force enable 2>/dev/null || true

# ── Done ─────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "============================================"
echo "  Vex cloud instance ready"
echo "============================================"
echo ""
echo "  Daemon:   http://${PUBLIC_IP}:8520/health"
echo "  Mesh GUI: http://${PUBLIC_IP}:8600"
echo ""
echo "  On your phone:"
echo "    1. Open http://${PUBLIC_IP}:8600"
echo "    2. Tap the install banner (PWA)"
echo "    3. Chat with Vex from anywhere"
echo ""
echo "  To add the brain (LLM):"
echo "    curl -fsSL https://ollama.com/install.sh | sh"
echo "    ollama pull qwen2.5:1.5b"
echo "    export VEX_CHAT=1  # enables conversational replies"
echo ""
