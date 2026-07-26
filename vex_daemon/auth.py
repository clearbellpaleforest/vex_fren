"""
Auth for the Vex Daemon.

A single bearer token gates all mutating endpoints. The token is
generated on first run and stored in a 0600 file next to the daemon.
localhost is NOT a trust boundary once this code is public: other users,
rogue local processes, and DNS-rebinding browser tabs can all reach
127.0.0.1. Every write must prove it holds the token.
"""

import os
import secrets
import stat
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from config import TOKEN_PATH

# Reject request bodies larger than this — prevents disk/memory DoS via
# oversized diary/memory writes.
MAX_BODY_BYTES = 256 * 1024  # 256 KB


def _load_or_create_token() -> str:
    """Return the daemon token, generating a 0600 file on first run."""
    if TOKEN_PATH.exists():
        token = TOKEN_PATH.read_text().strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    TOKEN_PATH.write_text(token)
    try:
        os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # Windows / read-only FS — file defaults apply
    return token


TOKEN = _load_or_create_token()


def check_auth(request: Request) -> JSONResponse | None:
    """Return an error response if the request is not authorized, else None.

    Accepts `Authorization: Bearer <token>`. Uses constant-time compare.
    """
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    supplied = header[len(prefix):] if header.startswith(prefix) else ""
    if not supplied or not secrets.compare_digest(supplied, TOKEN):
        return JSONResponse(
            {"ok": False, "error": "unauthorized"}, status_code=401
        )
    return None


async def read_json_limited(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """Read and parse a JSON body, rejecting oversized payloads.

    Returns (body, None) on success or (None, error_response) on failure.
    Guards against a Content-Length lie by also measuring the actual bytes.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None, JSONResponse(
            {"ok": False, "error": "payload too large"}, status_code=413
        )
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return None, JSONResponse(
            {"ok": False, "error": "payload too large"}, status_code=413
        )
    try:
        import json
        return json.loads(raw or b"{}"), None
    except (ValueError, TypeError):
        return None, JSONResponse(
            {"ok": False, "error": "invalid JSON"}, status_code=400
        )
