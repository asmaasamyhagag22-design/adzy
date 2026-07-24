# -*- coding: utf-8 -*-
"""Per-brand PRINT PACK (dashboard/print_pack.py) — a printable folder per website.

Owner 2026-07-18: a folder named after the site with a printable page per artifact
(profile/SWOT/plan/calendar/…). Hermetic: builds from on-disk JSON fixtures, zero spend.
"""
from __future__ import annotations

import json


def _fixture(tmp_path):
    result = {
        "subject_url": "https://acme.example/", "subject_category": "education",
        "generated_at": "2026-07-18T00:00:00+00:00", "competitor_count": 1,
        "competitors": [{"selection": {"name": "Peer", "website": "https://p/",
                                        "peer_fit_score": 0.5, "why_selected": "rank 1"},
                         "candidate": {"name": "Peer"}}],
        "swot": {"mode": "competitive",
                 "strengths": [{"text": "Cruelty-free formulas", "citation": ["your profile"]}],
                 "weaknesses": [{"text": "Thin blog", "citation": ["your profile"]}],
                 "opportunities": [], "threats": []},
        "tows": {"posture": "leverage",
                 "strategies": [{"type": "SO", "title": "Lead with ethics",
                                 "description": "press the cruelty-free advantage"}],
                 "priority_actions": [{"rank": 1, "action": "Fix: X", "horizon": "now"}]},
        "profile": {"name": {"value": "Acme"}, "category": {"value": "education"},
                    "description": {"value": "We teach."},
                    "offerings": [{"name": "Course A"}, {"name": "Course B"}],
                    "value_propositions": [{"value": "Hands-on learning"}],
                    "trust_signals": [{"value": "10k students"}]},
    }
    plan = {"business_name": "Acme", "days": 14,
            "items": [{"date": "2026-07-05", "platform": "instagram", "content_type": "reel",
                       "topic": "Launch week", "hook": "See it live"}]}
    media_plan = {"objectives": [{"objective": "OUTCOME_LEADS", "destination": "lead_form",
                                  "funnel_stage": "bofu", "rationale": "r",
                                  "kpi_target": {"metric": "cost_per_lead"}}],
                  "base_geo": {"mode": "national"}}
    (tmp_path / "acme_result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "acme_profile.json").write_text(json.dumps(result["profile"], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "acme_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "acme_media_plan.json").write_text(json.dumps(media_plan, ensure_ascii=False), encoding="utf-8")


def test_print_pack_writes_a_folder_named_after_the_brand(tmp_path):
    from dashboard.print_pack import build_print_pack
    _fixture(tmp_path)
    dest = build_print_pack("acme", out_dir=str(tmp_path), reports_root=str(tmp_path / "reports"))
    assert dest.name == "acme" and dest.is_dir()
    names = {p.name for p in dest.glob("*.html")}
    # index + the artifacts that have data
    assert "index.html" in names
    assert "01_business_profile.html" in names
    assert "02_swot_tows.html" in names
    assert "04_media_plan.html" in names
    assert "05_calendar.html" in names


def test_each_page_is_printable_and_rtl(tmp_path):
    from dashboard.print_pack import build_print_pack
    _fixture(tmp_path)
    dest = build_print_pack("acme", out_dir=str(tmp_path), reports_root=str(tmp_path / "reports"))
    for p in dest.glob("*.html"):
        h = p.read_text(encoding="utf-8")
        assert "@media print" in h, f"{p.name} has no print stylesheet"
        assert 'dir="rtl"' in h, f"{p.name} is not RTL"
        assert "window.print()" in h, f"{p.name} has no print button"


def test_pages_carry_the_real_artifact_data_not_empty_shells(tmp_path):
    from dashboard.print_pack import build_print_pack
    _fixture(tmp_path)
    dest = build_print_pack("acme", out_dir=str(tmp_path), reports_root=str(tmp_path / "reports"))
    swot = (dest / "02_swot_tows.html").read_text(encoding="utf-8")
    assert "Cruelty-free formulas" in swot and "Lead with ethics" in swot   # SWOT + TOWS real content
    cal = (dest / "05_calendar.html").read_text(encoding="utf-8")
    assert "Launch week" in cal                                             # calendar row real content
    mp = (dest / "04_media_plan.html").read_text(encoding="utf-8")
    assert "خطة الإعلان" in mp


def test_absent_artifact_is_skipped_not_fabricated(tmp_path):
    """No trends data on disk -> NO trends page (honest absence, not an empty shell)."""
    from dashboard.print_pack import build_print_pack
    _fixture(tmp_path)   # fixture has no "trends" key
    dest = build_print_pack("acme", out_dir=str(tmp_path), reports_root=str(tmp_path / "reports"))
    assert not (dest / "03_trends.html").exists()


def test_missing_analysis_raises(tmp_path):
    from dashboard.print_pack import build_print_pack
    import pytest
    with pytest.raises(FileNotFoundError):
        build_print_pack("nobody", out_dir=str(tmp_path), reports_root=str(tmp_path / "reports"))
