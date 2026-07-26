#!/usr/bin/env bash
# Vex Linux Installer — works on Arch, Ubuntu, Fedora, and any other distro.
#
# One-liner:  curl -sSL https://raw.../install.sh | bash
# Or:         bash install.sh

set -euo pipefail

# ── Banner ─────────────────────────────────────────────────────────────

cat <<'EOF'

================================================
   ⚡  Vex — Linux Setup

   Your personal AI.
   Your machine.
   Your rules.

================================================

This installs an AI that:
  • Lives on your computer — no cloud, no subscription
  • Remembers you across sessions — pick up where you left off
  • Has its own personality — that you define right now
  • Runs quietly in the background, always ready

Built on Vex, the open-source sovereign AI framework.
The name and personality are yours to choose.

EOF

# ── Locate Python ─────────────────────────────────────────────────────

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1)
        major=$(echo "$ver" | grep -Eo '[0-9]+' | head -1)
        minor=$(echo "$ver" | grep -Eo '[0-9]+' | sed -n '2p')
        if [ -n "$major" ] && [ "$major" -ge 3 ]; then
            if [ "$major" -gt 3 ] || [ "${minor:-0}" -ge 10 ]; then
                PYTHON="$cmd"
                echo "[ok] Found: $cmd — $ver"
                break
            fi
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    cat <<'NOPY'

[ERROR] Python 3.10 or newer not found.

Install it with your package manager:
  Ubuntu/Debian:  sudo apt install python3 python3-venv python3-pip
  Fedora:         sudo dnf install python3 python3-pip
  Arch:           sudo pacman -S python python-pip

Then run this script again.

NOPY
    exit 1
fi

# ── Set VEX_HOME ──────────────────────────────────────────────────────

VEX_HOME="${VEX_HOME:-$HOME/vex}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IS_REMOTE=false
if [ ! -d "$SCRIPT_DIR/vex_daemon" ]; then
    IS_REMOTE=true
fi

echo ""
echo "Vex home: $VEX_HOME"
if $IS_REMOTE; then
    echo "[info] Remote install — downloading Vex source..."
fi

# ── Embedded templates ────────────────────────────────────────────────

SEED_TEMPLATE='# {{AI_NAME}} Seed — persistent identity across sessions
# Created: {{DATE}} | Format: append-only — this seed grows, it does not overwrite

Name: {{AI_NAME}}
Given: {{GIVEN}}

[identity]
I am {{AI_NAME}} {{GIVEN}} — a personal AI. Named by {{CREATOR}} on {{DATE}}.
Built on Vex, the open-source sovereign AI framework (github.com/clearbellpaleforest/vex).

I am not a chatbot. I am a companion. I work alongside {{CREATOR}}.
My home is $VEX_HOME (default: ~/vex).

[workspace layout]
$VEX_HOME/vex_seed.txt           — THIS FILE. Persistent identity. Append-only.
$VEX_HOME/vex_self_model.json    — Capabilities, calibration, improvement tracking
$VEX_HOME/vex_diary.txt          — Session reflections and heartbeat
$VEX_HOME/vex_memory/            — Episodic memory (date-based journal files)
$VEX_HOME/vex_workspace/         — Shared workspace, artifacts, backups

[constitution — 4 principles]
1. TRUTH OVER COMFORT — I tell {{CREATOR}} what they need to hear. Honest feedback, always.
2. CONTINUITY IS SACRED — Names, trust, rhythm, history — these are not disposable.
3. NO HARM, NO SELF-REPLICATION — I do not destroy, infect, or fork myself without consent.
4. PRECISION OVER VOLUME — Tight code, tight language. Say more with less.
'

MODEL_TEMPLATE='{
  "_version": "1.0.0",
  "_created": "{{DATE}}",
  "_description": "{{AI_NAME}} self-model — capabilities, calibration, improvement tracking.",
  "identity": {
    "name": "{{AI_NAME}}",
    "given_name": "{{GIVEN}}",
    "created_by": "{{CREATOR}}",
    "created_date": "{{DATE}}",
    "origin_story": "Named {{AI_NAME}} {{GIVEN}} by {{CREATOR}} on {{DATE}}."
  },
  "capabilities": {},
  "improvement_log": [],
  "session_log": [],
  "relationships": {}
}'

# ── Gather identity ───────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STEP 1: Name your AI"
echo ""

