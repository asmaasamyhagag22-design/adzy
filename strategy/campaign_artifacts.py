from dataclasses import dataclass
from typing import TypedDict
from uuid import UUID


MAX_CAMPAIGN_ARCHIVE_BYTES = 16 * 1024 * 1024


class PosterHashes(TypedDict):
    portrait: str
    square: str


class CampaignArtifactManifest(TypedDict):
    version: int
    proposal_sha256: str
    poster_sha256: PosterHashes
    measurements_sha256: PosterHashes
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedCampaignBundle:
    run_id: UUID
    profile_snapshot_id: UUID
    previous_approval_id: str | None
    approval_id: UUID
    manifest: CampaignArtifactManifest
    archive: bytes
