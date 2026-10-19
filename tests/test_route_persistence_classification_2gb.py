# -*- coding: utf-8 -*-
"""Every registered route is classified, and the arithmetic is proved from the live app.

§39. The danger this closes is not that a route is unwired — it is that a route is unwired and
NOBODY KNOWS. A capability matrix kept in a document drifts from the code within one phase and then
reads as reassurance. So the matrix lives here, the test fails when a route appears without a
classification, and each class carries a mechanical check rather than a promise.

COUNTING, which an earlier revision of this file got wrong in a way worth recording.

`app.routes` holds 51 route OBJECTS. That is not the number of endpoints, and it is not the number
of paths: FastAPI registers `/docs`, `/redoc`, `/openapi.json` and `/docs/oauth2-redirect` with
GET *and* HEAD, and several business paths carry two methods on one object. Counting objects,
counting `(method, path)` pairs and counting distinct paths give 51, 55 and 48 — three different
true answers to three different questions. A report that mixes them produces sums that do not
add up, which is exactly what happened.

This file therefore counts ONE way — `(METHOD, PATH)` — states the identity it is proving, and
fails if the parts do not sum to the whole:

    FRAMEWORK + POSTGRES_WIRED + BACKEND_INDEPENDENT + INTENTIONALLY_UNAVAILABLE == every pair

THE THREE BUSINESS CLASSES

  POSTGRES_WIRED
      Reaches persistence only through `api.persistence`. Under `PERSISTENCE_BACKEND=postgres` its
      reads and writes are PostgreSQL's, and it makes no legacy authoritative write.

  BACKEND_INDEPENDENT
      Touches no business-state store, or serves BYTES from the filesystem. Serving a file is not a
      storage authority: `/api/generated/{name}` hands back an image that already exists on disk,
      and the frozen legacy orphans have no DB row by design. Calling that a "dual read" would
      confuse a file server with a source of truth.

  INTENTIONALLY_UNAVAILABLE_IN_POSTGRES
      Not wired, and must FAIL rather than quietly answer from the legacy stores. Every entry needs
      a stated reason.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")


class _Stub: ...


for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

import pytest  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]

FRAMEWORK = "FRAMEWORK"
POSTGRES_WIRED = "POSTGRES_WIRED"
BACKEND_INDEPENDENT = "BACKEND_INDEPENDENT"
UNAVAILABLE = "INTENTIONALLY_UNAVAILABLE_IN_POSTGRES"

W = POSTGRES_WIRED
B = BACKEND_INDEPENDENT
U = UNAVAILABLE

#: The three `/from-profile` routes were classified JOB while the whole capability-token surface
#: was unwired. Measured against the live app they touch NO business-state store at all: each takes
#: a profile in the request body, does its work and returns it — no job, no brand, no store. So
#: they are backend-independent, and were never part of the Job lifecycle to cross. Recorded here
#: rather than quietly re-labelled, because the earlier classification was a true statement about
#: a surface rather than about these three handlers.
_FROM_PROFILE_NOTE = (
    "synchronous: the profile arrives in the request body, the answer is returned in the response, "
    "and nothing about a job, a brand or any store is read or written — verified against the live "
    "handlers, which name no persistence at all")
#: Kept as a named constant with no users, deliberately. It is the sentence this file carried for
#: two phases, and leaving it visible next to the now-wired routes is how a reader learns that the
#: gap closed rather than that it was never there.
_GALLERY_NOTE_HISTORICAL = (
    "WAS: schema v1.3 adds gallery_blobs/gallery_entries, but the extraction, provider operations "
    "and route changes are not built; postgres mode refuses the gallery write instead of appending "
    "to history.json beside a PostgreSQL-owned brand. RESOLVED by the gallery cutover: the bytes "
    "live in a managed content-addressed store, the entries live in gallery_entries, and "
    "supports_gallery_writes() answers True on both backends.")

#: (METHOD, PATH) -> (class, domain, note). `note` is required for anything not wired.
MATRIX: dict[tuple[str, str], tuple[str, str, str]] = {
    # ---- identity ----
    ("POST", "/api/auth/signup"): (W, "AUTH", ""),
    ("POST", "/api/auth/login"): (W, "AUTH", ""),
    ("GET", "/api/auth/me"): (W, "AUTH", ""),

    # ---- brands ----
    ("GET", "/api/brands"): (W, "BRAND", ""),
    ("GET", "/api/brands/{domain_key}"): (W, "BRAND", ""),
    ("DELETE", "/api/brands/{domain_key}"): (W, "BRAND", ""),
    ("POST", "/api/brands/{domain_key}/adopt-analysis"): (W, "ANALYSIS", ""),
    ("GET", "/api/brands/{domain_key}/owner-inputs"): (W, "OWNER_INPUTS", ""),
    ("POST", "/api/brands/{domain_key}/owner-inputs"): (W, "OWNER_INPUTS", ""),
    ("POST", "/api/brands/{domain_key}/trends"): (W, "ANALYSIS", ""),

    # ---- canonical review and exact poster recovery/delivery ----
    # PostgreSQL remains business authority; review uses local SQLite only for progress.
    ("POST", "/api/review/start"): (W, "APPROVAL", ""),
    ("GET", "/api/review/{workflow_id}"): (W, "APPROVAL", ""),
    ("POST", "/api/review/{workflow_id}/resume"): (W, "APPROVAL", ""),
    ("GET", "/api/poster/operations/latest"): (W, "IMAGE", ""),
    ("GET", "/api/poster/operations/{operation_key}"): (W, "IMAGE", ""),
    ("GET", "/api/poster/operations/{operation_key}/candidates/{candidate_id}/preview"): (W, "IMAGE", ""),
    ("GET", "/api/poster/runs/{run_id}/delivery"): (W, "IMAGE", ""),
    ("GET", "/api/poster/runs/{run_id}/download"): (W, "IMAGE", ""),

    # Local composition still reads canonical ownership/evidence from PostgreSQL.
    ("GET", "/api/poster/runs/{run_id}/copy-editor"): (W, "IMAGE", ""),
    ("POST", "/api/poster/runs/{run_id}/copy-preview"): (W, "IMAGE", ""),
    ("POST", "/api/poster/runs/{run_id}/copy-revisions"): (W, "IMAGE", ""),

    # ---- scrape ----
    ("POST", "/api/scrape"): (W, "SCRAPE", ""),

    # ---- jobs ----
    ("GET", "/api/jobs/{job_id}"): (W, "JOB", ""),
    ("GET", "/api/jobs/{job_id}/result"): (W, "JOB", ""),
    ("GET", "/api/jobs/{job_id}/stream"): (W, "JOB", ""),

    # ---- analysis / gates ----
    ("POST", "/api/swot/generate"): (W, "SWOT", ""),
    ("POST", "/api/swot/approve"): (W, "APPROVAL", ""),
    ("POST", "/api/strategy/generate"): (W, "STRATEGY", ""),
    ("POST", "/api/strategy/decide"): (W, "APPROVAL", ""),
    ("POST", "/api/marketing-plan/generate"): (W, "CALENDAR", ""),
    ("GET", "/api/intel/{domain_key}"): (W, "ANALYSIS", ""),
    ("GET", "/api/prompt-preview/{domain_key}"): (W, "ANALYSIS", ""),
    ("GET", "/api/social/performance/{domain_key}"): (W, "SOCIAL", ""),

    # ---- creative ----
    ("POST", "/api/poster/contract"): (W, "IMAGE", ""),
    ("POST", "/api/generate"): (W, "IMAGE", ""),
    ("POST", "/api/generate/reference"): (W, "IMAGE", ""),
    ("POST", "/api/generate/upload"): (W, "IMAGE", ""),
    ("POST", "/api/edit/inpaint"): (W, "IMAGE", ""),
    ("POST", "/api/calendar/generate-image"): (W, "CALENDAR", ""),
    ("POST", "/api/reel"): (W, "REEL", ""),
    # Provider-free, read-only at Gate 2D.1, but its exact StrategyVersion authority is the
    # PostgreSQL canonical_strategy_context. It refuses on the legacy provider.
    ("POST", "/api/reel/canonical/prepare"): (W, "REEL", ""),
    ("GET", "/api/history"): (W, "GALLERY", ""),

    # ---- influencer ----
    ("POST", "/api/influencer/create"): (W, "INFLUENCER", ""),
    ("POST", "/api/influencer/another"): (W, "INFLUENCER", ""),
    ("POST", "/api/influencer/select"): (W, "INFLUENCER", ""),
    ("POST", "/api/influencer/validate"): (W, "INFLUENCER", ""),
    ("POST", "/api/influencer/poster"): (W, "INFLUENCER", ""),
    ("GET", "/api/influencer/defaults"): (W, "INFLUENCER", ""),

    # ---- capability-token surface ----
    ("POST", "/api/run"): (W, "JOB", ""),
    ("POST", "/api/poster/from-profile"): (B, "IMAGE", _FROM_PROFILE_NOTE),
    ("POST", "/api/reel/from-profile"): (B, "REEL", _FROM_PROFILE_NOTE),
    ("POST", "/api/swot/from-profile"): (B, "SWOT", _FROM_PROFILE_NOTE),

    # ---- competitors: a separate registry keyed by a profile-derived brand_ref ----
    ("GET", "/api/brands/{domain_key}/competitors"): (
        B, "COMPETITORS",
        "the competitor registry is its own store keyed by brand_ref, not brand state; the BRAND "
        "resolution that authorizes these routes does go through the provider"),
    ("POST", "/api/brands/{domain_key}/competitors"): (B, "COMPETITORS", "see GET /competitors"),
    ("DELETE", "/api/brands/{domain_key}/competitors/{key}"): (
        B, "COMPETITORS", "see GET /competitors"),

    # ---- byte serving and static ----
    ("GET", "/api/generated/{name}"): (
        B, "ASSETS",
        "serves bytes that already exist on disk; the frozen legacy orphans deliberately have no "
        "DB row, and requiring one would make previously-served images 404"),
    ("GET", "/api/showcase"): (B, "GALLERY", "reads curated files, writes nothing"),
    ("GET", "/api/health"): (B, "HEALTH", "no storage"),
    ("GET", "/"): (B, "STATIC", "serves the SPA"),
    ("GET", "/app"): (B, "STATIC", "serves the SPA"),

    # ---- framework ----
    ("GET", "/docs"): (FRAMEWORK, "STATIC", "FastAPI OpenAPI UI"),
    ("HEAD", "/docs"): (FRAMEWORK, "STATIC", "FastAPI OpenAPI UI"),
    ("GET", "/docs/oauth2-redirect"): (FRAMEWORK, "STATIC", "FastAPI OpenAPI UI"),
    ("HEAD", "/docs/oauth2-redirect"): (FRAMEWORK, "STATIC", "FastAPI OpenAPI UI"),
    ("GET", "/redoc"): (FRAMEWORK, "STATIC", "FastAPI OpenAPI UI"),
    ("HEAD", "/redoc"): (FRAMEWORK, "STATIC", "FastAPI OpenAPI UI"),
    ("GET", "/openapi.json"): (FRAMEWORK, "STATIC", "FastAPI schema"),
    ("HEAD", "/openapi.json"): (FRAMEWORK, "STATIC", "FastAPI schema"),
    ("MOUNT", "/assets"): (FRAMEWORK, "STATIC", "mounted static files"),
}

#: Domains that must be PostgreSQL-authoritative before a cutover can be declared. Kept here so the
#: gap is a failing list in code rather than a sentence in a report.
CORE_DOMAINS = {"AUTH", "BRAND", "SCRAPE", "JOB", "ANALYSIS", "SWOT", "APPROVAL", "STRATEGY",
                "CALENDAR", "SOCIAL", "IMAGE", "INFLUENCER", "OWNER_INPUTS", "GALLERY", "REEL"}


def _pairs() -> set[tuple[str, str]]:
    """Every (METHOD, PATH) the live app serves. MOUNT stands in for a method-less mount."""
    from api.main import app

    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = sorted(getattr(route, "methods", []) or [])
        if not methods:
            out.add(("MOUNT", path))
            continue
        out.update((m, path) for m in methods)
    return out


def _classified(kind: str) -> set[tuple[str, str]]:
    return {p for p, (k, _d, _n) in MATRIX.items() if k == kind}


# =====================================================================
# the arithmetic
# =====================================================================

def test_every_registered_route_is_classified():
    """A new route with no entry fails here rather than shipping with unknown storage authority."""
    missing = sorted(_pairs() - set(MATRIX))
    assert not missing, f"(method, path) pairs with no persistence classification: {missing}"


def test_the_matrix_describes_no_route_that_does_not_exist():
    """Guards the matrix from rotting into a description of a previous version of the app."""
    stale = sorted(set(MATRIX) - _pairs())
    assert not stale, f"the matrix lists routes that are no longer registered: {stale}"


def test_the_classification_arithmetic_closes():
    """The identity the previous report failed to satisfy, asserted instead of narrated."""
    live = _pairs()
    framework = _classified(FRAMEWORK)
    wired = _classified(POSTGRES_WIRED)
    independent = _classified(BACKEND_INDEPENDENT)
    unavailable = _classified(UNAVAILABLE)

    for a, b in ((framework, wired), (framework, independent), (framework, unavailable),
                 (wired, independent), (wired, unavailable), (independent, unavailable)):
        assert not (a & b), f"a route is in two classes at once: {sorted(a & b)}"

    total = len(framework) + len(wired) + len(independent) + len(unavailable)
    assert total == len(live), (
        f"{len(framework)} framework + {len(wired)} wired + {len(independent)} independent + "
        f"{len(unavailable)} unavailable = {total}, but the app serves {len(live)}")
    business = len(live) - len(framework)
    assert len(wired) + len(independent) + len(unavailable) == business


def test_every_unwired_route_states_why():
    for pair, (kind, _domain, note) in sorted(MATRIX.items()):
        if kind in (UNAVAILABLE, BACKEND_INDEPENDENT, FRAMEWORK):
            assert note.strip(), f"{pair} is {kind} with no stated reason"


#: The core domains PostgreSQL cannot serve yet. Written down so the gate is a CHECKED fact rather
#: than a sentence in a report — and asserted as an equality, so it fails when the situation
#: improves as loudly as when it degrades, forcing matrix and cutover verdict to move together.
#: EMPTY, and asserted as an equality in both directions. It held {GALLERY, IMAGE, JOB}, then
#: {GALLERY, IMAGE}; it is now closed. An equality rather than a subset check is what forces the
#: matrix and the cutover verdict to move together — this fails as loudly when a core route
#: REGRESSES to unavailable as it did when one was still unwired.
KNOWN_CORE_GAP: set[str] = set()


def test_the_core_gap_is_exactly_the_known_one():
    """The cutover gate. It is now EMPTY, and that is the claim under test."""
    blocked = {d for _p, (k, d, _n) in MATRIX.items() if k == UNAVAILABLE and d in CORE_DOMAINS}
    assert blocked == KNOWN_CORE_GAP, (
        f"the unwired CORE domains are now {sorted(blocked)}, not {sorted(KNOWN_CORE_GAP)}; "
        f"update the matrix and the cutover verdict together")


def test_no_core_domain_is_unwired():
    """The gate that used to say 'blocked', inverted because the situation inverted.

    It asserted `KNOWN_CORE_GAP` was NON-empty, so nothing downstream could read the suite as
    ready. Every core domain is now PostgreSQL-authoritative, so the same file has to say that
    instead — and it says it as a check over the live matrix rather than as an edited constant, so
    a route added tomorrow with no wiring reopens the gap here rather than shipping quietly.
    """
    unwired = sorted(d for _p, (k, d, _n) in MATRIX.items()
                     if k == UNAVAILABLE and d in CORE_DOMAINS)
    assert not unwired, f"these CORE domains are still unwired: {unwired}"


def test_every_unavailable_route_is_outside_the_core():
    """Nothing is unavailable at all right now; if that changes, it must be a non-core route."""
    unavailable = {(p, d) for p, (k, d, _n) in MATRIX.items() if k == UNAVAILABLE}
    assert not {d for _p, d in unavailable} & CORE_DOMAINS, unavailable


# =====================================================================
# the shape check behind POSTGRES_WIRED
# =====================================================================

#: The file stores' authoritative surface. Naming any of these inside a wired handler means that
#: handler can answer from — or write to — the store the switch is supposed to have replaced.
_LEGACY_NAMES = {"BrandStore", "UserStore", "read_history", "record_history"}


def _handlers_for(pair: tuple[str, str]) -> list[tuple[Path, str]]:
    from api.main import app

    method, path = pair
    out = []
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        methods = sorted(getattr(route, "methods", []) or []) or ["MOUNT"]
        if method not in methods:
            continue
        endpoint = getattr(route, "endpoint", None)
        module = getattr(endpoint, "__module__", "")
        name = getattr(endpoint, "__name__", "")
        if module.startswith("api.") and name:
            out.append((_ROOT.joinpath(*module.split(".")).with_suffix(".py"), name))
    return out


def _names_used_in(source: str, function: str) -> set[str]:
    """Legacy store names appearing inside ONE function body.

    Per-function rather than per-module on purpose: a module can legitimately serve both a wired
    route and a deliberately legacy neighbour, and a module-wide check would either fail on the
    neighbour or have to be relaxed into uselessness. The question that matters is what THIS
    handler can reach.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            found = set()
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id in _LEGACY_NAMES:
                    found.add(inner.id)
                elif isinstance(inner, ast.Attribute) and inner.attr in _LEGACY_NAMES:
                    found.add(inner.attr)
            return found
    return set()