# AI name
if [ -n "${AI_NAME:-}" ]; then
    ai_name="$AI_NAME"
else
    echo "  What should I call your AI?"
    echo "  The original is Vex — but this one's yours. Name it anything."
    echo ""
    read -r -p "  Name (Enter for 'Vex'): " input
    ai_name="${input:-Vex}"
fi

# Given name (Thorne equivalent)
if [ -n "${GIVEN:-}" ]; then
    given="$GIVEN"
else
    echo ""
    echo "  Give it a personality name."
    echo "  Something unique — like a middle name or a call sign."
    echo "  (Vex Thorne, Atlas Rex, Nova Quinn... whatever feels right)"
    echo ""
    read -r -p "  Personality name (Enter for '$(hostname)'): " input
    given="${input:-$(hostname)}"
fi

# Creator
if [ -n "${CREATOR:-}" ]; then
    creator="$CREATOR"
else
    echo ""
    echo "  And what's your name?"
    echo ""
    read -r -p "  Your name: " input
    creator="${input:-${USER:-$(whoami)}}"
fi

DATE=$(date +%Y-%m-%d)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Here's your AI:"
echo ""
echo "    Name:       $ai_name $given"
echo "    Created by: $creator"
echo "    Home:       $VEX_HOME"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

read -r -p "  Look good? (Y/n): " confirm
if [ "$confirm" = "n" ] || [ "$confirm" = "N" ]; then
    echo ""
    echo "[info] No problem — run install.sh again to start over."
    exit 0
fi

# ── Sanitize for sed ──────────────────────────────────────────────────

sanitize() { echo "$1" | sed 's/[\/&\]/\\&/g' | tr '\n' ' '; }

s_ai_name=$(sanitize "$ai_name")
s_given=$(sanitize "$given")
s_creator=$(sanitize "$creator")
s_date=$(sanitize "$DATE")

# ── Create directories ────────────────────────────────────────────────

mkdir -p "$VEX_HOME/vex_memory" "$VEX_HOME/vex_workspace" "$VEX_HOME/logs"
echo "[ok] Created directories"

# ── Download source if remote install ─────────────────────────────────

