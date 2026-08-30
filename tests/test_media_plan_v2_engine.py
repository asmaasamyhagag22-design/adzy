# -*- coding: utf-8 -*-
"""Media Plan v2 assembler (2026-07-18) — §1 marketing-intel input · §2 strategic narrative ·
§3 test cells · §5 reality check · §6 guardrails · §7 human-input seam. Deterministic; the evidence-
bound marketing intel (already past the moat) is the only model-derived input. No LLM here, no spend."""
from __future__ import annotations

from datetime import datetime, timezone

from business_profile.llm.caller import MockCaller
from marketing_intel import extract_marketing_intel
from marketing_intel.extractor import _RawItem, _RawResponse
from media_plan_v2 import build_media_plan_v2
from social_intel.schemas import SocialSignal

_NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _sig(kind, url, text, own=True):
    return SocialSignal(source="snapshot", source_url=url, signal_kind=kind,
                        author_hash="hash1234abcd", text=text, is_own_brand=own, scraped_at=_NOW)


def _marketing_intel():
    snap = [_sig("caption", "https://ig/p/AAA", "خصم 20% على كل الجلسات هذا الأسبوع")]
    caller = MockCaller({"ig_marketing_intel": _RawResponse(items=[
        _RawItem(kind="offer", text="20% off all sessions this week",
                 quote="خصم 20% على كل الجلسات", permalink="https://ig/p/AAA")])})
    return extract_marketing_intel(snap, caller, brand_ref="clinic_x")


_PROFILE = {"source_url": "https://clinic.sa/", "name": {"value": "Baseera Clinic"},
            "category": {"value": "clinic"}, "audience_type": {"value": "نساء 25-45 في الرياض"},
            "offerings": [{"name": {"value": "Laser"}, "price_text": {"value": "800 SAR"}}]}

_SWOT = {"strengths": [{"text": "أطباء جلدية معتمدون"}],
         "opportunities": [{"text": "لا منافس يقدم استشارة مجانية"}]}


def test_v2_assembles_all_sections_from_a_closed_universe():
    mi = _marketing_intel()
    plan = build_media_plan_v2(_PROFILE, swot=_SWOT, marketing_intel=mi, capacity=12,
                               objective="leads", monthly_budget=30000)
    # §1 marketing-intel input flows in (evidence-bound offer)
    assert any("20% off" in o for o in plan.offers)
    # §2 narrative pairs the client strength with the market gap
    assert "أطباء جلدية معتمدون" in plan.strategic_narrative
    assert "لا منافس يقدم استشارة مجانية" in plan.strategic_narrative
    # §3 exactly three cells, budget shares sum to 1.0, the offer cell cites the real offer
    assert len(plan.test_cells) == 3
    assert abs(sum(c.budget_share for c in plan.test_cells) - 1.0) < 1e-9
    axes = {c.axis for c in plan.test_cells}
    assert axes == {"audience", "offer", "competitor"}
    offer_cell = next(c for c in plan.test_cells if c.axis == "offer")
    assert "20% off" in offer_cell.hypothesis and "verbatim" in offer_cell.grounded_in
    # §4 every ad set carries a creative brief (funnel + angle + CTA)
    assert all(c.funnel_stage in ("tofu", "mofu", "bofu") and c.angle and c.cta for c in plan.test_cells)
    assert offer_cell.funnel_stage == "bofu"                   # the offer cell drives action
    # §5 reality check is present + grounded (price path)
    assert plan.reality_check.kill_switch.basis == "price_margin"
    # §6 guardrails name the closed universe + the anti-fluff moat
    assert any("كون مغلق" in g for g in plan.guardrails)
    assert any("موات" in g or "بدليل حرفي" in g for g in plan.guardrails)
    # §7 all human inputs supplied -> no open asks
    assert plan.open_inputs == []


def test_v2_asks_for_missing_human_numbers_instead_of_guessing():
    plan = build_media_plan_v2(_PROFILE, swot=_SWOT, marketing_intel=None)  # nothing human supplied
    assert plan.reality_check.capacity_source == "config_default"
    joined = " ".join(plan.open_inputs)
    assert "طاقة الاستيعاب" in joined and "هدف الحملة" in joined and "الميزانية" in joined
    # with no marketing intel, the offer cell degrades honestly (no invented offer)
    offer_cell = next(c for c in plan.test_cells if c.axis == "offer")
    assert "لا يوجد عرض" in offer_cell.hypothesis


def test_v2_no_strength_evidence_narrative_is_honest_not_invented():
    plan = build_media_plan_v2(_PROFILE, swot=None, marketing_intel=None, capacity=10)
    assert "لا توجد بعد ميزة تنافسية مؤكَّدة" in plan.strategic_narrative
