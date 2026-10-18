# ADZY review foundation — implemented and locally verified

Date: 2026-10-16 (Africa/Cairo). Scope: LangGraph review orchestration, deterministic typed LangChain tools, durable local pause/resume, real opt-in Langfuse SDK observations, and a product-first reviewer README. The owner's new prioritization supersedes earlier instructions postponing all Lang* implementation. WP0/WP1 were reused, not repeated.

## What runs

`workflows/graph.py` compiles a real `StateGraph`: assess existing research and prerequisites → conditionally block/reject, pause for an exact human StrategyVersion decision, or use an existing approval → apply the decision through the canonical PostgreSQL service → recheck authority → compile the existing CreativeContract. `workflows/runtime.py` is the reusable actor-bound application service; `python -m workflows.demo` is the executable entry point.

The three actual `langchain-core` tools execute through `StructuredTool.invoke`: `inspect_research_readiness`, `get_review_subject`, and `inspect_creative_contract_requirements`. No prompt/LLM reproduces business rules. The research evaluator, verifier, approval transactions/idempotency, and compiler remain the existing application authorities.

SQLite stores local workflow bindings/checkpoints; PostgreSQL stores authoritative application records. Exact reviewed IDs/hashes are validated at resume. Rejection never compiles a contract. `human_review` has no approval writes; `apply_decision` is a separate replayable node. A subprocess hard-exits with `os._exit(73)` after a real approval commit; recovery observes one approval row and completes without duplicating the effect.

## Exact evidence (do not sum overlapping reruns)

| Check | Result |
|---|---|
| Final foundation suite, `python -m workflows.verify -q tests/test_review_workflow.py` | **16 passed**, 1 existing Alembic deprecation warning, **34.73s**; real isolated PostgreSQL application services, no provider/network calls outside loopback |
| Existing research/approval/compiler regressions, selected commands in validation record | **38 passed / 1 deselected**, **13.79s**; four real PostgreSQL approval/contract cases included |
| Initial expanded regression selection | **38 passed / 1 failed**, **16.93s**; the known benchmark depends on absent private `outputs/buffaloburger_com_result.json`. Recorded, not repaired or fabricated. The final selection explicitly deselects that case |
| Final SDK-focused rerun (overlaps the 16) | **3 passed / 13 deselected**, **13.07s**; actual SDK export capture, sensitive-input/exception redaction, exporter failure isolation |
| Source-only release export | Actual init/start → persisted NEEDS_REVIEW → fresh-process approved resume → READY_FOR_CREATIVE → status → cleanup. Selected reproducibility tests **2 passed / 14 deselected**, **10.14s** (overlap; not a new total) |
| Actual SDK export artifact | **12 SDK-produced observations** across graph start/pause/resume, one stable trace ID, measured start/end durations, node/tool outcomes, cost UNKNOWN; no Langfuse server contacted |
| Dependency isolation | Python **3.12.3**, new optional venv installed from `requirements-workflow.txt`; imports run and `pip check` returns **No broken requirements found**. Base application requirements/lock untouched |

Evidence: [validation log](docs/architecture/foundation_validation.txt), [source-only demo outputs](docs/architecture/foundation_demo_evidence.json), [local SDK observations](docs/architecture/foundation_sdk_observations.json), [behavioral tests](tests/test_review_workflow.py), and the [53-file source manifest](docs/architecture/review_source_manifest.json). The JSON export is **not** a Langfuse screenshot or proof of server ingestion.

The default demo disables private `.env` loading, paid image execution, Langfuse export, and LangSmith tracing; it blocks external DNS/socket connections. Docker image acquisition and dependency installation are setup prerequisites, distinct from runtime. The demo uses synthetic evidence and real application services, with no model/QA/media provider outputs required. Paid/model/media calls and spend: **0 / $0**. No Reel/Veo execution or edits, production database access, hosting, or external account export occurred.

## Tested demo commands

With Python 3.12, Docker, the public PostgreSQL image, and the optional environment active (full installation instructions in README):

```bash
python -m workflows.demo init --directory .review-demo
python -m workflows.demo start --directory .review-demo
# Previous process has exited; this is a new Python process.
python -m workflows.demo resume --directory .review-demo --decision approved
python -m workflows.demo status --directory .review-demo

python -m workflows.demo init --directory .review-demo-stale
python -m workflows.demo start --directory .review-demo-stale
python -m workflows.demo stale --directory .review-demo-stale
python -m workflows.demo resume --directory .review-demo-stale --decision approved

python -m workflows.demo cleanup --directory .review-demo
python -m workflows.demo cleanup --directory .review-demo-stale
```

Observed statuses: `NEEDS_REVIEW` → `READY_FOR_CREATIVE`, with a stable exact StrategyVersion and contract reference/hash. The separate stale case returns `BLOCKED / STALE_SUBJECT` without approving a newer subject. Rejection/insufficiency, foreign-owner entry points, idempotency, crash recovery, tools, and observability failure are verified by the focused suite.

## README and scoped diff

README now opens with the product and intended user, includes two real application screenshots of **explicitly synthetic** research/creative test data, describes a concrete journey, points to actual framework code, and separates the offline demo from full application setup. It includes a current architecture diagram, implementation-status table, focused validation, and repository map. Authorship/license terms are unchanged; no badges, ROI/performance/zero-hallucination/production claims were added.

This task added `workflows/*.py`, optional dependency input/lock, one focused test module, architecture/release/evidence docs, and stable screenshot copies under `docs/images/`; it updated README, local-runtime ignore rules, progress/handoff/spend notes. **No existing application service/compiler/research source, application migrations, frontend behavior, provider code, or base dependency file was changed by this task.** Pre-existing staged and unstaged implementation work stays in place.

## Limits and publication

- Local workflow SQLite is not PostgreSQL checkpoint persistence; no distributed worker/scheduler or cross-database atomic transaction is claimed.
- Langfuse server/hosted ingestion and dashboard UI are unverified. SDK integration is tested through the supported local exporter transport. Optional activation requires explicit credentials and a compatible server; setup is in the technical deep dive.
- The graph ends at creative readiness; existing paid creative execution stays outside it. No automatic paid retry, provider exactly-once guarantee, operation reservation, or complete claims-semantic certification.
- Dashboard buttons do not invoke the new graph yet. The reusable synchronous service trusts an authenticated actor supplied by application composition; the CLI is a local operator demo, not a public authentication layer.
- No full repository test run, live provider quality run, full-app installation certification, Windows certification, or production certification. One existing private-fixture benchmark remains unavailable.
- Initial failure before the first checkpoint can leave a workflow binding without a resumable checkpoint; local lock/storage operational errors require inspection. No automatic checkpoint migration or durable worker recovery daemon is implemented.

Before GitHub reproduction, ship **all** required existing application sources in the [release checklist](docs/architecture/review_release_files.md) and [source manifest](docs/architecture/review_source_manifest.json), including earlier unpublished PostgreSQL/approval/positioning/compiler dependencies, plus new workflow/dependency/demo/test/docs/screenshot files. Publishing only `workflows/` is insufficient. Exclude secrets, private records/dumps/assets, checkpoint stores, venvs, generated media, and audit scratch. No staging, commit, push, branch switch, reset, stash, or deployment was performed.
