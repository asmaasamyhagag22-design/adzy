"""Authenticated single-instance review orchestration. Sync routes run in FastAPI's worker pool."""
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .auth import require_user
from ..persistence import get_persistence
from ..user_auth import webapp_dir

router = APIRouter()
logger = logging.getLogger(__name__)


class StartReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    domain_key: str = Field(min_length=1, max_length=255)
    subject_id: str = Field(min_length=1, max_length=64)


class ResumeReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    subject_id: str = Field(min_length=1, max_length=64)
    subject_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['approved', 'rejected']


class ReviewResponse(BaseModel):
    workflow_id: str
    status: Literal['NEEDS_REVIEW', 'BLOCKED', 'REJECTED', 'READY_FOR_CREATIVE', 'FAILED']
    subject_id: str = ''
    subject_hash: str = ''
    required_actions: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    creative_contract: dict | None = None
    contract_id: str = ''
    contract_hash: str = ''


@lru_cache(maxsize=1)
def telemetry_client():
    from workflows.telemetry import Telemetry
    return Telemetry.from_env().client


def get_review_telemetry():
    from workflows.telemetry import Telemetry
    # Execution-local stack/correlation; only the exporter client is shared.
    return Telemetry(telemetry_client())


def shutdown_review_telemetry():
    if telemetry_client.cache_info().currsize:
        client = telemetry_client()
        if client:
            try: client.shutdown()
            except Exception: pass
        telemetry_client.cache_clear()


def workflow(user, telemetry):
    persistence = get_persistence()
    if not persistence.supports_canonical_strategy():
        raise HTTPException(503, detail={'code':'CANONICAL_BACKEND_REQUIRED'})
    from workflows.runtime import ReviewWorkflow, setup
    raw = os.getenv('ADZY_REVIEW_STATE_DIR')
    directory = Path(raw).expanduser() if raw else webapp_dir() / 'review'
    if not directory.is_absolute():
        raise HTTPException(503, detail={'code':'REVIEW_CONFIGURATION_ERROR'})
    setup(directory)
    return ReviewWorkflow(directory,persistence,actor_id=user['id'],telemetry=telemetry)


def execute(user, telemetry, operation):
    from workflows.services import ReviewBoundaryError
    try:
        result = operation(workflow(user,telemetry))
        # Stale is a conflict even when discovered in a graph node, never approve latest.
        if 'STALE_SUBJECT' in result.get('reasons',[]):
            raise HTTPException(409, detail={'code':'STALE_SUBJECT'})
        response = ReviewResponse.model_validate(result)
        if response.status != 'READY_FOR_CREATIVE':
            response.creative_contract = None
            response.contract_id = response.contract_hash = ''
        return response
    except ReviewBoundaryError as exc:
        code = exc.code
        raise HTTPException(404 if code=='NOT_VISIBLE' else 409,
                            detail={'code':code if code in {'NOT_VISIBLE','REVIEW_SUBJECT_MISMATCH','DECISION_CHANGED','START_FAILED','INCOMPATIBLE_VERSION'} else 'REVIEW_CONFLICT'}) from None
    except HTTPException:
        raise
    except Exception:
        logger.warning('Review operation failed; consult authoritative state before retrying writes.')
        raise HTTPException(503,detail={'code':'REVIEW_UNAVAILABLE','required_action':'RELOAD_REVIEW_STATE'}) from None
    finally:
        telemetry.flush()


@router.post('/review/start', response_model=ReviewResponse)
def start(req: StartReview, user: dict = Depends(require_user), telemetry=Depends(get_review_telemetry)):
    return execute(user,telemetry,lambda w:w.start(domain_key=req.domain_key,subject_id=req.subject_id,reuse=True))


@router.get('/review/{workflow_id}', response_model=ReviewResponse)
def status(workflow_id: str, user: dict = Depends(require_user), telemetry=Depends(get_review_telemetry)):
    return execute(user,telemetry,lambda w:w.status(workflow_id))


@router.post('/review/{workflow_id}/resume', response_model=ReviewResponse)
def resume(workflow_id: str, req: ResumeReview, user: dict = Depends(require_user), telemetry=Depends(get_review_telemetry)):
    return execute(user,telemetry,lambda w:w.resume(workflow_id,req.model_dump()))
