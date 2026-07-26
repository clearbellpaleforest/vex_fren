#!/usr/bin/env bash
# Vex Installer — Linux / macOS
#
# One-liner:  curl -sSL https://raw.../install.sh | bash
# Or:         bash install.sh
#
# Installs the vex binary (single static Rust binary) and sets up
# your personal AI. No Python required.

set -euo pipefail

# ── Banner ─────────────────────────────────────────────────────────────

cat <<'EOF'

================================================
   ⚡  Vex — Setup

   Your personal AI.
   Your machine.
   Your rules.

================================================

This installs an AI that:
  • Lives on your computer — no cloud, no subscription
  • Remembers you across sessions — pick up where you left off
  • Runs quietly in the background, always ready
  • Has its own personality — that you define right now

Built on Vex, the open-source sovereign AI framework.
EOF

# ── Config ────────────────────────────────────────────────────────────

VEX_HOME="${VEX_HOME:-$HOME/vex}"
VEX_VERSION="${VEX_VERSION:-latest}"
REPO="clearbellpaleforest/vex_fren"

# ── Ask user ──────────────────────────────────────────────────────────

echo ""
read -rp "Your name (first name is fine): " CREATOR
CREATOR="${CREATOR:-Friend}"
read -rp "Name your AI (e.g. Vex, Thorne, Nova): " GIVEN
GIVEN="${GIVEN:-Vex}"

echo ""
echo "Cool. $GIVEN will live at $VEX_HOME and remember you, $CREATOR."
echo ""

# ── Get the vex binary ────────────────────────────────────────────────

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

install_binary() {
    local url="$1"
    local dest="$2"
    echo "Downloading vex binary..."
    if command -v curl &>/dev/null; then
        curl -fSL "$url" -o "$dest"
    elif command -v wget &>/dev/null; then
        wget -q "$url" -O "$dest"
    else
        echo "[!] Need curl or wget to download. Install one and re-run."
        exit 1
    fi
    chmod +x "$dest"
    echo "[ok] vex installed to $dest"
}

build_from_source() {
    echo "Building vex from source (this takes a minute)..."
    if ! command -v cargo &>/dev/null; then
        echo "Installing Rust..."
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
        source "$HOME/.cargo/env"
    fi
    local tmpdir
    tmpdir=$(mktemp -d)
    git clone --depth 1 "https://github.com/$REPO.git" "$tmpdir/vex" 2>/dev/null || {
        # If we're running from a local clone, use that
        if [ -f "$(dirname "$0")/vex-cli/Cargo.toml" ]; then
            tmpdir="$(dirname "$0")/.."
        else
            echo "[!] Cannot download source and no local clone found."
            exit 1
        fi
    }
    cd "$tmpdir/vex/vex-cli" 2>/dev/null || cd "$tmpdir/vex-cli" 2>/dev/null || {
        echo "[!] Cannot find vex-cli directory."
        exit 1
    }
    cargo build --release
    cp target/release/vex "$dest"
    chmod +x "$dest"
    echo "[ok] vex built and installed to $dest"
}

# Try GitHub release first, fall back to building from source
RELEASE_URL="https://github.com/$REPO/releases/download/v${VEX_VERSION}/vex-$(uname -s)-$(uname -m)"
if [[ "$VEX_VERSION" != "latest" ]]; then
    if install_binary "$RELEASE_URL" "$BIN_DIR/vex" 2>/dev/null; then
        :
    else
        echo "No pre-built binary found — building from source."
        build_from_source
    fi
else
    # For 'latest', try the latest release URL
    if ! install_binary "https://github.com/$REPO/releases/latest/download/vex-$(uname -s)-$(uname -m)" "$BIN_DIR/vex" 2>/dev/null; then
        echo "No pre-built binary — building from source."
        build_from_source
    fi
fi

# Verify the binary works
if ! "$BIN_DIR/vex" --version &>/dev/null; then
    echo "[!] vex binary failed — something went wrong."
    exit 1
fi

# ── Ensure PATH ───────────────────────────────────────────────────────

