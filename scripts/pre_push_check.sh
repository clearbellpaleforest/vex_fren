#!/usr/bin/env bash
# pre_push_check.sh — quality gates that block push if any fail.
# Run before `git push`. Exits 0 (allow push) or 1 (block).

set -euo pipefail
cd "$(dirname "$0")/.."

VERBOSE="${VERBOSE:-0}"
FAILED=0
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}PASS${NC} $*"; }
fail() { echo -e "  ${RED}FAIL${NC} $*"; FAILED=1; }
warn() { echo -e "  ${YELLOW}WARN${NC} $*"; }

# ── Discover changed files ──────────────────────────────────────

CHANGED=$(git diff --cached --name-only 2>/dev/null || git diff --name-only 2>/dev/null)
if [ -z "$CHANGED" ]; then
    CHANGED=$(git diff --name-only HEAD~1 2>/dev/null || echo "")
fi

PY_FILES=$(echo "$CHANGED" | grep '\.py$' || true)
RS_FILES=$(echo "$CHANGED" | grep '\.rs$' || true)
SH_FILES=$(echo "$CHANGED" | grep '\.sh$' || true)

# Load baseline of known pre-existing violations
BASELINE="scripts/quality_baseline.json"

echo ""
echo "═══ Vex Quality Gate ═══"
echo "Changed: $(echo "$CHANGED" | wc -l) files ($(echo "$PY_FILES" | grep -c . || echo 0) .py, $(echo "$RS_FILES" | grep -c . || echo 0) .rs)"
echo ""

# ── Gate 1: Python Syntax ───────────────────────────────────────

if [ -n "$PY_FILES" ]; then
    echo "── Python syntax ──"
    for f in $PY_FILES; do
        if python3 -c "import ast; ast.parse(open('$f').read())" 2>/dev/null; then
            [ "$VERBOSE" = "1" ] && pass "$f"
        else
            fail "$f — syntax error"
        fi
    done
    echo ""
fi

# ── Gate 2: Rust Check ──────────────────────────────────────────

if [ -n "$RS_FILES" ]; then
    echo "── Rust check ──"
    if cargo check --manifest-path vex-cli/Cargo.toml 2>&1 | tail -3; then
        pass "cargo check"
    else
        fail "cargo check"
    fi
    echo ""
fi

# ── Gate 3: Error Audit ─────────────────────────────────────────

echo "── Error audit ──"
VIOLATIONS="$TMPDIR/error_violations.txt"
> "$VIOLATIONS"

# Load baseline exemptions
BASELINE_VIOLATIONS=""
if [ -f "$BASELINE" ]; then
    BASELINE_VIOLATIONS=$(python3 -c "
import json
with open('$BASELINE') as f:
    d = json.load(f)
for v in d.get('exemptions',[]):
    print(f\"{v['file']}:{v['line']}:{v['pattern']}\")
" 2>/dev/null || echo "")
fi

for f in $PY_FILES $SH_FILES; do
    [ -f "$f" ] || continue
    lineno=0
    while IFS= read -r line; do
        lineno=$((lineno + 1))
        # Match bare except: or except Exception: pass (or any bare pass after except)
        if echo "$line" | grep -qE '^\s*except\s*:' && echo "$line" | grep -qE '(pass|\.\.\.)\s*$'; then
            key="$f:$lineno:bare_except_pass"
            if ! echo "$BASELINE_VIOLATIONS" | grep -qF "$key"; then
                echo "$key" >> "$VIOLATIONS"
                fail "$f:$lineno — bare except: pass (no logging)"
            fi
        fi
        if echo "$line" | grep -qE '^\s*except\s+Exception\s*:' && echo "$line" | grep -qE 'pass\s*$'; then
            key="$f:$lineno:except_exception_pass"
            if ! echo "$BASELINE_VIOLATIONS" | grep -qF "$key"; then
                # Check next line for logger or note
                echo "$key" >> "$VIOLATIONS"
                fail "$f:$lineno — except Exception: pass (silent swallow)"
            fi
        fi
    done < "$f"
done

if [ ! -s "$VIOLATIONS" ]; then
    pass "no silent error handlers in changed files"
fi
echo ""

# ── Gate 4: Import Sanity (daemon) ──────────────────────────────

if echo "$CHANGED" | grep -q "vex_daemon/"; then
    echo "── Import sanity ──"
    if python3 -c "from vex_daemon.daemon import app" 2>/dev/null; then
        pass "daemon imports clean"
    else
        fail "daemon imports broken"
    fi
    echo ""
fi

# ── Gate 5: API Live Check ──────────────────────────────────────

if echo "$CHANGED" | grep -q "vex_daemon/daemon.py\|vex_daemon/heartbeat.py\|vex_daemon/temporal"; then
    echo "── API live ──"
    if curl -sf --max-time 3 http://localhost:8520/health > /dev/null 2>&1; then
        pass "daemon responding on :8520"
    else
        warn "daemon not reachable — restart and recheck before push"
    fi
    echo ""
fi

# ── Gate 6: Commit Message ──────────────────────────────────────

echo "── Commit message ──"
if git log --format=%B -1 2>/dev/null | grep -qE '^.{10,}$' && git log --format=%B -1 2>/dev/null | wc -l | xargs -I{} test {} -ge 2; then
    pass "commit has body"
else
    fail "commit message needs body explaining WHY (not just subject line)"
fi
echo ""

# ── Result ──────────────────────────────────────────────────────

echo "══════════════════════════"
if [ "$FAILED" -eq 0 ]; then
    echo -e "${GREEN}All gates passed. Safe to push.${NC}"
    echo ""

    # Log success
    mkdir -p vex_workspace
    COMMIT_HASH=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    echo "{\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"commit\":\"$COMMIT_HASH\",\"gates_passed\":true,\"files_checked\":$(echo "$CHANGED" | wc -l)}" >> vex_workspace/quality_log.jsonl

    exit 0
else
    echo -e "${RED}$FAILED gate(s) failed. Fix them before pushing.${NC}"
    echo -e "${YELLOW}Existing known violations: run with VERBOSE=1 to see baseline skips.${NC}"
    echo ""
    exit 1
fi
