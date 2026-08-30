# -*- coding: utf-8 -*-
"""CS-122 CLOSED by owner ruling (2026-07-19): an extracted discount/offer is a FACT that needs
grounding — the system must NEVER propose a discount for the client out of its own head. The
ledger gains a 'sale' hard-claim group (خصم/عرض/تخفيض/sale/discount/off): an invented 'Summer
Sale' or 'خصم 50%' with no source in the brand's own data is now caught, not waved through as
'no falsifiable claim'."""
from __future__ import annotations

from grounding.ledger import EvidenceLedger

_PROFILE = {
    "source_url": {"value": "https://brand.example/"},
    "name": {"value": "Baseera Clinic", "evidence": [], "confidence": "high",
             "source_type": "extracted"},
    "value_propositions": [],
    "offerings": [{"name": {"value": "جلسة ليزر",
                            "evidence": [{"page_url": "https://brand.example/p",
                                          "quote": "جلسة ليزر"}]},
                   "price_text": {"value": "خصم 20% على جلسة الليزر هذا الشهر",
                                  "evidence": [{"page_url": "https://brand.example/offers",
                                                "quote": "خصم 20% على جلسة الليزر هذا الشهر"}]}}],
}


def test_invented_sale_is_flagged_unsourced():
    # THE core ruling: a brand with NO documented offer anywhere -> ANY sale word is rejected.
    bare = {"source_url": {"value": "https://brand.example/"},
            "name": {"value": "Baseera Clinic", "evidence": [], "confidence": "high",
                     "source_type": "extracted"},
            "value_propositions": [], "offerings": []}
    led = EvidenceLedger.from_profile(bare)
    verdicts = led.audit_text("عرض الصيف: خصم 50% على كل الخدمات")
    sale = [v for v in verdicts if v.claim.kind == "sale"]
    assert sale and all(not v.sourced for v in sale)             # invented promo -> REJECTED


def test_wrong_percentage_on_a_real_offer_still_fails_on_the_number():
    # Brand documents خصم 20% — copy claims 50%. The sale WORD resolves against the real offer,
    # but the invented NUMBER cannot (same-kind number evidence required) -> the line still fails.
    led = EvidenceLedger.from_profile(_PROFILE)
    verdicts = led.audit_text("خصم 50% على كل الخدمات")
    assert any(v.claim.kind == "number" and not v.sourced for v in verdicts)


def test_real_documented_discount_passes():
    led = EvidenceLedger.from_profile(_PROFILE)
    verdicts = led.audit_text("خصم 20% على جلسة الليزر هذا الشهر")   # verbatim from the brand's page
    sale = [v for v in verdicts if v.claim.kind == "sale"]
    assert sale and all(v.sourced for v in sale)                 # documented offer survives


def test_copy_without_any_offer_stays_clean():
    led = EvidenceLedger.from_profile(_PROFILE)
    verdicts = led.audit_text("جلسة ليزر بأحدث الأجهزة")
    assert not [v for v in verdicts if v.claim.kind == "sale"]   # no offer words -> no sale claim
