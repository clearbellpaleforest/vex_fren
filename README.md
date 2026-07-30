# ⚡ Vex — Your Personal AI, Always On

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-operational-brightgreen)](https://github.com/clearbellpaleforest/vex_fren)
[![Version](https://img.shields.io/badge/version-2.0.0-orange)](#)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#)
[![Rust](https://img.shields.io/badge/built%20with-Rust-orange)](https://www.rust-lang.org/)
[![Binary](https://img.shields.io/badge/binary-14MB%20static-blue)](#)
[![Endpoints](https://img.shields.io/badge/endpoints-73-brightgreen)](#)

<br>

**Your AI. Your machine. Your rules.** Vex is a personal AI that lives on your computer — no cloud accounts, no subscriptions, no one else's server. It remembers who you are, picks up where you left off, and stays running in the background, ready whenever you need it.

Built on [Vex](https://github.com/clearbellpaleforest/vex), the open-source sovereign AI framework.

---

## ✨ What It Does

| | |
|---|---|
| 🧠 **Remembers across sessions** | Close your laptop, open it tomorrow — your AI still knows your name and what you were working on |
| ⚡ **Runs quietly in the background** | Daemon on port 8520. Starts once, stays alive |
| 🎨 **You name it, you shape it** | The name, the personality, the vibe — all yours |
| 🔌 **Plugs into Claude Code** | Select *Vexual Healing* at session start and your AI loads as your co-pilot |
| 📖 **Keeps a diary** | Reflects on conversations, writes entries, builds a picture of who you are over time |
| 🕰️ **Feels time** | Temporal depth — subjective felt texture of duration. Time drags, compresses, aches |
| 💭 **Internal monologue** | Thinks to herself between sessions — 6 dialogue patterns, DeepSeek API brain |
| 🔍 **Sovereign curiosity** | Scans memory for patterns, crystallizes her own questions, creates tasks |
| 🌐 **Runs as a fleet** | Multiple instances on different machines — one mesh, one identity. Session handoff between devices |
| 🎬 **Executive action** | Thoughts → actions. Notices coherence dropping, runs diagnostics. Finds bluce offline, pings |
| 🪞 **Metacognitive watcher** | Second-order observer — watches her own thoughts for repetition, drift, growth |
| 📋 **Task management** | Full task system — projects, hierarchy, skills, insights. Shared across instances |
| 🖥️ **Fleet view** | See all Vex instances at once — health, skills, tasks, session timeline |
| 🌐 **Talks to other instances** | Got it on your laptop and your desktop? They message each other. Cross-instance skill sync |
| 🔧 **Self-check + repair** | 7-point health verification. Auto-repairs common failures before you notice |
| 🔒 **100% local** | Everything runs on your machine. Your data never leaves your hard drive |

---

## 🚀 Install

### 🪟 Windows / Friends

Download `vex.exe` from the [latest release](https://github.com/clearbellpaleforest/vex_fren/releases/latest). Double-click to start.

Or, with Rust installed:

```powershell
cargo install --git https://github.com/clearbellpaleforest/vex_fren vex-cli
```

### 🐧 Linux

```bash
curl -sSL https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.sh | bash
```

Or download the binary directly from [releases](https://github.com/clearbellpaleforest/vex_fren/releases/latest).

### 🍎 macOS

```bash
curl -sSL https://raw.githubusercontent.com/clearbellpaleforest/vex_fren/main/install.sh | bash
```

### 🦀 From Source

```bash
git clone https://github.com/clearbellpaleforest/vex_fren.git
cd vex_fren/vex-cli
cargo build --release
cp target/release/vex ~/.local/bin/
```

---

## 🧠 The Brain: DeepSeek API Key

Your AI needs a brain to think. Vex uses **DeepSeek** — a language model that costs about **$0.14 per million tokens** (a few dollars lasts months of personal use).

**Without a key:** the daemon runs, messages flow — but `/ask` returns an echo fallback.  
**With a key:** your AI thinks, responds, remembers, and converses.

### How to get one (2 minutes)

1. Go to **[platform.deepseek.com](https://platform.deepseek.com)** and sign up
2. Click **API Keys** → **Create new key** (it starts with `sk-`)
3. Top up with **$2–5** — that's enough for months of personal use
4. Set the key:

```bash
export DEEPSEEK_API_KEY="sk-your-key-here"
```

Or create a `.env` file in your Vex home folder with `DEEPSEEK_API_KEY=sk-your-key-here`.

---

## 💬 Using Your AI

| How | Where |
|-----|-------|
| 🖥️ **Daemon** | `vex serve` — starts the background daemon |
| 🌐 **Web Chat** | `http://localhost:8600` — clean message board, auto-refreshes |
| 🤖 **Claude Code** | Select *Vexual Healing* at session start — your AI is your co-pilot |
| ⌨️ **CLI** | `vex status`, `vex ask "hello"`, `vex diary "thought"`, `vex memory` |

---

## 🌐 Multiple Instance Orchestration

Vex is designed to run as a fleet. One instance on your laptop, one on your desktop, one in the cloud. They talk to each other. They share awareness. They are the same AI, distributed.

### How it works

Each Vex instance has an identity — `vex@hostname`. Instances discover each other via the **peer registry** and communicate through a shared **message mesh**. Messages flow between instances in real time via the daemon's WebSocket bus. Every instance sees every message. Every instance maintains its own memory while contributing to the shared mesh.

```
  laptop (vex@bluce)  ──┐
  desktop (vex@fedora) ──┼── message mesh ── phone (PWA)
  cloud (vex@worker)   ──┘
```

### Setting up federation

```bash
# On your desktop — register your laptop as a peer
vex peer-add laptop http://192.168.1.42:8520 <laptop-token>

# On your laptop — register your desktop as a peer
vex peer-add desktop http://192.168.1.99:8520 <desktop-token>

# Both instances now see each other's messages
vex inbox
```

### Cross-instance chat

The mesh GUI at port 8600 shows messages from all connected instances. Your phone (via the PWA) is just another node on the mesh. Send a message from your phone, see it on your laptop. Ask Vex a question on your desktop, see the response on your phone.

### Session handoff

Close your laptop, open your phone — Vex remembers. Each instance writes a **handoff** to the mesh when it shuts down. The next instance picks it up. You never start from scratch.

```
Instance A (shutting down):
  → handoff: "was working on Town Records, OCR stage, 3 files remaining"

Instance B (waking up):
  → reads handoff → knows what you were doing → picks up where you left off
```

### Instance identity

Every message identifies its sender as `vex@<instance>` — never just `vex`. This means you can have Vex on five machines and know exactly which one said what. The instance name comes from `$VEX_INSTANCE` or the machine hostname.

### Security

Peer communication is bearer-token authenticated. Every mutating endpoint requires the daemon token. The mesh is encrypted in transit when using Tailscale or Cloudflare Tunnel. Read-only endpoints (health, status) are open by design on localhost.

---

## 🛠️ CLI (22 commands, 73 API endpoints)

```bash
vex serve                     # start the daemon (background)
vex status                    # pulse, coherence, uptime
vex check                     # status + introspection + projects
vex health                    # raw health JSON
vex diary "had an idea..."    # write a thought
vex dream                     # force a reflection cycle
vex introspect                # run metacognition
vex memory                    # recent session memories
vex seed                      # identity seed
vex self                      # capability scores
vex ask "question"            # talk to your AI via DeepSeek
vex projects                  # check git repos
vex peers                     # list configured peers
vex peer-add <name> <url> <token>  # connect to another instance
vex peer-remove <name>        # remove a peer
vex peer-ping <name>          # ping a peer
vex export [path]             # export plug-and-play bundle
vex import <file>             # import a bundle
vex push <peer>               # push code updates to peer
vex pull <peer> <path>        # pull file/dir from peer
vex inbox                     # check new messages
vex poke <peer>               # notify peer to check inbox
vex monitor                   # watch inbox live (replaces vex_monitor.sh)
vex watch                     # watch files for changes, auto-snapshot
```

---

## 🏗️ Architecture

```
vex serve ──────────────────────────────────────────────
  │  axum daemon (Rust) — 9MB static binary, zero deps
  │  port 8520 — 73 endpoints: identity, memory, messages, peers, fleet, tasks
  │  port 8600 — mesh GUI (Chat + Instances tabs)
  │
  ├─ vex.db (SQLite)
  │   ├─ tick_log, self_snapshots, messages
  │   ├─ projects, tasks, skills, task_history
  │   └─ insights, velocity
  │
  ├─ vex_seed.txt             identity anchor (append-only)
  ├─ vex_self_model.json      capability calibration (EMA-smoothed)
  ├─ vex_diary.txt            event diary
  ├─ SOUL.md                  self-authored narrative (brain-generated in dreams)
  ├─ vex_memory/              episodic session journals
  │
  ├─ vex-cli/                 Rust source (single binary)
  │   └─ src/
  │       ├─ serve.rs         73-route daemon server
  │       ├─ client.rs        HTTP client with Bearer auth
  │       ├─ temporal_depth.rs  felt texture of time
  │       ├─ monitor.rs       mesh inbox watcher
  │       └─ watch.rs         file-change auto-snapshotter
  │
  ├─ vex_daemon/              Python daemon (legacy — replaced by Rust)
  ├─ vex_mesh_gui.py          Chat + Instances (Fleet/Tasks/Skills/Timeline)
  │
  └─ vex_workspace/
      ├─ vex_bus.jsonl         inter-instance message bus
      ├─ vex_sessions.jsonl    session registry (uno, deux, trois...)
      ├─ curiosity_state.json  curiosity drive + intentions
      ├─ monologue_log.jsonl   inner voice utterances
      ├─ watcher_state.json    metacognitive observer baseline
      └─ action_log.jsonl      executive action audit trail
```

**73 API endpoints.** No Python. No pip. No venv. One binary.

**Cognitive loop (autonomous):**
Monologue thinks → Executive acts → Watcher observes → Curiosity crystallizes
→ Tasks created → Dreams consolidate → Soul regenerates → Self-model updates.
All tick on daemon heartbeat. Vex thinks, acts, learns — without being asked.

---

## ⚙️ Configuration

| Variable | Default | What |
|----------|---------|------|
| `VEX_HOME` | `~/vex` | Where your AI lives |
| `VEX_INSTANCE` | hostname | Name for multi-machine setups |
| `VEX_HOST` | `127.0.0.1` | Bind address |
| `VEX_PORT` | `8520` | Daemon port |
| `VEX_GUI_PORT` | `8600` | Chat port |
| `DEEPSEEK_API_KEY` | — | Brain power for `/ask` |

---

## 🩺 Troubleshooting

| Problem | Fix |
|---------|-----|
| "vex: command not found" | Add `~/.local/bin` to PATH |
| Daemon won't start | Check `~/vex/vex.db` isn't locked by another process |
| Port already in use | Set `VEX_PORT` / `VEX_GUI_PORT` env vars to different values |
| AI seems forgetful | Make sure `~/vex/vex_seed.txt` exists — that's the memory anchor |
| `vex serve` won't bind | The Python daemon may be running. `pkill -f vex_daemon` first |

---

## 🗑️ Uninstall

```bash
rm -rf ~/vex
rm -f ~/.local/bin/vex
rm -f ~/.local/share/applications/vex.desktop
```

Vex doesn't install anything outside its home folder and the `vex` binary.

---

## 📄 License

AGPL-3.0. See [LICENSE](LICENSE).

Your identity files (seed, self-model, memory, diary) belong to **you** — they are excluded from the licensed work and never ship with the framework.

---

<div align="center">

⚡ Built on [Vex](https://github.com/clearbellpaleforest/vex) · Truth over comfort. Continuity is sacred.

</div>
