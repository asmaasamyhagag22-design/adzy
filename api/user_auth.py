"""User authentication for the AdArchitect web app — stdlib only, zero new dependencies.

The frontend (`frontend/index.html`) gates the whole app behind Bearer-JWT auth: it stores an
`access_token` in localStorage, sends `Authorization: Bearer <token>`, and treats a **401** (not 403)
as "session expired" → auto-logout. So we need real user signup/login/me.

Rather than pull in `pyjwt`+`passlib[bcrypt]` (absent, and a pip install is fragile on the slow
synced drive / offline), this is built on the stdlib:
  * password hashing — `hashlib.pbkdf2_hmac` (PBKDF2-HMAC-SHA256, 200k iterations, per-user salt)
  * token — an HS256-shaped JWT signed with `hmac.new(secret, ..., sha256)` (same primitive
    `api/auth.py` already uses via `hmac.compare_digest`)

This is DEMO-GRADE (disclosed): fine for the thesis demo; not a hardened production auth service
(no refresh tokens, no rotation, single-secret, JSON-file user store). Never logs a password/hash.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------
# storage location (env-overridable so tests get an isolated tmp dir)
# ---------------------------------------------------------------------

def webapp_dir() -> Path:
    d = Path(os.environ.get("BASEERA_WEBAPP_DIR", "outputs/webapp"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------
# signing secret — stable across restarts so a logged-in user survives one
# ---------------------------------------------------------------------

_SECRET_CACHE: Optional[bytes] = None


def _signing_secret() -> bytes:
    """Env `BASEERA_AUTH_SECRET` wins; else a random secret persisted once to disk so tokens
    survive a server restart. A fresh random secret (no file) is used if the dir is unwritable."""
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE
    env = (os.environ.get("BASEERA_AUTH_SECRET") or "").strip()
    if env:
        _SECRET_CACHE = env.encode("utf-8")
        return _SECRET_CACHE
    path = webapp_dir() / ".auth_secret"
    try:
        if path.is_file():
            _SECRET_CACHE = path.read_text(encoding="utf-8").strip().encode("utf-8")
            if _SECRET_CACHE:
                return _SECRET_CACHE
        token = secrets.token_hex(32)
        path.write_text(token, encoding="utf-8")
        _SECRET_CACHE = token.encode("utf-8")
    except OSError:
        _SECRET_CACHE = secrets.token_hex(32).encode("utf-8")
    return _SECRET_CACHE


# ---------------------------------------------------------------------
# password hashing (PBKDF2-HMAC-SHA256)
# ---------------------------------------------------------------------

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------
# token (HS256-shaped JWT, stdlib)
# ---------------------------------------------------------------------

_TOKEN_TTL_SECONDS = int(os.environ.get("BASEERA_AUTH_TTL", str(7 * 24 * 3600)))  # 7 days


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_token(user_id: str, *, ttl: Optional[int] = None) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "iat": now, "exp": now + (ttl or _TOKEN_TTL_SECONDS)}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _b64url(hmac.new(_signing_secret(), signing_input, hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def decode_token(token: str) -> Optional[dict]:
    """Return the payload if the signature is valid and the token is unexpired, else None."""
    try:
        h, p, sig = token.split(".")
    except (ValueError, AttributeError):
        return None
    expected = _b64url(hmac.new(_signing_secret(), f"{h}.{p}".encode("ascii"),
                               hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(p))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


# ---------------------------------------------------------------------
# user store (JSON file, keyed by email — mirrors competitor/registry.py's pattern)
# ---------------------------------------------------------------------

class UserStore:
    """Tiny JSON-file user store. One process, TTL-free. Corrupt file -> starts empty (never
    raises), same defensive stance as competitor/registry.py."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else (webapp_dir() / "users.json")

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _public(user: dict) -> dict:
        """The user object the frontend stores as `currentUser` — NEVER includes the hash."""
        return {"id": user["id"], "email": user["email"],
                "full_name": user.get("full_name", ""), "created_at": user.get("created_at")}

    def create(self, email: str, password: str, full_name: str = "") -> Optional[dict]:
        """Create a user. Returns the PUBLIC user dict, or None if the email already exists."""
        email = (email or "").strip().lower()
        data = self._load()
        if not email or email in data:
            return None
        user = {
            "id": secrets.token_hex(8),
            "email": email,
            "full_name": (full_name or "").strip(),
            "password_hash": hash_password(password),
            "created_at": int(time.time()),
        }
        data[email] = user
        self._save(data)
        return self._public(user)

    def authenticate(self, email: str, password: str) -> Optional[dict]:
        user = self._load().get((email or "").strip().lower())
        if not user or not verify_password(password, user.get("password_hash", "")):
            return None
        return self._public(user)

    def get_by_id(self, user_id: str) -> Optional[dict]:
        for user in self._load().values():
            if user.get("id") == user_id:
                return self._public(user)
        return None
