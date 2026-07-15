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


def test_a_ledger_ERROR_rejects_the_rewrite_instead_of_blessing_it(monkeypatch):
    """P6 #4 — PD-3: "evidence or it doesn't ship". The Ledger half of the re-gate used to
    `return True, ["evidence ledger unavailable — policy lint only"]` on ANY exception, i.e. it
    reported CLEAN precisely when it could not check. An LLM rewrite carrying a fabricated
    superiority claim would then pass with only the regex linter having looked at it, and land in
    the client's ads_manager.csv.

    (Scope note, verified: `profile=None` does NOT trigger this — EvidenceLedger.from_profile(None)
    returns an EMPTY ledger, so every hard claim is unsourced and the gate already fails closed.
    This hole needs an actual ledger exception, which makes it latent, not routine.)
    """
    import policy_linter.rewrite as rw

    def _boom(*a, **k):
        raise RuntimeError("ledger down")
    monkeypatch.setattr("grounding.EvidenceLedger.from_profile", _boom)
    ok, reasons = rw._ledger_clean({"name": {"value": "X"}}, "أفضل مركز معتمد في القاهرة")
    assert ok is False, "a ledger error blessed an unverified rewrite"
    assert any("cannot" in r.lower() or "unavailable" in r.lower() for r in reasons)


def test_profile_none_already_fails_closed_via_an_empty_ledger():
    # pins the scope note above: no evidence => nothing resolves => not clean. No exception needed.
    import policy_linter.rewrite as rw
    ok, reasons = rw._ledger_clean(None, "We are the #1 clinic in Cairo")
    assert ok is False and reasons
