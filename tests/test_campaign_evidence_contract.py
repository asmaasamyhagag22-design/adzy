from datetime import datetime, timezone

import pytest

from strategy.campaign_package import check, digest
from strategy.campaign_planner import PlanInput, build_plan, refresh_plan, review_draft
from strategy.campaign_research import Page
from strategy.campaign_v2 import CampaignConflict
from workflows.campaign_context_eval import evaluate_context
from workflows.campaign_eval import review_input


def test_frozen_source_context_changes_complete_reviewed_packages():
    result = evaluate_context(review_input)
    assert result["blockers"] == []
    assert sum(row["journey"] == "READY" for row in result["results"]) == 7


def test_review_cannot_turn_source_conditions_into_unqualified_marketing():
    url = "https://example.org/trackers"
    clock = lambda: datetime(2026, 11, 14, tzinfo=timezone.utc)
    page = Page(url, b"<html><body><h1>Known Trackers</h1>"
                      b"<p>The browser blocks thousands of known trackers while you browse.</p>"
                      b"<p>This feature requires a paid subscription.</p></body></html>")
    request = PlanInput(url=url, objective="Increase visits to the Known Trackers explanation", language="ar")
    draft = build_plan(request, "Publisher", lambda *_: page, clock)
    assert review_draft(draft, review_input(draft["brief"]))["status"] == "READY"
    edit = {"field": "message", "text": "يحجب المتصفّح آلاف المتعقّبات المعروفة أثناء التصفّح."}
    with pytest.raises(CampaignConflict, match="COPY_REQUIRES_EVIDENCE_REVIEW"):
        review_draft(draft, review_input(draft["brief"], edit))

    old = {**draft, "evidence": [{key: value for key, value in draft["evidence"][0].items() if key != "anchor"}]}
    old = {**old, "sha256": digest({key: value for key, value in old.items() if key != "sha256"})}
    assert check(old, clock()) == "SOURCE_RECHECK_REQUIRED"
    assert refresh_plan(old, lambda *_: page, clock)["evidence"][0]["anchor"]["qualifications"]


@pytest.mark.parametrize("adjacent", [
    "<h2>Requirements</h2><p>This feature requires a paid subscription.</p>",
    "<aside><p>This feature requires a paid subscription.</p></aside>",
])
def test_adjacent_requirement_under_heading_or_aside_is_retained(adjacent):
    result = reviewed("Known Trackers", "The browser blocks thousands of known trackers while you browse.", adjacent)
    assert result["package"]["factual_review"] == "QUALIFIED_SOURCE_GUIDE"
    assert result["evidence"][0]["anchor"]["qualifications"][0]["quote"] == (
        "This feature requires a paid subscription."
    )


def test_disclaimer_with_pronoun_blocks_conflicting_claim():
    result = reviewed("Studio Audio Monitor", "The Studio Audio Monitor streams live mixes to a paired device.",
                      "<p>This feature does not support live streaming.</p>")
    assert result["status"] == "BLOCKED"
    assert result["planner"]["gap"] == "SOURCE_CONFLICT"


def test_pronoun_after_another_named_subject_does_not_license_copy():
    result = reviewed("Trail Day Pass", "The annual Tourist Pass is sold at the entrance.",
                      "<p>It includes access to trails outside the signed woodland area.</p>")
    assert result["status"] == "BLOCKED"


def reviewed(topic, quote, following_markup):
    url = "https://example.org/topic"
    html = f"<html><main><h1>{topic}</h1><p>{quote}</p>{following_markup}</main></html>"
    request = PlanInput(url=url, objective=f"Increase visits to the {topic} explanation", language="ar")
    draft = build_plan(request, "Publisher", lambda *_: Page(url, html.encode()),
                       lambda: datetime(2026, 11, 14, tzinfo=timezone.utc))
    return review_draft(draft, review_input(draft["brief"])) if draft["brief"] else draft
