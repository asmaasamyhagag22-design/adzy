"""Content-calendar brand-safety audit trail (Evidence-Ledger Step 3c, calendar) — hermetic.

Same proof contract as the poster trail: a grounded item resolves each claim to a REAL http
source URL with the cited quote in the SAME language as the copy; a blanked item is recorded
as HANDLED (hook not in the shipped trail, topic present); the artifact states its own scoping.
"""
from __future__ import annotations

from grounding.ledger import EvidenceLedger
from grounding.measure_step3b import _AR
from strategy.audit import build_calendar_audit
from strategy.builder import _PlanItem, _PlanResponse, build_strategy
from business_profile.llm.caller import MockCaller


def _calendar(plan_items):
    plan = _PlanResponse(items=plan_items)
    return build_strategy(_AR, MockCaller({"content_strategy": plan}), days=7,
                          start_date="2026-06-20", ledger=EvidenceLedger.from_profile(_AR))


def test_calendar_audit_scoping_lists_poster_and_calendar_excludes_reel():
    audit = build_calendar_audit(_AR, _calendar([
        _PlanItem(day_offset=0, platform="instagram", content_type="post", topic="مبادرة شبابية")]))
    cov = audit["coverage"]
    assert "imagery" in cov["excluded_surfaces"]   # reel copy GATED 2026-07-12
    assert any("reel" in s for s in cov["covered_surfaces"])
    assert any("calendar" in s for s in cov["covered_surfaces"])
    assert any("poster" in s for s in cov["covered_surfaces"])
    assert audit["asset_type"] == "content_calendar"


def test_grounded_item_claim_resolves_to_real_url_same_language():
    audit = build_calendar_audit(_AR, _calendar([
        _PlanItem(day_offset=0, platform="instagram", content_type="post",
                  topic="مبادرة شبابية", angle="ابدأ رحلتك معنا",
                  hook="الأفضل في التدريب التقني")]))     # grounded 'best' (Arabic)
    item = audit["items"][0]
    claims = [c for f in item["shipped_copy"]["fields"] for c in f["claims"]]
    assert claims and all(c["sourced"] for c in claims)
    best = next(c for c in claims if c["kind"] == "best")
    assert (best["source_url"] or "").startswith("http")     # real URL, not a placeholder
    assert best["source_tier"] == "brand"
    assert best["copy_lang"] == "ar" and best["matched_lang"] == "ar"   # Arabic claim -> Arabic quote
    assert best["lang_mismatch"] is False
    assert item["remediated"] is False


def test_blanked_item_is_recorded_handled_not_in_shipped_copy_topic_runs():
    audit = build_calendar_audit(_AR, _calendar([
        _PlanItem(day_offset=0, platform="tiktok", content_type="reel",
                  topic="تدريب البرمجة", angle="",
                  hook="المعهد الوحيد المعتمد دوليًا")]))    # fabricated only+cert -> blanked
    item = audit["items"][0]
    assert item["remediated"] is True
    hook_rec = next(r for r in item["remediation"] if r["field"] == "hook")
    assert hook_rec["original_text"] == "المعهد الوحيد المعتمد دوليًا"   # not hidden
    assert hook_rec["action"] == "blanked" and hook_rec["unsourced_claims"]
    # The fabricated hook is NOT in the shipped-copy trail; the sourced topic IS.
    texts = " ".join(f["text"] for f in item["shipped_copy"]["fields"])
    assert "الوحيد" not in texts and "المعتمد" not in texts
    assert "تدريب البرمجة" in texts


def test_cross_language_claim_is_flagged_in_calendar_trail():
    # Only ENGLISH evidence for a number -> an Arabic hook still resolves but is LABELED.
    prof = {
        "name": {"value": "X", "evidence": [{"page_url": "https://b/", "quote": "X"}]},
        "languages": ["ar"],
        "description": {"value": "we train youth up to 32 years old",
                        "evidence": [{"page_url": "https://b/en", "quote": "up to 32 years old"}]},
    }
    plan = _PlanResponse(items=[_PlanItem(day_offset=0, platform="instagram",
                                          content_type="post", topic="برنامج", hook="ندعمك حتى 32 عاما")])
    cal = build_strategy(prof, MockCaller({"content_strategy": plan}), days=7,
                         start_date="2026-06-20", ledger=EvidenceLedger.from_profile(prof))
    audit = build_calendar_audit(prof, cal)
    claims = [c for f in audit["items"][0]["shipped_copy"]["fields"] for c in f["claims"]]
    num = next(c for c in claims if c["kind"] == "number")
    assert num["sourced"] is True
    assert num["copy_lang"] == "ar" and num["matched_lang"] == "en" and num["lang_mismatch"] is True


def test_calendar_audit_includes_surviving_factual_cta():
    cal = _calendar([
        _PlanItem(day_offset=0, platform="instagram", content_type="post",
                  topic="مبادرة شبابية", cta="استفد من خبرة 30 عامًا"),
    ])
    audit = build_calendar_audit(_AR, cal)
    fields = audit["items"][0]["shipped_copy"]["fields"]
    cta = next(f for f in fields if f["field"] == "cta")
    assert cta["claims"] and all(claim["sourced"] for claim in cta["claims"])
