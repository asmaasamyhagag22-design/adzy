"""Ownership principals for process-local jobs.

The web app already has authenticated users, so its jobs reuse that identity.  The legacy
``/api/run`` route has no user/session identity; it receives an opaque per-job capability instead.
Only a one-way capability digest is stored on the Job.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Request

from .user_auth import user_for_token


#: Public because the persistence layer must parse a principal to choose the right durable lookup,
#: and a second copy of these two strings in `api/persistence.py` would be a second definition of
#: what "owned by a user" means.
USER_PREFIX = "user:"
CAPABILITY_PREFIX = "capability:"

_USER_PREFIX = USER_PREFIX
_CAPABILITY_PREFIX = CAPABILITY_PREFIX
_CAPABILITY_DOMAIN = b"baseera-job-capability\0"


def user_job_principal(user_id: str) -> str:
    """Stable internal principal for one existing authenticated web-app user."""
    value = str(user_id or "").strip()
    if not value:
        raise ValueError("job owner user id is required")
    return _USER_PREFIX + value


def _capability_principal(token: str) -> str:
    raw = str(token or "").encode("utf-8")
    digest = hashlib.sha256(_CAPABILITY_DOMAIN + raw).hexdigest()
    return _CAPABILITY_PREFIX + digest


def issue_job_capability() -> tuple[str, str]:
    """Return ``(raw capability, stored principal)`` using 256 bits of OS randomness."""
    token = secrets.token_urlsafe(32)
    return token, _capability_principal(token)


def request_job_principals(request: Request) -> tuple[str, ...]:
    """Resolve only credentials the request actually supplied; invalid credentials yield none."""
    principals: list[str] = []

    authorization = str(request.headers.get("authorization") or "")
    if authorization.lower().startswith("bearer "):
        user = user_for_token(authorization[7:].strip())
        if user:
            principals.append(user_job_principal(str(user.get("id") or "")))

    capability = str(request.headers.get("x-job-token") or "")
    # Bound attacker-controlled hashing input.  Issued tokens are ~43 characters.
    if 8 <= len(capability) <= 512:
        principals.append(_capability_principal(capability))

    return tuple(principals)


def bearer_authentication_failed(request: Request) -> bool:
    """True only when the caller explicitly supplied a Bearer credential that is no longer valid.

    The SPA treats 401 as session expiry and logs out.  This check is independent of the job id,
    so returning 401 cannot be used to distinguish an existing job from an absent one.
    """
    authorization = str(request.headers.get("authorization") or "")
    return (authorization.lower().startswith("bearer ")
            and user_for_token(authorization[7:].strip()) is None)


def request_owns_job(request: Request, owner_principal: str) -> bool:
    """Constant-time equality against every valid principal presented by the caller."""
    expected = str(owner_principal or "")
    if not expected:
        return False
    return any(hmac.compare_digest(expected, candidate)
               for candidate in request_job_principals(request))
