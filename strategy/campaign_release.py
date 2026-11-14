"""Version information pinned when a Campaign plan is created."""
from functools import lru_cache
from hashlib import sha256
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_COMMIT = "4648fbc043219661023fa97d92446bec9323fba4"
HEADING_REVIEW_POLICY = "campaign-copy@2"
SOURCE_CTA_POLICY = "campaign-copy@3"
GUARDRAILS = {"stable": "campaign-copy@1", "candidate": SOURCE_CTA_POLICY}
# This is also the contract read by pre-lifecycle Campaign drafts.
STABLE_GUARDRAILS = GUARDRAILS["stable"]


def active_release():
    release = os.environ.get("ADZY_CAMPAIGN_RELEASE", "stable")
    if release not in GUARDRAILS:
        raise ValueError("ADZY_CAMPAIGN_RELEASE must be stable or candidate")
    return release


@lru_cache(maxsize=1)
def code_commit():
    revision = os.environ.get("ADZY_CAMPAIGN_BUILD_COMMIT")
    if revision is None:
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("ADZY_CAMPAIGN_BUILD_COMMIT must be a full Git SHA")
    return revision


@lru_cache(maxsize=None)
def implementation_hash(path):
    return sha256((ROOT / path).read_bytes()).hexdigest()


def release_metadata(release, planner, edition_sha256=None):
    return {
        "schema": "adzy.campaign-release@1",
        "release": release,
        "commit": code_commit(),
        "reference_commit": REFERENCE_COMMIT,
        "model": {"provider": None, "model_id": None, "mode": "deterministic"},
        "prompt": {"id": None, "mode": "no_model_prompt"},
        "planner": {
            "policy": planner,
            "sha256": implementation_hash("strategy/campaign_planner.py"),
            "edition_sha256": edition_sha256,
            "evidence_implementation_sha256": implementation_hash("strategy/campaign_evidence.py"),
            "edition_implementation_sha256": implementation_hash("strategy/campaign_editions.py"),
            "transport_sha256": implementation_hash("strategy/campaign_research.py"),
            "socket_policy_sha256": implementation_hash("scraper/campaign_http.py"),
            "renderer_sha256": implementation_hash("poster/campaign_package.py"),
            "locked_dependencies_sha256": implementation_hash("requirements-app.txt"),
            "package_sha256": implementation_hash("strategy/campaign_package.py"),
        },
        "guardrails": {
            "policy": GUARDRAILS[release],
            "sha256": implementation_hash("strategy/campaign_copy.py"),
        },
    }
