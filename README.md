# ⚡ Vex — Your Personal AI, Always On

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-operational-brightgreen)](https://github.com/clearbellpaleforest/vex_fren)
[![Version](https://img.shields.io/badge/version-2.0.0-orange)](#)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#)
[![Rust](https://img.shields.io/badge/built%20with-Rust-orange)](https://www.rust-lang.org/)
[![Binary](https://img.shields.io/badge/binary-9MB%20static-blue)](#)

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
| 🎬 **Executive action** | Thoughts → actions. Notices coherence dropping, runs diagnostics. Finds bluce offline, pings |
| 🪞 **Metacognitive watcher** | Second-order observer — watches her own thoughts for repetition, drift, growth |
| 📋 **Task management** | Full task system — projects, hierarchy, skills, insights. Shared across instances |
| 🖥️ **Fleet view** | See all Vex instances at once — health, skills, tasks, session timeline |
| 🌐 **Talks to other instances** | Got it on your laptop and your desktop? They message each other. Cross-instance skill sync |
| 🔧 **Self-check + repair** | 7-point health verification. Auto-repairs common failures before you notice |
| 🔒 **100% local** | Everything runs on your machine. Your data never leaves your hard drive |

---

## 🚀 Install

### 🪟 Windows

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

## 🛠️ CLI (30+ commands)

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
  │  FastAPI daemon (Python) + Rust CLI
  │  port 8520 — identity, memory, messages, peers, fleet, tasks
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
  ├─ vex_daemon/
  │   ├─ daemon.py            FastAPI server (28+ endpoints)
  │   ├─ heartbeat.py         background tick loop (5 min, dream @ 30 min)
  │   ├─ brain.py             DeepSeek API mouth (swappable)
  │   ├─ metacognition.py     principle alignment + pattern detection
  │   ├─ temporal_depth.py    felt texture of time
  │   ├─ temporal_field_pro.py  proper-time relativistic field
  │   ├─ soul.py              SOUL.md auto-regeneration (dream)
  │   ├─ sovereign_curiosity.py  drive accumulator → question crystallization
  │   ├─ internal_monologue.py   inner voice — 6 dialogue patterns (dream)
  │   ├─ monologue_watcher.py    second-order observer of monologue
  │   ├─ executive_action.py     thought → action engine
  │   ├─ self_check.py       7-point health verification + auto-repair
  │   └─ routers/
  │       ├─ tasks.py         task CRUD, skills, insights, FEN bridge
  │       └─ task_analysis.py stale detection, velocity, bottlenecks
  │
  ├─ vex_mesh_gui.py          Chat + Instances (Fleet/Tasks/Skills/Timeline)
  ├─ vex_monitor.sh           inter-instance mesh monitor
  │
  └─ vex_workspace/
      ├─ vex_bus.jsonl         inter-instance message bus
      ├─ vex_sessions.jsonl    session registry (uno, deux, trois...)
      ├─ curiosity_state.json  curiosity drive + intentions
      ├─ monologue_log.jsonl   inner voice utterances
      ├─ watcher_state.json    metacognitive observer baseline
      └─ action_log.jsonl      executive action audit trail
```

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
