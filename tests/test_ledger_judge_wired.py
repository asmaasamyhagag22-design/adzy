# -*- coding: utf-8 -*-
"""CS-46 + CS-75 (owner ruling 2026-07-18): with the subject judge wired, the ledger REJECTS a
fabricated superlative and a fabricated issuer instead of certifying them brand-tier.

Owner's rule: evidence NARROWER than the claim, or a DIFFERENT named issuer, is a mismatch — reject.
The judge is the mechanism. Here we inject a deterministic MOCK judge (the real one is a Flash call,
verified in the owner's environment; paid calls 403 here). This pins the WIRING + the resolution
logic hermetically.
"""
from __future__ import annotations

from grounding.ledger import EvidenceLedger


def _ef(v):
    return {"value": v, "evidence": [{"page_url": "https://b/p", "quote": v}]}


# a deterministic subject judge: returns False (different subject) unless the claim and evidence
# share the key content word — models "same subject?" without an LLM.
def _mock_judge(claim: str, ev: str, token: str) -> bool:
    cw = {w for w in claim.replace("،", " ").split() if len(w) > 3}
    ew = {w for w in ev.replace("،", " ").split() if len(w) > 3}
    return len(cw & ew) >= 2


def _led(judge, **fields):
    prof = {"name": {"value": "X"}}
    prof.update({k: _ef(v) for k, v in fields.items()})
    return EvidenceLedger.from_profile(prof, subject_judge=judge)


def test_cs46_fabricated_superlative_rejected_with_judge():
    # the owner's exact example: brand evidence = fastest in PARCEL DELIVERY; poster fabricates =
    # fastest in THE MARKET (wider scope). narrower evidence must NOT verify the broader claim.
    led = _led(_mock_judge, tagline="الأسرع في توصيل الطرود داخل المدينة")
    assert led.resolve_claim("الأسرع في السوق") is None


def test_cs46_matching_scope_still_resolves_with_judge():
    led = _led(_mock_judge, tagline="الأسرع في توصيل الطرود داخل المدينة")
    # same scope (shares 'توصيل'+'الطرود') -> the judge confirms -> resolves
    assert led.resolve_claim("الأسرع في توصيل الطرود") is not None


def test_cs75_fabricated_issuer_rejected_with_judge():
    led = _led(_mock_judge, description="البرنامج معتمد من المجلس المحلي للتدريب المهني")
    # a DIFFERENT issuer must not resolve (owner ruling: certification must name the real issuer)
    assert led.resolve_claim("معتمد من جامعة هارفارد") is None


def test_cs75_matching_issuer_resolves_with_judge():
    led = _led(_mock_judge, description="البرنامج معتمد من المجلس المحلي للتدريب المهني")
    assert led.resolve_claim("معتمد من المجلس المحلي") is not None


def test_no_judge_stays_lenient_certification_is_backward_compatible():
    # without a judge (offline / no caller) certification behaves exactly as before — a bare claim
    # still resolves, so wiring is purely additive and safe to ship even where the LLM is unavailable.
    led = _led(None, description="البرنامج معتمد من المجلس المحلي")
    assert led.resolve_claim("البرنامج معتمد") is not None