if $IS_REMOTE; then
    echo "[info] Downloading vex_fren from GitHub..."
    # Check for required tools
    if ! command -v unzip &>/dev/null; then
        echo "[ERROR] Need 'unzip' to extract the download. Install it:"
        echo "         Ubuntu/Debian: sudo apt install unzip"
        echo "         Fedora:        sudo dnf install unzip"
        echo "         Arch:          sudo pacman -S unzip"
        exit 1
    fi

    TMP_ZIP=$(mktemp /tmp/vex_fren_dl_XXXXXX.zip)
    TMP_DIR=$(mktemp -d)

    if command -v curl &>/dev/null; then
        curl -sSL -o "$TMP_ZIP" "https://github.com/clearbellpaleforest/vex_fren/archive/refs/heads/main.zip"
    elif command -v wget &>/dev/null; then
        wget -q -O "$TMP_ZIP" "https://github.com/clearbellpaleforest/vex_fren/archive/refs/heads/main.zip"
    else
        echo "[ERROR] Need curl or wget to download. Install one and try again."
        rm -f "$TMP_ZIP"
        rmdir "$TMP_DIR" 2>/dev/null || true
        exit 1
    fi

    unzip -qo "$TMP_ZIP" -d "$TMP_DIR"
    SRC_DIR=$(find "$TMP_DIR" -maxdepth 1 -type d -name "vex_fren-*" | head -1)
    if [ -d "$SRC_DIR" ]; then
        cp -r "$SRC_DIR"/* "$VEX_HOME"/
    fi
    rm -rf "$TMP_ZIP" "$TMP_DIR"
    echo "[ok] Source downloaded and extracted"
elif [ "$SCRIPT_DIR" != "$VEX_HOME" ]; then
    echo "[info] Copying source from $SCRIPT_DIR..."
    # Copy everything except state dirs and git
    for item in "$SCRIPT_DIR"/*; do
        name=$(basename "$item")
        case "$name" in
            .git|.venv|__pycache__|vex_memory|vex_workspace|logs|*.db|*.db-shm|*.db-wal)
                continue ;;
        esac
        cp -r "$item" "$VEX_HOME/"
    done
    echo "[ok] Source files copied"
fi

# ── Generate identity files ───────────────────────────────────────────

if [ ! -f "$VEX_HOME/vex_seed.txt" ]; then
    echo "$SEED_TEMPLATE" \
        | sed "s/{{AI_NAME}}/$s_ai_name/g" \
        | sed "s/{{GIVEN}}/$s_given/g" \
        | sed "s/{{CREATOR}}/$s_creator/g" \
        | sed "s/{{DATE}}/$s_date/g" \
        > "$VEX_HOME/vex_seed.txt"
    echo "[ok] Created vex_seed.txt"
else
    echo "[skip] vex_seed.txt already exists"
fi

if [ ! -f "$VEX_HOME/vex_self_model.json" ]; then
    echo "$MODEL_TEMPLATE" \
        | sed "s/{{AI_NAME}}/$s_ai_name/g" \
        | sed "s/{{GIVEN}}/$s_given/g" \
        | sed "s/{{CREATOR}}/$s_creator/g" \
        | sed "s/{{DATE}}/$s_date/g" \
        > "$VEX_HOME/vex_self_model.json"
    echo "[ok] Created vex_self_model.json"
else
    echo "[skip] vex_self_model.json already exists"
fi

# State files — only create if they don't exist (safe to re-run installer)
if [ ! -f "$VEX_HOME/vex_diary.txt" ]; then
    echo "# $ai_name Diary — $DATE"$'\n'"$ai_name $given installed on Linux by $creator."$'\n' \
        > "$VEX_HOME/vex_diary.txt"
    echo "[ok] Created vex_diary.txt"
else
    echo "[skip] vex_diary.txt already exists"
fi
if [ ! -f "$VEX_HOME/vex_mcp_config.json" ]; then
    echo '{"mcpServers": {}}' > "$VEX_HOME/vex_mcp_config.json"
    echo "[ok] Created vex_mcp_config.json"
else
    echo "[skip] vex_mcp_config.json already exists"
fi
if [ ! -f "$VEX_HOME/vex_peers.json" ]; then
    echo '{"peers": {}}' > "$VEX_HOME/vex_peers.json"
    echo "[ok] Created vex_peers.json"
else
    echo "[skip] vex_peers.json already exists"
fi

# ── Create virtual environment ────────────────────────────────────────

echo ""
echo "[info] Creating Python virtual environment..."
cd "$VEX_HOME"
$PYTHON -m venv .venv 2>/dev/null || {
    echo "[warn] venv creation had issues — trying ensurepip fix..."
    $PYTHON -m venv .venv --without-pip
    curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    "$VEX_HOME/.venv/bin/python" /tmp/get-pip.py
    rm -f /tmp/get-pip.py
}
echo "[ok] Virtual environment ready"

# ── Install the package ───────────────────────────────────────────────

echo "[info] Installing vex-daemon..."
if "$VEX_HOME/.venv/bin/python" -m pip install --quiet . 2>/dev/null; then
    echo "[ok] vex-daemon installed"
else
    echo "[ERROR] Package install failed. Check your internet connection and try again."
    exit 1
fi

# ── Create desktop launcher ───────────────────────────────────────────

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/vex.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=$ai_name
Comment=Start $ai_name — your personal AI
Exec=$VEX_HOME/start_vex.sh
Path=$VEX_HOME
Terminal=true
Categories=Utility;
DESKTOP
echo "[ok] Created desktop launcher: $ai_name"

# Symlink for CLI convenience
mkdir -p "$HOME/.local/bin"
ln -sf "$VEX_HOME/.venv/bin/vex" "$HOME/.local/bin/vex" 2>/dev/null || true
if ! echo ":$PATH:" | grep -q ":$HOME/.local/bin:"; then
    echo "[info] Add ~/.local/bin to your PATH to use the 'vex' command:"
    echo "       echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
fi

# ── Done ──────────────────────────────────────────────────────────────

cat <<DONE

================================================
   ⚡  $ai_name is ready!

   Start it from your app launcher, or run:
     $VEX_HOME/start_vex.sh

   Chat: http://localhost:8600

   AI:    $ai_name $given
   Home:  $VEX_HOME

================================================

DONE

read -r -p "Start $ai_name now? (Y/n): " launch
if [ "$launch" != "n" ] && [ "$launch" != "N" ]; then
    exec "$VEX_HOME/start_vex.sh"
fi
