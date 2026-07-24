"""P-02 / P-03 / P-06 (D-8): the dashboard must not show Egypt-specific content to a non-Egyptian
brand — Egyptian marketing seasons, "كل مصر" (all of Egypt), or an "EGP" currency default."""
from __future__ import annotations

from dashboard.build import _seasons_ok_for, _calendar_row, _market_pulse


def _prof(url):
    return {"source_url": {"value": url}}


# ---- P-02: season calendar is Egypt-only ----
def test_seasons_shown_only_for_egypt():
    assert _seasons_ok_for(_prof("https://brand.com.eg/")) is True
    assert _seasons_ok_for(_prof("https://brand.sa/")) is False        # Saudi
    assert _seasons_ok_for(_prof("https://brand.qa/")) is False        # Qatar
    assert _seasons_ok_for({}) is False


def test_calendar_row_omits_season_tag_for_non_egypt():
    it = {"platform": "instagram", "content_type": "reel", "topic": "x",
          "date": "2026-06-20"}  # inside an Egyptian summer window
    egypt = _calendar_row(it, season_ok=True)
    gulf = _calendar_row(it, season_ok=False)
    assert "cal-season" in egypt        # Egyptian brand may see the season tag
    assert "cal-season" not in gulf     # Gulf brand must not


def test_market_pulse_no_egyptian_season_for_gulf_brand():
    comp = {"generated_at": "2026-06-20T00:00:00", "profile": _prof("https://brand.qa/"),
            "trends": [{"title": "coffee trends 2026", "url": "https://x.com/a"}]}
    html = _market_pulse(comp)
    assert "الموسم القادم" not in html   # no "upcoming (Egyptian) season" block
    assert "coffee trends 2026" in html  # but live trend chips still show


# ---- P-03: NATIONAL is not "all of Egypt" ----
def test_national_geo_is_country_neutral():
    from dashboard.build import _explained_plan
    mp = {"objectives": [{"objective": "OUTCOME_LEADS", "destination": "phone_call",
                          "kpi_target": {"metric": "cost_per_lead"}}],
          "base_geo": {"mode": "national"}}
    html = _explained_plan(mp, "restaurant")
    assert "كل مصر" not in html
    assert "على المستوى الوطني" in html or "nationwide" in html


# ---- P-06: no EGP currency default ----
def test_default_kpi_unit_is_neutral_not_egp():
    from media_plan.config import default_kpi
    from media_plan.schemas import MetaObjective
    assert default_kpi(MetaObjective.LEADS).unit == ""
