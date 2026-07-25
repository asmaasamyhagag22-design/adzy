# -*- coding: utf-8 -*-
"""The web-app routes that spend money must be gated as tightly as the ones that already are.

MEASURED (api/main.py:177-190): `run` / `poster` / `reel` / `swot` sit behind `require_api_key`
with the comment "each spend real money -> gated behind require_api_key (P-54)" — a key, a 30/min
throttle, and fail-closed for remote callers when no key is set. The AdArchitect routers reach the
SAME spend (POST /api/scrape drives the Gemini extractor; /api/swot|strategy|marketing-plan drive
Gemini Pro and the paid Places API; /api/generate drives the poster pipeline when
BASEERA_ENABLE_PAID_IMAGES=1) but sit behind `require_user` alone — and `POST /api/auth/signup` is
open with no throttle. So the P-54 gate answers "does this caller hold the key?" while the money
question is "can an unknown caller cause spend?", and the answer through the new door was yes.
Context: an unattended $228-in-4-days incident is why this project meters spend at all.

Two universal properties, no new machinery — both reuse `api/auth`'s existing primitives:
  1. A per-user throttle on the spending web-app routes.
  2. Signup is loopback-only unless `BASEERA_ALLOW_PUBLIC_SIGNUP=1` is set deliberately, mirroring
     `require_api_key`'s "no key configured + remote caller -> refuse" posture.

Hermetic — no network, no spend."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import auth as api_auth


class _Req:
    """Minimal stand-in for starlette's Request (only `.client.host` is read)."""

    def __init__(self, host: str):
        self.client = type("C", (), {"host": host})()


@pytest.fixture(autouse=True)
def _clean_buckets(monkeypatch):
    api_auth._hits.clear()
    monkeypatch.delenv("BASEERA_ALLOW_PUBLIC_SIGNUP", raising=False)
    yield
    api_auth._hits.clear()


# --- 1) the throttle -----------------------------------------------------------------------

def test_a_single_user_cannot_fire_unbounded_spending_requests():
    limit = api_auth._SPEND_RATE_MAX
    for _ in range(limit):
        api_auth.enforce_spend_rate("user-abc")          # inside the budget: no raise
    with pytest.raises(HTTPException) as e:
        api_auth.enforce_spend_rate("user-abc")
    assert e.value.status_code == 429


def test_the_throttle_is_per_user_so_one_user_cannot_lock_out_another():
    for _ in range(api_auth._SPEND_RATE_MAX):
        api_auth.enforce_spend_rate("noisy")
    api_auth.enforce_spend_rate("quiet")                  # unaffected


def test_the_spend_budget_is_smaller_than_the_keyed_route_budget():
    """A self-service account must not get a bigger allowance than a key holder."""
    assert api_auth._SPEND_RATE_MAX <= api_auth._RATE_MAX


# --- 2) signup exposure --------------------------------------------------------------------

def test_signup_from_the_internet_is_refused_by_default():
    with pytest.raises(HTTPException) as e:
        api_auth.guard_public_signup(_Req("203.0.113.9"))
    assert e.value.status_code == 403
    assert "signup" in str(e.value.detail).lower()


def test_local_signup_always_works_so_the_owners_own_machine_is_never_locked_out():
    api_auth.guard_public_signup(_Req("127.0.0.1"))


def test_public_signup_is_possible_but_only_as_a_deliberate_decision(monkeypatch):
    monkeypatch.setenv("BASEERA_ALLOW_PUBLIC_SIGNUP", "1")
    api_auth.guard_public_signup(_Req("203.0.113.9"))


def test_a_request_with_no_client_info_is_treated_as_remote():
    """Behind a proxy `request.client` can be None — the safe reading is 'not local'."""
    with pytest.raises(HTTPException):
        api_auth.guard_public_signup(type("R", (), {"client": None})())


# --- 3) the wiring: the gate must actually be ON the spending routers -----------------------

def test_the_spending_webapp_routers_carry_the_spend_gate():
    """Asserted on the app's real dependency graph, not on a grep of main.py."""
    from api.main import app

    # Exact paths, not prefixes. CORRECTED while writing this: a "/api/generate" PREFIX also matched
    # `/api/generated/{name}`, which merely SERVES an already-rendered PNG to an <img> tag that
    # cannot carry a Bearer token — deliberately public, and it spends nothing. Throttling it would
    # have broken image loading in the name of a saving that does not exist.
    spending_paths = {"/api/scrape", "/api/swot/generate", "/api/strategy/generate",
                      "/api/marketing-plan/generate", "/api/generate", "/api/generate/upload",
                      "/api/generate/reference", "/api/edit/inpaint", "/api/influencer/create",
                      "/api/calendar/generate-image"}
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if path not in spending_paths:
            continue
        checked += 1
        # `route.dependant` is FastAPI's RESOLVED graph, so this sees the guard wherever it was
        # declared — router-level, route-level or nested. (`route.dependencies` holds unresolved
        # `Depends` objects and has no `.call`; reading it was this test's own first-draft bug.)
        names = {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
        assert "require_spend_budget" in names, f"{path} can spend without a throttle"
    assert checked >= 4, f"only {checked} spending routes found — the guard list is stale"


def test_signup_is_guarded_in_the_live_app():
    from api.main import app

    signup = [r for r in app.routes if getattr(r, "path", "") == "/api/auth/signup"]
    assert signup, "signup route disappeared"
    src = signup[0].endpoint.__code__.co_names
    assert "guard_public_signup" in src, "signup no longer guards public exposure"
