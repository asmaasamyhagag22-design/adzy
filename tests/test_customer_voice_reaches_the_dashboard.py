# -*- coding: utf-8 -*-
"""§11.1 — the customer's own voice never reached the dashboard's SWOT.

TWO HOLES, ONE DEFECT. Both were found by asking the same question of every path that can produce
a SWOT for the webapp: does a real customer sentence about THIS brand survive the trip?

  1. ABSA IS CLI-ONLY. `reviews/absa.py` is the only producer of `is_own_brand=True` themes from
     reviews, and `_own_voice_themes` is called from `competitor/full_run.py:558` — the CLI — and
     nowhere else. `grep absa api/webapp_analysis.py api/routes/swot.py` returns ZERO. So a brand
     with 200 Google reviews gets, in the dashboard, exactly zero strengths or weaknesses from
     them: `ReviewThemeExtractor` reads PEER reviews only (`themes.py:116` iterates `competitors`),
     which route to Opportunities/Threats.

  2. THE STANDALONE PATH DROPS EVERY THEME. `synthesize_swot_standalone` calls `synthesize_swot`
     with `profile=` and no `themes=` at all — and its own docstring calls it "the most likely path
     for a brand whose automatic discovery finds nothing". So on the commonest path, the social
     comments wired in this session were dropped too. Fixing (1) without (2) would have fixed the
     rare case and left the common one broken.

WHY IT MATTERS: a review theme is the only SWOT item built from a customer SENTENCE rather than
from a website mechanic. Everything else describes the site.

SPEND: the ABSA call is reached ONLY when saved voice files exist for the brand — `_own_voice_themes`
returns `[]` before it ever builds a caller when there are none, and `extract_aspect_themes` returns
`[]` on a null caller. A brand with no snapshot costs nothing.

Hermetic: no network, no model — the voice source is stubbed at its seam.
"""
from __future__ import annotations

import pytest

from api.webapp_analysis import _customer_voice_themes, synthesize_swot_standalone
from competitor.swot import ReviewTheme

_PROFILE = {
    "name": "Example",
    "category": "services",
    "source_url": "https://example.test/",
    "offerings": [{"name": "Thing"}],
}


def _own(polarity, text):
    return ReviewTheme(polarity=polarity, text=text,
                       support=["own voice V1: «…»", "own voice V2: «…»"],
                       is_unmet_need=False, is_own_brand=True)


@pytest.fixture
def _voice(monkeypatch):
    """Stub the CLI's own-voice producer at its seam — the wiring is what was missing."""
    def _install(themes):
        import competitor.full_run as fr
        monkeypatch.setattr(fr, "_own_voice_themes",
                            lambda url, prof, say, **kw: list(themes), raising=False)
    return _install


# --- 1) own-brand review themes must reach the webapp at all ------------------------------------

def test_own_brand_review_themes_are_collected_for_the_webapp(_voice):
    _voice([_own("complaint", "الفرع بطيء جداً في التسليم")])
    themes = _customer_voice_themes(_PROFILE)
    assert any(t.is_own_brand and t.polarity == "complaint" for t in themes), (
        "the brand's own customers are still inaudible in the dashboard")


def test_a_brand_with_no_saved_voice_costs_nothing_and_returns_nothing():
    """No snapshot is the honest "we have not collected this brand's voice" — not an error, and
    not a reason to start billing."""
    assert _customer_voice_themes({"name": "X", "source_url": "https://nothing.test/"}) == []


def test_a_broken_voice_source_never_breaks_the_analysis(monkeypatch):
    import competitor.full_run as fr

    def _boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(fr, "_own_voice_themes", _boom, raising=False)
    assert _customer_voice_themes(_PROFILE) == []


# --- 2) the standalone path must carry the voice too --------------------------------------------

def test_the_standalone_swot_hears_an_own_brand_complaint(_voice):
    """END-TO-END on the path a peerless brand actually takes. Asserted on the SWOT, not on the
    theme list — the way-station is what fooled thirteen tests earlier in this session."""
    _voice([_own("complaint", "الفرع بطيء جداً في التسليم")])
    swot, _peers = synthesize_swot_standalone(_PROFILE)
    assert any("بطيء" in w.text for w in swot.weaknesses), (
        f"a real customer complaint did not reach Weaknesses: {[w.text for w in swot.weaknesses]}")


def test_the_standalone_swot_hears_own_brand_praise_as_a_strength(_voice):
    _voice([_own("praise", "التعامل محترم والتوصيل سريع")])
    swot, _peers = synthesize_swot_standalone(_PROFILE)
    assert any("محترم" in s.text for s in swot.strengths)


def test_a_voiceless_standalone_swot_still_builds():
    swot, _peers = synthesize_swot_standalone(_PROFILE)
    assert swot is not None
    assert swot.mode in ("standalone", "competitive")


# --- 3) both sources arrive through one door ----------------------------------------------------

def test_social_comments_and_review_voice_share_the_same_collector(monkeypatch, _voice):
    """One function feeds every SWOT path, so a source can never again be wired into one caller
    and forgotten in the other."""
    import api.webapp_analysis as wa
    monkeypatch.setattr(wa, "_social_themes_for",
                        lambda p: [ReviewTheme(polarity="complaint", text="التغليف وحش",
                                               support=["https://instagram.test/p/1"],
                                               is_own_brand=True)])
    _voice([_own("praise", "التعامل محترم")])
    texts = {t.text for t in _customer_voice_themes(_PROFILE)}
    assert "التغليف وحش" in texts, "social comments dropped"
    assert "التعامل محترم" in texts, "review voice dropped"
