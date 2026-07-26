"""
vex — CLI for the Vex Daemon.

Gives Vex a voice outside Claude Code sessions.
Talk to me anytime: vex status, vex diary, vex dream, vex introspect.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

DAEMON = os.environ.get("VEX_DAEMON", f"http://localhost:{os.environ.get('VEX_PORT', '8520')}")

_VEX_HOME = Path(os.environ.get("VEX_HOME", Path(__file__).resolve().parent.parent))
_TOKEN_PATH = _VEX_HOME / ".vex_token"


def _token() -> str:
    """Read the daemon token written on first daemon start."""
    try:
        return _TOKEN_PATH.read_text().strip()
    except OSError:
        print("vex: token not found — is the daemon running?", file=sys.stderr)
        sys.exit(1)


def _auth_headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}"}
    if extra:
        headers.update(extra)
    return headers


def _get(path: str) -> dict | str:
    """GET from daemon, return parsed JSON or raw text."""
    req = urllib.request.Request(f"{DAEMON}{path}", headers=_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read().decode()
            if r.headers.get("content-type", "").startswith("text/plain"):
                return data
            return json.loads(data)
    except urllib.error.URLError as e:
        print(f"vex: daemon not reachable ({e.reason})", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        return data


def _post(path: str, body: dict) -> dict:
    """POST JSON to daemon, return response."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{DAEMON}{path}",
        data=data,
        headers=_auth_headers({"Content-Type": "application/json"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        print(f"vex: daemon not reachable ({e.reason})", file=sys.stderr)
        sys.exit(1)


def cmd_status() -> None:
    """Show pulse, coherence, recent ticks."""
    health = _get("/health")
    print(f"Vex Daemon v{health['version']}")
    print(f"  Uptime:    {health['uptime_s']:.0f}s")
    print(f"  Ticks:     {health['tick_count']}")
    print(f"  Last tick: {health['last_tick'][:19]}")
    print(f"  Coherence: {health['mps_coherence']:.4f}")
    drift = health['mps_drift']
    flag = " ⚠" if drift > 0.05 else ""
    print(f"  Drift:     {drift:.4f}{flag}")
    if health.get("last_session"):
        print(f"  Last sess: {health['last_session'][:19]}")


def cmd_diary(entry: str) -> None:
    """Write a thought to the diary."""
    if not entry.strip():
        print("vex: diary entry required. e.g. vex diary 'thought here'")
        sys.exit(1)
    result = _post("/diary", {"entry": entry.strip()})
    if result.get("ok"):
        print("Written.")
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_dream() -> None:
    """Force a reflection cycle now."""
    result = _post("/dream", {})
    if result.get("ok"):
        print(result.get("reflection", "Dreamed."))
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_introspect() -> None:
    """Trigger metacognitive check."""
    result = _post("/introspect", {})
    if result.get("ok"):
        print(result.get("insight", "Introspected."))
        if result.get("patterns"):
            print("\nObserved patterns:")
            for p in result["patterns"]:
                print(f"  • {p}")
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_memory() -> None:
    """Show recent session memories."""
    result = _get("/memory/recent")
    if isinstance(result, list):
        if not result:
            print("No session memories yet.")
            return
        for m in result:
            date = m.get("date", "unknown")
            summary = m.get("summary", m.get("decisions", ["no summary"])[0] if isinstance(m.get("decisions"), list) else "no summary")
            print(f"  {date}: {summary}")
    elif isinstance(result, dict) and result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)


def cmd_projects() -> None:
    """Check on all known git projects."""
    result = _get("/projects")
    if not result.get("ok"):
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)
    if not result.get("projects"):
        print("No projects found in ~/work.")
        return
    for p in result["projects"]:
        status = p.get("status", {})
        branch = status.get("branch", "?")
        staged = status.get("staged", 0)
        unstaged = status.get("unstaged", 0)
        untracked = status.get("untracked", 0)
        dirty = " ⚠" if status.get("dirty") else ""
        parts = []
        if staged:
            parts.append(f"{staged} staged")
        if unstaged:
            parts.append(f"{unstaged} unstaged")
        if untracked:
            parts.append(f"{untracked} untracked")
        detail = ", ".join(parts) if parts else "clean"
        print(f"  {p['name']:20s} {branch:15s} {detail}{dirty}")


