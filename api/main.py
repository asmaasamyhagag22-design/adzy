"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env from the project root BEFORE any pipeline imports run, so that
# OPENAI_API_KEY (and any other env-driven config) is visible to both the
# API layer and to library calls deeper in the pipeline (e.g., OpenAICaller
# reads os.environ at construction time).
#
# Loaded through the ONE shared loader (config_env), which anchors on the repository so the answer
# does not depend on where uvicorn was started. This module used to hand-roll its own search that
# looked in the CWD FIRST and stopped at the first `.env` it found — meaning a stray `.env` in the
# launch directory silently shadowed the project's own credentials. That is precisely the failure
# config_env.py was written for (a valid Meta token in `.env` the pipeline could not see,
# 2026-07-20), reproduced in the entry point that boots everything.
import config_env  # noqa: E402

config_env.load_env()


from pathlib import Path  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi import Request, Response  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .auth import require_api_key, require_spend_budget  # noqa: E402
from .jobs.store import JobStore, bind_active_store, reset_active_store  # noqa: E402
from .persistence import (  # noqa: E402
    BackendMisconfigured,
    ENV_BACKEND,
    POSTGRES,
    get_persistence,
)
from .routes import (  # noqa: E402
    analysis, auth, brands, canonical_reel, generate, health, influencer, jobs, poster, reel,
    run, scrape, showcase, swot, review,
)
from .routes.auth import require_user  # noqa: E402

# The AdArchitect web app (static SPA) served same-origin so no API key ever reaches the browser.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Lifespan: start/stop the JobStore's TTL cleanup task
# ---------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    store = JobStore(ttl_seconds=int(os.environ.get("JOB_TTL_SECONDS", "3600")))
    store.start_cleanup()
    app.state.store = store

    logger.info("JobStore initialized (ttl=%ds)", store._ttl)

    # CRASH REPAIR AT STARTUP, and only at startup (§N: the minimum reconciler, not a scheduler).
    #
    # Called unconditionally: the legacy provider answers zeros because an in-memory store dies
    # with its process and leaves nothing to be inconsistent with, so this needs no backend check.
    # It touches only jobs whose lease has genuinely expired or whose result demonstrably
    # committed, which is why a second process starting beside a busy one repairs nothing of its.
    try:
        try:
            repaired = await asyncio.to_thread(get_persistence().reconcile_jobs)
            if repaired.get("class_a") or repaired.get("class_b"):
                logger.warning("crash repair: %s", repaired)
        except BackendMisconfigured as e:
            explicit_postgres = (
                (os.environ.get(ENV_BACKEND) or "").strip().lower() == POSTGRES
            )
            if explicit_postgres:
                logger.exception("PostgreSQL readiness failed at startup: %s", e)
                raise
            logger.exception("job reconciliation failed at startup: %s", e)
        except Exception as e:  # noqa: BLE001 — legacy reconciliation remains best-effort
            logger.exception("job reconciliation failed at startup: %s", e)

        yield
    finally:
        await asyncio.to_thread(review.shutdown_review_telemetry)
        await store.stop_cleanup()


# ---------------------------------------------------------------------
# App
# ---------------------------------------------------------------------

