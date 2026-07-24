# -*- coding: utf-8 -*-
"""Your differentiator going mainstream is a THREAT, not an opportunity.

MEASURED (nanocare re-run, 2026-07-21). The brand's whole positioning is Liposomal delivery — its
tagline sells "الجيل الجديد من تكنولوجيا المكملات". The run surfaced these market signals and filed
ALL of them as Opportunities:

    "Why Liposomal Technology Is Becoming the Standard"
    "Future of Liposomal Delivery Systems: 2026 Nano-Nutrition"        ("finally replacing tablets")
    "Liposomal Technology: A Breakthrough in Nutrient ..."

The first two say the moat is closing: when the thing that makes you special becomes the standard,
your differentiator stops differentiating. That is the most consequential strategic fact in the
whole run, and it shipped in the Opportunities box.

Threats stayed 0 for an honest reason too — on every measured dimension nanocare is ahead of or
level with its 3 scraped peers — which is exactly when a commoditization signal matters most.
Hermetic."""
from __future__ import annotations

from competitor.swot import derived_threats


class _T:
    def __init__(self, title):
        self.title = title
        self.url = "https://x.example/a"


def test_a_differentiator_becoming_the_standard_is_a_threat():
    out = derived_threats(trends=[_T("Why Liposomal Technology Is Becoming the Standard")],
                          weaknesses=[], maturity=None)
    assert out, "the moat closing is the most consequential signal in the run"
    assert out[0].claim_strength == "directional_not_validated"
    assert "Becoming the Standard" in out[0].evidence


def test_replacement_language_counts_too():
    out = derived_threats(
        trends=[_T("Future of Liposomal Delivery: finally replacing traditional tablets")],
        weaknesses=[], maturity=None)
    assert out


def test_arabic_commoditization_is_caught():
    out = derived_threats(trends=[_T("لماذا أصبحت تقنية الليبوسومال هي المعيار الجديد")],
                          weaknesses=[], maturity=None)
    assert out


def test_a_neutral_science_headline_is_not_a_threat():
    # "A Breakthrough in Nutrient Delivery" is category news, not a moat-closing signal —
    # inventing a threat from it would be the fabrication this whole gate exists to prevent.
    assert derived_threats(trends=[_T("Liposomal Technology: A Breakthrough in Nutrient Delivery")],
                           weaknesses=[], maturity=None) == []


def test_a_growth_signal_still_works():
    out = derived_threats(trends=[_T("Qatar Catering Services Market Size & Forecast")],
                          weaknesses=[], maturity=None)
    assert out


def test_only_one_market_threat_is_emitted():
    out = derived_threats(trends=[_T("Liposomal is becoming the standard"),
                                  _T("Supplements market size doubles")],
                          weaknesses=[], maturity=None)
    assert len([t for t in out if "سوق" in t.text or "معيار" in t.text or "قياسي" in t.text]) <= 2