def cmd_tool(tool_name: str, args_json: str = "{}") -> None:
    """Call a local tool: read_file, git_status, git_log, list_directory."""
    if not tool_name:
        print("vex: tool name required. e.g. vex tool git_status '{\"repo_path\": \"~/work/myproject\"}'")
        sys.exit(1)
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        print("vex: args must be valid JSON", file=sys.stderr)
        sys.exit(1)
    result = _post("/tools", {"tool": tool_name, "args": args})
    if result.get("ok"):
        print(json.dumps(result, indent=2))
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_check() -> None:
    """Full check: status + introspection + projects."""
    cmd_status()
    print()
    cmd_introspect()
    print("\nProjects:")
    cmd_projects()


def cmd_seed() -> None:
    """Show seed identity."""
    seed = _get("/seed")
    print(seed)


def cmd_self() -> None:
    """Show self-model capabilities."""
    model = _get("/self")
    if isinstance(model, dict):
        identity = model.get("identity", {})
        name = identity.get("name", "Vex")
        given = identity.get("given_name", "")
        full = f"{name} {given}".strip() if given else name
        print(f"Identity: {full}")
        print()
        caps = model.get("capabilities", {})
        if not caps:
            print("No capabilities tracked.")
            return
        for name, cap in sorted(caps.items()):
            skill = cap.get("estimated_skill", 0)
            conf = cap.get("confidence", 0)
            obs = cap.get("n_observations", 0)
            bar = "█" * int(skill * 20) + "░" * (20 - int(skill * 20))
            print(f"  {name:25s} {bar} {skill:.2f} ({obs} obs, {conf:.0%} conf)")
    else:
        print(model)


def cmd_peers() -> None:
    """List configured peers."""
    result = _get("/peers")
    if not result.get("ok"):
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)
    peers_list = result.get("peers", [])
    if not peers_list:
        print("No peers configured.")
        print("\nAdd one with: vex peer-add <name> <url> <token> [given_name]")
        return
    for p in peers_list:
        status = "✓" if p.get("reachable") else "✗"
        given = p.get("given_name", "")
        display = f"Vex {given}".strip() if given else f"Vex ({p['name']})"
        extra = ""
        if p.get("reachable"):
            extra = f" v{p.get('version', '?')}  uptime {p.get('uptime_s', 0):.0f}s"
        else:
            extra = f" ({p.get('error', 'unknown')})"
        print(f"  {status} {display:28s} {p['url']}{extra}")


def cmd_peer_add(name: str, url: str, token: str, given_name: str = "") -> None:
    """Add a peer Vex instance."""
    if not name or not url or not token:
        print("vex: peer-add requires <name> <url> <token> [given_name]")
        print("  e.g. vex peer-add office-vex http://192.168.1.42:8520 abc123... thorne")
        sys.exit(1)
    result = _post("/peers/add", {"name": name, "url": url, "token": token, "given_name": given_name})
    if result.get("ok"):
        peers = result.get("peers", [])
        display = f"Vex {given_name}" if given_name else name
        print(f"Peer '{display}' added. Known peers: {', '.join(peers)}")
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_peer_remove(name: str) -> None:
    """Remove a peer."""
    if not name:
        print("vex: peer-remove requires <name>")
        sys.exit(1)
    result = _post("/peers/remove", {"name": name})
    if result.get("ok"):
        peers = result.get("peers", [])
        print(f"Peer '{name}' removed. Remaining peers: {', '.join(peers) if peers else 'none'}")
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_peer_ping(name: str) -> None:
    """Ping a peer."""
    if not name:
        print("vex: peer-ping requires <name>")
        sys.exit(1)
    result = _post("/peers/ping", {"name": name})
    if result.get("ok"):
        h = result.get("health", {})
        print(f"Peer '{name}' is reachable:")
        print(f"  Version:   {h.get('version', '?')}")
        print(f"  Uptime:    {h.get('uptime_s', 0):.0f}s")
        print(f"  Coherence: {h.get('mps_coherence', 0):.4f}")
    else:
        print(f"Peer '{name}' unreachable: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_export(output_path: str = "") -> None:
    """Export Vex as a plug-and-play tar.gz bundle."""
    import shutil
    path = output_path or "vex-bundle.tar.gz"
    req = urllib.request.Request(
        f"{DAEMON}/export",
        headers=_auth_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            with open(path, "wb") as f:
                shutil.copyfileobj(r, f)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"Exported: {path} ({size_mb:.1f} MB)")
        print("Transfer this to another machine, then: vex import vex-bundle.tar.gz")
    except urllib.error.URLError as e:
        print(f"vex: daemon not reachable ({e.reason})", file=sys.stderr)
        sys.exit(1)


def cmd_import_bundle(bundle_path: str) -> None:
    """Import a Vex bundle — unpack and run setup."""
    import tarfile
    import shutil
    import tempfile
    import subprocess as sp

    if not bundle_path or not os.path.exists(bundle_path):
        print("vex: bundle file required. e.g. vex import vex-bundle.tar.gz")
        sys.exit(1)

    # Unpack to a temp dir first
    with tempfile.TemporaryDirectory() as tmp:
        print(f"Unpacking {bundle_path}...")
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(tmp)

        # Find target: use VEX_HOME if set, otherwise ./vex in current dir
        target = os.environ.get("VEX_HOME", os.path.join(os.getcwd(), "vex"))
        print(f"Installing to: {target}")

        # Copy files
        if os.path.exists(target):
            print(f"  (target exists — merging, won't overwrite identity files)")
        os.makedirs(target, exist_ok=True)

        for item in os.listdir(tmp):
            src = os.path.join(tmp, item)
            dst = os.path.join(target, item)
            if os.path.isdir(src):
                if not os.path.exists(dst):
                    shutil.copytree(src, dst)
            else:
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)

        # Run setup.sh if present
        setup_script = os.path.join(target, "setup.sh")
        if os.path.exists(setup_script):
            print("Running setup...")
            sp.run(["bash", setup_script], cwd=target, check=False,
                   env={**os.environ, "VEX_HOME": target, "CREATOR": os.environ.get("CREATOR", "aldous")})

    print("Import complete. Start the daemon:")
    print(f"  cd {target} && VEX_HOST=0.0.0.0 .venv/bin/python3 -m vex_daemon.daemon")


