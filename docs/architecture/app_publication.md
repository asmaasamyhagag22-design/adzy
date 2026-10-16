# Application publication checklist

The release combines the current application and review integration with the default branch. docs/publication/manifest.json inventories the selected files and their SHA-256 digests. Internal handoffs, private datasets and runtime state stay outside this selection.

## Required application

- [ ] Application runtime entrypoints (`config_env.py`, `spend_tripwire.py`, `vertex_spend.py`, the legacy media entrypoint), `alembic.ini`, and application source packages selected explicitly in `tools/prepare_app_publication.py`, including `api/`, `db/` and migrations, `poster/`, acquisition, strategy, creative YAML catalogs, social/reviews/trends/media plans, ops and existing media sources. Existing Reel files are copied unchanged for packaging completeness; they are not modified or executed by this task.
- [ ] `frontend/index.html`, `landing.html`, JS/CSS/SVG assets and application logo. Existing Adzy shell and prior WP0/WP1 source changes must accompany the integration.
- [ ] `config/runtime.production.toml`, schema/migration templates and package `__init__.py` files.
- [ ] `requirements-app.in` and **`requirements-app.txt`**, plus the preserved older input/lock files for provenance.

## Workflow feature, validation and documentation

- [ ] `workflows/*.py`, `api/routes/review.py`, integration edits to `api/main.py` and `frontend/assets/adzy-app.js`.
- [ ] `requirements-workflow.in` / `requirements-workflow.txt`: standalone demo environment, not an overlay on the application lock.
- [ ] All current Python/Node test sources, including `tests/test_review_workflow.py`, `tests/test_app_review_graph.py`, `tests/conftest.py` and the earlier approval/application regressions. Private fixture datasets are excluded.
- [ ] README, `docs/getting-started.md`, reports under `docs/validation/`, current architecture/checklist and integration progress.
- [ ] Actual synthetic application screenshots under `docs/images/`, including `app-review-paused.jpg`, `app-review-ready.jpg` and `app-review-contract.jpg`; never describe them as customer campaign results.
- [ ] Generated candidate `docs/publication/manifest.json`: path and SHA-256 inventory, exclusion policy and credential-pattern scan findings. Review source diffs separately before publication.

## Dependency inventory

The application input retains every package from the existing `requirements.txt`, and the graph/tool/SDK set from the standalone workflow lock. Shared versions are resolved in the application environment. OpenCV is corrected from the unpublished `5.0.0` to `5.0.0.93`; Tenacity `9.1.4` satisfies the existing Google SDK and LangChain ranges. The resolved application lock was installed in a fresh Python 3.12/Linux venv and passed `pip check`.

The inventory also includes Arabic reshaping/Bidi used by existing text layout, `instaloader` from the existing Instagram subsystem requirements, Mongo support for existing projection options, and the optional ElevenLabs SDK used by the legacy media entrypoint. Installing those libraries is not evidence of live-provider behavior. Existing OpenAI/Google/Playwright, extraction, image/OCR support, DB and other application dependencies are retained even though the review demo does not invoke them.

External prerequisites remain separate: Docker plus PostgreSQL for disposable demos; a configured migrated PostgreSQL database for the application; Playwright Chromium for browser acquisition/render paths; ffmpeg where required by existing media operation; private provider credentials for live operations. No external service/account is configured by the export. The older standalone Facebook pull script imports a quarantined, out-of-repository `facebook_collector` with its own Apify dependency. That source is absent here and must be explicitly supplied/reviewed if releasing that optional pull; it is not silently represented as available.

## Exclude

- [ ] `.env*`, auth secrets, API tokens, credentials, private config, DSNs with credentials.
- [ ] `database_export/`, BSON/dumps, `outputs/`, `scrapes/`, `runs/`, `registry/`, customer/root profile JSON, photos, generated media and private audit artifacts.
- [ ] Virtual environments, Git metadata/index, pytest scratch/staged PNGs, bytecode, checkpoint/binding/execution SQLite files, sidecars and demo manifests/capture runtime directories.
- [ ] Existing `frontend/assets/showcase/` campaign imagery: absent from the source candidate because rights/customer disclosure are not established by this task. The showcase endpoint and empty-gallery behavior remain in the application.

The scanner rejects common private-key/provider-token literals and never prints their values. It is a limited automated check, not proof that all secrets or publication rights have been assessed. The candidate is intentionally source-only; missing customer assets are not filled with private data.

## Curated visible results

`docs/results/` contains selected historical generated concepts and one separately labeled fictional integration render. `examples/volume-cafe/` contains a narrow public-source excerpt, not the full stored profile. These explicit demonstration assets are included at the project owner's request; raw galleries and customer/account stores remain excluded. Provenance hashes and copy limitations accompany the results.
