"""Bounded Campaign stage outcomes for Langfuse and operational logs."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
from time import monotonic
from urllib.error import HTTPError, URLError

from strategy.campaign_research import ResearchGap
from strategy.campaign_v2 import CampaignConflict
from workflows.telemetry import Telemetry

LOGGER = logging.getLogger("uvicorn.error")
LOG_PREFIX = "ADZY_CAMPAIGN_EVENT "
MODES = {"public_http", "publisher_replay", "simulated_transport"}
TIMEOUTS = {"SOURCE_TIMEOUT", "SOURCE_DEADLINE"}
REASONS = TIMEOUTS | {
    "SOURCE_READ", "SOURCE_HEADING_CTA", "CTA_COPY_MISMATCH", "WRONG_EVIDENCE_SELECTION", "WRITING_EXCEEDS_EVIDENCE", "UNEXPECTED_STOP",
    "SOURCE_FETCH_FAILURE", "CTA_OR_JOURNEY_MISMATCH", "UNSAFE_COPY_OR_GUARD_FAILURE",
    "PAGE_TOO_LARGE", "SOURCE_UNAVAILABLE", "SOURCE_REDIRECT", "SOURCE_URL_CHANGED",
    "SOURCE_NOT_IN_SNAPSHOT", "SOURCE_TEXT_MISSING", "HTML_REQUIRED",
    "UNSUPPORTED_CONTENT_ENCODING", "UNSUPPORTED_TRANSFER_ENCODING", "INVALID_SOURCE_LENGTH", "SOURCE_TRUNCATED", "SOURCE_PARTIAL_RESPONSE",
    "DECISION_EVIDENCE_FOUND", "BUDGET_EXHAUSTED", "NO_MORE_RELEVANT_SOURCES",
    "EDITORIAL_SOURCE_GAP", "SOURCE_CONFLICT", "FEATURE_EVIDENCE",
    "COPY_REQUIRES_EVIDENCE_REVIEW", "CLAIM_EXCEEDS_SOURCE", "POSTER_RENDER_UNAVAILABLE",
    "CURRENT_PACKAGE_APPROVAL_REQUIRED", "CAMPAIGN_CHANGED", "EXPORT_BUSY",
    "BRIEF_REVIEW_REQUIRED", "EVIDENCE_GAP", "PROFILE_CHANGED", "PACKAGE_REQUIRED",
    "DIRECTION_NOT_FOUND", "UNKNOWN", "COMMERCIAL_TERMS", "ACTION_DESTINATION", "QUALIFIED_ACTION",
    "SOURCE_FIELD_CHANGED", "SOURCE_FIELD_MISSING", "SOURCE_SUBJECT_CHANGED",
    "SOURCE_REDIRECT_OUT_OF_SCOPE",
}
STAGES = {
    "campaign.plan", "campaign.persist", "campaign.research", "campaign.fetch", "campaign.select_evidence",
    "campaign.write_copy", "campaign.review_copy", "campaign.verify", "campaign.approval",
    "campaign.export", "campaign.refresh", "campaign.eval",
}


def source_error(exc):
    if isinstance(exc, HTTPError):
        return "SOURCE_REDIRECT" if 300 <= exc.code < 400 else "SOURCE_UNAVAILABLE"
    if isinstance(exc, ResearchGap):
        return str(exc) if str(exc) in REASONS else "UNKNOWN"
    underlying = exc.reason if isinstance(exc, URLError) else exc
    return "SOURCE_TIMEOUT" if isinstance(underlying, TimeoutError) else "SOURCE_UNAVAILABLE"


class CampaignTrace:
    def __init__(self, telemetry=None, *, mode="public_http", emit=False):
        if mode not in MODES:
            raise ValueError("Unknown Campaign source mode")
        self.telemetry = telemetry if telemetry is not None else Telemetry()
        self.mode = mode
        self.emit = emit

    @property
    def ref(self):
        return self.telemetry.trace_id

    @contextmanager
    def stage(self, name):
        if name not in STAGES:
            raise ValueError("Unknown Campaign stage")
        started = monotonic()
        outcome = {"status": "OK"}
        with self.telemetry.observation(name, metadata={"source_mode": self.mode}, result=outcome):
            try:
                yield outcome
            except CampaignConflict as exc:
                outcome.update(status="ERROR", reason=str(exc))
                raise
            except BaseException as exc:
                detail = getattr(exc, "detail", None)
                outcome.update(status="ERROR", reason=detail.get("code") if isinstance(detail, dict) else None)
                raise
            finally:
                reason = outcome.get("reason")
                if reason is not None and reason not in REASONS:
                    outcome["reason"] = "UNKNOWN"
                if self.emit:
                    LOGGER.info("%s%s", LOG_PREFIX, json.dumps({
                        "schema": "adzy.campaign-event@1", "stage": name, "trace_id": self.ref,
                        "at": datetime.now(timezone.utc).isoformat(), "source_mode": self.mode,
                        "elapsed_ms": round((monotonic() - started) * 1000, 3),
                        "status": outcome["status"], "reason": outcome.get("reason"),
                        **({"evidence_hash": outcome["evidence_hash"]} if "evidence_hash" in outcome else {}),
                    }, sort_keys=True))

    def loader(self, load):
        def observed(url, timeout):
            failure = None
            with self.stage("campaign.fetch") as outcome:
                try:
                    page = load(url, timeout)
                    outcome["reason"] = "SOURCE_READ"
                    return page
                except (OSError, ResearchGap) as exc:
                    failure = exc
                    outcome.update(status="ERROR", reason=source_error(exc))
            raise failure
        return observed
