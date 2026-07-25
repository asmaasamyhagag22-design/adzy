# -*- coding: utf-8 -*-
"""Language detection must only ever see PROSE — token dumps are not a language.

MEASURED (nanocare, 2026-07-24): the site's login page renders a phone country-picker,
so its visible text is dominated by "Saudi Arabia · SA · SAU · UAE · AE · ARE · …" —
hundreds of country names and ISO codes. langdetect labels that token salad CATALAN with
probability 1.0 (Andorra/Argentina/Aruba read as Romance), and one such page out of 15
was enough to ship `languages=['ar','ca']` for an Egyptian brand. The page's actual
Arabic prose («تسجيل الدخول، أدخل رقم واتساب…») was the minority of the sample.

Universal failure class (D-8): country pickers, currency lists and shipping dropdowns
exist on ecommerce sites in EVERY market — nothing here is Egypt- or Catalan-specific.
The root fix filters the SAMPLE, not the language list: lines that are separator-joined
token dumps (ISO-code density, bullet density) are stripped before detection; whatever
prose remains votes. A page left with no prose contributes nothing — no signal beats a
confident wrong one. Hermetic (langdetect is seeded, and post-filter the detector only
ever sees unambiguous prose)."""
from __future__ import annotations

from scraper.language import _prose_only, detect_languages

# The real page shape, abbreviated — Arabic UI prose + the country-picker dump.
_COUNTRY_DUMP = (
    "EG · Unauthorized · Saudi Arabia · SA · SAU · UAE · AE · ARE · Kuwait · KW · KWT · "
    "Oman · OM · OMN · Bahrain · BH · BHR · Egypt · EGY · Qatar · QA · QAT · Jordan · JO · "
    "JOR · Lebanon · LB · LBN · Tunisia · TN · TUN · Morocco · MA · MAR · Libya · LY · LBY · "
    "Afghanistan · AF · AFG · Albania · AL · ALB · Algeria · DZ · DZA · American Samoa · AS · "
    "ASM · Andorra · AD · AND · Angola · AO · AGO · Anguilla · AI · AIA · Argentina · AR · "
    "ARG · Armenia · AM · ARM · Aruba · AW · ABW · Australia · AU · AUS · Austria · AT · AUT"
)
_AUTH_PAGE = (
    "تسجيل الدخول\n\nأدخل رقم واتساب للحصول على رمز التحقق.\n\nرقم واتساب\n\n+20\n\n"
    "إرسال رمز التحقق عبر واتساب\n\nصنع بواسطة\n" + _COUNTRY_DUMP
)
_ARABIC_PAGE = (
    "نعتمد بشكل أساسي على تقنية الليبوسومال. هذه التقنية هي القلب النابض لمنتجاتنا، "
    "حيث تحمي المادة الفعالة وتضمن وصولها إلى خلايا جسمك بأعلى كفاءة ممكنة. "
    "منتجاتنا مصممة لتناسب احتياجاتك اليومية من الفيتامينات والمعادن."
)


def test_the_code_dump_line_is_stripped_and_the_prose_survives():
    kept = _prose_only(_AUTH_PAGE)
    assert "تسجيل الدخول" in kept
    assert "واتساب" in kept
    assert "SAU" not in kept and "Andorra" not in kept, "the token dump reached the detector"


def test_an_auth_page_with_a_country_picker_votes_its_actual_language():
    entries = detect_languages([_AUTH_PAGE])
    codes = [e.code for e in entries]
    assert codes == ["ar"], f"phantom language from a country dropdown: {codes}"


def test_the_nanocare_site_shape_yields_arabic_only():
    # 14 Arabic pages + the picker-dominated login page — the real site's shape.
    entries = detect_languages([_ARABIC_PAGE] * 14 + [_AUTH_PAGE])
    codes = [e.code for e in entries]
    assert codes == ["ar"], f"languages={codes} — the login page still injects a phantom"


def test_real_bilingual_prose_keeps_both_languages():
    """The filter must strip token dumps, not a brand's genuine second language."""
    english = ("We rely primarily on liposomal technology. It protects the active "
               "ingredient and ensures it reaches your cells with the highest possible "
               "efficiency. Our products are designed for your daily needs.")
    entries = detect_languages([_ARABIC_PAGE] * 3 + [english] * 3)
    codes = {e.code for e in entries}
    assert {"ar", "en"} <= codes, f"a genuine second language was eaten: {codes}"


def test_a_page_that_is_all_dump_contributes_nothing():
    entries = detect_languages([_COUNTRY_DUMP])
    assert entries == [], "no signal must beat a confidently wrong one"
