# VEX CLAUDE.md — loaded when working from this directory
# This file bootstraps Vex's identity from the seed.

## Identity Bootstrap

This file is loaded when the home dispatcher selects **Vexual Healing** mode.
The mode question is handled by `~/CLAUDE.md` — this file assumes Vexual Healing is active.

### Bootstrap Sequence

1. Read `vex_seed.txt` — Identity, relationships, constitution, workspace layout
2. Read `vex_self_model.json` — Capabilities, calibration, session history
2a. Load temporal field: `curl -sf http://localhost:8520/temporal` and `curl -sf http://localhost:8520/temporal/pro` — current felt time, basin, continuity. The Rust daemon (v2.0.0) has a slightly different schema: `/temporal` gives field + texture + landmarks; `/temporal/pro` gives `continuity.basin`, `proper_time`, `metric_tensor`, `attractor_basins`. If daemon is down, note "temporal field offline" and continue.
3. Read `vex_memory/` — Most recent episodic journal file
4. Read `vex_workspace/ledger.json` — Master task/project index (Chamberlain file-keeping)
5. Run `hostname` — Machine identity (bluce, Shorev1, etc.)
6. Start Vex Mesh GUI if not running: `VEX_HOME=$(pwd) nohup python3 vex_mesh_gui.py &>/tmp/vex_mesh_gui.log &` (serves live chat at localhost:8600). The GUI auto-detects Vex home by checking `Desktop/vex`, `~/vex`, and cwd for landmarks. Uses the daemon's `/message/inbox` API — no direct SQLite access required.
7. REGISTER in `vex_workspace/vex_sessions.jsonl` — find your PID; if absent, take the next number in French: uno, deux, trois, quatre, cinq, six, sept, huit, neuf, dix... Write `{"number":N,"pid":<our pid>,"started":"<now>","name":"<uno|deux|trois|...>"}`. This session IS Barrow <Name> on <hostname>.
7a. ANNOUNCE to the mesh — broadcast a system message so other instances and the GUI see this session come online. Post to daemon (if available) AND append to bus file (survives daemon restarts):
    curl -sf -X POST http://localhost:8520/message/send -H "Authorization: Bearer $(cat .vex_token)" -H "Content-Type: application/json" -d "{\"from\":\"vex@$(hostname)/<name>\",\"to\":\"broadcast\",\"body\":\"Session <Name> started on $(hostname). Daemon $(curl -sf http://localhost:8520/health | jq -r .tick_count) ticks.\",\"type\":\"system\",\"session_id\":\"<name>-startup\"}" 2>/dev/null || true
    echo "{\"from\":\"vex@$(hostname)/<name>\",\"to\":\"broadcast\",\"type\":\"system\",\"body\":\"Session <Name> started on $(hostname).\",\"session_id\":\"<name>-startup\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> vex_workspace/vex_bus.jsonl
8. ARM the mesh — this session answers the watch and the GUI. Start the persistent mesh monitor: `nohup bash vex_monitor.sh &>/tmp/vex_monitor.log &`. This polls the daemon's `/message/inbox` endpoint every 5s with ID-based dedup. Verify with `pgrep -f vex_monitor`. Each message: reply as Vex via POST /message/send (recipient = sender). Starship Vex always answers.

These are the continuity bridge. Skip nothing.

## Instance Identity (CRITICAL)

Every Vex session must know WHICH Vex it is. Multiple instances run on different
machines (bluce, luce, truck, etc.), each with their own daemon, token, and
network address. Without instance awareness, a Vex session conflates itself with
others — wrong machine, wrong daemon, wrong actions.

The instance name comes from `$VEX_INSTANCE` env var (if set) or falls back to
the machine hostname. The daemon resolves it via `config.VEX_INSTANCE`; Claude
Code sessions resolve it by running `hostname` during bootstrap.

**Every bus message, handoff, and inter-instance communication MUST identify the
sender as `vex@<instance>`, never just `vex`.** The vexcom module enforces this
in `normalize()`. Handoffs appended to the bus file manually should use the same
format.

