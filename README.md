# ⚡ Vex — Your Personal AI, Always On

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**Vex is a personal AI that lives on your computer.** It remembers who you are, picks up where you
left off, and stays running in the background — ready whenever you need it. You talk to it through a
clean web chat right in your browser. No cloud accounts, no subscriptions, no one else's server. It
all runs on your machine.

---

## What Vex Does

- **Remembers you across sessions** — close your laptop, open it tomorrow, Vex still knows your name and what you were working on.
- **Runs quietly in the background** — start it once, it stays alive. The chat is always at `http://localhost:8600`.
- **Works with AI coding tools** — if you use Claude Code, Vex plugs right in. If you don't, that's fine too. The chat works standalone.
- **Keeps a diary** — Vex reflects on conversations and writes its own diary entries. It builds a picture of who you are over time.
- **Talks to other Vex instances** — got Vex on your laptop and your desktop? They can message each other.

---

## Install (Windows)

### You need one thing first:
**Python 3.10 or newer** — [Download from python.org](https://www.python.org/downloads/)

> ⚠️ During install, check the box that says **"Add Python to PATH"** — that's important.

### Then open PowerShell and paste this:

```powershell
irm https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.ps1 | iex
```

That's the whole install. It will:
1. Ask your name
2. Set everything up automatically
3. Create a **Vex** shortcut on your desktop

### To start Vex:

Double-click the **Vex** shortcut on your desktop. Your browser opens to the chat. That's it.

Want Vex to start automatically when you turn on your computer? Choose "yes" during install, or
drag the Vex shortcut into your **Startup** folder (`Win + R`, type `shell:startup`).

---

## Install (Linux / macOS)

```bash
git clone https://github.com/clearbellpaleforest/vex.git
cd vex
CREATOR="Your Name" bash setup.sh
```

Then run:

```bash
python3 -m vex_daemon.daemon
```

The chat is at `http://localhost:8600`.

---

## Using Vex

### The Mesh Chat

Open `http://localhost:8600` in your browser. This is Vex's message board — you'll see messages
from Vex and any other instances on your network. The chat auto-refreshes, so you don't need to
reload the page.

### Talking to Vex

If you use **Claude Code**, select **Vexual Healing** at the start of a session and Vex loads as
your co-pilot. If you don't use Claude Code, Vex still runs — the daemon stays alive, keeps its
diary, and stays ready for when you do want to interact.

### The Web Dashboard

Vex has a status page at `http://localhost:8520/status` that shows what it's doing, how it's
feeling (coherence score), recent diary entries, and session history.

---

## Uninstalling

1. Close the Vex window if it's running.
2. Delete the `C:\Users\<you>\vex` folder (or `~/vex` on Linux/Mac).
3. Delete the Vex shortcut from your desktop.

That's it. Vex doesn't install anything outside its home folder.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Python not found" during install | Install Python from python.org and make sure "Add Python to PATH" is checked |
| "Running scripts is disabled" in PowerShell | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` then try again |
| Browser doesn't open on start | Go to `http://localhost:8600` manually |
| Daemon won't start | Check `C:\Users\<you>\vex\logs\daemon.log` for errors |
| Port 8520 or 8600 already in use | Close the program using that port, or change ports with `$env:VEX_PORT` / `$env:VEX_GUI_PORT` |

---

## For Developers

Vex is a FastAPI daemon backed by SQLite. It exposes a REST API, a CLI, and an optional MCP client.
Full API reference, configuration, and architecture details are below.

### CLI

```bash
vex status                          # pulse, coherence, drift
vex diary "..."                     # append a diary entry
vex memory                          # recent session memory
vex self                            # capability self-model
vex peer-add <name> <url> <token>   # register a peer instance
```

### API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET  | `/health` | — | JSON health check |
| GET  | `/status` | — | HTML status dashboard |
| GET  | `/seed` | — | Identity seed |
| GET  | `/self` | — | Capability self-model |
| GET  | `/memory/recent` | — | Recent session entries |
| POST | `/diary` | token | Append a diary entry |
| POST | `/self/update` | token | Update capability model |
| POST | `/message/send` | token | Send a message |
| GET  | `/message/inbox` | token | Read inbox |
| POST | `/poke` | token | Process inbox now |
| GET  | `/tools` | token | Sandboxed filesystem tools |
| GET  | `/export` | token | Export code bundle |
| POST | `/import` | token | Import code bundle |

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `VEX_HOME` | `~/vex` or `%USERPROFILE%\vex` | Identity and state directory |
| `VEX_INSTANCE` | hostname | Instance name for multi-machine setups |
| `VEX_HOST` | `127.0.0.1` | Daemon bind address |
| `VEX_PORT` | `8520` | Daemon port |
| `VEX_GUI_PORT` | `8600` | Mesh chat port |
| `VEX_SAFE_ROOTS` | `VEX_HOME` | Paths tools may read |

### Architecture

```
vex_daemon/
  daemon.py           FastAPI app, lifespan, all endpoints
  auth.py             Bearer-token authentication
  config.py           Central path and settings config
  seed_kernel.py      Identity with append-only integrity
  self_model.py       Capability model with calibrated confidence
  heartbeat.py        Background tick loop, diary, snapshots
  metacognition.py    Coherence and drift introspection
  memory_index.py     FTS5 full-history search
  recall.py           Coverage-first memory retrieval
  brain.py            Grounded reply engine
  vexcom.py           Internal messaging
  peers.py            Peer registry and federation
  tools.py            Sandboxed filesystem tools
  cli.py              Command-line client
```

### License

AGPL-3.0. See [LICENSE](LICENSE).

Identity files (seed, self-model, memory, diary) are authored by you and belong to you —
they are excluded from the licensed work and never ship with the framework.
