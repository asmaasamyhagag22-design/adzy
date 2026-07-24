"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env from the project root BEFORE any pipeline imports run, so that
# OPENAI_API_KEY (and any other env-driven config) is visible to both the
# API layer and to library calls deeper in the pipeline (e.g., OpenAICaller
# reads os.environ at construction time).
#
# We search upwards from this file's directory so the API works whether
# you `cd scraper_v01 && uvicorn api.main:app` or `uvicorn` from elsewhere.
try:
    from dotenv import load_dotenv  # type: ignore

    _search_roots = []
    # First, the current working directory (where uvicorn was started from).
    _search_roots.append(Path.cwd())
    # Then walk up from the file location (covers running from anywhere).
    _here = Path(__file__).resolve()
    _search_roots.append(_here.parent)
    _search_roots.extend(_here.parents)

    _seen: set[Path] = set()
    for root in _search_roots:
        try:
            root_resolved = root.resolve()
        except OSError:
            continue

        if root_resolved in _seen:
            continue

        _seen.add(root_resolved)

        candidate = root_resolved / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            break

except ImportError:
    # python-dotenv is optional; the API still works if env is set externally.
    pass


from pathlib import Path  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .auth import require_api_key  # noqa: E402
from .jobs.store import JobStore  # noqa: E402
from .routes import (  # noqa: E402
    analysis, auth, brands, generate, health, influencer, jobs, poster, reel, run, scrape,
    showcase, swot,
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

    try:
        yield
    finally:
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
# Route mounts
# ---------------------------------------------------------------------

def _serve(name: str):
    """Serve a frontend HTML file, or a JSON hint if the web app isn't present."""
    f = _FRONTEND_DIR / name
    if f.is_file():
        return FileResponse(str(f))
    return JSONResponse({
        "status": "ok", "app": "Marketing Strategist API",
        "hint": "Backend is up; the web app files were not found.",
        "docs": "/docs", "health": "/api/health",
    })


@app.get("/", include_in_schema=False)
def landing():
    """The marketing landing page (frontend/landing.html)."""
    return _serve("landing.html")


@app.get("/app", include_in_schema=False)
def webapp():
    """The AdArchitect SPA (frontend/index.html)."""
    return _serve("index.html")


# Static assets (logos, showcase images) referenced as /assets/... by both HTML files.
if (_FRONTEND_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="assets")


# ---- API routers ----
# health + jobs are unauthenticated: they spend nothing (liveness, job status/stream polling).
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
app.include_router(scrape.router, prefix="/api", tags=["webapp"], dependencies=_authed)
app.include_router(brands.router, prefix="/api", tags=["webapp"], dependencies=_authed)
app.include_router(analysis.router, prefix="/api", tags=["webapp"], dependencies=_authed)
app.include_router(generate.router, prefix="/api", tags=["webapp"], dependencies=_authed)
app.include_router(influencer.router, prefix="/api", tags=["webapp"], dependencies=_authed)
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