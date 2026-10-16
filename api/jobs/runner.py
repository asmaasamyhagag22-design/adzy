"""Pipeline stage runner.

Drives the manifest → profile pipeline stage-by-stage, emitting
StageEvents between stages so the SSE endpoint can stream progress.

Runs in a worker thread (via asyncio.to_thread) because:
- The scraper uses Playwright (synchronous)
- The OpenAI SDK calls are blocking
- We don't want to stall the FastAPI event loop for 30+ seconds

Cross-thread event pushes go through JobStore.emit_threadsafe()
which marshals back to the original loop via call_soon_threadsafe.

The pipeline imports the SAME functions the CLI uses. No duplication.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Pipeline imports — identical to what the CLI uses.
from business_profile.build import _attach_catalog
from business_profile.llm.caller import Caller, MockCaller, default_caller
from business_profile.llm.embeddings import embed_texts
from business_profile.llm.evidence_pack import build_evidence_pack
from business_profile.llm.extractor import run_llm_extraction
from business_profile.llm.rag import RagRetriever, build_full_evidence_pack
from business_profile.llm.validator import validate_llm_extraction
from business_profile.merger import merge_profile
from business_profile.rules.orchestrator import apply_rules
from scraper import scrape as scraper_scrape

from ..schemas import Stage, StageEvent, StageStatus
from .recorder import JobRecorder
from .store import JobStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(
    job_id: str, stage: Stage, status: StageStatus,
    duration_ms: int = 0,
    payload: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> StageEvent:
    return StageEvent(
        job_id=job_id, stage=stage, status=status, timestamp=_now(),
        duration_ms=duration_ms, payload=payload, error=error,
    )


# ---------------------------------------------------------------------
# The main runner
# ---------------------------------------------------------------------

def run_pipeline_job(
    job_id: str,
    url: str,
    store: "JobStore | JobRecorder",   # anything with emit_threadsafe/set_result_threadsafe
    *,
    skip_llm: bool = False,
    model: str = "",   # advisory only — the pipeline uses the configured Gemini stack
    persist_to_disk: bool = True,
    caller_factory: Optional[Any] = None,  # for tests: callable returning a Caller
    scraper_fn: Optional[Any] = None,      # for tests: callable replacing scraper.scrape
    # Per-job crawl snapshot.  None preserves the direct scraper/CLI config defaults; the web
    # scrape route always supplies concrete validated values from its request.
    max_pages: Optional[int] = None,
    max_depth: Optional[int] = None,
    delay: Optional[float] = None,
    # "" = resolved by `scraper.config.scrapes_root()` inside `scrape()` — the webapp and the CLI
    # must write to the SAME corpus, and a literal here is how they stop doing that.
    output_root: str = "",
    use_rag: Optional[bool] = None,        # Pillar 2; default: on in prod, off when a test injects a caller
    embed_fn: Any = embed_texts,           # for tests: inject a fake embedder
    # Web-app scrape has one more REQUIRED commit (BrandStore).  It asks the pipeline to return the
    # final payload and publishes FINAL/DONE only after that commit succeeds.  Legacy /api/run keeps
    # the default and therefore its existing terminal contract.
    defer_final_event: bool = False,
) -> Optional[dict[str, Any]]:
    """Run the full pipeline for one job. Called in a worker thread.

    Emits a stream of StageEvents via store.emit_threadsafe(). On any
    stage failure, emits an ERROR event for that stage and stops; the
    job transitions to FAILED automatically via the store's status
    transition rules.

    On success, stores the full profile via set_result_threadsafe and normally emits FINAL/DONE.
    With ``defer_final_event=True``, returns the terminal payload instead so the caller can first
    complete a required persistence step.
    Optionally persists the profile to disk next to the manifest.
    """
    emit = lambda stage, status, **kw: store.emit_threadsafe(
        job_id, _make_event(job_id, stage, status, **kw),
    )

    try:
        # ---- 1. Scrape ----
        emit(Stage.SCRAPE, StageStatus.STARTED)
        t0 = time.monotonic()
        scrape_fn = scraper_fn or scraper_scrape
        crawl_options = {
            key: value for key, value in (
                ("max_pages", max_pages),
                ("max_depth", max_depth),
                ("delay", delay),
            )
            if value is not None
        }
        manifest, out_dir = scrape_fn(url, output_root, **crawl_options)
        scrape_ms = int((time.monotonic() - t0) * 1000)
        manifest_path = str(Path(out_dir) / "manifest.json")
        emit(
            Stage.SCRAPE, StageStatus.DONE, duration_ms=scrape_ms,
            payload={
                "manifest_path": manifest_path,
                "pages_succeeded": manifest.scrape_meta.pages_succeeded,
                "pages_attempted": manifest.scrape_meta.pages_attempted,
                "ready_for_extraction": manifest.readiness.ready_for_extraction,
                "languages": [e.code for e in manifest.languages],
                "failures": len(manifest.failures),
            },
        )
        if not manifest.readiness.ready_for_extraction:
            # Scrape ran but produced nothing useful — fail fast
            raise RuntimeError(
                f"Scrape not ready for extraction: missing {manifest.readiness.major_missing}",
            )

        # ---- 2. Rules ----
        emit(Stage.RULES, StageStatus.STARTED)
        t0 = time.monotonic()
        rules_profile = apply_rules(manifest, manifest_path=manifest_path)
        # THE CATALOG, ON THE DASHBOARD PATH TOO.
        #
        # `business_profile.build` attaches it on both of ITS paths, and this runner is a third
        # pipeline that never called it: `apply_rules` -> LLM -> `merge_profile` -> stored. So the
        # CLI knew what the shop sells and the dashboard did not. MEASURED on a fresh Al Fouad
        # scrape run from the dashboard this minute: the crawl reached 42 pages, 40 of them product
        # pages, and `build_catalog` on that very manifest returns 24 priced items WITH images —
        # while the stored brand said `catalog: []`. Nothing failed; a step simply was not wired
        # into this caller, which is the defect class this whole day has been about.
        #
        # ONE place, not two: `merge_profile` starts with `deepcopy(rules_profile)`, so anything
        # attached here survives the LLM merge, and the skip_llm branch returns this same object.
        _attach_catalog(rules_profile, manifest)
        rules_ms = int((time.monotonic() - t0) * 1000)
        emit(
            Stage.RULES, StageStatus.DONE, duration_ms=rules_ms,
            payload={
                "extractors_used": rules_profile.extraction_meta.rule_extractors_used,
                "fields_extracted": rules_profile.quality.fields_extracted,
                "fields_missing": rules_profile.quality.fields_missing,
                "major_missing": rules_profile.quality.major_missing,
            },
        )

        # If skipping LLM, jump straight to FINAL with the rules-only profile
        if skip_llm:
            # Persist if requested
            profile_path: Optional[str] = None
            if persist_to_disk:
                profile_path = str(Path(out_dir) / "business_profile.json")
                Path(profile_path).write_text(
                    rules_profile.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            profile_dict = rules_profile.model_dump(mode="json")
            store.set_result_threadsafe(
                job_id, profile_dict,
                manifest_path=manifest_path, profile_path=profile_path,
            )
            final_payload = {
                "ready_for_strategy": rules_profile.quality.ready_for_strategy,
                "fields_extracted": rules_profile.quality.fields_extracted,
                "fields_inferred": rules_profile.quality.fields_inferred,
                "fields_missing": rules_profile.quality.fields_missing,
                "llm_skipped": True,
                "profile_path": profile_path,
            }
            if not defer_final_event:
                emit(Stage.FINAL, StageStatus.DONE, payload=final_payload)
            return final_payload

        # ---- 3. Evidence pack ----
        emit(Stage.EVIDENCE_PACK, StageStatus.STARTED)
        t0 = time.monotonic()
        pack = build_evidence_pack(manifest, rules_profile)
        pack_ms = int((time.monotonic() - t0) * 1000)

        # Pillar 2 RAG (default ON in production; OFF when a test injects a
        # caller_factory, so unit tests never fire a real embedding call). The
        # retriever serves each group its top-K relevant blocks from the FULL
        # (uncapped) pack; the validator must then see that full pack (a superset
        # of the capped `pack`), else a legit retrieved citation reads as a
        # hallucination. Degrades to the capped pack if embeddings are unavailable.
        rag_on = use_rag if use_rag is not None else (caller_factory is None)
        retriever = None
        validation_pack = pack
        if rag_on and not skip_llm:
            full_pack = build_full_evidence_pack(manifest, rules_profile)
            retriever = RagRetriever(full_pack, pack, embed_fn=embed_fn)
            validation_pack = full_pack

        emit(
            Stage.EVIDENCE_PACK, StageStatus.DONE, duration_ms=pack_ms,
            payload={
                "blocks_total_in_manifest": pack.blocks_total_in_manifest,
                "blocks_after_filter": pack.blocks_after_filter,
                "block_count": pack.block_count,
                "rag_enabled": retriever is not None,
                "rag_full_block_count": validation_pack.block_count,
                "page_type_distribution": pack.page_type_distribution,
                "missing_fields": pack.missing_fields,
            },
        )

        # ---- 4. LLM extraction ----
        emit(Stage.LLM, StageStatus.STARTED)
        t0 = time.monotonic()
        if caller_factory is not None:
            caller: Caller = caller_factory()
        else:
            # Gemini ONLY (owner directive: no OpenAI). gpt-4o-mini UNDER-extracted
            # offerings on big multi-service sites (MEASURED te.eg: 7 service pages
            # scraped, all the offering text present, yet only 1 offering survived).
            # Flash is the cheap token-heavy default; the complex creative steps use Pro.
            caller = default_caller(strong=False)
        # v0.2-b1: surface the rules-derived category so the offerings
        # prompt can swap in per-category guidance.
        rules_category_value = (
            rules_profile.category.value.value
            if rules_profile.category.value is not None
            else None
        )
        llm_result = run_llm_extraction(
            pack, caller, rules_category=rules_category_value, retriever=retriever,
        )
        llm_ms = int((time.monotonic() - t0) * 1000)
        # v0.2-b1: detect silent failure for the LLM stage's DONE event.
        # If the pipeline asked for LLM (skip_llm=False) but every group
        # call failed, emit a warning in the event payload so the SSE
        # consumer can render a red banner instead of a green check.
        llm_warning: Optional[str] = None
        if not skip_llm:
            total_groups = llm_result.total_calls + llm_result.total_failed
            if llm_result.total_calls == 0:
                llm_warning = f"llm_silent_failure: 0/{total_groups or 4} group calls succeeded"
            elif llm_result.total_failed > 0:
                # CS-81: a PARTIAL failure (e.g. 3/4 groups failed) previously reported SUCCEEDED
                # with a green FINAL/DONE and NO warning — whole profile sections silently missing.
                # Surface it so the SSE consumer renders a warning instead of a clean success.
                llm_warning = (f"llm_partial_failure: {llm_result.total_failed}/{total_groups} "
                               "group calls failed — some profile sections are incomplete")
        emit(
            Stage.LLM, StageStatus.DONE, duration_ms=llm_ms,
            payload={
                "calls": llm_result.total_calls,
                "failed": llm_result.total_failed,
                "input_tokens": llm_result.total_input_tokens,
                "output_tokens": llm_result.total_output_tokens,
                "cost_usd": llm_result.total_cost_usd,
                "model": llm_result.model,
                "warning": llm_warning,
            },
        )

        # ---- 5. Validate ----
        emit(Stage.VALIDATE, StageStatus.STARTED)
        t0 = time.monotonic()
        payload = validate_llm_extraction(llm_result, validation_pack)
        validate_ms = int((time.monotonic() - t0) * 1000)
        emit(
            Stage.VALIDATE, StageStatus.DONE, duration_ms=validate_ms,
            payload={
                "rejections": payload.diagnostics.total_rejections,
                "fields_dropped": payload.diagnostics.fields_dropped,
                "items_dropped": dict(payload.diagnostics.items_dropped),
                # v0.2-b1: candidate visibility + blacklist visibility
                "candidates_seen": dict(payload.diagnostics.candidates_seen),
                "blacklist_rejections": {
                    k: len(v) for k, v in
                    payload.diagnostics.blacklist_rejections.items()
                },
            },
        )

        # ---- 6. Merge ----
        emit(Stage.MERGE, StageStatus.STARTED)
        t0 = time.monotonic()
        final_profile = merge_profile(
            rules_profile, payload, llm_result, expected_llm=not skip_llm,
        )
        merge_ms = int((time.monotonic() - t0) * 1000)
        emit(
            Stage.MERGE, StageStatus.DONE, duration_ms=merge_ms,
            payload={
                "ready_for_strategy": final_profile.quality.ready_for_strategy,
                "ready_for_poster": final_profile.quality.ready_for_poster,
                "poster_blockers": list(final_profile.quality.poster_blockers),
                "offerings_extraction_note": final_profile.offerings_extraction_note,
                "llm_silent_failure": final_profile.llm_silent_failure,
                "fields_extracted": final_profile.quality.fields_extracted,
                "fields_inferred": final_profile.quality.fields_inferred,
                "fields_missing": final_profile.quality.fields_missing,
                "major_missing": final_profile.quality.major_missing,
            },
        )

        # ---- 7. Persist (audit trail) ----
        profile_path = None
        if persist_to_disk:
            profile_path = str(Path(out_dir) / "business_profile.json")
            Path(profile_path).write_text(
                final_profile.model_dump_json(indent=2),
                encoding="utf-8",
            )

        # ---- 8. Final ----
        profile_dict = final_profile.model_dump(mode="json")
        store.set_result_threadsafe(
            job_id, profile_dict,
            manifest_path=manifest_path, profile_path=profile_path,
        )
        final_payload = {
            "ready_for_strategy": final_profile.quality.ready_for_strategy,
            "ready_for_poster": final_profile.quality.ready_for_poster,
            "poster_blockers": list(final_profile.quality.poster_blockers),
            "offerings_extraction_note": final_profile.offerings_extraction_note,
            "llm_silent_failure": final_profile.llm_silent_failure,
            "schema_version": final_profile.schema_version,
            "llm_cost_usd": final_profile.extraction_meta.llm_cost_usd,
            "llm_calls": final_profile.extraction_meta.llm_calls,
            "profile_path": profile_path,
        }
        if not defer_final_event:
            emit(Stage.FINAL, StageStatus.DONE, payload=final_payload)
        return final_payload

    except Exception as e:  # noqa: BLE001
        logger.exception("Pipeline failed for job %s: %s", job_id, e)
        # Tag the error to the current stage (best effort — check job state)
        current_stage = _infer_failed_stage(store, job_id)
        # CS-80: the /jobs/{id} route is UNAUTHENTICATED, so the client-facing error must NOT carry
        # the full Python traceback OR the raw exception message — either can leak file paths, env
        # values, or internal detail. The full traceback is logged server-side above; the stream
        # gets only the safe exception CLASS name + the stage that failed.
        emit(
            current_stage, StageStatus.ERROR,
            error=f"{current_stage.value} stage failed ({type(e).__name__})",
            payload={"error_type": type(e).__name__},
        )
        return None


def _infer_failed_stage(store, job_id: str) -> Stage:
    """Best-effort: return the last STARTED stage as the failed stage.

    A `JobRecorder` emitted every event this run produced, so it answers from what it already knows
    rather than reading job state back out of a store — which matters under the durable backend,
    where "read the job's history to describe its own failure" would be a database round trip on
    the error path. A raw JobStore caller keeps the original backwards scan.
    """
    tracked = getattr(store, "failed_stage", None)
    if callable(tracked):
        return tracked()
    job = store.get(job_id)
    if job is None:
        return Stage.FINAL
    for ev in reversed(job.events):
        if ev.status == StageStatus.STARTED:
            return ev.stage
    return Stage.SCRAPE


def has_llm() -> bool:
    """Used by the run route to fail fast if LLM extraction is requested but NO provider is
    configured. CS-96: the extraction pipeline is GEMINI-ONLY (default_caller -> Vertex/Gemini;
    Anthropic + OpenAI were removed from extraction). An OPENAI_API_KEY does NOT let the runner
    serve a request, so it must NOT satisfy this gate — that was a fail-fast that passed for a
    config the runner cannot actually run. Gate on Gemini credentials only."""
    return bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
