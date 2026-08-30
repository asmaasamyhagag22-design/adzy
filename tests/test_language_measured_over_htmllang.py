"""P-67: measured body-text language must dominate the <html lang> attribute.

`<html lang="en-US">` is a common template default that mislabels non-English sites (elkbabgi, an
Arabic-first restaurant, recorded languages:["en"] — the root of P-20's forced-Egyptian-Arabic
calendar). The old code counted each html-lang hint as +1 vs langdetect's 0..1, so boilerplate
lang tags outvoted measured content. Now langdetect is authoritative and html lang is a capped
tie-breaker (or the sole signal only when nothing is detectable).
"""
from __future__ import annotations

from scraper.language import detect_languages

_AR = ("قصر الكبابجي أكبر قصر للمشويات في مصر. فروعنا في مدينتي والشيخ زايد والتجمع الخامس "
       "والساحل الشمالي. احجز طاولتك الآن واستمتع بأشهى المشويات الفاخرة.")
_EN = ("Qasr ElKbabgi is the largest grill palace in Egypt with branches in Madinaty, Sheikh "
       "Zayed and the North Coast. Book your table now and enjoy our premium grills.")


def test_arabic_content_beats_many_html_lang_en_hints():
    res = detect_languages([_AR, _AR], html_lang_hints=["en"] * 8)
    assert res, "should detect a language"
    assert res[0].code == "ar", [(e.code, round(e.proportion, 3)) for e in res]


def test_english_content_beats_html_lang_ar_hints():
    # Symmetric: the fix is not Arabic-biased — measured English wins over lang="ar" tags.
    res = detect_languages([_EN, _EN], html_lang_hints=["ar"] * 8)
    assert res[0].code == "en", [(e.code, round(e.proportion, 3)) for e in res]


def test_html_lang_is_the_fallback_when_no_text_is_detectable():
    # No body text (e.g. a JS shell that still declared a lang) -> html lang is the only signal.
    res = detect_languages(["", "  "], html_lang_hints=["tr", "tr", "en"])
    codes = [e.code for e in res]
    assert codes[0] == "tr", codes


def test_genuinely_bilingual_content_keeps_both_from_measurement():
    res = detect_languages([_AR, _EN], html_lang_hints=["en"])
    codes = {e.code for e in res}
    assert {"ar", "en"} <= codes, codes
