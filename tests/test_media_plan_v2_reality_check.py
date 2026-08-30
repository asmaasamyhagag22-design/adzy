# -*- coding: utf-8 -*-
"""Media Plan v2 · §5 Reality Check + Kill-Switch (2026-07-18) — the owner's exact spec.
Capacity = a human number OR the vertical default; Kill-Switch max-CPL = scraped price × profit
margin × 30% (or a DISCLOSED default when no price — never a silent invented number); scale/fatigue
= the decision_engine's hardcoded industry rules, NOT an LLM guess. Fully deterministic, no spend."""
from __future__ import annotations

from media_plan.config import capacity_default, kill_switch_cpl, profit_margin
from media_plan_v2 import build_reality_check


def test_kill_switch_cpl_is_price_times_margin_times_30pct():
    # a clinic: margin 0.60, so a 800-priced session -> 800 * 0.60 * 0.30 = 144.0
    cpl, basis = kill_switch_cpl(800.0, "clinic")
    assert basis == "price_margin" and cpl == round(800 * profit_margin("clinic") * 0.30, 2) == 144.0
    # no price -> the disclosed default, never a silent number
    cpl2, basis2 = kill_switch_cpl(None, "clinic")
    assert basis2 == "default_kpi" and cpl2 == 150.0


def test_reality_check_grounded_price_path():
    prof = {"source_url": "https://clinic.sa/", "category": {"value": "clinic"},
            "offerings": [{"name": {"value": "Laser session"}, "price_text": {"value": "800 SAR"}}]}
    rc = build_reality_check(prof, capacity=12)         # human capacity number
    assert rc.max_daily_leads == 12 and rc.capacity_source == "human"
    assert rc.kill_switch.basis == "price_margin" and rc.kill_switch.max_cpl == 144.0
    assert rc.kill_switch.currency == "ريال" and rc.kill_switch.disclosure == ""
    assert rc.daily_spend_ceiling == round(12 * 144.0, 2)
    # scale/fatigue come from the decision engine's industry rules (not guessed)
    assert "20%" in rc.scale_rule and "3.5" in rc.fatigue_rule and "25%" in rc.fatigue_rule


def test_scraped_price_handles_thousands_dot_and_uses_the_median():
    # MEASURED on NTI (2026-07-19, caught on the War Room vault): the first priced offering was
    # "الحد الأقصى للتمويل 65.000 جنية." — the Arabic thousands-DOT read as a decimal (65.0), and a
    # financing CAP (not a service price) drove the kill-switch to a nonsense 9.75 EGP. The parser
    # must read 65.000 as 65000 and take the MEDIAN across offerings, not the first blind number.
    from media_plan_v2.reality_check import _parse_price_number, _scraped_price
    assert _parse_price_number("الحد الأقصى للتمويل 65.000 جنية.") == 65000.0
    assert _parse_price_number("رسوم التسجيل: 1500 جنيه مصري") == 1500.0
    assert _parse_price_number("1,500 EGP") == 1500.0
    assert _parse_price_number("from EGP 800") == 800.0
    assert _parse_price_number("799.99 SAR") == 799.99          # a real decimal stays a decimal
    prof = {"offerings": [
        {"price_text": {"value": "الحد الأقصى للتمويل 65.000 جنية."}},   # outlier cap
        {"price_text": {"value": "رسوم التسجيل: 1500 جنيه مصري"}},
        {"price_text": {"value": "رسوم التسجيل: 1000 جنيه مصري"}},
    ]}
    assert _scraped_price(prof) == 1500.0                       # median, not the 65k outlier


def test_reality_check_no_price_discloses_the_default_and_uses_vertical_capacity():
    prof = {"source_url": "https://clinic.eg/", "category": {"value": "clinic"},
            "offerings": [{"name": {"value": "Consultation"}}]}   # no price_text
    rc = build_reality_check(prof)                       # no human capacity -> vertical default
    assert rc.max_daily_leads == capacity_default("clinic") and rc.capacity_source == "config_default"
    assert rc.kill_switch.basis == "default_kpi" and rc.kill_switch.max_cpl == 150.0
    # the owner's EXACT disclosure wording — the default is stated, never silent
    assert "افتراضي لعدم توفر الأسعار" in rc.kill_switch.disclosure
    assert "جنيه" in rc.kill_switch.disclosure           # Egypt currency word
    assert any("افتراضية للمجال" in n for n in rc.notes)  # capacity assumption surfaced too
