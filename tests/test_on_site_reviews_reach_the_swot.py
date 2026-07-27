# -*- coding: utf-8 -*-
"""Nine real customer reviews must travel from the page to the SWOT, not stop halfway.

`scraper/extractors/testimonials.py` now recovers the reviews printed on the brand's own homepage
(9 of them on alfouadpharmacies.com, each with the customer's name). MEASURED afterwards, they
still could not reach the analysis, because the road ends twice:

    api/brand_translate.py:181     "testimonials": []      <- hardcoded empty, whatever the profile holds
    grep is_own_brand=True         only the Instagram path  <- nothing converts a site review into a theme

So the extraction alone changes nothing on screen. This is the same failure the whole session keeps
finding — produced, serialized, and dropped one step before it counts — and finding it twice in one
file is why the test covers the WHOLE road rather than each hop.

The polarity rule matters as much as the plumbing: a review is evidence only when it says something
good or bad. "متاح العرض على الموقع؟" is a question, not praise, and must not become a Strength.
"""
from __future__ import annotations

_REVIEWS = [
    {"author": "Abdelrahman Badr",
     "quote": "التوصيل سريع ومتابعه العملاء ممتازة اتمنى لكم دوام النجاح"},
    {"author": "Shams Mohamed",
     "quote": "الخدمة ممتازة المنتج جودته ممتازة ووصل بسرعه والتعامل كويس جدا"},
    {"author": "Nada Heshmat",
     "quote": "أنا بشتري منكم بس لأني عارفة ان الحجات أصلية ومش ممكن اشتري من اي مكان تاني"},
]


def test_a_review_becomes_an_own_brand_theme():
    from competitor.social_themes import themes_from_testimonials
    out = themes_from_testimonials(_REVIEWS)
    assert out, "nine real reviews produced no theme"
    assert all(getattr(t, "is_own_brand", False) for t in out), (
        "a customer praising THIS brand must route to Strengths, not to a rival's column")
    assert all(getattr(t, "polarity", "") in ("praise", "complaint") for t in out)


def test_the_reviewers_name_is_the_citation():
    """A quote with no attributable source is exactly what the grounding gate exists to refuse."""
    from competitor.social_themes import themes_from_testimonials
    out = themes_from_testimonials(_REVIEWS)
    joined = " ".join(" ".join(getattr(t, "support", []) or []) for t in out)
    assert "Abdelrahman Badr" in joined


def test_a_neutral_review_is_not_evidence():
    from competitor.social_themes import themes_from_testimonials
    assert themes_from_testimonials(
        [{"author": "X", "quote": "العرض ده متاح على الموقع بس ولا في الفروع كمان؟"}]) == []


def test_an_unattributed_quote_is_refused_here_too():
    from competitor.social_themes import themes_from_testimonials
    assert themes_from_testimonials([{"author": "", "quote": "خدمة ممتازة جدا وسريعة والتعامل راقي"}]) == []


def test_the_brand_projection_stops_discarding_them():
    """`"testimonials": []` was hardcoded, so the field could never carry anything."""
    from api.brand_translate import profile_to_brand
    brand = profile_to_brand({"source_url": "https://x.test/", "testimonials": _REVIEWS})
    assert len(brand.get("testimonials") or []) == 3, (
        "the projection still drops the reviews it was handed")


def test_the_collector_picks_them_up():
    """One door for customer voice — a source wired into one caller and forgotten in the other is
    how the Instagram path ended up being the only one."""
    from api.webapp_analysis import _customer_voice_themes
    out = _customer_voice_themes({"source_url": "https://nowhere.test/", "testimonials": _REVIEWS})
    assert out, "on-site reviews are not part of the customer-voice collector"
    assert any(getattr(t, "is_own_brand", False) for t in out)
