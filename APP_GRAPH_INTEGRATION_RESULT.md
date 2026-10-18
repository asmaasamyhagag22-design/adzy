# ADZY application graph integration — 2026-10-16

The existing Adzy Strategy interface now invokes the real review graph through authenticated start/status/resume routes. PostgreSQL canonical services remain the only approval authority. Create receives the graph-compiled authoritative CreativeContract; media generation stays outside the graph.

## Observed application journey

A fictional Studio Fixture account signed in through the actual application login form. Think → Review strategy caused `POST /api/review/start`, paused the real graph at `human_review` and displayed `NEEDS_REVIEW / REVIEW_STRATEGY`. Browser refresh restored it. The application process was stopped and restarted while paused; the same workflow remained available. A UI click on Lock this strategy resumed that exact workflow to `READY_FOR_CREATIVE`; PostgreSQL contained one approval for the exact subject. Prepare the poster displayed its WHO/SAY/PROVE/DO contract. No generation button was clicked. Screenshots are actual application UI with fictional evidence, not customer results.

The same workflow ID before/after restart was `fea5006b-3a96-4707-8fd9-0a0d666c0c6e`. Before: `pending_nodes=[human_review]`, approvals=0. After: `pending_nodes=[]`, approvals=1, subject matches=true, contract `cc_ad7543f1fa775971a55c182bf17a035d`. These are synthetic local identities, not customer data.

## Focused review and repairs

Confirmed defects in the original code: a binding could survive a failed start with no checkpoint, then status reported incompatible version; SQLite connection context managers did not close connections; the binding commit released serialization before first checkpoint; the dashboard's decision bypassed the graph entirely. The new runtime reports unusable starts as `FAILED / START_FAILED`, does not restart them automatically, closes registry/gate connections explicitly and holds a separate local execution gate across binding commit/checkpoint creation. Application lookup reuses actor/domain/exact subject/version bindings. Approval prerequisites are rechecked before writing.

The existing checkpoint-gap recovery and canonical idempotency are retained. Ambiguous write exceptions become blocked; no automatic write retry is added. Graph-safe exceptions map to UI statuses and HTTP boundaries return fixed safe errors. A stale reviewed subject returns 409 and never silently approves latest. Ownership checks precede checkpoint access. Sync route handlers execute in FastAPI's worker pool. No PostgreSQL transaction spans a graph pause or external call.

The UI binds decisions to the graph's subject/hash, blocks creation without READY, restores through server lookup and uses the existing selection epoch to discard late brand/session responses. Its current review action no longer also calls `/strategy/decide`. The legacy API remains for existing callers, not as a second writer for this action.

## Measured verification

- Original focused graph suite after initial integration: **16 passed**, 37.38s.
- Combined graph + initial application HTTP suite: **21 passed**, 59.73s.
- Expanded application suite: **7 passed**, 28.38s (overlaps the combined run; do not sum).
- The application cases use real persisted users/auth routes, fresh guarded disposable PostgreSQL, canonical services and real SQLite. They cover concurrent start lookup, exact subject/hash, concurrent/repeated resume with one approval, foreign-owner 404, stale409, rejection, insufficient research, initial-start failure, strict decisions/path rejection, real SDK capture and exporter failure isolation.
- Actual browser shell journey is separate from the CLI/TestClient checks, and includes refresh, server restart and UI decision. Browser calls and database inspection confirmed the same workflow and subject.
- Two existing deprecation-warning families remain: Alembic path separator and Starlette/AnyIO BlockingPortal.

## Langfuse scope

Real SDK observations are captured locally through the application invocation, correlated across pause/resume and masked before export. Application tests cover automatic node/tool observations, private identifiers/URLs absent, no model usage/cost fields and exporter exceptions leaving the canonical outcome intact. The browser resume also writes redacted local SDK capture. Default operation creates no exporter. Each request has a separate Telemetry stack and correlation; only the SDK client is shared and shut down with the application. A test-only demo capture is saved during operation/lifespan shutdown, including SIGTERM.

This is **local SDK export capture**, not Langfuse hosted/server ingestion. No external accounts or real customer data were sent.

## Packaging and limitations

The README is updated around the implemented application path. New application input/lock files retain the existing feature dependencies; original lock files remain intact. The old documented installation failed on an unpublished OpenCV pin; an application/workflow Tenacity conflict was also reproduced and resolved in the new lock. A fresh Python 3.12/Linux environment installed `requirements-app.txt` and passed `pip check`. Locked versions do not certify Windows, browser binaries or live providers.

The source-only candidate is prepared with `tools/prepare_app_publication.py`; it inventories current application sources rather than reusing the old 53-file subset. Credential patterns and private/runtime exclusions are checked without printing sensitive contents. Final clean-candidate results are recorded below after validation.

Supported deployment is **one application instance with persistent local SQLite**. No shared/network-filesystem, distributed worker, exactly-once or production claim is made. Authentication remains the existing demo-grade implementation. Complete claims-semantic grounding and paid-operation reservations remain open. Live scraping/provider/media functionality and hosted telemetry are unverified. The optional quarantined Facebook collector is absent; private showcase imagery is intentionally excluded. These do not block the supported synthetic review journey.

No Reel/Veo changes or execution, provider/media spend, staging, commit, push, reset, stash, clean or deployment. Spend **$0**. All pre-existing working-tree/index work is retained.

## Final clean-candidate evidence

- Final combined run: **23 passed / 0 failed / 0 skipped**, 69.08s; overlapping earlier runs are not added to it. Log: [app validation](docs/architecture/app_validation.txt).
- Fresh `/tmp/adzy-clean-app-venv` installed the exact `python -m pip install -r requirements-app.txt` command successfully; `pip check` found no broken requirements.
- Source-only export at `/tmp/adzy-app-publication-candidate` reproduced documented workflow init/start/fresh-process resume/status/cleanup, then full application import/lifespan, `/app` HTTP200, real login, graph pause and exact contract after resume. Both disposable containers were stopped. All **11 README local links/images** resolved in the export. [Machine-readable candidate evidence](docs/architecture/app_candidate_evidence.json).
- Actual-shell browser evidence and the six correlated local resume SDK observations are summarized in [browser evidence](docs/architecture/app_browser_evidence.json). Application HTTP tests separately capture start and resume in one real SDK trace; the first pre-restart browser process did not persist its in-memory capture, so those original spans are not claimed as retained evidence. The synthetic demo now saves capture per operation and during lifespan shutdown.
- Startup verification does not establish live model, crawl, image/OCR provider, media or hosted telemetry functionality. Publication scope still excludes private campaign imagery and the external quarantined Facebook collector source. There is no remaining blocker for the documented local synthetic review journey; distributed/production/provider certification remains outside it.

## Scoped diff

New application boundary: `api/routes/review.py`; route registration/lifecycle in `api/main.py`. Existing Adzy JS decision/refresh/Create flow now uses graph results with selection-epoch guards. Foundation runtime/services/contracts/graph receive the failure, lifecycle, serialization and authoritative-contract changes; `workflows/app_demo.py` adds the guarded real-shell demo. New HTTP tests, application input/lock, source-export tool, README/architecture/checklist/evidence/screenshots and append-only continuity updates complete the handoff. Existing approval/DB schema/compiler/paid services were reused. No unrelated pre-existing changes were reverted or staged.
