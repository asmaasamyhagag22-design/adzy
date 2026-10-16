# -*- coding: utf-8 -*-
"""C4: hook, body and CTA are three ROLES, not three copies of one sentence.

Every generated line runs through the same EvidenceLedger gate the poster's copy uses, and a line
the gate cannot source is replaced by trusted profile copy — the tagline, or the description. That
substitution is right. Doing it three times independently is not: when the gate rejects all three
generated lines, hook, body and CTA each fall back to the SAME proposition and the caption ships as
one sentence printed three times, under a structure that promises three different things.

The invariant restored here is deliberately narrow and deterministic:

    ONE grounded fallback proposition may satisfy AT MOST ONE role.

It is not a semantic role classifier. Nothing here decides whether a sentence "sounds like a CTA" —
that is exactly the open-ended judgement a keyword list would fake. A role that has no grounded
content left to give is honestly EMPTY, because an empty field is a smaller lie than a duplicated
one, and inventing a distinct claim to fill it would be the biggest lie of the three.

No network, no paid call: the caller is a local fake, but the Ledger gate is the real one.
"""
from __future__ import annotations

import pytest

import api.webapp_social_copy as sc

# `tagline` is the first fallback `_gate_text` reaches for; `description` is the second.
_PROFILE = {
    "name": {"value": "Nahdi"},
    "tagline": {"value": "Care you can trust"},
    "description": {"value": "A pharmacy chain in Jeddah"},
    "category": {"value": "pharmacy"},
}
# An unsourced superlative. VERIFIED against the real Ledger below — this is rejected.
_UNSOURCED = "We are the best pharmacy in the entire world with 99% cures"


class _Resp:
    def __init__(self, hook, body, cta, hashtags=()):
        self.hook, self.body, self.cta = hook, body, cta
        self.hashtags = list(hashtags)


def _caller_returning(hook, body, cta, hashtags=()):
    def caller(_system, _user, _schema, group_name=""):
        return _Resp(hook, body, cta, hashtags), None
    return caller


def _roles(out: dict) -> list[str]:
    return [out["hook"], out["body"], out["cta"]]


def _nonempty(out: dict) -> list[str]:
    return [r for r in _roles(out) if r]


# =====================================================================
# the gate's own authority is unchanged — the premise of everything below
# =====================================================================

def test_the_ledger_gate_still_decides_what_is_grounded():
    """C4 must not become a way to smuggle unsourced copy through."""
    assert sc._gate_text(_PROFILE, _UNSOURCED) == "Care you can trust", (
        "an unsourced superlative must still fall back to trusted profile copy")
    assert sc._gate_text(_PROFILE, "Care you can trust") == "Care you can trust", (
        "grounded copy must pass through untouched")
    assert sc._gate_text({"name": {"value": "X"}}, _UNSOURCED) == "", (
        "with no trusted copy to fall back to, the honest answer is nothing")


# =====================================================================
# R3.6.1 — the regression: one fallback, three roles
# =====================================================================

def test_one_fallback_proposition_cannot_fill_all_three_roles():
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning(_UNSOURCED, _UNSOURCED, _UNSOURCED))

    used = [r for r in _roles(out) if r == "Care you can trust"]
    assert len(used) <= 1, (
        f"the same fallback proposition filled {len(used)} roles: {_roles(out)}")
    assert out["hook"] == "Care you can trust", "the first role keeps the fallback"
    assert out["body"] != out["hook"], "body repeated the hook"
    assert out["cta"] != out["hook"], "cta repeated the hook"


def test_the_caption_does_not_repeat_the_same_sentence():
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning(_UNSOURCED, _UNSOURCED, _UNSOURCED))
    caption = out["caption"]
    assert caption.count("Care you can trust") <= 1, (
        f"the assembled caption printed one sentence more than once:\n{caption}")


# =====================================================================
# R3.6.2 / R3.6.3 — accepted content is never sacrificed to the rule
# =====================================================================

def test_an_accepted_hook_is_preserved_when_the_other_roles_are_rejected():
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning("Care you can trust", _UNSOURCED, _UNSOURCED))
    assert out["hook"] == "Care you can trust", "the accepted hook was dropped"
    assert out["body"] != out["hook"]
    assert out["cta"] != out["hook"]


