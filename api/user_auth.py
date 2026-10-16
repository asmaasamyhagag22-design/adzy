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
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Serialises the check-then-write in `UserStore.create`. Signup is a sync endpoint, so FastAPI runs
# concurrent signups on separate threadpool threads against one shared users.json.
_USERS_LOCK = threading.RLock()


# ---------------------------------------------------------------------
# storage location (env-overridable so tests get an isolated tmp dir)
# ---------------------------------------------------------------------

# Anchored on the REPOSITORY, not the working directory. MEASURED: with the old relative default,
# starting uvicorn from `C:/Users/Admin` put every account, the signing secret, the brand catalog and
# all generated images under `C:/Users/Admin/outputs/webapp` — so logins failed, tokens were
# invalidated by a freshly generated secret, and the catalog read empty, all without an error. And
# api/main.py explicitly supports being run from elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def webapp_dir() -> Path:
    """Where all web-app state lives. `BASEERA_WEBAPP_DIR` still overrides it (tests, containers);
    a RELATIVE override is resolved against the repo, never against the launch directory."""
    raw = os.environ.get("BASEERA_WEBAPP_DIR", "outputs/webapp")
    d = Path(raw)
    if not d.is_absolute():
        d = _REPO_ROOT / d
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
        # A temp file private to this writer — a shared `users.json.tmp` lets two threads publish
        # each other's partial bytes (and raises PermissionError on Windows).
        tmp = self._path.with_suffix(f".json.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @staticmethod
    def _public(user: dict) -> dict:
        """The user object the frontend stores as `currentUser` — NEVER includes the hash."""
        return {"id": user["id"], "email": user["email"],
                "full_name": user.get("full_name", ""), "created_at": user.get("created_at")}

    def create(self, email: str, password: str, full_name: str = "") -> Optional[dict]:
        """Create a user. Returns the PUBLIC user dict, or None if the email already exists.

        The whole check-then-write runs under one lock. It used to be a bare read-modify-write, and
        signup is a sync endpoint, so FastAPI ran two of them on separate threadpool threads: both
        loaded the same base dict and the second `_save` overwrote the first user completely. The
        victim held a validly signed token for an account that no longer existed, so `require_user`
        401'd them and the app logged out someone who had just registered successfully. The same
        window let one address be registered TWICE — both callers passed the `email in data` check
        and the second hash replaced the first."""
        email = (email or "").strip().lower()
        with _USERS_LOCK:
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

    def get_by_email(self, email: str) -> Optional[dict]:
        user = self._load().get((email or "").strip().lower())
        return self._public(user) if user else None

    def emails(self) -> list[str]:
        """Registered addresses — used by `python -m api.claim_brands --list`."""
        return list(self._load().keys())


def user_for_token(token: str) -> Optional[dict]:
    """Resolve a signed token to an account that still exists, or return ``None``.

    Shared by the normal ``require_user`` dependency and job ownership checks so both boundaries
    agree on what constitutes an authenticated web-app principal. That sharing is why the backend
    switch is applied HERE rather than in the route: resolving identity from PostgreSQL for requests
    and from the JSON file for job-ownership checks would let the same token be two different
    principals, which is worse than either store being wrong on its own.

    The provider is imported lazily because it adapts THIS module — importing it at module scope
    would recreate the exact import cycle C7 removed. With no ``PERSISTENCE_BACKEND`` set the
    provider is the legacy adapter, so this is the same UserStore lookup it has always been.
    """
    payload = decode_token(str(token or ""))
    subject = str((payload or {}).get("sub") or "")
    if not subject:
        return None
    from api.persistence import get_persistence

    return get_persistence().user_for_token_subject(subject)
