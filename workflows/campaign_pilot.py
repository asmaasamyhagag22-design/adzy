"""Run the single-worker, operator-local Campaign surface on PostgreSQL."""
import argparse
from asyncio import timeout
from contextlib import asynccontextmanager
from getpass import getpass
import os
from pathlib import Path
from tempfile import TemporaryFile
from urllib.parse import urlsplit


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
MAX_BODY = 32 * 1024


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        from starlette.responses import JSONResponse

        body = bytearray()
        try:
            async with timeout(5):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY:
                        await JSONResponse({"detail": {"code": "REQUEST_TOO_LARGE"}}, 413)(scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await JSONResponse({"detail": {"code": "REQUEST_BODY_TIMEOUT"}}, 408)(scope, receive, send)
            return

        async def bounded_receive():
            nonlocal body
            if body is None:
                return await receive()
            data, body = bytes(body), None
            return {"type": "http.request", "body": data, "more_body": False}

        await self.app(scope, bounded_receive, send)


def configure(state, *, offline=False):
    from workflows.demo import isolate

    isolate(offline=offline)
    secret = os.environ.get("BASEERA_AUTH_SECRET", "").strip()
    if len(secret.encode("utf-8")) < 32:
        raise SystemExit("Set BASEERA_AUTH_SECRET to an operator-provisioned secret of at least 32 bytes")
    dsn = urlsplit(os.environ.get("BASEERA_DATABASE_URL", ""))
    if (dsn.scheme != "postgresql+psycopg" or dsn.hostname != "127.0.0.1" or not dsn.username
            or not dsn.password or dsn.path in ("", "/") or dsn.query or dsn.fragment
            or os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE")):
        raise SystemExit("A password-authenticated postgresql+psycopg DSN on 127.0.0.1 is required")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.stat().st_mode & 0o077:
        raise SystemExit("Campaign state must be an owner-only directory (mode 0700)")
    os.environ.update(PERSISTENCE_BACKEND="postgres", BASEERA_WEBAPP_DIR=str(state))


def create_app(state, *, snapshot=None):
    from fastapi import APIRouter, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, RedirectResponse
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.staticfiles import StaticFiles
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from api.persistence import EXPECTED_SCHEMA_HEAD, get_persistence
    from api.routes import auth, brands, v2_campaigns
    from db.session import dispose, get_engine

    def ready():
        with get_engine().connect() as conn:
            if conn.scalar(text("SELECT version_num FROM alembic_version")) != EXPECTED_SCHEMA_HEAD:
                raise RuntimeError("schema mismatch")
        with TemporaryFile(dir=state):
            pass

    @asynccontextmanager
    async def lifespan(_app):
        try:
            get_persistence()
            ready()
            yield
        finally:
            dispose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.get("/api/health")
    def health():
        return {"status": "alive"}

    @app.get("/api/ready")
    def readiness():
        try:
            ready()
        except (RuntimeError, OSError, SQLAlchemyError):
            raise HTTPException(503, detail={"code": "CAMPAIGN_NOT_READY"}) from None
        return {"status": "ready", "schema": EXPECTED_SCHEMA_HEAD}

    @app.get("/")
    def home():
        return RedirectResponse(url="/app")

    @app.get("/app")
    def frontend():
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    exposed_endpoints = (
        (auth.router, {auth.login, auth.me}),
        (brands.router, {brands.list_brands, brands.get_brand}),
        (v2_campaigns.router, {
            v2_campaigns.planning_state, v2_campaigns.plan_campaign, v2_campaigns.current,
            v2_campaigns.save_brief, v2_campaigns.revise_direction, v2_campaigns.review,
            v2_campaigns.preview_package, v2_campaigns.download_package, v2_campaigns.recheck,
        }),
    )
    public = APIRouter()
    for router, allowed in exposed_endpoints:
        public.routes.extend(route for route in router.routes if route.endpoint in allowed)
    app.include_router(public, prefix="/api")

    if snapshot is not None:
        from workflows.campaign_snapshot import open_snapshot

        loader, clock = open_snapshot(snapshot)
        app.dependency_overrides[v2_campaigns.get_package_loader] = lambda: loader
        app.dependency_overrides[v2_campaigns.get_clock] = lambda: clock
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "account"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--email")
    args = parser.parse_args()
    state = args.state.resolve()
    configure(state, offline=args.snapshot is not None or args.action == "account")
    if args.action == "account":
        from api.persistence import get_persistence
        from api.routes.auth import SignupRequest

        if not args.email:
            parser.error("account requires --email")
        password = getpass("Password (12–128 characters): ")
        if not 12 <= len(password) <= 128 or password != getpass("Repeat password: "):
            parser.error("password rejected")
        account = SignupRequest(email=args.email, password=password)
        if get_persistence().create_user(email=account.email, password=account.password) is None:
            raise SystemExit("Account already exists")
        print("Account provisioned")
        return

    import uvicorn

    uvicorn.run(create_app(state, snapshot=args.snapshot), host="127.0.0.1", port=args.port,
                workers=1, proxy_headers=False, access_log=False, limit_concurrency=16)


if __name__ == "__main__":
    main()