def test_an_accepted_body_is_not_removed_as_a_false_duplicate():
    """The body is grounded and DIFFERENT; the rule must not touch it."""
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning(_UNSOURCED, "A pharmacy chain in Jeddah", "Order now"))
    assert out["body"] == "A pharmacy chain in Jeddah", "a grounded body was wrongly removed"
    assert out["cta"] == "Order now", "a grounded CTA was wrongly removed"
    assert out["hook"] == "Care you can trust", "the hook's own fallback was wrongly removed"
    assert len(set(_nonempty(out))) == len(_nonempty(out)), "roles collided"


# =====================================================================
# R3.6.4 — identical candidates across roles resolve deterministically
# =====================================================================

def test_identical_generated_strings_across_roles_are_deduplicated_in_order():
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning("Care you can trust", "Care you can trust",
                                 "Care you can trust"))
    assert out["hook"] == "Care you can trust", "first role wins, deterministically"
    assert out["body"] == ""
    assert out["cta"] == ""


def test_duplicate_detection_ignores_whitespace_and_case_not_meaning():
    """Same proposition, cosmetically different — still one proposition.

    This compares SURFACE only (whitespace, case). It deliberately does not try to decide that two
    differently-worded sentences mean the same thing; that judgement needs real semantics, and a
    keyword list pretending to have them is what this project refuses to build.
    """
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning("Care you can trust", "  care   YOU can trust  ", "Order now"))
    assert out["hook"] == "Care you can trust"
    assert out["body"] == "", "a cosmetic respelling of the hook filled the body"
    assert out["cta"] == "Order now", "an unrelated grounded CTA was collateral damage"


# =====================================================================
# R3.6.5 / R3.6.6 — distinct content preserved; nothing invented
# =====================================================================

def test_three_distinct_grounded_values_all_survive():
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning("Care you can trust", "A pharmacy chain in Jeddah",
                                 "Order now"))
    assert _roles(out) == ["Care you can trust", "A pharmacy chain in Jeddah", "Order now"]


def test_with_no_fallback_available_nothing_is_invented():
    bare = {"name": {"value": "X"}}
    out = sc.social_caption(
        bare, language="en", caller=_caller_returning(_UNSOURCED, _UNSOURCED, _UNSOURCED))
    assert _roles(out) == ["", "", ""], "copy was invented to fill empty roles"
    assert out["caption"].strip() in ("", "#X"), f"unexpected assembled caption: {out['caption']!r}"


def test_empty_generated_roles_do_not_all_become_the_tagline():
    """The model omitting every field is the cheapest way to reach the collapse."""
    out = sc.social_caption(_PROFILE, language="en", caller=_caller_returning("", "", ""))
    used = [r for r in _roles(out) if r == "Care you can trust"]
    assert len(used) <= 1, f"empty generation collapsed into a repeated tagline: {_roles(out)}"


# =====================================================================
# R3.6.7 / R3.6.8 — blast radius: hashtags and language are untouched
# =====================================================================

def test_hashtags_are_unaffected_by_role_separation():
    out = sc.social_caption(
        _PROFILE, language="en",
        caller=_caller_returning(_UNSOURCED, _UNSOURCED, _UNSOURCED,
                                 hashtags=["pharmacy", "Nahdi"]))
    assert "#Nahdi" in out["hashtags"], "the brand tag was lost"
    # Hashtags opt out of textual fallback entirely, so the role budget must not reach them.
    assert all(t.startswith("#") for t in out["hashtags"])
    assert "Care you can trust" not in " ".join(out["hashtags"])


@pytest.mark.parametrize("language,expected", [("en", "en"), ("ar", "ar")])
def test_language_selection_is_unchanged(language, expected):
    out = sc.social_caption(
        _PROFILE, language=language,
        caller=_caller_returning(_UNSOURCED, _UNSOURCED, _UNSOURCED))
    assert out["language"] == expected


def test_the_no_caller_path_still_returns_one_grounded_body():
    """The deterministic fallback shape already avoided duplication; keep it that way."""
    out = sc.social_caption(_PROFILE, language="en", caller=None)
    assert out["hook"] == ""
    assert out["body"] == "Care you can trust"
    assert out["cta"] == ""


def test_content_only_mode_carries_the_same_separation():
    out = sc.content_only(
        _PROFILE, language="en",
        caller=_caller_returning(_UNSOURCED, _UNSOURCED, _UNSOURCED))
    assert out["content_only"] is True
    used = [r for r in _roles(out) if r == "Care you can trust"]
    assert len(used) <= 1
