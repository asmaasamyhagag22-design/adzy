"""P-18: the content calendar must hard-stop on an unidentified brand and never ship a template
placeholder. marasim shipped a calendar named "The brand" with hooks like "See how [Customer Name]
transformed…" — this pins that both are now blocked.
"""
from __future__ import annotations

from strategy.builder import build_strategy, _name_is_placeholder, _PLACEHOLDER_RE


def test_fallback_name_is_detected():
    for n in ("The brand", "the brand", "Unknown Business", "", "N/A"):
        assert _name_is_placeholder(n) is True
    assert _name_is_placeholder("Qasr ElKbabgi") is False


def test_placeholder_regex_catches_template_slots():
    assert _PLACEHOLDER_RE.search("See how [Customer Name] transformed their journey")
    assert _PLACEHOLDER_RE.search("Our take on the future of [Industry]")
    assert _PLACEHOLDER_RE.search("transformed [Client Type]'s digital journey")
    assert not _PLACEHOLDER_RE.search("Grilled kofta and molokhia at Qasr ElKbabgi")


def test_thin_profile_yields_empty_calendar_not_fabrication():
    # No name, no offerings -> the calendar must be EMPTY (an advisor Gap surfaces it), never a
    # "The brand" deliverable full of filler.
    cal = build_strategy({}, caller=None, days=14)
    assert cal.items == []


def test_named_brand_with_real_offerings_still_builds():
    prof = {"name": {"value": "Qasr ElKbabgi"}, "category": {"value": "restaurant"},
            "offerings": [{"name": {"value": "Mixed Grill"}}, {"name": {"value": "Molokhia"}}],
            "social_presence": [{"platform": "facebook", "url": "https://facebook.com/x"},
                                {"platform": "instagram", "url": "https://instagram.com/x"}]}
    cal = build_strategy(prof, caller=None, days=14)
    assert len(cal.items) > 0
    assert cal.business_name == "Qasr ElKbabgi"
    # no item may carry an unfilled placeholder
    for it in cal.items:
        assert not _PLACEHOLDER_RE.search(it.topic or "")
        assert not _PLACEHOLDER_RE.search(it.hook or "")
        assert not _PLACEHOLDER_RE.search(it.angle or "")