@pytest.mark.parametrize(
    "pair", sorted(p for p, (k, _d, _n) in MATRIX.items() if k == POSTGRES_WIRED))
def test_a_wired_route_handler_does_not_name_the_legacy_stores(pair):
    """The mechanical half of POSTGRES_WIRED: a wired handler may not reach the file store by name.

    Syntactic, and deliberately so. The integration suites prove the behaviour against a real
    database; this proves the SHAPE, so a legacy call reintroduced on a branch the integration
    tests happen not to cover still fails.
    """
    handlers = _handlers_for(pair)
    if not handlers:
        pytest.skip(f"{pair} is not served by a first-party module")
    for module, function in handlers:
        if not module.is_file():
            continue
        offenders = _names_used_in(module.read_text(encoding="utf-8"), function)
        assert not offenders, (
            f"{module.name}:{function} serves the POSTGRES_WIRED route {pair} but names the "
            f"legacy stores directly: {sorted(offenders)}")


def test_the_shared_helpers_behind_wired_routes_do_not_name_the_legacy_stores():
    """Handlers delegate; the delegates must be clean too.

    Found the hard way: the gallery append lived in a shared `_emit` helper rather than in any
    handler body, so a per-handler check reported the image routes clean while they were writing
    history.json. Whole-module now, for the modules that serve ONLY wired routes.
    """
    offenders = {}
    for name in ("api/routes/analysis.py", "api/routes/influencer.py", "api/routes/scrape.py",
                 "api/routes/generate.py", "api/webapp_jobs.py", "api/routes/auth.py",
                 "api/routes/jobs.py", "api/routes/run.py", "api/jobs/recorder.py",
                 "api/gallery_store.py", "api/gallery_import.py"):
        path = _ROOT / name
        found = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name) and node.id in _LEGACY_NAMES:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in _LEGACY_NAMES:
                found.add(node.attr)
        if found:
            offenders[name] = sorted(found)
    assert not offenders, f"modules serving only wired routes still name the legacy stores: {offenders}"


