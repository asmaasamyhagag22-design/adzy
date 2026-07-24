# -*- coding: utf-8 -*-
"""Client-deliverable rubric I/B — the media plan as a NARRATED plan a non-marketer reads
and can explain back (owner: "cards I don't even understand"). Hermetic.

Every number is evidence-linked or honestly marked; no fabricated targets, ever."""
from __future__ import annotations

from dashboard.build import _explained_plan


def _mp(**obj_over):
    obj = {"objective": "OUTCOME_SALES", "destination": "online_store", "funnel_stage": "bofu",
           "confidence": "high", "rationale": "You have a real checkout.",
           "kpi_target": {"metric": "cost_per_purchase", "target_value": None,
                          "unit": "EGP", "window_days": 7},
           "evidence": [{"claim": "checkout exists",
                         "evidence": [{"quote": "Sale price LE 440.00", "page_url": "u"}],
                         "resolved": True}]}
    obj.update(obj_over)
    return {"objectives": [obj],
            "base_geo": {"mode": "national"},
            "base_persona": {"interests": [{"claim": "the brand's audience: B2C",
                                            "evidence": [{"quote": "q"}]},
                                           {"claim": "beauty shoppers"}]}}


def test_plan_reads_as_plain_language_not_enums():
    html = _explained_plan(_mp(), "ecommerce")
    assert "خطة الإعلان — مشروحة · Your ad plan, explained" in html
    # objective as a sentence, not the enum
    assert "بيع منتجاتك أونلاين" in html and "sell your products online" in html
    assert "OUTCOME_SALES" not in html                 # the raw enum never leaks
    # the evidence RECEIPT is shown inline (the moat's proof, not a bare tick)
    assert "Sale price LE 440.00" in html and "الدليل من موقعك" in html
    # confidence surfaced — but labelled as the MODEL's self-estimate, not a measured probability (P-28)
    assert "تقدير الموديل" in html and "ثقة عالية" in html
    assert "model self-estimate" in html


def test_budget_split_surfaces_channel_weights():
    html = _explained_plan(_mp(), "ecommerce")
    # ecommerce weights (config): IG 40 / FB 30 / TikTok 20 / YT 10 — shown as human bars
    assert "إنستجرام · Instagram" in html and "40%" in html
    assert "فيسبوك · Facebook" in html and "30%" in html
    assert "توزيع الميزانية على المنصّات" in html


def test_kpi_is_honest_never_fabricated():
    html = _explained_plan(_mp(), "ecommerce")
    assert "تكلفة البيعة الواحدة · Cost per sale" in html       # metric explained, not snake_case
    assert "cost_per_purchase" not in html
    assert "يُعاير بعد أول أسبوعين تشغيل" in html               # honest calibrate-later line
    assert "never invent a target" in html
    # a REAL target (if one existed) would show verbatim; a null one never fabricates
    html2 = _explained_plan(_mp(kpi_target={"metric": "cost_per_purchase", "target_value": 55,
                                            "unit": "EGP", "window_days": 7}), "ecommerce")
    assert "55 EGP" in html2


def test_audience_sentences_tagged_evidenced_or_assumption():
    html = _explained_plan(_mp(), "ecommerce")
    assert "B2C" in html and "بالأدلة · evidenced" in html      # grounded axis
    assert "beauty shoppers" in html and "افتراض · assumption" in html  # ungrounded axis


def test_plan_has_killswitch_and_next_steps_and_tooltips():
    html = _explained_plan(_mp(), "ecommerce")
    assert "متى توقف إعلاناً · When to stop an ad" in html      # kill-switch in plain words
    assert "٣ نتائج على الأقل" in html                          # the learning floor, humanised
    assert "الخطوات التالية · What happens next" in html
    assert html.count("<li>") >= 3                              # 3 numbered next steps + audience
    assert 'class="tip"' in html                                # (?) education tooltips


def test_empty_plan_renders_nothing():
    assert _explained_plan({}, "ecommerce") == ""
    assert _explained_plan({"objectives": [{}]}, "ecommerce") == ""
