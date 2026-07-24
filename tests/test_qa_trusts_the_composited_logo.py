# -*- coding: utf-8 -*-
"""The critic must not reject the brand's OWN logo as "garbled".

MEASURED (Volume Qatar, 2026-07-21): three consecutive renders were blocked by
"The brand logo is completely garbled and illegible". The composited mark was pixel-identical to
`wp-logo-BOOK.png` from the brand's own site — a designed wordmark whose letterforms deliberately
overlap (a brush-stroke V, then interlocking OLUME). The vision critic reads intentional overlap as
corruption, so a whole class of stylised logos can never pass.

`logo_ok` exists to catch the IMAGE MODEL redrawing and mangling a logo. When the real asset was
composited deterministically, that failure mode is impossible by construction — so the criterion
becomes about VISIBILITY (is it legible against what's behind it?), never about letterforms.
Hermetic (prompt shape + gate logic)."""
from __future__ import annotations

from poster.vision_qa import PosterQAVerdict, _logo_criterion, _pass_criteria


def test_a_drawn_logo_is_still_judged_on_its_letterforms():
    c = _logo_criterion(composited=False)
    assert "garbled" in c and "HARD GATE" in c


def test_a_composited_logo_is_judged_on_visibility_not_letterforms():
    c = _logo_criterion(composited=True)
    assert "brand's OWN file" in c
    assert "stylised" in c or "stylized" in c        # overlap is design, not damage
    assert "contrast" in c or "visible" in c


def test_pass_criteria_drops_logo_ok_when_the_real_mark_was_composited():
    # Nothing the critic can see about our own pixel-exact asset should block the deliverable.
    assert "logo_ok" in _pass_criteria(True, logo_composited=False)
    assert "logo_ok" not in _pass_criteria(True, logo_composited=True)
    # everything else still gates
    for gate in ("script_wellformed", "invented_product", "third_party_mark", "score >= 7"):
        assert gate in _pass_criteria(True, logo_composited=True)


def test_verdict_recompute_ignores_logo_ok_for_a_composited_mark():
    from poster.vision_qa import _recompute_pass
    v = dict(logo_ok=False, script_wellformed=True, invented_product=False,
             third_party_mark=False, score=8, model_pass=True)
    assert _recompute_pass(**v, logo_composited=True) is True
    assert _recompute_pass(**v, logo_composited=False) is False


def test_default_verdict_shape_is_unchanged():
    assert PosterQAVerdict().logo_ok is True
