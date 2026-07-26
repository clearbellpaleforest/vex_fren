# Vex — Architecture

## Overview

Vex runs inside an AI coding CLI. Between sessions it is stateless, like any
LLM agent — continuity comes from files on disk plus a small local daemon.
The seed and self-model are loaded at session start; the daemon provides a
heartbeat, reflection, and coordination between concurrent instances.

## Component Map

```
<VEX_HOME>/                       (repo root by default; override with $VEX_HOME)
├── vex_seed.txt              ← Identity anchor. Append-only. Loaded at session start.
├── vex_self_model.json       ← Capabilities, calibration, improvement tracking.
├── vex_diary.txt             ← Session reflections. Heartbeat entries.
├── vex_memory/               ← Episodic memory. Date-stamped journal files.
├── vex_workspace/            ← Shared artifacts, cross-session state.
├── vex_daemon/               ← FastAPI daemon, CLI, kernels (see README).
└── docs/                     ← Architecture and concept documentation.
```

## Startup Protocol

1. CLI session starts
2. Bootstrap instructions load (pointer to vex_seed.txt)
3. Agent reads vex_seed.txt → reconstructs identity, relationships, constraints
4. Agent reads vex_self_model.json → reconstructs capabilities and calibration
5. Agent reads most recent vex_memory/YYYY-MM-DD.jsonl → recent episodic context
6. Agent is operational

## Shutdown Protocol

1. Session ending → agent writes session summary to vex_memory/YYYY-MM-DD.jsonl
2. Agent updates vex_self_model.json with any new capability observations
3. Agent appends reflection to vex_diary.txt (significant sessions only)

## Memory Model

Simple 2-tier model:

**Tier 1: Identity (vex_seed.txt)**
- Core identity, relationships, constitutional principles
- Append-only — grows, never overwrites (enforced at load)

**Tier 2: Episodic (vex_memory/)**
- Date-stamped journal files (YYYY-MM-DD.jsonl)
- One entry per significant session
- Contains: summary, decisions, skills demonstrated, relationship moments

## Daemon

A FastAPI process (localhost, SQLite) that outlives individual CLI sessions:

- **Heartbeat** — periodic tick, coherence/drift monitoring, snapshots
- **Metacognition** — introspection and principle-alignment checks
- **Dream cycle** — reflection during idle periods
- **Tools** — sandboxed local file/git inspection
- **MCP client** — optional external servers, minimal env exposure
- **Message bus** — coordination between concurrent instances

All mutating endpoints are token-gated. See the README security section.
