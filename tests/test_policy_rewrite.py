"""U4 suggested-rewrite re-gate (v2.2 §6-W7): a rewrite may replace the original ONLY after it
re-passes the Policy Linter AND the Evidence Ledger. Hermetic: the caller is injected."""
from __future__ import annotations

from policy_linter import suggest_and_regate


def _caller(returns: str):
    class _R:
        rewritten = returns

    def caller(system, user, model, group_name=""):
        return _R(), None
    return caller


def test_clean_rewrite_passes_both_gates_and_is_accepted():
    out = suggest_and_regate(surface="hook", text="نتيجة مضمونة 100%", category="clinic",
                             caller=_caller("احجز استشارتك مع فريق المركز"), profile={})
    assert out.accepted is True and out.rewritten == "احجز استشارتك مع فريق المركز"


def test_still_violating_rewrite_is_rejected():
    out = suggest_and_regate(surface="hook", text="نتيجة مضمونة", category="clinic",
                             caller=_caller("نتيجة مضمونة تماماً"), profile={})   # still guaranteed-results
    assert out.accepted is False and any("policy" in r for r in out.reasons)


def test_rewrite_that_smuggles_an_unsourced_claim_is_rejected():
    # policy-clean but adds a superlative claim with no evidence in an empty profile -> ledger vetoes.
    out = suggest_and_regate(surface="hook", text="نتيجة مضمونة", category="clinic",
                             caller=_caller("نحن أفضل عيادة أسنان في مصر"), profile={})
    assert out.accepted is False and any("unsourced" in r or "policy" in r for r in out.reasons)


def test_no_caller_does_not_accept():
    out = suggest_and_regate(surface="hook", text="نتيجة مضمونة", category="clinic", caller=None)
    assert out.accepted is False and "no caller" in out.reasons[0]


def test_empty_rewrite_is_rejected():
    out = suggest_and_regate(surface="hook", text="نتيجة مضمونة", category="clinic",
                             caller=_caller("   "), profile={})
    assert out.accepted is False