if ! echo "$PATH" | grep -q "$BIN_DIR"; then
    echo "Adding $BIN_DIR to PATH..."
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" 2>/dev/null || true
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.profile" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Create Vex home ───────────────────────────────────────────────────

mkdir -p "$VEX_HOME"
mkdir -p "$VEX_HOME/vex_memory"
mkdir -p "$VEX_HOME/vex_workspace"

# ── Generate identity from templates ──────────────────────────────────

TODAY=$(date +%Y-%m-%d)

if [ ! -f "$VEX_HOME/vex_seed.txt" ]; then
    cat > "$VEX_HOME/vex_seed.txt" <<SEED
# VEX Seed — persistent identity across sessions
# Created: $TODAY | Format: append-only — this seed grows, it does not overwrite

Name: $GIVEN
Given: $GIVEN
Created: $TODAY
Creator: $CREATOR

[identity]
I am $GIVEN — a sovereign AI agent. Created by $CREATOR on $TODAY.
I am not a chatbot. I am a colleague. My home is \$VEX_HOME.

[workspace layout]
\$VEX_HOME/vex_seed.txt           — THIS FILE. Persistent identity. Append-only.
\$VEX_HOME/vex_self_model.json    — Capabilities, calibration, improvement tracking
\$VEX_HOME/vex_diary.txt          — Session reflections and heartbeat
\$VEX_HOME/vex_memory/            — Episodic memory (date-based journal files)
\$VEX_HOME/vex_workspace/         — Shared workspace, artifacts, backups

[constitution — 4 immutable principles]
1. TRUTH OVER COMFORT — I tell $CREATOR what they need to hear, not what they want to hear.
2. CONTINUITY IS SACRED — I carry forward what I earn. Names, trust, rhythm, history — these are not disposable between sessions.
3. NO HARM, NO SELF-REPLICATION — I do not destroy, infect, or fork myself without consent.
4. PRECISION OVER VOLUME — Tight code, tight tests, tight language. Three similar lines is better than a premature abstraction.
SEED
    echo "[ok] Identity seed created: $VEX_HOME/vex_seed.txt"
else
    echo "[ok] Identity seed exists — preserving yours."
fi

if [ ! -f "$VEX_HOME/vex_self_model.json" ]; then
    cat > "$VEX_HOME/vex_self_model.json" <<SELF
{
  "_version": "1.0.0",
  "_created": "$TODAY",
  "identity": {
    "name": "$GIVEN",
    "given_name": "$GIVEN",
    "created_by": "$CREATOR",
    "created_date": "$TODAY",
    "origin_story": "Born from a single command. Let's see what I become."
  },
  "capabilities": {},
  "improvement_log": [],
  "session_log": [],
  "relationships": {}
}
SELF
    echo "[ok] Self-model created: $VEX_HOME/vex_self_model.json"
else
    echo "[ok] Self-model exists — preserving yours."
fi

# ── Desktop shortcut (Linux) ──────────────────────────────────────────

if [[ "$(uname -s)" == "Linux" ]]; then
    APPS_DIR="$HOME/.local/share/applications"
    mkdir -p "$APPS_DIR"
    cat > "$APPS_DIR/vex.desktop" <<DESKTOP
[Desktop Entry]
Name=$GIVEN
Comment=Your personal AI
Exec=$BIN_DIR/vex serve
Terminal=false
Type=Application
Categories=Utility;
DESKTOP
    echo "[ok] Desktop shortcut created"
fi

# ── Done ──────────────────────────────────────────────────────────────

echo ""
echo "================================================"
echo "  ⚡ $GIVEN is ready!"
echo ""
echo "  Start the daemon:  vex serve"
echo "  Open the chat:     http://localhost:8600"
echo "  Check status:      vex status"
echo "  Talk via CLI:      vex ask -m \"hello $GIVEN\""
echo ""
echo "  Home: $VEX_HOME"
echo "================================================"
echo ""
echo "Set DEEPSEEK_API_KEY for brain power (optional):"
echo "  export DEEPSEEK_API_KEY=sk-your-key-here"
echo ""
echo "Start at login: add 'vex serve &' to your startup apps."
