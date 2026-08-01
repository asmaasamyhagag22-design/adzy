# -*- coding: utf-8 -*-
"""Five defects the owner found in five seconds, past four gates that each judged a part.

OWNER (2026-08-01), on a reel that passed seed QA, scene QA, the coherence lint AND the final
screening: «الريل بشع، القشرة أوفر موت، ودار الفوائد صيدلية في مصر وعندها عربي — عملت إنجليزي
ليه؟ والمتصفح المفتوح باين إنه مش بتاعهم… وفجأة اتحل من غير ما يمسك أي منتج».

The voiceover shipped verbatim as: "Tired of dandruff that just won't quit? Find real, original
solutions at…" — an English commercial for an Egyptian pharmacy.

Every gate this pipeline owns judges a PART: a still, a clip, the written plan, the delivered
container. `reel/final_screening.py` is the closest and is deliberately code-only — it asks
whether the FILE is broken, never whether the AD is any good. Five parts can each pass and the
film still be unwatchable, which is the failure class this repo keeps paying for arriving at the
last place it had not been closed.
"""
from __future__ import annotations

import types


def _resp(**kw):
    base = dict(product_causes_resolution=True, problem_is_attractive=True,
                speaks_audience_language=True, everything_belongs_to_brand=True,
                reads_as_an_ad=True, score=8, reason="fine", fixes=[])
    base.update(kw)
    return types.SimpleNamespace(**base)


# =====================================================================
# the verdict is computed in code, never taken from the model
# =====================================================================

def test_each_hard_axis_alone_fails_the_film():
    """Each of these is a defect the owner rejected a whole reel over."""
    from reel.film_critic import _recompute
    for axis in ("product_causes_resolution", "problem_is_attractive",
                 "speaks_audience_language", "everything_belongs_to_brand"):
        assert _recompute(_resp(**{axis: False})) is False, axis


def test_a_procedural_film_is_a_note_not_a_block():
    """"This feels like a tutorial" is a craft note; blocking a delivery on it would make the
    gate unusable. It lowers the grade and rides the fixes instead."""
    from reel.film_critic import _recompute
    assert _recompute(_resp(reads_as_an_ad=False, score=8)) is True


def test_a_low_score_fails_even_with_every_box_ticked():
    from reel.film_critic import _recompute
    assert _recompute(_resp(score=4)) is False
    assert _recompute(_resp(score=6)) is True


def test_the_model_cannot_pass_itself():
    """PD-1. The response model has no overall_pass to trust, and the verdict is recomputed."""
    from reel.film_critic import _CriticResponse
    assert "overall_pass" not in _CriticResponse.model_fields
    assert "passed" not in _CriticResponse.model_fields


# =====================================================================
# nobody watched is not the same as watched and fine
# =====================================================================

def test_no_caller_means_the_film_was_not_watched(tmp_path):
    from reel.film_critic import critique_film
    v = critique_film(tmp_path / "x.mp4", caller=None)
    assert v.checked is False and v.passed is None
    assert "not watched" in v.reason


def test_an_unreadable_film_is_not_reported_as_screened(tmp_path):
    from reel.film_critic import critique_film
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    v = critique_film(bad, caller=lambda *a, **k: (_resp(), None))
    assert v.checked is False and v.passed is None


def test_a_failed_critic_call_does_not_raise(tmp_path):
    from reel.film_critic import critique_film

    def _boom(*a, **k):
        raise RuntimeError("429")

    v = critique_film(tmp_path / "x.mp4", caller=_boom)
    assert v.checked is False


# =====================================================================
# the questions it asks are the ones the owner asked
# =====================================================================

def test_the_prompt_asks_about_each_defect_that_shipped():
    from reel.film_critic import _system
    p = _system("AlFouad Pharmacies", "Arabic", "eg").lower()
    assert "causes" in p and "nobody holding" in p          # solved with no product
    assert "flakes" in p or "off-putting" in p              # the symptom shown clinically
    assert "arabic" in p                                     # the audience's language, named
    assert "whose" in p                                      # ownership, not legibility
    assert "tutorial" in p or "demo" in p                    # ad vs procedure


def test_the_critic_is_told_the_website_language_is_not_the_audiences():
    """The exact confusion that produced the English ad."""
    from reel.film_critic import _system
    assert "does not make its customers" in _system("b", "Arabic", "eg")


def test_the_failed_axes_are_named_the_way_they_were_reported():
    from reel.film_critic import FilmVerdict, failed_axes
    v = FilmVerdict(product_causes_resolution=False, problem_is_attractive=False,
                    speaks_audience_language=False, everything_belongs_to_brand=False,
                    checked=True, passed=False)
    axes = failed_axes(v)
    assert len(axes) == 4
    assert any("causes the resolution" in a for a in axes)
    assert any("symptom" in a for a in axes)


def test_the_critic_sees_the_whole_film_not_one_frame():
    """The questions are about the ARC — a critic shown only the opening cannot say whether the
    product caused the ending."""
    from reel.film_critic import FRAMES
    assert FRAMES >= 6


