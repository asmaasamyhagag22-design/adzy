"""POST /api/run — kick off a pipeline job.

Returns immediately with a job_id. The caller subscribes to
GET /api/jobs/{job_id}/stream for live progress.

Pipeline runs in a thread (asyncio.to_thread) so the event loop
stays responsive for SSE streams of other concurrent jobs.

THE JOB IS CREATED BEFORE ANY EXTERNAL WORK (§2), through the persistence provider — so the id the
caller polls is the id the selected backend actually stores, not an in-memory handle that a durable
read would never find. Ownership is a capability DIGEST; the raw token is returned once, to the
caller, and never stored anywhere.

FINALISATION IS THE ROUTE'S, NOT THE RUNNER'S (§4). The runner used to publish FINAL/DONE itself,
which under a durable backend would mark the job succeeded before its result was committed —
a succeeded job that cannot say what it produced. So it defers, and `finish_direct_result_job`
commits the result, the terminal status and the terminal event as one fact.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from scraper.url_utils import is_safe_public_url

from ..jobs.recorder import JobRecorder
from ..jobs.runner import has_llm, run_pipeline_job
from ..job_access import issue_job_capability
from ..persistence import get_persistence
from ..schemas import RunRequest, RunResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/run", response_model=RunResponse)
async def run(req: RunRequest, request: Request) -> RunResponse:
    # SSRF guard: the pipeline fetches this URL server-side, so reject anything
    # that isn't a public http(s) address (blocks localhost / internal ranges /
    # cloud metadata 169.254.169.254).
    if not is_safe_public_url(str(req.url)):
        raise HTTPException(
            status_code=400,
            detail="URL not allowed: must be a public http(s) address (no localhost/internal/metadata hosts).",
        )

    # Fail fast if LLM requested but no provider configured (Gemini OR OpenAI).
    if not req.skip_llm and not has_llm():
        raise HTTPException(
            status_code=400,
            detail=(
                "No LLM provider configured on the server. Set GOOGLE_CLOUD_PROJECT "
                "(Gemini via Vertex/ADC) or OPENAI_API_KEY, or pass skip_llm=true for rules-only."
            ),
        )

    persistence = get_persistence()
    job_token, owner_principal = issue_job_capability()
    job_id = persistence.create_capability_job(capability_digest=owner_principal)
    recorder = JobRecorder(job_id=job_id, provider=persistence)

    # Spawn the pipeline in a worker thread so we don't block the event loop.
    # Note: we do NOT await this task — fire-and-forget. Status flows back
    # through the recorder and is read back from the selected backend.
    asyncio.create_task(_run_in_thread(
        job_id=job_id,
        url=str(req.url),
        recorder=recorder,
        skip_llm=req.skip_llm,
        model=req.model,
        persist_to_disk=req.persist_to_disk,
    ))

    return RunResponse(job_id=job_id, job_token=job_token)


async def _run_in_thread(
    *, job_id: str, url: str, recorder: JobRecorder, skip_llm: bool,
    model: str, persist_to_disk: bool,
) -> None:
    """Bridge: run the synchronous pipeline in a worker thread, then finalize it."""
    persistence = get_persistence()
    try:
        final_payload = await asyncio.to_thread(
            run_pipeline_job,
            job_id, url, recorder,
            skip_llm=skip_llm,
            model=model,
            persist_to_disk=persist_to_disk,
            # The route owns the terminal transition, because only the route can commit the
            # result WITH it. See the module docstring.
            defer_final_event=True,
        )
        if final_payload is None:
            # The pipeline already emitted its own terminal ERROR event, which IS the failure
            # transition on both backends. Nothing to add; adding it would be a second terminal.
            return
        if recorder.result is None:
            raise RuntimeError("pipeline completed without a profile result")
        committed = await asyncio.to_thread(
            persistence.finish_direct_result_job,
            job_id=job_id, profile=recorder.result,
            manifest_path=recorder.manifest_path or "",
            profile_path=recorder.profile_path or "",
            payload=final_payload)
        if not committed:
            raise OSError("the job result could not be committed")
    except Exception as e:  # noqa: BLE001
        # Last-resort guard for failures OUTSIDE the pipeline's own try block — the finalizing
        # commit, or asyncio.to_thread itself. A job that got this far must not stay running.
        logger.exception("run job %s failed outside the pipeline: %s", job_id, e)
        try:
            await asyncio.to_thread(
                persistence.finish_job_failure,
                job_id=job_id, error="Run result could not be saved.",
                payload={"error_type": type(e).__name__})
        except Exception:  # noqa: BLE001 — logged; the original failure is what matters
            logger.exception("could not terminalize job %s", job_id)
