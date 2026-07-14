"""Reel brand-safety gate — the director's 'no third-party marks' contract enforced in CODE
(PD-1: gates are code, the LLM never self-grades), not the prompt alone. coherence_lint flags a
scene whose Veo prompt would render a logo/trademark that is NOT the brand's own. Hermetic; the
gate is a pure function over the plan text."""
from __future__ import annotations

from reel.coherence import coherence_lint
from reel.creative_director import CreativeReel, CreativeScene


def _reel(veo0: str, veo1: str = "the product held up on a clean table") -> CreativeReel:
    return CreativeReel(
        concept="c", hook="h", cta="اطلب دلوقتي", language="ar", dialect="egyptian",
        images=["https://x/0.jpg", "https://x/1.jpg"],
        scenes=[
            CreativeScene(image_index=0, veo_prompt=veo0, voiceover="v0",
                          on_screen_text="NTI", caption_role="", hero_product=True),
            CreativeScene(image_index=1, veo_prompt=veo1, voiceover="v1",
                          on_screen_text="اطلب دلوقتي", caption_role="cta", hero_product=True),
        ])


def _profile(name: str = "NTI") -> dict:
    return {"name": {"value": name}}


def _mark_flagged(rep) -> bool:
    return any(("third-party" in i) or ("logo and trademark" in i) for i in rep.issues)


def test_competitor_logo_in_a_scene_is_flagged():
    rep = coherence_lint(_reel("a runner in shoes with a Nike logo visible on screen"),
                         profile=_profile())
    assert _mark_flagged(rep)


def test_hard_marks_swoosh_and_trademark_symbol_are_flagged():
    assert _mark_flagged(coherence_lint(_reel("a bold swoosh sweeps across the frame"),
                                        profile=_profile()))
    assert _mark_flagged(coherence_lint(_reel("the product box stamped with a ® symbol"),
                                        profile=_profile()))


def test_the_brands_own_mark_is_allowed():
    # the brand's OWN logo is explicitly permitted by the contract
    assert not _mark_flagged(coherence_lint(
        _reel("the NTI logo fades softly into the corner"), profile=_profile("NTI")))
    # generic self-reference ('our/brand logo') is self, not third-party
    assert not _mark_flagged(coherence_lint(
        _reel("our brand logo sits in the lower third"), profile=_profile("NTI")))


def test_ordinary_nouns_do_not_trip_the_gate():
    # precision: 'orange' the colour / 'apple' the fruit are NOT marks (no logo/trademark word)
    rep = coherence_lint(_reel("fresh orange juice beside a green apple on warm oak"),
                         profile=_profile())
    assert not _mark_flagged(rep)
