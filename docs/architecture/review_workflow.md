> Historical foundation scope. The application integration now supersedes the earlier dashboard limitation; see [current application architecture](app_review.md).

# ADZY campaign review foundation

Implemented scope: an existing owner-visible canonical strategy and its immutable evidence → local durable review progress → exact human strategy decision → existing CreativeContract compiler. Entry points are `workflows.runtime.ReviewWorkflow` and `python -m workflows.demo`. The existing Adzy dashboard now invokes this runtime through authenticated application routes; see [application integration](app_review.md). There is no paid execution, provider retry, collection, worker, scheduler, or deployment in this graph.

## Executable boundaries

| Component | Implementation |
|---|---|
| Typed/versioned state and resume payload | `workflows/contracts.py`: `ReviewState`, `Decision`, `adzy.review.v1` |
| Actual compiled graph | `workflows/graph.py`: `build_graph`, `StateGraph(...).compile(checkpointer=...)` |
| Application adapter | `workflows/services.py`: `CampaignReviewServices` → `api.persistence.PostgresPersistence` |
| Three typed tools | `workflows/tools.py`: actual `StructuredTool.invoke`, restricted current-view/forbid-extra input schemas, structured result envelope |
| Local workflow service | `workflows/runtime.py`: explicit `setup`, `start`, `status`, `resume` |
| SDK observations | `workflows/telemetry.py`: Langfuse `start_as_current_observation`, mask, explicit outcomes, flush |
| Synthetic demo / isolated validation | `workflows/demo.py`, `workflows/synthetic.py`, `workflows/verify.py` |

`assess` actually executes all three tools. The research tool deserializes the existing canonical ResearchQuestion, EvidenceRecord, and Claim contracts and calls **the existing `evaluate_research_sufficiency`**. It does not call an LLM or copy the sufficiency algorithm. Requirements inspection reads the existing persisted verifier and the canonical current SWOT approval projection. Missing SWOT approval is a typed blocking prerequisite: review it in the application first. This bounded graph reviews StrategyVersion, rather than manufacturing a new SWOT or strategy.

`apply_decision` delegates to the existing `PostgresPersistence.decide_strategy` / `PersistenceService.decide_current_strategy`. The compiler node invokes the existing `CreativeContractCompiler.compile` against a fresh authoritative context. A compiler rejection remains a rejection; the workflow cannot weaken proof/message checks.

## Actual graph

The checked-in [review_graph.mmd](review_graph.mmd) is generated using `compiled.get_graph().draw_mermaid()`. A behavioral test compares it to a freshly compiled graph. Regenerate it from an initialized/started demo:

```bash
python -m workflows.demo diagram --directory .review-demo > docs/architecture/review_graph.mmd
```

Routing:

- `assess`: insufficient/unverified research or unmet prerequisites → `BLOCKED` plus reasons/actions; rejected decision → `REJECTED`; existing approval → `compile_contract`; otherwise → `human_review`.
- `human_review`: durable interrupt carrying subject ID/hash, validated `Decision` resume schema, no approval writes.
- `apply_decision`: exact subject revalidation then canonical decision write. Rejection terminates; approval routes to compiler.
- `compile_contract`: rechecks research, eligibility, current SWOT approval, exact current subject and authoritative strategy approval, then compiles. Returns the authoritative contract, reference and hash.

`READY_FOR_CREATIVE` describes a successfully compiled contract at that inspection time. It is not a reservation, generation authorization, permanent approval of future work, or guarantee that the brand cannot change immediately afterward. The existing creative pipeline must perform its normal checks when execution starts.

## Ownership, state, and persistence

Application PostgreSQL stays authoritative for users, brands, profile snapshots, immutable Runs, approvals, and artifacts. There are **no application schema changes**.

Local storage setup is explicit: `setup(directory)` creates `bindings.sqlite` and calls `SqliteSaver.setup()` for `checkpoints.sqlite`. The binding table stores only workflow UUID, actor UUID, domain reference, exact subject UUID, and implementation version. Checkpoints contain version, opaque IDs, SHA-256 digests, reason/action projections, decision tokens and the compiled CreativeContract, including its approved strategy text. Treat these files as private application state. Full profiles, media, DSNs, secrets and sessions are not checkpoint inputs. SQLite WAL sidecars are runtime files.

The caller constructs `ReviewWorkflow(..., actor_id=authenticated_actor)`. This is a **trusted service argument**, not an unauthenticated public API field. Each start/status/resume validates both the server-side workflow binding and current application visibility. Knowledge of `thread_id` confers no access. The CLI uses the synthetic actor created in its isolated database; it is a local operator tool, not an authentication implementation for public clients. The authenticated HTTP wrapper derives the actor from existing authentication; synchronous FastAPI handlers run in the worker pool.

A separate SQLite execution gate serializes local entry points across the binding commit and first checkpoint while the saver writes to its own file. This is a deliberately coarse local lock with a 30-second acquisition timeout; it does not claim distributed scheduling or durable workers. Protect all three SQLite files as application-private storage. Direct checkpoint access is trusted administrative access and is not exposed through the service.

Every workflow pins `adzy.review.v1` in registry and state. Unknown versions are refused; no automatic checkpoint migration is implemented. The application record remains readable through existing APIs regardless of workflow compatibility.

## Exact decisions and crash recovery

