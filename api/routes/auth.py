"""User auth routes for the web app: signup / login / me (Bearer JWT).

Contract the frontend depends on (frontend/index.html):
  * POST /api/auth/signup {email, password, full_name} -> {access_token, user}
  * POST /api/auth/login  {email, password}            -> {access_token, user}
  * GET  /api/auth/me     (Bearer)                      -> the user object
A bad/expired/missing token on a protected route returns **401** (the frontend treats 401 as
"session expired" and logs out — 403 would trap the user). Error envelope is FastAPI's default
`{"detail": ...}`.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.auth import guard_public_signup
from api.persistence import get_persistence
from api.user_auth import decode_token, issue_token, user_for_token

router = APIRouter()

# Light email shape check — avoids a hard dependency on `email-validator` (pydantic EmailStr).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _store():
    """The configured persistence backend — the file stores unless an operator selected otherwise.

    Routes never name a store. With no ``PERSISTENCE_BACKEND`` set this resolves to the legacy
    adapter over UserStore, so the behaviour below is unchanged; selecting `postgres` moves these
    three endpoints without editing them.
    """
    return get_persistence()


class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""

    @field_validator("email")
    @classmethod
    def _email_ok(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def require_user(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency: resolve the Bearer token to a user, or 401. Use on every protected
    web-app route so an expired token cleanly logs the frontend out."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    user = user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists.")
    return user


@router.post("/auth/signup", response_model=AuthResponse)
def signup(req: SignupRequest, request: Request) -> AuthResponse:
    # Every account can trigger paid generation, so account creation is loopback-only unless the
    # operator opened it deliberately (api/auth.guard_public_signup).
    guard_public_signup(request)
    if len(req.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    user = _store().create_user(email=req.email, password=req.password, full_name=req.full_name)
    if user is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return AuthResponse(access_token=issue_token(user["id"]), user=user)


@router.post("/auth/login", response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    user = _store().authenticate(email=req.email, password=req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return AuthResponse(access_token=issue_token(user["id"]), user=user)


@router.get("/auth/me")
def me(user: dict = Depends(require_user)) -> dict:
    return user