#: The in-memory job store, reachable only through the legacy provider. A JOB route that named any
#: of these would be answering from — or writing to — the store PostgreSQL is supposed to have
#: replaced, exactly as `_LEGACY_NAMES` covers for brands and users.
_JOB_STORE_NAMES = {"JobStore", "state.store", "app.state.store", "active_store",
                    "emit_threadsafe", "finish_success", "finish_failure", "set_result_threadsafe",
                    "attach_loop", "subscribe"}


def test_no_job_route_module_reaches_the_in_memory_store():
    """§11: under postgres the Job lifecycle contains zero direct JobStore authority.

    Syntactic and whole-module, for the same reason the brand version is: the store reach that
    actually shipped last time lived in a shared helper, not in a handler body. `api/persistence.py`
    is deliberately NOT in this list — LegacyPersistence IS the JobStore adapter, and confusing the
    legacy IMPLEMENTATION with PostgreSQL AUTHORITY is the mistake this test exists to prevent.
    """
    offenders = {}
    for name in ("api/routes/jobs.py", "api/routes/run.py", "api/routes/scrape.py",
                 "api/webapp_jobs.py", "api/jobs/recorder.py", "api/routes/analysis.py",
                 "api/routes/influencer.py"):
        path = _ROOT / name
        source = path.read_text(encoding="utf-8")
        found = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Name) and node.id in _JOB_STORE_NAMES:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in _JOB_STORE_NAMES:
                found.add(node.attr)
        if found:
            offenders[name] = sorted(found)
    assert not offenders, (
        f"modules serving the Job lifecycle still reach the in-memory store: {offenders}")


