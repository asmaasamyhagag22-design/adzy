# -*- coding: utf-8 -*-
"""Rebuild the API's brand document from relational rows, without rebuilding the legacy aggregate.

THE PROBLEM. The file-backed store keeps one large mutable JSON document per brand. Every stage —
scrape, SWOT, strategy, plan, calendar, influencer — read that document, computed a `patch`, and
merged the patch back in. The document is therefore a *fold* of every patch ever applied, with no
record of which stage contributed what, no version, and no way to tell a current fact from a stale
one. The frontend reads that shape, so the API promises it.

THE RESOLUTION. Keep the shape at the boundary; refuse it in storage. Each stage's patch is stored
as the immutable `output` of its own Run, and this module replays the CURRENT successful Run of each
kind, in dependency order, over the current profile. Same document at the boundary, and underneath
it: every contribution attributable, every superseded value still on disk, nothing overwritten.

Three consequences worth stating, because they are the point rather than side effects:

  * A FAILED run changes nothing. It is not current, so it never enters the fold — which is the
    §15 requirement ("a failed newer SWOT does not displace the current successful one") falling out
    of the construction instead of being enforced by a flag.
  * `swot_approved` is DERIVED from an Approval row bound to the exact current SWOT Run, never
    stored as a boolean. A newer SWOT becomes current, the approval no longer matches it, and the
    gate closes by itself. The legacy boolean could not express that and did not.
  * Re-scraping cannot erase analysis. The legacy path needed `_PRESERVE_ON_RESCRAPE` — a hand-kept
    list of keys a new scrape must not clobber — because a fresh `profile_to_brand` emits empty
    placeholders over real work. Here the profile is only the BASE of the fold and the analysis Runs
    are layered on top of it afterwards, so there is nothing to preserve and no list to forget.

ORDER MATTERS. `_FOLD_ORDER` runs base → SWOT → strategy → plan/calendar → creative, so a later
stage's view of a shared key wins, matching the order the legacy store happened to apply patches in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

#: Run kinds folded into the brand document, in the order their patches were historically applied.
#: A kind absent here is simply not part of the brand projection — it is not silently dropped from
#: storage, it is just not something the SPA reads off the brand object.
#: These are `db.models.RUN_KINDS` values — the schema's vocabulary, not a parallel one invented
#: here. `calendar` carries the marketing plan and the content calendar together because that is
#: what one plan run produces; `influencer_poster` carries the `ai_influencer` block.
_FOLD_ORDER = (
    "trends",
    "swot",
    "strategy",
    "calendar",
    "social",
    "influencer_poster",
)

#: Keys the relational model owns outright. A Run patch may not overwrite them, because they are
#: derived from rows (ownership, approval) rather than from any stage's output. Without this, an old
#: patch carrying `swot_approved: true` — and the legacy documents DO carry it — would forge an
#: approval that no Approval row backs.
_DERIVED_KEYS = frozenset({
    "owner_id", "unclaimed", "domain_key", "swot_approved", "owner_inputs", "competitors",
    "has_strategy", "strategy_run_id", "strategy_version_id", "strategy_approval_state",
    "strategy_candidate", "strategy_authority",
})


def _epoch(value: Optional[datetime]) -> int:
    """Legacy documents carry integer epoch seconds; timestamps here are aware datetimes."""
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _sanitised(patch: Any) -> dict:
    """A Run output, with the keys the relational model owns stripped out."""
    if not isinstance(patch, dict):
        return {}
    return {k: v for k, v in patch.items() if k not in _DERIVED_KEYS}


class BrandReadModel:
    """Assembles brand documents. Holds no state beyond the service it reads through."""

    def __init__(self, service: Any) -> None:
        self._service = service

    # ------------------------------------------------------------------ one brand
    def document(self, *, brand, user_id, include_runs: bool = True) -> dict:
        """The API-shaped document for one BrandRecord.

        `include_runs=False` is for list views, which render cards and would otherwise pay for a
        current-run lookup per kind per brand. It is a COST decision and never a truth decision: the
        keys it omits are the analysis blocks, and the card does not read them.
        """
        from api.brand_translate import profile_to_brand

        profile = None
        if brand.current_profile_id is not None:
            profile = self._service.current_profile(brand_id=brand.id, user_id=user_id)

        payload = dict(profile.profile) if profile is not None else {}
        document = profile_to_brand(payload, source_url=brand.source_url or "",
                                    domain_key=brand.domain_key)
        if payload:
            # The analysis stages feed off the raw BusinessProfile, not the lossy projection —
            # the same reason the scrape path stashes it.
            document["_profile"] = payload

        swot_run_id = ""
        canonical_strategy_run = None
        legacy_strategy_seen = False
        if include_runs:
            for kind in _FOLD_ORDER:
                run = self._service.current_successful_run(
                    brand_id=brand.id, kind=kind, user_id=user_id)
                if run is None:
                    continue
                if kind == "swot":
                    # Published so an approving client can say WHICH SWOT it read. Without it, the
                    # only thing an approval can mean is "the brand", and a SWOT that became current
                    # while the human was reading would be approved by a decision never made about
                    # it. The legacy document has no such id, and could not express the difference.
                    swot_run_id = str(run.id)
                if kind == "strategy" and isinstance(run.output, dict):
                    if isinstance(run.output.get("canonical_strategy"), dict):
                        canonical_strategy_run = run
                        # Canonical output is not a legacy patch. It is projected only after an
                        # Approval row binds to this exact Run below.
                        continue
                    legacy_strategy_seen = bool(run.output.get("marketing_strategy"))
                if run.output:
                    document.update(_sanitised(run.output))

        document["domain_key"] = brand.domain_key
        document["owner_id"] = str(brand.owner_id) if brand.owner_id else ""
        if not brand.owner_id:
            # Same disclosure the legacy store makes: an unowned brand is flagged, never presented
            # as owned and never assigned to a guess.
            document["unclaimed"] = True
        document["created_at"] = _epoch(brand.created_at)
        document["updated_at"] = _epoch(brand.updated_at)

        if include_runs:
            document["swot_run_id"] = swot_run_id
            document["swot_approved"] = self._approved(brand_id=brand.id, user_id=user_id)
            document["owner_inputs"] = self._service.owner_inputs(
                brand_id=brand.id, user_id=user_id) or {}
            document["competitors"] = self._service.list_competitors(
                brand_id=brand.id, user_id=user_id) or []
            if canonical_strategy_run is not None:
                canonical = dict(canonical_strategy_run.output.get("canonical_strategy") or {})
                decision_record = self._service.decision_for_current_strategy(
                    brand_id=brand.id, user_id=user_id)
                approval_state = (decision_record.decision if decision_record is not None
                                  else "pending")
                run_id = str(canonical_strategy_run.id)
                document["strategy_run_id"] = run_id
                document["strategy_version_id"] = run_id if approval_state == "approved" else ""
                document["strategy_approval_state"] = approval_state
                document["strategy_authority"] = "postgresql_canonical"
                document["strategy_candidate"] = {
                    "run_id": run_id, "approval_state": approval_state,
                    "eligible_for_approval": bool(
                        (canonical.get("verification") or {}).get("eligible_for_approval")),
                    "objective": dict(
                        canonical_strategy_run.requested_config.get("objective") or {}),
                    **canonical,
                }
                projection_output = dict(canonical_strategy_run.output)
                projection_output["_requested_objective"] = dict(
                    canonical_strategy_run.requested_config.get("objective") or {})
                from strategy.positioning.projection import project_approved_strategy
                projected = project_approved_strategy(
                    projection_output, run_id=run_id, approval_state=approval_state)
                document["has_strategy"] = projected is not None
                document["marketing_strategy"] = projected or {}
            else:
                # A pre-Gate-2/file-shaped result may remain readable, but never carries the
                # canonical label or StrategyVersion identity.
                document["has_strategy"] = legacy_strategy_seen
                document["strategy_run_id"] = ""
                document["strategy_version_id"] = ""
                document["strategy_approval_state"] = "legacy_unversioned" if legacy_strategy_seen else ""
                document["strategy_authority"] = "legacy_compat" if legacy_strategy_seen else ""
                document["strategy_candidate"] = {}
        return document

    def _approved(self, *, brand_id, user_id) -> bool:
        """True only when an Approval row points at the SWOT Run that is current RIGHT NOW.

        This is the whole of §16-§18. There is no stored flag to go stale: generate a newer SWOT and
        this answer flips to False on the next read, because `approval_for_current_swot` resolves the
        current run first and then asks whether anything approved *that* run.
        """
        approval = self._service.approval_for_current_swot(brand_id=brand_id, user_id=user_id)
        return approval is not None and approval.decision == "approved"

    # ------------------------------------------------------------------ many brands
    def documents(self, *, brands, user_id) -> list[dict]:
        """List projection, newest first — the ordering the home grid depends on."""
        out = [self.document(brand=b, user_id=user_id, include_runs=True) for b in brands]
        out.sort(key=lambda b: b.get("updated_at", 0), reverse=True)
        return out
