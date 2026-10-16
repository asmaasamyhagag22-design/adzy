from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict

VERSION = 'adzy.review.v1'
Status = Literal['NEEDS_REVIEW', 'BLOCKED', 'REJECTED', 'READY_FOR_CREATIVE', 'FAILED']

class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    subject_id: str
    subject_hash: str
    decision: Literal['approved', 'rejected']

class ReviewState(TypedDict, total=False):
    version: str
    workflow_id: str
    subject_id: str
    subject_hash: str
    profile_id: str
    evidence_hash: str
    status: Status
    reasons: list[str]
    required_actions: list[str]
    decision: dict
    contract_id: str
    contract_hash: str

    creative_contract: dict