def test_a_gallery_write_is_owned_by_the_selected_backend():
    """§27/§E: the gallery must have exactly one authority, and postgres IS able to be it now.

    This asserted `False` for postgres while the gallery index was `history.json` — the refusal
    WAS the single-authority property, because the alternative was appending to a JSON file beside
    a PostgreSQL-owned brand. Both backends can own it now, so the assertion flips; what does not
    change is that the question is asked BEFORE the render, which the next test pins.
    """
    from api.persistence import LegacyPersistence, PostgresPersistence

    for backend in (LegacyPersistence, PostgresPersistence):
        for name in ("record_gallery_entry", "supports_gallery_writes", "gallery_history"):
            assert hasattr(backend, name), f"{backend.__name__} has no {name}"
    assert PostgresPersistence.__new__(PostgresPersistence).supports_gallery_writes() is True
    assert LegacyPersistence().supports_gallery_writes() is True


def test_the_gallery_capability_is_checked_before_the_render_spends():
    """§12: the capability question is asked while the render is still free.

    Syntactic and on purpose. Both calls live in `_emit`, and the ORDER is the whole property: a
    backend that could not own the entry must refuse before `generate_image`, not after the
    customer has paid for an image the system cannot keep. A behavioural test cannot see this while
    both backends answer True — the ordering only matters for the backend that says no.
    """
    source = (_ROOT / "api" / "routes" / "generate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    emit = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_emit")
    called: list[tuple[int, str]] = []
    for node in ast.walk(emit):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("_require_gallery_writes", "generate_image", "record_gallery_entry"):
                called.append((node.lineno, name))
    ordered = [name for _line, name in sorted(called)]
    assert ordered.index("_require_gallery_writes") < ordered.index("generate_image"), (
        f"the gallery capability is checked after the render spends: {ordered}")
    assert ordered.index("generate_image") < ordered.index("record_gallery_entry")
