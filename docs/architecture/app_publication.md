# Application publication checklist

This checklist selects current working-tree source. It neither stages files nor assumes that pre-existing staged/untracked work is already on the remote. The old foundation manifest is historical evidence for its smaller tested scope.

## Required application

- [ ] All current root Python modules, `alembic.ini`, and application source packages selected explicitly in `tools/prepare_app_publication.py`, including `api/`, `db/` and migrations, `poster/`, acquisition, strategy, creative YAML catalogs, social/reviews/trends/media plans, ops and existing media sources. Existing Reel files are copied unchanged for packaging completeness; they are not modified or executed by this task.
- [ ] `frontend/index.html`, `landing.html`, JS/CSS/SVG assets and application logo. Existing Adzy shell and prior WP0/WP1 source changes must accompany the integration.
- [ ] `config/runtime.production.toml`, schema/migration templates and package `__init__.py` files.
- [ ] `requirements-app.in` and **`requirements-app.txt`**, plus the preserved older input/lock files for provenance.

## Workflow feature, validation and documentation

- [ ] `workflows/*.py`, `api/routes/review.py`, integration edits to `api/main.py` and `frontend/assets/adzy-app.js`.
- [ ] `requirements-workflow.in` / `requirements-workflow.txt`: standalone demo environment, not an overlay on the application lock.
- [ ] `tests/test_review_workflow.py`, `tests/test_app_review_graph.py`, `tests/conftest.py` and root test support modules selected by the export.
- [ ] README, integration result, architecture/graph/checklist, existing foundation result labeled historical, and `docs/upgrade_audit/PROGRESS.md` continuity.
- [ ] Actual synthetic application screenshots under `docs/images/`, including `app-review-paused.jpg`, `app-review-ready.jpg` and `app-review-contract.jpg`; never describe them as customer campaign results.
- [ ] Generated candidate `PUBLICATION_MANIFEST.json`: path and SHA-256 inventory, exclusion policy and credential-pattern scan findings. Review source diffs separately before publication.

## Dependency inventory

The application input retains every package from the existing `requirements.txt`, and the graph/tool/SDK set from the standalone workflow lock. Shared versions are resolved in the application environment. OpenCV is corrected from the unpublished `5.0.0` to `5.0.0.93`; Tenacity `9.1.4` satisfies the existing Google SDK and LangChain ranges. The resolved application lock was installed in a fresh Python 3.12/Linux venv and passed `pip check`.

The inventory also includes Arabic reshaping/Bidi used by existing text layout, `instaloader` from the existing Instagram subsystem requirements, Mongo support for existing projection options, and the optional ElevenLabs SDK used by the legacy media entrypoint. Installing those libraries is not evidence of live-provider behavior. Existing OpenAI/Google/Playwright, extraction, image/OCR support, DB and other application dependencies are retained even though the review demo does not invoke them.

External prerequisites remain separate: Docker plus PostgreSQL for disposable demos; a configured migrated PostgreSQL database for the application; Playwright Chromium for browser acquisition/render paths; ffmpeg where required by existing media operation; private provider credentials for live operations. No external service/account is configured by the export. The older standalone Facebook pull script imports a quarantined, out-of-repository `facebook_collector` with its own Apify dependency. That source is absent here and must be explicitly supplied/reviewed if releasing that optional pull; it is not silently represented as available.

## Exclude

- [ ] `.env*`, auth secrets, API tokens, credentials, private config, DSNs with credentials.
- [ ] `database_export/`, BSON/dumps, `outputs/`, `scrapes/`, `runs/`, `registry/`, customer/root profile JSON, photos, generated media and private audit artifacts.
- [ ] Virtual environments, Git metadata/index, pytest scratch/staged PNGs, bytecode, checkpoint/binding/execution SQLite files, sidecars and demo manifests/capture runtime directories.
- [ ] Existing third-party `frontend/assets/showcase/` campaign imagery: excluded. Three explicit fictional/synthetic project fixtures now populate the public gallery, using the existing endpoint unchanged.

The scanner rejects common private-key/provider-token literals and never prints their values. It is a limited automated check, not proof that all secrets or publication rights have been assessed. The candidate is intentionally source-only; missing customer assets are not filled with private data.

## Poster V1 release candidate

The current export additionally includes the exact-revision delivery service, read-only authenticated status/download routes, Create delivery controls, offline synthetic poster responses, and the executable release checks. Apply migrations through `0007_unused_reservations`, including `0006_poster_operations`, before application startup. The source-only candidate is tested with the application lock in a fresh Python 3.12 environment; no private `.env`, credentials or customer stores accompany it.

Run `python tools/check_poster_v1.py` with Node.js 22 and Docker available. Run `python tools/verify_poster_v1_browser.py --directory /tmp/new-poster-demo --evidence-dir /tmp/poster-browser` after installing Playwright Chromium. The demo directory must be new. These checks create guarded disposable PostgreSQL, exercise real authentication and canonical services, inject fake provider responses, and close their own containers. They include exact approvals, restart/pause/resume, duplicate/reservation safety, copy/visual separation and exact draft/final delivery. Repository-wide/private-fixture tests remain separate; there is no blanket exclusion masking them.

`PUBLICATION_MANIFEST.json` is the exact required file inventory with hashes, credential-pattern findings and checked public documentation links. Root diagnostic scripts, environment files, customer exports/media and audit scratch are excluded. Synthetic UI screenshots are labeled; they establish no provider quality. The supported deployment is one local instance, with PostgreSQL, managed blobs and SQLite review state persisted. Langfuse server ingestion, live image quality and broad semantic grounding are unverified; the semantic candidate remains shadow/unqualified. See [the release result](../../POSTER_V1_RELEASE_RESULT.md) for the bounded live-validation proposal and blockers.

## Current Poster V1 closeout

See [the publication review](../../POSTER_V1_PUBLICATION_REVIEW.md). Source export now includes compiler/canonical regression dependencies, rendering/QA/recovery changes and the state-aware rejected preview UI. Its public-doc link validation covers README, demo and both current result documents. Runtime operation/accounting JSON is excluded even from the optional preview bundle. The asset notice distinguishes bundled OFL font terms from third-party marks with no supplied redistribution grant. The generated commit inventory is against observed remote main; it is not the current index and does not include pure line-ending changes or unrelated Reel changes.

## Public contents resolved

The public demo is Studio Fixture (`workflows.app_demo`), with no Firefox artwork or private case files. Optional branded concepts and opaque logo.png are excluded only in the separate publication checkout; original media are preserved in the owner workspace. The source exporter retains all test sources and required fixtures, CI and documented demo dependencies. Existing code-authored project SVG marks are retained and replace the old PNG references. See THIRD_PARTY_ASSETS.md at repository root for the final asset disposition; this is not a new blanket source license. Historical asset manifests are superseded by the final exact inventory.
