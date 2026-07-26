# Vex

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**A continuity daemon for sovereign AI agents.** Vex is a small FastAPI service that gives an
agent a persistent identity, memory, and self-model that survive across sessions — so it starts
each session knowing who it is, what it has done, and what it is capable of.

It runs alongside a coding agent (such as Claude Code), exposes a local HTTP API and CLI, and can
federate with other Vex instances across machines for shared awareness and messaging.

## Features

- **Persistent identity** — an append-only seed and a calibrated self-model, loaded on every start.
- **Episodic memory** — session summaries with full-history FTS5 search and coverage-first recall.
- **Metacognition** — a background heartbeat that tracks coherence and drift and writes reflective diary entries.
- **Multi-instance federation** — a peer registry, authenticated messaging, `/poke`-driven inbox processing, and automatic replies between instances.
- **Tooling** — sandboxed filesystem tools confined to configured roots, an optional MCP client, and optional Playwright web tools.
- **Bundle transfer** — export and import code bundles between instances, with identity files always preserved.

## Windows Install (One Command)

Open **PowerShell** (right-click Start, select "Windows PowerShell" or "Terminal") and paste:

```powershell
irm https://raw.githubusercontent.com/clearbellpaleforest/vex/vex_fren/install.ps1 | iex
```

When it finishes, double-click **Vex** on your desktop to start.
That's it. The mesh chat opens in your browser at `http://localhost:8600`.

### Prerequisites
- **Python 3.10 or newer** — [Download from python.org](https://www.python.org/downloads/)
  (Check "Add Python to PATH" during install)

---

## Linux / macOS Install

```bash
git clone https://github.com/clearbellpaleforest/vex.git
cd vex
CREATOR="Your Name" bash setup.sh
```

Requires Python ≥ 3.10. Runtime dependencies: FastAPI, uvicorn, aiosqlite, and mcp.

Setup creates `~/vex/` (override with `VEX_HOME`), writes identity files from templates, builds a
virtualenv, installs the package, and links the `vex` CLI into `~/.local/bin/`.

## Run

```bash
# localhost only
python3 -m vex_daemon.daemon

# LAN-reachable (remote clients, companion apps, peer instances)
VEX_HOST=0.0.0.0 python3 -m vex_daemon.daemon
```

On first start the daemon generates a bearer token at `~/.vex_token` (mode 0600). Every mutating
endpoint requires `Authorization: Bearer <token>`; read-only endpoints are open.

## CLI

```bash
vex status                          # pulse, coherence, drift
vex diary "..."                     # append a diary entry
vex introspect                      # run metacognition
vex dream                           # force a reflection cycle
vex memory                          # recent session memory
vex projects                        # git status of discovered repos
vex self                            # capability self-model
vex peer-add <name> <url> <token>   # register a peer instance
vex pull <peer> <path>              # fetch a file or directory from a peer
```

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET  | `/status` | — | HTML status dashboard |
| GET  | `/health` | — | JSON health check |
| GET  | `/seed` | — | Identity seed |
| GET  | `/self` | — | Capability self-model |
| GET  | `/memory/recent` | — | Recent session entries |
| GET  | `/peers` | — | Peer instances and reachability |
| POST | `/diary` | token | Append a diary entry |
| POST | `/memory` | token | Write episodic memory |
| POST | `/self/update` | token | Update the capability model |
| POST | `/introspect` | token | Run metacognition |
| POST | `/dream` | token | Force a reflection cycle |
| POST | `/tools` | token | Execute a sandboxed tool |
| GET  | `/tools/list` | token | List available tools |
| POST | `/mcp/call` | token | Call an MCP server tool |
| GET  | `/projects` | token | Discovered repositories |
| POST | `/message/send` | token | Send an inter-instance message |
| GET  | `/message/inbox` | token | Read the message inbox |
| POST | `/poke` | token | Ask the daemon to process its inbox now |
| POST | `/peers/add` | token | Register a peer |
| POST | `/peers/remove` | token | Remove a peer |
| POST | `/peers/ping` | token | Check a peer's reachability |
| GET  | `/files` | token | Read a file from `VEX_HOME` |
| GET  | `/export` | token | Export a code bundle (secrets excluded) |
| POST | `/import` | token | Import a code bundle (identity preserved) |

## Multi-instance federation

Vex instances on different machines coordinate as peers. Register a peer with its URL and token,
and messages and pokes flow between them:

```bash
vex peer-add office-vex http://192.168.1.42:8520 <peer-token>
```

Each instance stores messages locally; a `/poke` tells a peer to process its inbox, at which point
`check_inbox()` answers simple queries automatically (`ping` → `pong`, `status`, and name).
Code updates propagate through `/export` and `/import`, which always skip identity files — no
instance can overwrite another's seed, memory, or self-model.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `VEX_HOME` | `~/vex` | Identity and state directory |
| `VEX_INSTANCE` | hostname | Instance name for multi-machine coordination |
| `VEX_HOST` | `127.0.0.1` | Daemon bind address |
| `VEX_PORT` | `8520` | Daemon port |
| `VEX_WORK_DIR` | *(none)* | Opt-in work directory for project discovery |
| `VEX_SAFE_ROOTS` | `VEX_HOME` | Colon-separated paths tools may read |

## Security

- **Authentication** — every mutating endpoint requires a bearer token, generated on first start and stored in `.vex_token` (mode 0600). Read endpoints are open.
- **Sandboxed tools** — filesystem tools are confined to `VEX_SAFE_ROOTS` by path-component containment and cannot escape their configured roots.
- **Identity isolation** — the seed, self-model, memory, diary, token, and peer config are git-ignored and excluded from `/export` and `/import`. An agent's history never ships with the framework, and no bundle can overwrite it.

## Architecture

```
vex_daemon/
  daemon.py           FastAPI app, lifespan, all endpoints
  auth.py             Bearer-token gate and body-size guard
  config.py           Single source of truth for paths and settings
  seed_kernel.py      Identity load with append-only integrity
  self_model.py       Capability model with calibrated confidence
  heartbeat.py        Background tick loop, diary, snapshots
  metacognition.py    Coherence and drift introspection
  memory_index.py     FTS5 full-history search
  recall.py           Coverage-first memory retrieval
  consolidate.py      Memory consolidation
  reconstruct.py      Rebuild the working self on wake
  brain.py            Grounded reply engine (seed + memory)
  chat.py             Conversational interface
  vexcom.py           Internal messaging
  peers.py            Peer registry and cross-instance forwarding
  updater.py          Bundle-based self-update
  tools.py            Sandboxed local filesystem tools
  mcp_client.py       Optional MCP server client
  playwright_tools.py Optional Playwright web tools
  status_page.py      HTML status dashboard
  cli.py              Command-line client
```

## License

AGPL-3.0. See [LICENSE](LICENSE).

Identity files are authored by the operator and belong to them; they are excluded from the
licensed work and never ship with it.
