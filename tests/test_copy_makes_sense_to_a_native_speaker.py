# -*- coding: utf-8 -*-
"""The copy has to MEAN something to a native speaker.

OWNER (2026-07-26), reading the headline the system shipped: «هو إيه "وقّفي البقعة"؟ مش فاهمة
أصلاً».

Every existing gate judged the copy on a different axis — Latin characters in Arabic, unsourced
claims, therapeutic verbs, contrast, stray text. NOT ONE of them asks the only question a reader
asks: does this sentence make sense? «وقّفي البقعة في مكانها» is Arabic, grounded, cosmetic-safe,
readable — and meaningless. So it shipped.

This is the missing gate: a native-speaker sense check on the copy, with a concrete rewrite when
it fails. Hermetic here — the judge is injected.
"""
from __future__ import annotations


def _judge(**verdicts):
    """A fake judge: {field: (ok, fix)}."""
    def caller(system, user, schema, **kw):
        class _R:
            fields = [{"field": k, "natural": v[0], "issue": "" if v[0] else "nonsense",
                       "rewrite": v[1]} for k, v in verdicts.items()]
        return _R(), {"total_tokens": 5}
    return caller


def test_a_meaningless_headline_is_rewritten():
    from poster.copy_qa import sense_check
    out = sense_check({"headline": "وقّفي البقعة في مكانها"}, language="ar",
                      caller=_judge(headline=(False, "احمي بشرتك من البقع")))
    assert out.checked is True and out.passed is False
    assert out.rewrites.get("headline") == "احمي بشرتك من البقع"


def test_natural_copy_passes_untouched():
    from poster.copy_qa import sense_check
    out = sense_check({"headline": "احمي بشرتك من الشمس"}, language="ar",
                      caller=_judge(headline=(True, "")))
    assert out.passed is True and out.rewrites == {}


def test_no_caller_is_could_not_verify_not_a_pass():
    from poster.copy_qa import sense_check
    out = sense_check({"headline": "أي كلام"}, language="ar", caller=None)
    assert out.checked is False and out.passed is None


def test_a_failed_line_with_no_rewrite_is_still_reported():
    """A judge that condemns without proposing must not silently pass the line."""
    from poster.copy_qa import sense_check
    out = sense_check({"headline": "وقّفي البقعة"}, language="ar",
                      caller=_judge(headline=(False, "")))
    assert out.passed is False and "headline" in out.failed


def test_the_concept_applies_the_rewrites():
    import inspect

    import poster.concept as c
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(c).splitlines())
    assert "sense_check" in src, "nothing ever asks whether the copy means anything"
