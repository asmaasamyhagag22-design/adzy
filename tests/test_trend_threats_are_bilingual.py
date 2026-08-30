# -*- coding: utf-8 -*-
"""§11.3 — an Arabic headwind was filed as an OPPORTUNITY, silently.

`_TREND_THREAT_KEYS` (`competitor/brand_signals.py:155-158`) is sixteen English words, and the
router around it (`:203`) is an if/else with no third branch:

    headwind word in the title  ->  Threat
    anything else               ->  Opportunity   ("Coverage on <terms>: <title>")

So a trend titled «حظر استيراد المكملات الغذائية» — a ban, in the brand's own language — contains
none of the sixteen words and falls through to the else. The client is shown their own category's
regulatory crackdown labelled as an opening.

WHAT MAKES THIS EASY TO MISS: the two OTHER keyword lists in this same pipeline are already
bilingual. `swot.py:89` `_GROWTH_SIGNALS` carries «نمو» «توسع» «استثمار» «افتتاح» «حجم السوق», and
`swot.py:99` `_COMMODITIZATION_SIGNALS` carries «المعيار» «معيار جديد» «أصبحت شائعة» «يحل محل».
The lesson was learned once and this list was left behind — so the defect looks like a decision
when it is an omission.

D-8: the fix adds terms, it does not add a language check. A brand in any market publishing in any
script keeps working; a script we have no terms for degrades to today's behaviour, never worse.

Hermetic: trend objects in, items out. No network, no model.
"""
from __future__ import annotations

from competitor.brand_signals import opportunities_threats_from_trends


class _Trend:
    def __init__(self, title, url="https://www.reuters.com/x", terms=("مكملات",)):
        self.title = title
        self.url = url
        self.matched_terms = terms


def _route(title, **kw):
    """-> ('threat' | 'opportunity' | 'dropped')"""
    opps, threats = opportunities_threats_from_trends({}, [_Trend(title, **kw)])
    if threats:
        return "threat"
    if opps:
        return "opportunity"
    return "dropped"


# --- the Arabic headwinds that were being sold as openings ---------------------------------------

def test_an_arabic_ban_is_a_threat():
    assert _route("حظر استيراد المكملات الغذائية غير المسجلة") == "threat"


def test_an_arabic_regulation_is_a_threat():
    assert _route("تنظيم جديد لتداول المكملات الغذائية في السوق") == "threat"


def test_an_arabic_crisis_is_a_threat():
    assert _route("أزمة نقص المكملات الغذائية تضرب الصيدليات") == "threat"


def test_an_arabic_price_shock_is_a_threat():
    assert _route("ارتفاع أسعار المكملات وتراجع الطلب") == "threat"


def test_an_arabic_recall_is_a_threat():
    assert _route("سحب دفعات من المكملات الغذائية من الأسواق") == "threat"


def test_an_arabic_lawsuit_is_a_threat():
    assert _route("دعوى قضائية ضد شركات المكملات الغذائية") == "threat"


def test_an_arabic_boycott_is_a_threat():
    assert _route("حملة مقاطعة تستهدف علامات المكملات المستوردة") == "threat"


# --- the English list must keep working exactly as before -----------------------------------------

def test_the_english_headwinds_still_route_to_threats():
    for title in ("New regulation on supplements", "Supplement imports banned",
                  "Category decline continues", "Nationwide product recall"):
        assert _route(title) == "threat", title


def test_a_neutral_title_is_still_an_opportunity_in_either_script():
    assert _route("Supplement market overview 2026") == "opportunity"
    assert _route("نظرة على سوق المكملات الغذائية في 2026") == "opportunity"


# --- the guard against over-reach: a word that only LOOKS like a headwind -------------------------

def test_a_positive_arabic_title_is_not_dragged_into_threats():
    """The Arabic terms must be specific enough not to catch ordinary market coverage. «تراجع»
    (decline) is a headwind; «مراجعة» (review) shares letters and is not."""
    assert _route("مراجعة أفضل المكملات الغذائية لهذا العام") == "opportunity"


def test_growth_language_stays_an_opportunity():
    assert _route("نمو سوق المكملات الغذائية بنسبة كبيرة") == "opportunity"


# --- unchanged invariants -------------------------------------------------------------------------

def test_a_trend_with_no_url_is_dropped_whatever_its_language():
    assert _route("حظر استيراد المكملات", url="") == "dropped"


def test_an_off_topic_trend_is_dropped():
    assert _route("حظر استيراد السيارات", terms=()) == "dropped"


def test_a_threat_is_still_graded_as_one_uncorroborated_signal():
    _o, threats = opportunities_threats_from_trends({}, [_Trend("حظر استيراد المكملات الغذائية")])
    assert threats[0].claim_strength == "directional_not_validated"
