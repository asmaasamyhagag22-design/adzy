# Review foundation release-file checklist

No files were staged, committed, pushed, reset, stashed, or deployed in this task. This is a **file selection guide**, not permission to stage the dirty tree. `git_status` in the [source manifest](review_source_manifest.json) records existing staged/unstaged/untracked dependencies as well as the new workflow files; tracked files may themselves contain earlier unpublished work.

## Required existing application sources

Publishing only `workflows/` will fail. The optional demo invokes the real PostgreSQL application services, canonical approval transactions, strategy builder/research evaluator, and CreativeContract compiler. The following exact current local files must be available in the reviewer checkout (retain their package `__init__.py` files):

- [ ] `alembic.ini`
- [ ] `api/__init__.py`
- [ ] `api/brand_translate.py`
- [ ] `api/job_access.py`
- [ ] `api/persistence.py`
- [ ] `api/read_model.py`
- [ ] `api/user_auth.py`
- [ ] `config_env.py`
- [ ] `db/__init__.py`
- [ ] `db/base.py`
- [ ] `db/config.py`
- [ ] `db/errors.py`
- [ ] `db/legacy_identity.py`
- [ ] `db/migrations/env.py`
- [ ] `db/migrations/script.py.mako`
- [ ] `db/migrations/versions/0001_initial_schema.py`
- [ ] `db/migrations/versions/0002_legacy_compat_and_currentness.py`
- [ ] `db/migrations/versions/0003_job_events_and_gallery.py`
- [ ] `db/migrations/versions/0004_job_results.py`
- [ ] `db/migrations/versions/0005_strategy_approvals.py`
- [ ] `db/models.py`
- [ ] `db/records.py`
- [ ] `db/repositories.py`
- [ ] `db/security.py`
- [ ] `db/services.py`
- [ ] `db/session.py`
- [ ] `db/test_database_safety.py`
- [ ] `db/uow.py`
- [ ] `poster/__init__.py`
- [ ] `poster/locale.py`
- [ ] `poster/strategy_contract.py`
- [ ] `strategy/__init__.py`
- [ ] `strategy/builder.py`
- [ ] `strategy/positioning/__init__.py`
- [ ] `strategy/positioning/adapters.py`
- [ ] `strategy/positioning/contracts.py`
- [ ] `strategy/positioning/distinctness.py`
- [ ] `strategy/positioning/objective.py`
- [ ] `strategy/positioning/projection.py`
- [ ] `strategy/positioning/research.py`
- [ ] `strategy/positioning/service.py`
- [ ] `strategy/positioning/verifier.py`

The manifest contains SHA-256 digests for the required Python/schema sources. It combines modules observed during the actual canonical synthetic build/start/resume with explicit migration/setup dependencies. A source-only export of this selection is exercised independently; this is stronger than assuming the current dirty checkout is already on GitHub. Do not replace required pre-existing files with older HEAD versions merely because this task did not edit them.

## Required new feature, validation, dependencies, and presentation

- [ ] Every source `workflows/*.py`: `__init__`, `contracts`, `services`, `tools`, `graph`, `runtime`, `telemetry`, `synthetic`, `demo`, `verify`.
- [ ] `requirements-workflow.in` and `requirements-workflow.txt` (optional environment; no overlay onto base lock).
- [ ] `tests/test_review_workflow.py` and existing `tests/conftest.py` for the documented test command.
- [ ] Existing regression support: `tests/test_gate2b_positioning_contracts.py`, `tests/test_gate2_research_gap_resolution.py`, `tests/test_gate2b_postgres_strategy.py`, `tests/product_slice_fixtures.py`, `vertex_spend.py` (required by the existing test fixtures), `db/session.py`, and `db/test_database_safety.py`.
- [ ] `README.md`, `FOUNDATION_RESULT.md`, `.gitignore` additions.
- [ ] `docs/images/synthetic-research.png`, `docs/images/synthetic-poster.png`: existing application screenshots copied for stable public links; fictional evidence/provider/QA data, explicitly captioned.
- [ ] `docs/architecture/review_workflow.md`, `review_graph.mmd`, `review_release_files.md`, `review_source_manifest.json`, `foundation_demo_evidence.json`, `foundation_sdk_observations.json`, `foundation_validation.txt`.
- [ ] Updated `docs/upgrade_audit/PROGRESS.md` and `logs/HANDOFF.md`/`logs/spend.md` for project continuity.
- [ ] Existing linked `docs/upgrade_audit/PRODUCT_SLICE_RESULT.md`, `config/runtime.production.toml`, `business_profile/llm/caller.py`, and existing full-app `requirements.txt` for README references. Do not publish only this list as a standalone complete application.

## Full application versus the credential-free foundation

The list above reproduces the new review feature; it is not a complete full-app packaging audit. To publish the assembled dashboard and existing paid pipeline, review all current application source packages, `api/main.py`, `api/routes/`, `api/jobs/`, `api/` helpers, `frontend/index.html`, `frontend/landing.html`, and the complete `frontend/assets/` tree, plus the application's existing configuration/dependency files. In particular earlier local WP0/WP1 work in those files is not magically present on the remote repository. Preserve it and release it through its own reviewed file selection. The source-only export proof intentionally does not require launching that full shell or live providers.

## Exclude from release

- `.env*`, keys, credential files, private configuration, database DSNs or account tokens.
- `database_export/`, database dumps, BSONs, customer profiles/scrapes, `outputs/`, galleries, private customer assets and arbitrary root JSON/media files.
- `.review-demo*/`, all SQLite checkpoint/binding files/sidecars, `demo.json`, temporary ports/container identities, test PostgreSQL data, `.venv-workflow*/`, bytecode and pytest scratch data.
- Historical audit transcripts and noisy artifacts except the explicitly linked scoped result/progress documents and safe synthetic evidence listed above.
- Existing staged pytest PNG scratch artifacts; staged status is not evidence that they belong in this release.
- Reel/Veo changes and generated media from earlier work are outside this foundation's diff and authorization.

No GitHub publication was performed. Review each selected diff before any later user-authorized commit/push; never blindly stage the entire tree.
