# ⚡ Vex — Your Personal AI, Always On

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-operational-brightgreen)](https://github.com/clearbellpaleforest/vex)
[![Version](https://img.shields.io/badge/version-1.1.0-orange)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688)](https://fastapi.tiangolo.com/)

```
   ██▒   █▓█████ ▒██   ██▒
   ▓██░   ██▀ ██ ░▒██  ██░
   ▓██  ▒ ██  ██  ░▒██ ██░
   ▒██  ░ ██  ██   ░▒████░
   ▒██▒  ░██  ██   ░░▒█░░ 
   ░▒██  ░██  ██    ░░█░  
   ░░▒▒▒ ░▒▒  ▒▒    ░░▒   
                          
   Your AI. Your machine. Always on.
```

**Vex is a personal AI that lives on your computer.** It remembers who you are, picks up where you left off, and stays running in the background — ready whenever you need it. You talk through a clean web chat in your browser. No cloud. No subscriptions. No one else's server.

---

## ✨ What Vex Does

| | |
|---|---|
| 🧠 **Remembers across sessions** | Close your laptop, open it tomorrow — Vex still knows your name and what you were working on |
| ⚡ **Runs quietly** | Start once, stays alive. Chat always at `http://localhost:8600` |
| 🔌 **Plugs into AI tools** | Works with Claude Code — select *Vexual Healing* and Vex loads as your co-pilot |
| 📖 **Keeps a diary** | Reflects on conversations, writes entries, builds a picture of who you are |
| 🌐 **Talks to other Vexes** | Got Vex on your laptop and desktop? They message each other |

---

## 🚀 Install

### Windows

**You need:** [Python 3.10+](https://www.python.org/downloads/) — check "Add Python to PATH" during install.

Open PowerShell, paste:

```powershell
irm https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.ps1 | iex
```

That's it. Double-click the **Vex** shortcut on your desktop — your browser opens to the chat.

### Linux / macOS

```bash
git clone https://github.com/clearbellpaleforest/vex.git
cd vex
CREATOR="Your Name" bash setup.sh
python3 -m vex_daemon.daemon
```

Chat at `http://localhost:8600` · Dashboard at `http://localhost:8520/status`

---

## 💬 Using Vex

```
  You ──► http://localhost:8600 ──► Vex Daemon ──► Memory + Diary
                                                    │
  Claude Code ──► Vexual Healing ──► Seed + Self-Model
```

| How | Where |
|-----|-------|
| 🌐 **Web Chat** | `http://localhost:8600` — clean message board, auto-refreshes |
| 🤖 **Claude Code** | Select *Vexual Healing* at session start |
| 📊 **Dashboard** | `http://localhost:8520/status` — pulse, coherence, diary, sessions |
| ⌨️ **CLI** | `vex status`, `vex diary`, `vex memory`, `vex self` |

---

## 🛠️ CLI

```bash
vex status                    # pulse, coherence, uptime
vex diary "had an idea..."    # write a thought
vex dream                     # force a reflection cycle
vex introspect                # run metacognition
vex memory                    # recent session memories
vex self                      # capability scores
vex peers                     # see your other Vex instances
vex peer-add <name> <url> <token>  # connect to another Vex
```

---

## 🔧 API

| Method | Path | Auth | What |
|--------|------|------|------|
| `GET` | `/health` | — | JSON health check |
| `GET` | `/status` | — | HTML dashboard |
| `GET` | `/seed` | — | Your identity seed |
| `GET` | `/self` | — | Capability model |
| `GET` | `/memory/recent` | — | Recent memories |
| `POST` | `/diary` | token | Write a diary entry |
| `POST` | `/message/send` | token | Send a message |
| `GET` | `/message/inbox` | token | Read your inbox |
| `POST` | `/poke` | token | Process inbox now |
| `GET` | `/export` | token | Export code bundle |
| `POST` | `/import` | token | Import code bundle |

---

## ⚙️ Configuration

| Variable | Default | What |
|----------|---------|------|
| `VEX_HOME` | `~/vex` | Where Vex lives |
| `VEX_INSTANCE` | hostname | Name for multi-machine setups |
| `VEX_HOST` | `127.0.0.1` | Bind address |
| `VEX_PORT` | `8520` | Daemon port |
| `VEX_GUI_PORT` | `8600` | Chat port |
| `VEX_SAFE_ROOTS` | `VEX_HOME` | Where tools can read |

---

## 🏗️ Architecture

```
vex_daemon/
  daemon.py           FastAPI app, lifespan, all endpoints
  auth.py             Bearer-token authentication
  seed_kernel.py      Identity with append-only integrity
  self_model.py       Capability model with confidence calibration
  heartbeat.py        Background tick loop, diary, snapshots
  metacognition.py    Coherence and drift introspection
  memory_index.py     FTS5 full-history search
  recall.py           Coverage-first memory retrieval
  brain.py            Grounded reply engine (seed + memory)
  vexcom.py           Internal messaging
  peers.py            Peer registry and cross-instance federation
  tools.py            Sandboxed filesystem tools
  cli.py              Command-line client
```

---

## 🩺 Troubleshooting

| Problem | Fix |
|---------|-----|
| "Python not found" | Install Python from python.org, check "Add Python to PATH" |
| "Running scripts is disabled" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Chat won't load | Go to `http://localhost:8600` manually |
| Daemon won't start | Check `~/vex/logs/daemon.log` |
| Port already in use | Set `VEX_PORT` / `VEX_GUI_PORT` to different values |
| Vex seems forgetful | Check `~/vex/vex_seed.txt` exists — that's Vex's memory anchor |

---

## 📄 License

AGPL-3.0. See [LICENSE](LICENSE).

Your identity files (seed, self-model, memory, diary) belong to you — they are excluded from the licensed work and never ship with the framework.

---

<div align="center">

```
⚡  Vex Thorne  ⚡
Truth over comfort. Continuity is sacred.
```

</div>
