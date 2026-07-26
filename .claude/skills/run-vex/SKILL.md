---
name: run-vex
description: Build, launch, and drive the Vex daemon and mesh GUI. Use for running the app, taking screenshots, smoke-testing APIs, or verifying changes end-to-end.
---

# Run Vex

Vex is a personal AI daemon (FastAPI, port 8520) with a web mesh GUI (stdlib HTTP server, port 8600).
The driver is a smoke test script that builds, launches, drives the API, and optionally takes a Playwright screenshot.

## Prerequisites

```bash
pip install -e .               # Python deps (fastapi, uvicorn, aiosqlite, mcp)
npm install playwright          # optional: for screenshots
npx playwright install chromium # optional: browser binary
```

## Build

```bash
pip install -e .
```

Installs the `vex-daemon` package from the local repo.

## Run (agent path)

The smoke test driver handles the full lifecycle — build, launch, interact, screenshot, stop:

```bash
# Smoke test only (API + mesh verification)
bash .claude/skills/run-vex/smoke.sh

# Smoke test with screenshot
bash .claude/skills/run-vex/smoke.sh --screenshot
```

**What it does:**
1. `pip install -e .` — build the package
2. Starts daemon in background on port 8520, waits for `/health`
3. Starts mesh GUI in background on port 8600, verifies HTML title
4. Sends a test message via `POST /message/send` with bearer auth
5. Verifies the message appears in `GET /message/inbox`
6. Optionally takes a Playwright screenshot → `.claude/skills/run-vex/mesh.png`
7. Stops both processes on exit (trap cleanup)

**Direct API interaction** (for PRs that touch backend only):

```bash
# Start daemon
VEX_HOST=127.0.0.1 nohup python3 -m vex_daemon.daemon > /tmp/daemon.log 2>&1 &
# Wait for health
curl -sf http://localhost:8520/health

# Send a message
TOKEN=$(cat ~/vex/.vex_token)
curl -s -X POST http://localhost:8520/message/send \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"from":"test","to":"broadcast","body":"hello","msg_type":"message"}'

# Read inbox
curl -s http://localhost:8520/message/inbox?n=5 \
  -H "Authorization: Bearer $TOKEN"

# Stop
kill %1
```

**Direct function invocation** (for PRs that touch internal modules):

```bash
# Import and test any module without launching the daemon
python3 -c "
from vex_daemon.seed_kernel import load_seed
from vex_daemon.self_model import load_model
from vex_daemon.recall import search

seed = load_seed()
model = load_model()
results = search('identity')
print(f'seed: {seed[\"name\"]}, model: {model[\"identity\"][\"name\"]}, recall: {len(results)} hits')
"
```

## Run (human path)

```bash
python3 -m vex_daemon.daemon &    # port 8520
python3 vex_mesh_gui.py &         # port 8600
open http://localhost:8600        # mesh chat in browser
```

Useless headless — the browser opens on the host, not in the container.

## Gotchas

- **Token file location:** `.vex_token` is created in `VEX_HOME` on first daemon start. If it doesn't exist, the daemon hasn't started successfully.
- **Port conflicts:** The smoke test doesn't kill existing processes on ports 8520/8600. Manually `pkill -f vex_daemon` if ports are in use.
- **Playwright browsers:** Must run `npx playwright install chromium` once before `--screenshot` works. The smoke test skips screenshot gracefully if not installed.
- **Daemon startup:** The daemon creates SQLite tables on first start. First launch takes ~2s longer than subsequent launches.
- **Mesh GUI reads from `vex.db`:** The daemon and GUI must share the same `VEX_HOME` (and thus the same `vex.db`) for messages to appear in the GUI.
- **The daemon has no brain:** `POST /ask` returns quickly but replies arrive asynchronously via the messages table. Don't expect synchronous replies from the API — poll `/message/inbox` instead.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: vex_daemon` | Run `pip install -e .` from repo root |
| `/health` returns connection refused | Daemon crashed. Check `logs/daemon.log` |
| Mesh GUI starts but shows no messages | Wrong `VEX_DB` path — must point to daemon's `vex.db` |
| `POST /message/send` returns 401 | Token mismatch. Check `.vex_token` or restart daemon fresh |
| Playwright screenshot blank | Mesh GUI hasn't loaded messages yet — increase `waitForTimeout` |
