# -*- coding: utf-8 -*-
"""Round-2 A — Market Pulse on the dashboard: live trends + the Egyptian season window +
per-calendar-item season attribution. Hermetic."""
from __future__ import annotations

import json

from dashboard.build import _market_pulse, build_dashboard_html


def test_market_pulse_shows_trends_and_season():
    # The season calendar is Egypt-only (P-02), so this test represents an EGYPTIAN brand; the
    # non-Egypt case (seasons hidden) is covered in test_dashboard_d8.py.
    comp = {"generated_at": "2026-07-12T00:00:00+00:00",
            "profile": {"source_url": {"value": "https://brand.com.eg/"}},
            "trends": [{"title": "AI upskilling surges in MENA", "url": "https://x/1", "source": "hn"},
                       {"title": "Fiber rollout accelerates", "url": "https://x/2", "source": "reddit"}]}
    html = _market_pulse(comp, "2026-07-12")
    assert "نبض السوق · Market pulse" in html
    assert "AI upskilling surges in MENA" in html and "https://x/1" in html
    # July -> summer season active
    assert "موسم الصيف" in html and ("active now" in html or "مستمر الآن" in html)


def test_market_pulse_season_only_when_no_trends():
    # Egyptian brand (season calendar is Egypt-only, P-02).
    html = _market_pulse({"trends": [], "profile": {"source_url": {"value": "https://x.eg/"}}},
                         "2026-05-01")   # May -> Eid al-Adha upcoming
    assert "عيد الأضحى" in html and "in " in html         # days-until framing
    assert "approx" in html                               # Hijri date honestly flagged


def test_no_pulse_when_nothing():
    # past the calendar window (nothing upcoming) + no trends -> no section
    assert _market_pulse({"trends": []}, "2027-05-01") == ""


def test_calendar_item_gets_season_attribution(tmp_path):
    competitor = {"subject_url": "https://x.eg/", "competitor_count": 0, "competitors": [],
                  "profile": {"source_url": {"value": "https://x.eg/"}},  # Egyptian brand (P-02)
                  "swot": {"mode": "standalone", "strengths": [], "weaknesses": [],
                           "opportunities": [], "threats": []}, "tows": {"strategies": []}}
    plan = {"items": [{"date": "2026-09-05", "platform": "instagram", "content_type": "post",
                       "topic": "New term promo", "hook": "H"}]}   # back-to-school window
    cp = tmp_path / "x_result.json"
    cp.write_text(json.dumps(competitor, ensure_ascii=False), encoding="utf-8")
    pp = tmp_path / "p.json"
    pp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    html = build_dashboard_html(str(cp), plan_path=str(pp))
    assert "قرب العودة للمدارس · near Back to school" in html
