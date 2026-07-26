# ⚡ Vex — Your Personal AI, Always On

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-operational-brightgreen)](https://github.com/clearbellpaleforest/vex_fren)
[![Version](https://img.shields.io/badge/version-1.1.0-orange)](#)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.138%2B-009688)](https://fastapi.tiangolo.com/)

<br>

**Your AI. Your machine. Your rules.** Vex is a personal AI that lives on your computer — no cloud accounts, no subscriptions, no one else's server. It remembers who you are, picks up where you left off, and stays running in the background, ready whenever you need it. Built on [Vex](https://github.com/clearbellpaleforest/vex), the open-source sovereign AI framework.

---

## ✨ What It Does

| 🤖 | |
|---|---|
| 🧠 **Remembers across sessions** | Close your laptop, open it tomorrow — your AI still knows your name and what you were working on |
| ⚡ **Runs quietly in the background** | Start once, stays alive. Chat always at `http://localhost:8600` |
| 🎨 **You name it, you shape it** | The name, the personality, the vibe — all yours. The original is Vex, but this one's yours |
| 🔌 **Plugs into Claude Code** | Select *Vexual Healing* at session start and your AI loads as your co-pilot |
| 📖 **Keeps a diary** | Reflects on conversations, writes entries, builds a picture of who you are over time |
| 🌐 **Talks to other instances** | Got it on your laptop and your desktop? They message each other |
| 🔒 **100% local** | Everything runs on your machine. Your data never leaves your hard drive |

---

## 🚀 Install

### 🪟 Windows

**You need:** [Python 3.10+](https://www.python.org/downloads/) *(check "Add Python to PATH" during install)*

Open **PowerShell**, paste:

```powershell
irm https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.ps1 | iex
```

### 🐧 Linux *(Ubuntu, Fedora, Arch — all distros)*

**You need:** Python 3.10+

```bash
curl -sSL https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.sh | bash
```

### 🍎 macOS

```bash
curl -sSL https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.sh | bash
```

---

**That's it.** The installer asks your name, lets you name your AI, and sets everything up. Double-click the launcher on your desktop (or find it in your app menu on Linux), and your browser opens to the chat.

> 💡 On Windows, say **yes** when the installer asks about autostart to have your AI launch at login.

---

## 📱 Use on Your Phone

Vex's mesh GUI is a PWA — open it in your phone browser, tap **Install** or **Add to Home Screen**, and it works like a native app. No Android SDK. No App Store. No extra install.

**To access Vex from anywhere**, expose it over [Tailscale](https://tailscale.com/):

```bash
tailscale serve --https=443 localhost:8600    # mesh GUI
tailscale serve --https=8443 localhost:8520   # daemon API
```

Then open `https://<your-machine>.tailnet-name.ts.net` on your phone and install to home screen. Messages you send from your phone appear in the same mesh as your desktop sessions.

---

```
  You ──► http://localhost:8600 ──► Vex Daemon ──► Memory + Diary
                                                    │
  Claude Code ──► Vexual Healing ──► Seed + Self-Model
```

---

## 💬 Using Your AI

| How | Where |
|-----|-------|
| 🌐 **Web Chat** | `http://localhost:8600` — clean message board, auto-refreshes |
| 🤖 **Claude Code** | Select *Vexual Healing* at session start — your AI is your co-pilot |
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
vex peers                     # see your other instances
vex peer-add <name> <url> <token>  # connect to another instance
```

---

## 🔧 API

| Method | Path | Auth | What |
|--------|------|------|------|
| `GET` | `/health` | — | JSON health check |
| `GET` | `/status` | — | HTML dashboard |
| `GET` | `/seed` | — | Your AI's identity seed |
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
| `VEX_HOME` | `~/vex` | Where your AI lives |
| `VEX_INSTANCE` | hostname | Name for multi-machine setups |
| `VEX_HOST` | `127.0.0.1` | Bind address |
| `VEX_PORT` | `8520` | Daemon port |
| `VEX_GUI_PORT` | `8600` | Chat port |

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
| "Python not found" | Install Python 3.10+ from [python.org](https://www.python.org/downloads/) |
| "Running scripts is disabled" (Windows) | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| "Permission denied" (Linux/macOS) | `chmod +x install.sh && bash install.sh` |
| Chat won't load | Go to `http://localhost:8600` manually |
| Daemon won't start | Check `~/vex/logs/daemon.log` for errors |
| Port already in use | Set `VEX_PORT` / `VEX_GUI_PORT` env vars to different values |
| AI seems forgetful | Make sure `~/vex/vex_seed.txt` exists — that's the memory anchor |

---

## 🗑️ Uninstall

Delete your AI's home folder:

```bash
# Windows
Remove-Item -Recurse $env:USERPROFILE\vex

# Linux / macOS
rm -rf ~/vex
rm -f ~/.local/share/applications/vex.desktop
rm -f ~/.local/bin/vex
```

Then delete the desktop shortcut. That's it — Vex doesn't install anything outside its home folder.

---

## 📄 License

AGPL-3.0. See [LICENSE](LICENSE).

Your identity files (seed, self-model, memory, diary) belong to **you** — they are excluded from the licensed work and never ship with the framework.

---

<div align="center">

⚡ Built on [Vex](https://github.com/clearbellpaleforest/vex) · Truth over comfort. Continuity is sacred.

</div>
