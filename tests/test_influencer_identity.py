# -*- coding: utf-8 -*-
"""Who the influencer is — from the owner's choices, or from the brand's measured market.

The owner's spec has two halves. When she describes the person, we render what she described. And
"طول ما هو محتار — يعمل حاجة جينرال": while she has not decided, the system must produce someone
sensible rather than nothing, and "sensible" cannot mean a default nationality.

D-8 IS THE WHOLE POINT HERE. An influencer is a PERSON, so a hardcoded market is not a rounding
error — it casts the wrong human being into every client's advertising. The reel already has this
bug: `reel/render3/prompts.py:62,101` writes "Egyptian" into the character block and
`CharacterSheet` has no market field at all, so a Qatari café's protagonist is Egyptian. The fix is
already in the codebase and merely unused here: `poster.locale.brand_locale(profile)` derives a
casting instruction from the brand's MEASURED country, and explicitly rejects a generic regional
look ("NOT a generic 'Arab' or Gulf/Khaleeji look, NOT Western/European casting").

Hermetic — pure text assembly, no model, no network."""
from __future__ import annotations

from api.influencer_identity import identity_lock_text, persona_from_brand, persona_from_owner


def _profile(country_url="https://x.com.eg/", **kw):
    p = {"name": {"value": "براند"}, "source_url": {"value": country_url},
         "languages": ["ar"], "audience_type": {"value": "B2C"},
         "tone_of_voice": {"value": "inspirational"},
         "market_category": {"value": "liposomal supplements"}}
    p.update(kw)
    return p


# --- the owner described the person ------------------------------------------------------------

def test_the_owners_choices_are_used_verbatim():
    text = persona_from_owner({"gender": "woman", "age": "30s", "hair_length": "long",
                               "expression": "warm"})
    for token in ("woman", "30s", "long", "warm"):
        assert token in text, token


def test_beard_and_glasses_are_only_mentioned_when_asked_for():
    on = persona_from_owner({"gender": "man", "beard": "yes", "glasses": "yes"})
    off = persona_from_owner({"gender": "man", "beard": "no", "glasses": "no"})
    assert "beard" in on and "glasses" in on
    assert "beard" not in off and "glasses" not in off


def test_placeholder_choices_are_not_treated_as_descriptions():
    """The UI sends "any"/"none" for an untouched dropdown — that is not a feature of a face."""
    text = persona_from_owner({"gender": "any", "hair_style": "none", "skin": ""})
    assert "any" not in text.lower() and "none" not in text.lower()


def test_nothing_chosen_is_reported_as_undecided_not_invented():
    assert persona_from_owner({}) == ""


# --- the owner is still undecided ---------------------------------------------------------------

def test_an_undecided_owner_still_gets_a_person():
    text = persona_from_brand(_profile())
    assert text.strip(), "an undecided owner got nothing at all"


def test_the_person_belongs_to_the_brands_measured_market():
    text = persona_from_brand(_profile("https://x.com.eg/"))
    assert "Egypt" in text


def test_a_different_market_gets_a_different_person():
    """The bug this file exists to prevent: everyone ends up from one country."""
    eg = persona_from_brand(_profile("https://x.com.eg/"))
    qa = persona_from_brand(_profile("https://x.com.qa/"))
    assert eg != qa
    assert "Egypt" not in qa, f"a Qatari brand got an Egyptian influencer: {qa}"


def test_an_unknown_market_produces_no_nationality_at_all():
    """No country measured means no country asserted — not a fallback to a favourite one."""
    text = persona_from_brand({"name": {"value": "X"}})
    assert "Egypt" not in text and "Saudi" not in text


def test_the_brand_persona_reflects_who_the_brand_talks_to():
    text = persona_from_brand(_profile())
    assert "inspirational" in text.lower() or "supplements" in text.lower()


# --- the identity lock --------------------------------------------------------------------------

def test_the_lock_names_the_face_as_unchangeable():
    text = identity_lock_text("a woman in her 30s", market="Egypt")
    low = text.lower()
    assert "same person" in low
    assert "do not change" in low
    assert "face" in low


def test_the_lock_carries_the_persona_and_the_market():
    text = identity_lock_text("a woman in her 30s", market="Qatar")
    assert "a woman in her 30s" in text
    assert "Qatar" in text


def test_the_lock_never_hardcodes_a_market():
    """The exact defect measured in reel/render3/prompts.py."""
    text = identity_lock_text("a person", market="Qatar")
    assert "Egyptian" not in text and "Egypt" not in text


def test_the_lock_works_without_a_known_market():
    text = identity_lock_text("a person", market="")
    assert "same person" in text.lower()
    assert "None" not in text and "{" not in text
