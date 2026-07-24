# -*- coding: utf-8 -*-
"""A REAL dashboard, not a long scroll (owner 2026-07-18: "اليوز مش هيفضل ينزل لتحته، ديزاين
داشبورد حقيقي، وظبط السنترة").

The "start here" arc used to be non-clickable decoration and the ~16 sections had no anchors, so
the only way through was to scroll the whole 1180px column. This turns the arc into a STICKY jump
nav (CSS-only — the standalone export forbids <script>) and gives each section-group an id to jump
to. Centering: the live studio must render RTL like the export (it was LTR -> mirror-flipped).
"""
from __future__ import annotations

import json


def _full(tmp_path):
    competitor = {
        "subject_url": "https://x/", "subject_category": "education", "competitor_count": 1,
        "competitors": [{"selection": {"name": "Peer", "website": "https://p/",
                                       "peer_fit_score": 0.5, "why_selected": "rank 1"},
                         "candidate": {"name": "Peer"}}],
        "swot": {"mode": "competitive", "strengths": [{"text": "S1", "citation": ["your profile"]}],
                 "weaknesses": [], "opportunities": [], "threats": []},
        "tows": {"posture": "leverage", "strategies": [{"type": "SO", "title": "Do the thing"}],
                 "priority_actions": [{"rank": 1, "action": "Fix: X", "horizon": "now"}]},
        "profile": {"name": {"value": "Acme"}, "description": {"value": "We teach."}},
    }
    plan = {"business_name": "Acme", "days": 14,
            "items": [{"date": "2026-07-05", "platform": "instagram", "content_type": "reel",
                       "topic": "T", "hook": "H"}]}
    cp = tmp_path / "acme_result.json"
    cp.write_text(json.dumps(competitor, ensure_ascii=False), encoding="utf-8")
    pp = tmp_path / "p.json"
    pp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    from dashboard.build import build_dashboard_html
    return build_dashboard_html(str(cp), plan_path=str(pp))


def test_reading_arc_is_clickable_jump_nav():
    """The arc steps must be real anchor links into the page, not inert <span>s."""
    from dashboard.build import build_dashboard_html
    import json as _j
    # minimal render
    import tempfile, os
    d = tempfile.mkdtemp()
    cp = os.path.join(d, "acme_result.json")
    with open(cp, "w", encoding="utf-8") as f:
        _j.dump({"subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
                 "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                          "opportunities": [], "threats": []}, "tows": {}}, f)
    html = build_dashboard_html(cp)
    # each arc step is now an <a href="#..."> (was a <span>)
    assert 'class="arc-step" href="#' in html or 'href="#' in html and "arc-step" in html
    assert html.count('href="#sec-') >= 5      # at least the main jump targets


def test_sections_have_anchor_ids():
    """The jump nav needs targets — the section groups must carry ids."""
    import tempfile, os, json as _j
    d = tempfile.mkdtemp()
    cp = os.path.join(d, "acme_result.json")
    with open(cp, "w", encoding="utf-8") as f:
        _j.dump({"subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
                 "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                          "opportunities": [], "threats": []}, "tows": {}}, f)
    from dashboard.build import build_dashboard_html
    html = build_dashboard_html(cp)
    for anchor in ("sec-market", "sec-strategy", "sec-plan", "sec-creative", "sec-calendar",
                   "sec-gaps"):
        assert f'id="{anchor}"' in html, f"missing anchor {anchor}"


def test_nav_is_sticky_so_the_user_does_not_lose_it_while_scrolling():
    import tempfile, os, json as _j
    d = tempfile.mkdtemp()
    cp = os.path.join(d, "acme_result.json")
    with open(cp, "w", encoding="utf-8") as f:
        _j.dump({"subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
                 "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                          "opportunities": [], "threats": []}, "tows": {}}, f)
    from dashboard.build import build_dashboard_html
    html = build_dashboard_html(cp)
    assert "position:sticky" in html          # the arc/nav sticks to the top
    assert "scroll-margin-top" in html        # anchored headings aren't hidden under the sticky bar


def test_standalone_stays_script_free_after_the_redesign(tmp_path):
    """The jump nav is CSS-only. The export must remain <script>-free (test_dashboard.py:76)."""
    html = _full(tmp_path)
    from dashboard.build import build_dashboard
    out = build_dashboard(str(tmp_path / "acme_result.json"),
                          plan_path=str(tmp_path / "p.json"),
                          out_path=str(tmp_path / "d.html"))
    from pathlib import Path
    h = Path(out).read_text(encoding="utf-8")
    assert "<script" not in h.lower()


def test_studio_page_renders_rtl_like_the_export():
    """CENTERING FIX: the live /studio page rendered LTR (<html lang=en>) while the download is
    RTL — so what the owner saw was mirror-flipped from the exported file. The studio must also be
    dir=rtl so the two match."""
    import dashboard.server as srv
    fn = getattr(srv, "_studio_page", None)
    assert fn is not None
    import inspect
    src = inspect.getsource(fn)
    assert 'dir="rtl"' in src, "studio page must emit dir=rtl to match the RTL export"
