# Application review integration

## Implemented behavior

The existing Adzy Think/Strategy interface starts, retrieves and resumes a real LangGraph review through authenticated FastAPI routes. Actor identity comes from application authentication. PostgreSQL's canonical services remain the sole approval authority; SQLite stores actor-bound workflow progress on local application-owned disk.

The UI displays the exact reviewed StrategyVersion and graph-required action. Stale decisions return409, foreign owners receive404, and rejected/insufficient cases cannot enable Create. At READY_FOR_CREATIVE, the graph compiles the existing CreativeContract, and the existing Create interface displays its WHO/SAY/PROVE/DO constraints. Paid generation remains outside the graph.

A failed start with no usable checkpoint is FAILED/START_FAILED and is not automatically replayed. The local execution gate covers binding commit and first checkpoint. SQLite connections close on exit. Approval prerequisites/currentness are rechecked; ambiguous writes become blocked rather than blindly retried. Existing canonical idempotency recovers a process exit after approval commit without another approval effect.

## Verification

- Focused graph/application suite: **23 passed, zero failed or skipped**,69.08s. This is scoped verification, not a full-suite claim.
- Real application shell: login → Think/Strategy → human-review pause → refresh → application-process restart → same workflow → UI approval → one exact PostgreSQL approval → READY_FOR_CREATIVE → Creative Direction. No generation button was clicked. Screenshots show fictional Studio Fixture evidence.
- Fresh Python3.12/Linux installation using `python -m pip install -r requirements-app.txt` passed `pip check`.
- A source-only export reproduced workflow init/start/fresh-process resume/status/cleanup and full application startup, `/app` HTTP200, real login and exact-contract approval through application routes. README links and images were checked in the export.
- [Synthetic browser evidence](../architecture/app_browser_evidence.json) records matching workflow/subject before and after restart. [Candidate evidence](../architecture/app_candidate_evidence.json) records the executed commands.

## Observability

Real Langfuse SDK observations were captured locally from application-triggered start/resume. Tests verify safe trace correlation, masked identifiers/URLs, no fabricated model spans/usage/costs, and exporter failure isolation. The browser resume retained six correlated local SDK observations. Its first pre-restart process did not retain its in-memory export, so those spans are not claimed as saved evidence. The demo now saves capture per operation and on lifespan shutdown.

Default application operation creates no exporter. Hosted Langfuse/server ingestion is **unverified**. No external account or customer data was used.

## Supported scope

One application instance with persistent local SQLite and PostgreSQL. Existing authentication is demo-grade. Distributed execution, production operation, complete semantic grounding, paid-operation reservations and live provider quality are not certified. The optional quarantined Facebook collector is an external dependency. Customer campaign imagery, private records and runtime state are excluded from this release.

Provider/model/media spend during verification: **$0**. Existing Reel/Veo sources are retained for application completeness; their execution is outside this verification.

## Release assembly

The release branch is based on the repository's default branch at `4bc3915a593571d0474f8532819f4f63926203dc`. Source comparison established that the older local branch and default-branch application implementations differed only in line endings; README and ignore rules were reconciled separately.467 executable application/source assets match the previously verified integration after line-ending normalization. No graph behavior was changed during publication preparation, so its successful focused suite was not repeated.

The expanded source export includes all current test sources and required prior application dependencies. The export demo, full application startup, real authentication and graph approval reproduced successfully;11 README links/images resolved. Credential-pattern checks examined1025 selected files and1085 indexed files with no findings. Previously tracked raw profiles, campaign imagery and internal logs are excluded from the release tree; existing Git history is preserved.
