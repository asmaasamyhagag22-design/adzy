"""P-09: the content calendar's platforms come from the BRAND'S own accounts + the media plan's
channel weights, not a hardcoded instagram/tiktok/linkedin trio.

The old trio produced ZERO Facebook for every brand (in a market where Facebook is the largest
channel and the system's own media plan weights it highest) and forced a LinkedIn slot onto
restaurants/retailers with no LinkedIn presence.
"""
from __future__ import annotations

from strategy.builder import _platforms_for


def _prof(category, platforms):
    return {"category": {"value": category},
            "social_presence": [{"platform": p, "url": f"https://{p}.com/x"} for p in platforms]}


def test_facebook_is_included_when_the_brand_has_it():
    plats = _platforms_for(_prof("retail", ["facebook", "instagram"]))
    assert "facebook" in plats


def test_linkedin_is_not_forced_when_absent():
    # A retailer with no LinkedIn page must NOT get a LinkedIn slot.
    plats = _platforms_for(_prof("retail", ["facebook", "instagram", "tiktok"]))
    assert "linkedin" not in plats


def test_linkedin_kept_when_the_brand_actually_has_it():
    plats = _platforms_for(_prof("education", ["facebook", "linkedin", "instagram"]))
    assert "linkedin" in plats


def test_ordered_by_channel_weight_facebook_first_for_government():
    # media_plan weights Facebook highest for government -> it should lead.
    plats = _platforms_for(_prof("government", ["instagram", "linkedin", "facebook"]))
    assert plats[0] == "facebook"


def test_messaging_and_dev_platforms_excluded():
    plats = _platforms_for(_prof("retail", ["whatsapp", "telegram", "github", "instagram"]))
    assert set(plats) <= {"facebook", "instagram", "tiktok", "linkedin", "youtube"}
    assert "whatsapp" not in plats and "github" not in plats


def test_empty_social_fallback_includes_facebook_not_the_old_trio():
    plats = _platforms_for({"category": {"value": "restaurant"}})
    assert "facebook" in plats
    assert plats != ["instagram", "tiktok", "linkedin"]