app = FastAPI(
    title="Marketing Strategist API",
    description=(
        "HTTP wrapper for the evidence-based business profile pipeline. "
        "POST /api/run to start a job, then GET /api/jobs/{id}/stream for live progress. "
        "POST /api/poster/from-profile to generate a printable poster from a profile."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# CORS — allow the Next.js dev server (Day 2) and Vercel deploy (Day 7).
# CORS_ALLOW_ORIGINS env var overrides; comma-separated list of origins.
# P-59: no baked-in LAN IP — that was one developer's machine address shipped to every deploy.
# A specific host belongs in CORS_ALLOW_ORIGINS on that machine, not in the source default.
_default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

_env_origins = os.environ.get("CORS_ALLOW_ORIGINS", "")
if _env_origins.strip():
    allow_origins = [o.strip() for o in _env_origins.split(",") if o.strip()]
else:
    allow_origins = _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------
# Security headers
#
# The SPA keeps a signed JWT in the browser and renders SCRAPED third-party content — brand names,
# taglines, offering copy taken off other people's sites — into the same document. Without a CSP,
# one hostile string in someone's <title> is a path to that token. CORSMiddleware was the only
# middleware installed.
#
# The policy is calibrated to the page that actually ships (tests/test_security_headers.py locks
# both directions): frontend/index.html uses inline <script> and inline style attributes and pulls
# webfonts from Google, so `'unsafe-inline'` and those two font hosts stay allowed — a stricter
# policy would test beautifully and serve a blank page. What it does buy: no framing, no plugins,
# no form posts to another origin, and images restricted to self/data/https (generated posters
# arrive as data: URIs). Tightening belongs with moving the inline script to a file, not here.
# ---------------------------------------------------------------------
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: https:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@app.middleware("http")
async def _bind_job_store(request, call_next):
    """Bind THIS app's JobStore for the duration of the request.

    The legacy persistence backend reads it through `api.jobs.store.active_store()`, so job
    routes can use the typed provider surface instead of reaching into `app.state` themselves.
    Request-scoped rather than process-global: two apps in one process must not rebind each
    other's store (see tests/test_legacy_store_isolation.py). Work spawned with
    `asyncio.create_task` here copies the context, so a background runner keeps this store.
    """
    token = bind_active_store(getattr(request.app.state, "store", None))
    try:
        return await call_next(request)
    finally:
        reset_active_store(token)


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# ---------------------------------------------------------------------
# Route mounts
# ---------------------------------------------------------------------

def _serve(name: str, request=None):
    """Serve a frontend HTML file, or a JSON hint if the web app isn't present.

    `Cache-Control: no-cache` — MEASURED 2026-07-26, right after three false claims about the tech
    stack were corrected on the landing page: the file on disk had 0 hits, curl over HTTP had 0
    hits, and the browser still rendered all three. The response carried an ETag and a
    Last-Modified but no Cache-Control, so the browser picked its own freshness window and simply
    never asked again.

    `no-cache` does not mean "don't store"; it means "revalidate before use". The ETag below turns
    that into a 304 with no body, so the cost is one conditional request and the guarantee is that
    an edit reaches a returning visitor. Assets under /assets stay hard-cached — they are addressed
    by filename and do not change under the same name.
    """
    f = _FRONTEND_DIR / name
    if f.is_file():
        # `stat_result` is passed so the ETag exists NOW: without it Starlette computes the header
        # lazily while sending, so reading it here returned None and the 304 branch below could
        # never fire — the check would have looked correct and done nothing.
        resp = FileResponse(str(f), headers={"Cache-Control": "no-cache"},
                            stat_result=f.stat())
        # Answer the revalidation ourselves. Starlette's FileResponse computes an ETag but does not
        # act on If-None-Match, so without this every reload re-sent the whole page — which would
        # have made the docstring above a claim rather than a description.
        etag = resp.headers.get("etag")
        inm = (request.headers.get("if-none-match") or "") if request is not None else ""
        if etag and inm and etag in [t.strip() for t in inm.split(",")]:
            return Response(status_code=304, headers={"Cache-Control": "no-cache",
                                                      "ETag": etag})
        return resp
    return JSONResponse({
        "status": "ok", "app": "Marketing Strategist API",
        "hint": "Backend is up; the web app files were not found.",
        "docs": "/docs", "health": "/api/health",
    })


@app.get("/", include_in_schema=False)
def landing(request: Request):
    """The marketing landing page (frontend/landing.html)."""
    return _serve("landing.html", request)


@app.get("/app", include_in_schema=False)
def webapp(request: Request):
    """The AdArchitect SPA (frontend/index.html)."""
    return _serve("index.html", request)


# Static assets (logos, showcase images) referenced as /assets/... by both HTML files.
if (_FRONTEND_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="assets")


# ---- API routers ----
# Health is public. Job routes self-authorize against the creating JWT user or legacy capability;
# mounting them without one global dependency lets those two existing client models coexist.
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(jobs.router, prefix="/api", tags=["jobs"])

# Web-app auth: signup/login must be reachable WITHOUT a token; /auth/me self-gates via Depends.
# showcase is the public landing gallery. Neither is gated.
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(showcase.router, prefix="/api", tags=["showcase"])

# run / poster / reel / swot each spend real money -> gated behind require_api_key (P-54).
_paid = [Depends(require_api_key)]
app.include_router(run.router, prefix="/api", tags=["run"], dependencies=_paid)
app.include_router(poster.router, prefix="/api", tags=["poster"], dependencies=_paid)
app.include_router(reel.router, prefix="/api", tags=["reel"], dependencies=_paid)
app.include_router(swot.router, prefix="/api", tags=["swot"], dependencies=_paid)

# Web-app routers — gated by JWT (require_user), not the API key. Brands are a shared catalog.
_authed = [Depends(require_user)]
# ...but scrape / analysis / generate / influencer reach the SAME paid engines the keyed routes
# above are gated for, while their token comes from a self-service signup. They additionally carry
# a per-user spend throttle, so an account cannot loop the paid pipeline (api/auth.py).
_authed_spend = [Depends(require_user), Depends(require_spend_budget)]
app.include_router(scrape.router, prefix="/api", tags=["webapp"], dependencies=_authed_spend)
app.include_router(review.router, prefix="/api", tags=["review"], dependencies=_authed)
app.include_router(brands.router, prefix="/api", tags=["webapp"], dependencies=_authed)
# Gate 2D.1 canonical Reel is provider-free.  It is authenticated, but deliberately not mounted
# behind the spend dependency and cannot reach the legacy renderer or any paid boundary.
app.include_router(canonical_reel.router, prefix="/api", tags=["canonical-reel"],
                   dependencies=_authed)
app.include_router(analysis.router, prefix="/api", tags=["webapp"], dependencies=_authed_spend)
app.include_router(generate.router, prefix="/api", tags=["webapp"], dependencies=_authed_spend)
app.include_router(influencer.router, prefix="/api", tags=["webapp"], dependencies=_authed_spend)
# ^ AdArchitect web app fully wired: auth · brands · scrape · analysis · generate · influencer.


# Generated images are loaded by <img src="/api/generated/..."> which can't send the Bearer token,
# so this serving route is PUBLIC (the images aren't secret). Path-sanitized in read_generated_png.
@app.get("/api/generated/{name}", include_in_schema=False)
def generated_image(name: str):
    from fastapi import Response
    from .webapp_images import read_generated_png
    png = read_generated_png(name)
    if png is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(content=png, media_type="image/png")
