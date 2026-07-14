# -*- coding: utf-8 -*-
"""Client-deliverable rubric A — the executive summary / first-screen story. Hermetic.

In 10 seconds the owner must see WHO the brand is, its MARKET POSITION, and THE one
recommended move — composed purely from already-computed intelligence (SWOT posture,
priority actions, media-plan objective). No fabrication."""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.build import _executive_summary, build_dashboard_html


def test_executive_summary_tells_who_position_and_the_one_move():
    prof = {"name": {"value": "NTI"}, "category": {"value": "education"},
            "description": {"value": "The National Telecommunication Institute offers "
                            "professional training. It has many programs."}}
    swot = {"strengths": [{"text": "Workforce-ready graduates"}, {"text": "Accredited"}],
            "weaknesses": [{"text": "Few CTAs"}]}
    tows = {"posture": "leverage",
            "priority_actions": [{"rank": 1, "action": "Leverage: Bridge the talent gap",
                                  "horizon": "now"}]}
    mp_obj = {"objective": "OUTCOME_LEADS"}
    html = _executive_summary("NTI", "education", prof, swot, tows, 4, mp_obj)
    # WHO
    assert "الخلاصة التنفيذية · Executive summary" in html
    assert "NTI" in html and "education" in html
    assert "professional training" in html and "It has many programs" not in html  # one sentence
    # MARKET POSITION (posture in plain words, both languages)
    assert "وضعك التنافسي قوي" in html and "Strong position" in html
    assert "2</b> نقطة قوة" in html and "4</b> منافس" in html
    # THE ONE MOVE (prefix stripped, horizon + objective)
    assert "الخطوة المُوصى بها الآن" in html
    assert "Bridge the talent gap" in html and "Leverage:" not in html
    assert "ابدأ الآن" in html and "عملاء محتملون" in html  # horizon + objective


def test_recommended_move_is_an_imperative_not_a_bare_description():
    # Regression (owner-flagged): the "recommended first move" must read as an INSTRUCTION, never a
    # raw strength/weakness label (the stored action is "{Type}: {swot text}", often truncated …).
    tows = {"posture": "leverage", "priority_actions": [{
        "rank": 1, "horizon": "now",
        "action": "Leverage: A one-stop digital store for essential, sector-specific bus…",
        "rationale": "Use your strength — A one-stop digital store for essential, sector-specific "
                     "business applications — to pursue the opportunity: online booking is absent."}]}
    html = _executive_summary("Vodafone", "telecom", {"name": {"value": "Vodafone"}}, {}, tows, 4, {})
    assert "وظّف نقطة قوتك:" in html                     # reframed as a verb-led instruction
    assert "business applications" in html               # full body recovered from rationale
    assert "bus…" not in html and "Leverage:" not in html   # no truncation, no bare label
    # a "Fix:" action reads as an instruction to act, not the bare problem statement
    tows2 = {"posture": "fix", "priority_actions": [
        {"rank": 1, "horizon": "now", "action": "Fix: No online booking path on-site"}]}
    h2 = _executive_summary("X", "", {}, {}, tows2, 0, {})
    assert "عالِج: No online booking path on-site" in h2


def test_executive_summary_degrades_without_actions_or_posture():
    html = _executive_summary("X", "", {}, {}, {}, 0, {})
    assert "الخلاصة التنفيذية" in html and "X" in html
    assert "الخطوة المُوصى بها" not in html          # no action -> no move block, no fabrication
    assert "0</b> منافس" in html


def test_exec_summary_is_first_content_block_in_the_page(tmp_path):
    competitor = {"subject_url": "https://x/", "subject_category": "education",
                  "competitor_count": 4, "competitors": [],
                  "swot": {"mode": "competitive", "strengths": [{"text": "S1"}],
                           "weaknesses": [], "opportunities": [], "threats": []},
                  "tows": {"posture": "leverage",
                           "priority_actions": [{"rank": 1, "action": "Fix: do X", "horizon": "now"}]},
                  "profile": {"name": {"value": "Acme"}, "description": {"value": "We do X."}}}
    cp = tmp_path / "acme_result.json"
    cp.write_text(json.dumps(competitor, ensure_ascii=False), encoding="utf-8")
    html = build_dashboard_html(str(cp))
    # the executive summary appears before the SWOT section header (first-screen story)
    assert html.index("Executive summary") < html.index("Strengths, weaknesses")
    # the exec-summary MOVE block strips the "Fix:" prefix (the raw action still shows later
    # in the priority-actions section — that's expected, so scope the check to the exec block)
    ex_block = html[html.index("Executive summary"):html.index("Strengths, weaknesses")]
    assert "do X" in ex_block and "Fix:" not in ex_block