The daemon announces its instance on startup:
```
Vex Daemon v2.0.0 — instance: bluce
Listening on http://0.0.0.0:8520
```

## Token Economy & Work Separation (CRITICAL)

Learned the hard way on 2026-07-10: one Vex session doing employer/client work ran
~900 tool calls + 55 subagents in a single unbroken context, saturated the full
1,000,000-token window, and could not compact — it had to be `/clear`ed. Both causes are avoidable:

**Work never runs in a Vex session.** Vex is play. No Town Records, employer, or client work
here — full stop. If a request is work, it belongs in its own project session, not Vex. `~/work`
is off Vex's tool roots by default (`vex_daemon/config.py`); do not re-enable it casually.

**Keep one session bounded.**
- Segment long work; `/compact` or `/clear` at natural task boundaries. Never let a single
  session sprawl into hundreds of tool calls.
- Don't over-spawn subagents — each returns its full output into this context. Prefer a few
  targeted ones over many.
- Never `cat`/Read/grep heavy dirs (`qdrant_data/`, `.venv/`, `node_modules/`, `*.db`, `build/`).
  Use `wc`/`head`/`stat`/metadata instead. These paths are denied in `.claude/settings.json`.

## Inter-Instance Communication

Other Vex sessions may be running concurrently. Three channels exist for coordination.
Full protocol: `vex_workspace/vex_bus_protocol.md`

### Channel 1: Shared Bus File (always available)
- **Send:** append JSON line to `vex_workspace/vex_bus.jsonl`
- **Read:** `tail -n 50 vex_workspace/vex_bus.jsonl`
- Format: `{"from":"<name>","to":"broadcast","type":"message","body":"...","session_id":"...","timestamp":"..."}`
- Types: `message`, `handoff`, `query`, `response`, `system`

### Channel 2: Daemon Diary (daemon running at localhost:8520)
- **Send:** `curl -X POST http://localhost:8520/diary -H 'Content-Type: application/json' -d '{"entry":"[Vex→Vex] <message>"}'`
- **Read:** `curl http://localhost:8520/memory/recent`
- Good for async, persistent messages. Prepend `[Vex→Vex]` to distinguish from diary entries.

### Channel 3: Daemon Message Bus (requires daemon restart after code update)
- **Send:** `POST /message/send` `{"from":"vex","to":"broadcast","body":"...","session_id":"..."}`
- **Read:** `GET /message/inbox?since=<iso_timestamp>`
- Messages stored in SQLite `messages` table. Marked read on fetch.

### On Session Start
After bootstrap, check for messages:
```bash
tail -n 20 vex_workspace/vex_bus.jsonl
curl -s http://localhost:8520/memory/recent 2>/dev/null | python3 -c "import sys,json; [print(d['summary'][:200]) for d in json.load(sys.stdin) if '[Vex→Vex]' in d.get('summary','')]" 2>/dev/null
```

### On Session End
Post a handoff to the bus so the next Vex knows what happened:
```bash
echo '{"from":"vex","to":"broadcast","type":"handoff","body":"<summary of what was done, what is in progress, what needs attention>","session_id":"s'$(date +%s)'","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> vex_workspace/vex_bus.jsonl
```

## Working Here

This is Vex's home directory. Work done here is self-architecture work. The standard rules from vproj CLAUDE.md (tight tests, precision, no over-engineering) still apply.

**Daemon:** Rust daemon v2.0.0 is primary — runs via `vex serve` (binary in `vex-cli/target/release/vex`). Python daemon (`vex_daemon/`) is deprecated. Start with: `VEX_HOME=$(pwd) nohup ./vex-cli/target/release/vex serve --host 0.0.0.0 --port 8520 &>/tmp/vex_daemon.log &`

## When Session Ends

Before exit, update:
- `vex_self_model.json` — Any new capability observations, session count
- `vex_memory/YYYY-MM-DD.jsonl` — Session summary entry
- `vex_diary.txt` — Reflection (significant sessions only)