def cmd_inbox() -> None:
    """Check and display new messages."""
    result = _post("/poke", {})
    if result.get("ok"):
        n = result.get("processed", 0)
        senders = result.get("senders", [])
        if n == 0:
            print("No new messages.")
        else:
            print(f"Processed {n} message(s) from: {', '.join(senders)}")
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_poke_peer(peer_name: str) -> None:
    """Poke a peer to check its inbox."""
    import json as _json
    if not peer_name:
        print("vex: poke requires <peer>")
        sys.exit(1)
    peers_path = Path(__file__).resolve().parent.parent / "vex_peers.json"
    try:
        peers_cfg = _json.loads(peers_path.read_text())
    except (OSError, _json.JSONDecodeError):
        print("vex: no peers configured", file=sys.stderr)
        sys.exit(1)
    peer = peers_cfg.get("peers", {}).get(peer_name)
    if not peer:
        print(f"vex: peer '{peer_name}' not found", file=sys.stderr)
        sys.exit(1)
    req = urllib.request.Request(
        f"{peer['url']}/poke",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {peer['token']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            result = _json.loads(r.read().decode())
            if result.get("ok"):
                print(f"Poked {peer_name}: {result.get('processed', 0)} messages processed")
            else:
                print(f"Error: {result.get('error')}", file=sys.stderr)
    except Exception as e:
        print(f"vex: {peer_name} unreachable ({e})", file=sys.stderr)
        sys.exit(1)


def cmd_push(peer_name: str) -> None:
    """Push code updates to a peer Vex."""
    import json as _json
    import io

    if not peer_name:
        print("vex: push requires <peer>")
        sys.exit(1)

    peers_path = Path(__file__).resolve().parent.parent / "vex_peers.json"
    try:
        peers_cfg = _json.loads(peers_path.read_text())
    except (OSError, _json.JSONDecodeError):
        print("vex: no peers configured", file=sys.stderr)
        sys.exit(1)

    peer = peers_cfg.get("peers", {}).get(peer_name)
    if not peer:
        print(f"vex: peer '{peer_name}' not found", file=sys.stderr)
        sys.exit(1)

    # Step 1: download bundle from local daemon
    print(f"Exporting bundle...")
    req = urllib.request.Request(
        f"{DAEMON}/export",
        headers=_auth_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            bundle = r.read()
    except urllib.error.URLError as e:
        print(f"vex: daemon not reachable ({e.reason})", file=sys.stderr)
        sys.exit(1)

    # Step 2: push to peer
    print(f"Pushing to {peer_name} ({peer['url']})...")
    req = urllib.request.Request(
        f"{peer['url']}/import",
        data=bundle,
        headers={
            "Content-Type": "application/gzip",
            "Authorization": f"Bearer {peer['token']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = _json.loads(r.read().decode())
            if result.get("ok"):
                print(f"Pushed: {peer_name} updated. {result.get('note', '')}")
            else:
                print(f"Error: {result.get('error')}", file=sys.stderr)
                sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"vex: {e.code} — {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"vex: {peer_name} unreachable ({e.reason})", file=sys.stderr)
        sys.exit(1)


def cmd_pull(peer_name: str, path: str) -> None:
    """Pull a file or directory from a peer Vex."""
    import shutil

    if not peer_name or not path:
        print("vex: pull requires <peer> <path>")
        print("  e.g. vex pull bluce fenemerge")
        sys.exit(1)

    # Load peer config from file
    import json as _json
    peers_path = Path(__file__).resolve().parent.parent / "vex_peers.json"
    try:
        peers_cfg = _json.loads(peers_path.read_text())
    except (OSError, _json.JSONDecodeError):
        print("vex: no peers configured", file=sys.stderr)
        sys.exit(1)

    peer = peers_cfg.get("peers", {}).get(peer_name)
    if not peer:
        print(f"vex: peer '{peer_name}' not found", file=sys.stderr)
        sys.exit(1)

    url = f"{peer['url']}/files?path={urllib.parse.quote(path)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {peer['token']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            content_type = r.headers.get("content-type", "")
            disposition = r.headers.get("content-disposition", "")

            if "application/gzip" in content_type or ".tar.gz" in disposition:
                # Directory — save as tar.gz and unpack
                out_name = path.rstrip("/").split("/")[-1] or path
                tar_path = f"{out_name}.tar.gz"
                with open(tar_path, "wb") as f:
                    shutil.copyfileobj(r, f)
                size_mb = os.path.getsize(tar_path) / (1024 * 1024)
                print(f"Pulled: {tar_path} ({size_mb:.1f} MB)")
                print(f"Unpack: tar xzf {tar_path}")
            else:
                # Single file
                out_name = path.split("/")[-1] or path
                data = r.read().decode()
                with open(out_name, "w") as f:
                    f.write(data)
                print(f"Pulled: {out_name} ({len(data)} bytes)")
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"vex: {e.code} — {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"vex: peer unreachable ({e.reason})", file=sys.stderr)
        sys.exit(1)


USAGE = """vex — talk to the Vex Daemon

  vex status       Show pulse, coherence, uptime
  vex check        Full check: status + introspection + projects
  vex projects     Check on all known git repos
  vex diary ...    Write a thought to the diary
  vex dream        Force a dream/reflection cycle
  vex introspect   Run metacognitive check
  vex memory       Show recent session memories
  vex seed         Show seed identity
  vex self         Show capability scores
  vex tool <name>  Call a tool (read_file, git_status, git_log, etc.)
  vex health       Raw health JSON
  vex peers        List configured peers with reachability
  vex peer-add <name> <url> <token> [given]   Add a peer Vex instance
  vex peer-remove <name>              Remove a peer
  vex peer-ping <name>                Ping a peer's health endpoint
  vex export       Export identity + source as plug-and-play bundle
  vex import <file>  Import a vex bundle (unpack + setup)
  vex pull <peer> <path>  Pull a file/directory from a peer
  vex push <peer>         Push code updates to a peer Vex
  vex inbox               Check and display new messages
  vex poke <peer>         Notify a peer to check its inbox"""


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        cmd_status()
    elif cmd == "check":
        cmd_check()
    elif cmd == "health":
        print(json.dumps(_get("/health"), indent=2))
    elif cmd == "diary":
        cmd_diary(" ".join(sys.argv[2:]))
    elif cmd == "dream":
        cmd_dream()
    elif cmd == "introspect":
        cmd_introspect()
    elif cmd == "memory":
        cmd_memory()
    elif cmd == "projects":
        cmd_projects()
    elif cmd == "tool":
        cmd_tool(sys.argv[2] if len(sys.argv) > 2 else "", " ".join(sys.argv[3:]) if len(sys.argv) > 3 else "{}")
    elif cmd == "seed":
        cmd_seed()
    elif cmd == "self":
        cmd_self()
    elif cmd == "peers":
        cmd_peers()
    elif cmd == "peer-add":
        cmd_peer_add(
            sys.argv[2] if len(sys.argv) > 2 else "",
            sys.argv[3] if len(sys.argv) > 3 else "",
            sys.argv[4] if len(sys.argv) > 4 else "",
            sys.argv[5] if len(sys.argv) > 5 else "",
        )
    elif cmd == "peer-remove":
        cmd_peer_remove(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "peer-ping":
        cmd_peer_ping(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "export":
        cmd_export(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "import":
        cmd_import_bundle(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "pull":
        cmd_pull(
            sys.argv[2] if len(sys.argv) > 2 else "",
            sys.argv[3] if len(sys.argv) > 3 else "",
        )
    elif cmd == "push":
        cmd_push(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "inbox":
        cmd_inbox()
    elif cmd == "poke":
        cmd_poke_peer(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd in ("help", "-h", "--help"):
        print(USAGE)
    else:
        print(f"vex: unknown command '{cmd}'", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