1. Start resolves an explicit StrategyVersion. The interrupt binds to that ID plus output hash, exact profile ID, and evidence hash.
2. Resume validates the submitted ID/hash, reauthorizes the actor, and rechecks currentness and immutable references. A newer subject yields `BLOCKED / STALE_SUBJECT`, with no approval of either version.
3. The write is in a separate node from the interrupt. The canonical PostgreSQL service checks currentness under its existing brand lock, then uses its existing idempotent decision semantics.
4. If the approval commits and the process dies before the next checkpoint, `apply_decision` remains pending with the persisted explicit decision. A fresh graph executes that node again; it calls the canonical service on the same subject/decision, receiving the existing approval rather than adding a second effect.
5. A conflicting authoritative decision causes `DECISION_CHANGED`, rather than overwriting it during recovery. Repeated completed resumes perform no approval write and recompile readiness from authority. `status` also rechecks a previously ready result.
6. No transaction spans PostgreSQL and SQLite; no exactly-once guarantee is claimed. Abrupt-failure recovery is tested with a subprocess calling `os._exit(73)` immediately after the real approval commit. Separate subprocess validation proves durable pause/resume. There are no external paid effects in replayable nodes.

Terminal service failures return a blocked result; there is no automatic retry loop. A user can inspect authoritative state and start a new review after repairing prerequisites. Initial creation is registered before graph execution. A binding without a usable checkpoint is reported as FAILED/START_FAILED, with authoritative inspection required; it is never presented as resumable or replayed automatically.

## Langfuse: real SDK, bounded export

The selected official API is **Langfuse Python SDK 5.0.1 observations**, not a custom JSON trace format. Execution segments, nodes, and tools emit SDK observations correlated by the same workflow trace ID. This adapter does not certify a parent/child hierarchy across graph executor threads. The workflow UUID deterministically hashes to a stable 128-bit trace ID, correlating resumed segments without exporting customer IDs or documents. Completed observations have SDK start/end timestamps. Outcomes and sanitized errors are explicit. No `generation` observations, token counts, model calls, prices, or dollar costs are fabricated; cost completeness is `UNKNOWN`.

The default service/demo has no Langfuse client. Activation is explicit in an application process (the demo/verification runners always disable remote export and retain their network firewall):

```bash
export ADZY_LANGFUSE_ENABLED=1
export LANGFUSE_BASE_URL='https://your-langfuse-host.example'
export LANGFUSE_PUBLIC_KEY='your-project-public-key'
export LANGFUSE_SECRET_KEY='your-project-secret-key'
```

Then construct/call `ReviewWorkflow` through your authenticated application composition. Configure secrets outside Git. SDK 5 requires a compatible Langfuse server; consult the [official upgrade guidance](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5) for server requirements (documented candidate minimum: 4.59.0). No external account was contacted and no server stack was deployed during this work. Invalid/incomplete tracing configuration degrades to disabled tracing; this is an observability fallback, never a business fail-open.

The adapter uses a private OpenTelemetry TracerProvider and the official SDK-only span filter, isolating unrelated instrumentation. Observations are explicit: no LangChain callback with raw auto-captured graph inputs/outputs is registered. Metadata and output are allowlisted before SDK calls. The SDK `mask` hook additionally strips arbitrary fields/strings from SDK-managed inputs, outputs, and metadata, tested by deliberately passing private-looking values directly to the SDK. The adapter catches exceptions **before** the SDK observation context can auto-capture their raw messages, records only `ERROR`, closes the SDK context cleanly, then propagates the original exception to business handling. SDK setup/update/export/flush errors cannot turn a rejected business result into success or prevent a valid result.

LangSmith is a transitive package required by langchain-core. Its tracing is explicitly disabled around graph execution, and the demo also disables inherited tracing environment flags. Do not attach a second Langfuse callback: that would duplicate observations. The adapter creates no accounting authority; application accounting remains separate.

Local evidence uses the SDK's supported `span_exporter` constructor injection with an OpenTelemetry `InMemorySpanExporter`. These are **actual SDK-produced observations captured locally**, not proof of a persistent Langfuse server, UI screenshots, or successful hosted ingestion. An exporter that raises is also tested without changing the graph outcome.

## Reproduction and dependencies

Use the exact commands in the [README](../../README.md). `requirements-workflow.in` names direct dependencies; `requirements-workflow.txt` pins the resolved optional set for the tested Python 3.12/Linux environment. The existing full application requirements and lock are preserved. Install into an isolated environment rather than overlaying the optional lock onto the full app environment; no parity claim is made for those two environments.

Docker image acquisition is an explicit prerequisite. Demo `init` uses `--pull=never` and a randomly named/labeled ephemeral container exposing PostgreSQL on a random **loopback-only** port. Its trust authentication is limited to disposable fictional data; it is not a deployment configuration. The schema guard validates the reserved test database before migration. Neither production nor existing demo databases are used. The running demo denies external DNS and socket connections before application imports, disables private `.env` loading, and makes no model/provider calls. Default installation-free runtime needs Docker locally; it is not a pure-Python SQLite replacement for the application database.

Cleanup validates the demo container name and label, stops that container, and deletes only enumerated files in the specified directory. It does not remove images, database volumes belonging to other applications, or arbitrary user files. After interruption during initialization, an orphaned labeled container may require explicit operator cleanup; no automatic broad Docker prune is used.

## Explicit remaining work

Complete semantic claims grounding and paid-operation reservation/journaling remain pending. This foundation does not relocate paid image/video/model execution, implement paid retries, guarantee provider exactly-once behavior, schedule distributed workers or certify live provider quality/production readiness. The existing pipeline remains the creative execution path.

Official references consulted for version-sensitive APIs: [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Langfuse Python API](https://python.reference.langfuse.com/langfuse), and [masking/advanced usage](https://langfuse.com/docs/observability/sdk/python/advanced-usage).
