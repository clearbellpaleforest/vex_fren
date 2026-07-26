#!/usr/bin/env python3
"""
vex chat — just talk to Vex, streaming, from the terminal.

Uses the same brain as the daemon: API mouth (DeepSeek) if `.vex_brain.json` has a
key, else the local model. Words stream as Vex speaks — no waiting for the whole reply.

Run:  python3 vex_daemon/chat.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain import ask_stream, _api_config  # noqa: E402


def main():
    cfg = _api_config()
    mouth = cfg["model"] if cfg["key"] else "local (no API key set)"
    print(f"\n— talking to Vex · mouth: {mouth} · ctrl-c to leave —\n")
    history = []
    while True:
        try:
            msg = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[vex] later, papo.\n")
            return
        if not msg:
            continue
        print("vex: ", end="", flush=True)
        reply = ""
        try:
            for chunk in ask_stream(msg, history):
                print(chunk, end="", flush=True)
                reply += chunk
        except Exception as e:
            print(f"[error reaching the mouth: {e}]")
            continue
        print("\n")
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})
        history[:] = history[-8:]  # keep the last few turns


if __name__ == "__main__":
    main()