# =====================================================================
# the audience's language, and the plumbing that starved it
# =====================================================================

def test_a_declared_edition_outranks_the_one_we_crawled():
    """hreflang is the brand publishing its own answer; a crawl is our answer about the brand."""
    from reel.audience import audience_language_for
    lang, _c, why = audience_language_for(
        {"languages": ["en"], "declared_languages": ["ar", "en"],
         "description": "An online retailer of health and beauty products."})
    assert lang == "Arabic"
    assert "declares" in why


def test_the_market_outranks_an_english_storefront():
    """«صيدلية في مصر» — a pharmacy in Cairo serves Arabic speakers whether or not its storefront
    software ships an English interface."""
    from reel.audience import audience_language_for
    lang, country, why = audience_language_for(
        {"languages": ["en"], "country": "Egypt",
         "description": "An online retailer of health and beauty products."})
    assert lang == "Arabic" and country == "eg"
    assert "customers speak" in why


def test_a_country_code_is_read_off_a_cctld():
    from reel.audience import audience_language_for
    for url, code in (("https://shop.example.eg/", "eg"),
                      ("https://shop.example.com.eg/", "eg"),
                      ("https://brand.sa/", "sa")):
        assert audience_language_for({"source_url": url})[1] == code, url


def test_a_plain_com_is_not_mistaken_for_a_country():
    from reel.audience import audience_language_for
    assert audience_language_for({"source_url": "https://brand.com/"})[1] == ""


def test_the_brands_own_script_still_counts_but_ranks_last():
    """Real evidence, and the weakest rung — it is exactly what a crawl of the English edition
    manufactures."""
    from reel.audience import audience_language_for
    lang, _c, why = audience_language_for({"name": "نانو كير", "description": "مكملات غذائية"})
    assert lang == "Arabic" and "writes about itself" in why


def test_every_answer_names_its_evidence():
    """A wrong answer must be arguable rather than mysterious: `why` names which rung decided."""
    from reel.audience import audience_language_for
    for prof in ({}, {"languages": ["en"]}, {"declared_languages": ["fr"]},
                 {"country": "eg"}, {"name": "نانو"}):
        assert audience_language_for(prof)[2].strip()


def test_nothing_here_hardcodes_one_market():
    """D-8: the country comes off the profile and the language off a table, so a market nobody
    has run yet works the day it appears."""
    from reel.audience import audience_language_for
    assert audience_language_for({"country": "tr"})[0] == "Turkish"
    assert audience_language_for({"declared_languages": ["ja"]})[0] == "Japanese"


# =====================================================================
# the plumbing gap that starved all of it
# =====================================================================

def test_the_profile_can_carry_what_the_site_declares():
    """It reached competitor/matrix.py and stopped there, so the decision that sets the language a
    CUSTOMER HEARS never saw it."""
    from business_profile.schemas import BusinessProfile
    assert "declared_languages" in BusinessProfile.model_fields


def test_the_builder_copies_it_off_the_manifest():
    import inspect

    import business_profile.rules.orchestrator as orch
    src = inspect.getsource(orch)
    assert "declared_languages=list(getattr(manifest" in src


# =====================================================================
# wired into the ship decision
# =====================================================================

def test_the_critic_runs_before_the_reel_ships():
    import inspect

    import reel.__main__ as M
    assert hasattr(M, "_film_critique")
    src = inspect.getsource(M._film_critique)
    assert "critique_film" in src and "audience_language_for" in src


def test_the_critic_never_blocks_on_its_own_blindness():
    """No caller, unreadable frames or a failed call must ship with a note, not stop a delivery."""
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M._film_critique)
    at = src.find("if not v.checked")
    assert at > 0
    assert "return None" in src[at:at + 220]


def test_the_override_and_the_off_switch_both_exist():
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M._film_critique)
    assert "REEL_FILM_CRITIC" in src          # off entirely (the hermetic suite)
    assert "REEL_ALLOW_BELOW_GRADE" in src    # ship anyway, deliberately


def test_the_verdict_is_stamped_on_the_sidecar():
    """A rejected film that shipped by override must say so on the artifact, not only in a log
    somebody has already scrolled past."""
    import inspect

    import reel.__main__ as M
    assert "film_critique" in inspect.getsource(M._film_critique)


# =====================================================================
# prevention, not only detection
# =====================================================================

def test_the_director_asks_the_audience_not_the_crawl():
    """The critic CATCHES an English ad for an Arabic market; this stops one being written. Both
    matter — a gate that only rejects burns a full render to tell you what a field could have."""
    import inspect

    import reel.creative_director as cd
    src = inspect.getsource(cd)
    assert "audience_language_code" in src
    at = src.find("lang = language or")
    assert at > 0
    assert "_aud" in src[at:at + 60], "the audience language is not in the resolution order"


def test_an_explicit_language_still_wins():
    """A caller asking for a specific cut is not guessing."""
    import inspect

    import reel.creative_director as cd
    src = inspect.getsource(cd)
    at = src.find("lang = language or")
    line = src[at:src.find("\n", at)]
    assert line.startswith("lang = language or"), line
