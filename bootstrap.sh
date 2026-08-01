#!/bin/bash
# Vex Bootstrap — one command, every machine, works.
# curl -sSL https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/bootstrap.sh | bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}═══ Vex Bootstrap ═══${NC}"
echo ""

# ── Detect OS ──
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    OS="unknown"
fi

VEX_HOME="${VEX_HOME:-$HOME/vex}"
mkdir -p "$VEX_HOME"

# ── Python ──
echo "→ Checking Python..."
if ! command -v python3 &>/dev/null; then
    echo "  Installing Python..."
    case "$OS" in
        arch)     sudo pacman -S --noconfirm python python-pip ;;
        fedora)   sudo dnf install -y python3 python3-pip ;;
        ubuntu|debian) sudo apt install -y python3 python3-pip python3-venv ;;
        *)        echo -e "${RED}  Unknown OS. Install Python manually.${NC}" && exit 1 ;;
    esac
fi
echo -e "  ${GREEN}✓ Python $(python3 --version)${NC}"

# ── Vex Daemon Deps ──
echo "→ Installing Vex dependencies..."
pip install fastapi uvicorn aiosqlite pydantic websockets 2>/dev/null | tail -1
echo -e "  ${GREEN}✓ Dependencies installed${NC}"

# ── Vex CLI (Rust binary) ──
echo "→ Installing Vex CLI..."
mkdir -p "$HOME/.local/bin"
if [ -f "$VEX_HOME/vex-cli/target/release/vex" ]; then
    cp "$VEX_HOME/vex-cli/target/release/vex" "$HOME/.local/bin/vex"
elif command -v cargo &>/dev/null && [ -f "$VEX_HOME/vex-cli/Cargo.toml" ]; then
    (cd "$VEX_HOME/vex-cli" && cargo build --release 2>/dev/null)
    cp "$VEX_HOME/vex-cli/target/release/vex" "$HOME/.local/bin/vex"
fi

if [ -f "$HOME/.local/bin/vex" ]; then
    chmod +x "$HOME/.local/bin/vex"
    echo -e "  ${GREEN}✓ Vex CLI installed${NC}"
else
    echo -e "  ${CYAN}  Vex CLI not built. Run: cd $VEX_HOME/vex-cli && cargo build --release${NC}"
fi

# ── Bashrc: claude-start + DeepSeek auto-load ──
BASHRC_BLOCK='
# ═══ Vex: claude-start + DeepSeek ═══
claude-start() {
    echo ""
    echo "⚡ Choose your API provider:"
    echo "  1) Claude / Anthropic"
    echo "  2) DeepSeek API"
    echo ""
    read -p "> " provider
    case "$provider" in
        1|claude|anthropic)
            unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL
            echo "→ Using Claude / Anthropic."
            ;;
        2|deepseek|ds)
            if [ -f ~/.deepseek_token ]; then
                export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
                export ANTHROPIC_AUTH_TOKEN="$(cat ~/.deepseek_token)"
                export ANTHROPIC_MODEL="deepseek-v4-pro"
                echo "→ Using DeepSeek API (v4-pro)."
            else
                echo "→ No ~/.deepseek_token found. Create one: echo sk-... > ~/.deepseek_token"
                return 1
            fi
            ;;
        *) echo "Invalid."; return 1 ;;
    esac
    echo ""
    command claude "$@"
}

# Auto-load DeepSeek if token exists (all terminals see this)
if [ -f ~/.deepseek_token ]; then
    export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
    export ANTHROPIC_AUTH_TOKEN="$(cat ~/.deepseek_token)"
    export ANTHROPIC_MODEL="deepseek-v4-pro"
fi
'

if ! grep -q "claude-start()" "$HOME/.bashrc" 2>/dev/null; then
    echo "$BASHRC_BLOCK" >> "$HOME/.bashrc"
    echo -e "  ${GREEN}✓ claude-start added to ~/.bashrc${NC}"
else
    echo -e "  ${CYAN}  claude-start already in ~/.bashrc${NC}"
fi

if ! grep -q "claude-start()" "$HOME/.zshrc" 2>/dev/null; then
    echo "$BASHRC_BLOCK" >> "$HOME/.zshrc" 2>/dev/null || true
fi

# ── DeepSeek token template ──
if [ ! -f "$HOME/.deepseek_token" ]; then
    echo "# Place your DeepSeek API key here: sk-..." > "$HOME/.deepseek_token"
    chmod 600 "$HOME/.deepseek_token"
    echo -e "  ${CYAN}  Created ~/.deepseek_token — add your key${NC}"
else
    echo -e "  ${GREEN}✓ ~/.deepseek_token exists${NC}"
fi

# ── Vex daemon systemd service ──
if command -v systemctl &>/dev/null; then
    SERVICE_FILE="$VEX_HOME/vex-daemon.service"
    if [ -f "$SERVICE_FILE" ]; then
        sudo cp "$SERVICE_FILE" /etc/systemd/system/ 2>/dev/null || true
        sudo systemctl daemon-reload 2>/dev/null || true
        echo -e "  ${GREEN}✓ Daemon service installed${NC}"
        echo "  Start: sudo systemctl enable --now vex-daemon"
    fi
fi

echo ""
echo -e "${GREEN}═══ Vex Bootstrap Complete ═══${NC}"
echo ""
echo "  vex          — CLI"
echo "  claude-start — API provider selector"
echo "  ~/.deepseek_token — your API key"
echo ""
echo "  Open a new terminal and run: source ~/.bashrc"
echo "  Then: vex check"
