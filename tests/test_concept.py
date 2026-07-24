"""Creative Concept layer + the Arabic LANGUAGE LOCK (build brief Steps B/C/D/E).

Hermetic: MockCaller only. No network.
"""
from __future__ import annotations

from poster.concept import (
    CreativeConcept, _ConceptResponse, brand_is_arabic, build_creative_concept, has_latin,
)
from business_profile.llm.caller import MockCaller

_AR = {
    "name": {"value": "WE"},
    "languages": ["en", "ar"],
    "description": {"value": "شركة المصرية للاتصالات تقدم خدمات الموبايل والإنترنت"},
    "offerings": [{"name": "مركز الاتصال المستضاف"}, {"name": "vehicle tracking"}],
}
_EN = {"name": {"value": "Acme"}, "languages": ["en"], "description": {"value": "A US shop"}}


def _resp(**kw) -> _ConceptResponse:
    base = dict(
        audience="عملاء", single_message="اتصال أقوى", core_benefit="سرعة",
        emotional_tone="ثقة", visual_idea="rim-lit subject under purple light beams",
        proof_points=["إنترنت أسرع", "تغطية أوسع"], headline="اتصالك أقوى",
        subheadline="جرّب الفرق", cta="اشترك الآن",
    )
    base.update(kw)
    return _ConceptResponse(**base)


def test_brand_is_arabic_detects_language_and_script():
    assert brand_is_arabic(_AR)
    assert not brand_is_arabic(_EN)


def test_has_latin():
    assert has_latin("Shop now")
    assert not has_latin("تسوق الآن")


def test_arabic_concept_passes_and_keeps_arabic_copy():
    c = build_creative_concept(_AR, caller=MockCaller({"poster_concept_brief": _resp()}))
    assert c.language == "ar"
    assert c.headline == "اتصالك أقوى" and not has_latin(c.headline)
    assert not has_latin(c.cta)
    assert c.proof_points == ["إنترنت أسرع", "تغطية أوسع"]


def test_poster_copy_dialect_follows_the_evidenced_country_not_hardcoded_egyptian():
    """CS-04 (D-8): the poster copy register must follow the brand's EVIDENCED country, exactly as
    the content calendar already does (P-04). It used to hardcode 'Egyptian Arabic' for EVERY Arabic
    brand (concept.py:256), so a Saudi brand's headline/CTA were commissioned in Masri — the same
    'any Arabic => Egyptian' bug D5 killed in the reel, still live in the poster. Assert on the
    SYSTEM PROMPT the LLM actually receives."""
    captured: dict = {}

    class _Rec:
        def __call__(self, system, user, response_model, group_name="", images=None):
            captured["system"] = system
            return _resp(), None

    # Saudi evidence via ccTLD -> country_of -> "Saudi Arabia" -> Gulf/Khaleeji register.
    saudi = {"name": {"value": "متجر"}, "languages": ["ar"], "source_url": "https://brand.com.sa/",
             "description": {"value": "متجر سعودي"}, "offerings": [{"name": "عبايات"}]}
    build_creative_concept(saudi, caller=_Rec(), arabic=True)
    s = captured["system"]
    assert "Khaleeji" in s or "خليجية" in s        # the brand's real register reaches the LLM
    assert "Egyptian" not in s and "مصرية" not in s  # NEVER Egyptian for a Saudi brand

    # And an Egyptian brand still resolves to Egyptian — the map didn't lose Egypt.
    captured.clear()
    egy = {"name": {"value": "متجر"}, "languages": ["ar"], "source_url": "https://brand.com.eg/",
           "description": {"value": "متجر مصري"}, "offerings": [{"name": "منتجات"}]}
    build_creative_concept(egy, caller=_Rec(), arabic=True)
    assert "Egyptian" in captured["system"] or "مصرية" in captured["system"]


def test_concept_prompt_enforces_the_stranger_test_and_grounds_the_visual():
    # Owner: the poster sold a FEELING ('your unfair advantage') + a generic stock woman, never
    # saying what ITI does. The concept prompt must now force ANCHOR-NAME-PROVE (the stranger test)
    # and ground the visual in the real service, banning stock people + faces-in-screens.
    captured: dict = {}

    class _Rec:
        def __call__(self, system, user, response_model, group_name="", images=None):
            captured["system"] = system
            captured["user"] = user
            return _resp(), None

    build_creative_concept(_AR, caller=_Rec())
    s, u = captured["system"], captured["user"]
    assert "STRANGER TEST" in s and "WHAT THEY DO" in s and "SPECIFIC" in s
    assert "anchored on a CONCRETE" in s                       # single_message must name the offering
    assert "staring at the camera holding a laptop" in s       # the exact stock cliché, banned
    assert "rendered INSIDE a screen" in s                     # no faces/text/logos in-scene (the glitch)
    assert "the real world of THIS service" in s               # SELF-CHECK grounds the visual
    # the real offerings reach the prompt so it CAN anchor on a concrete service
    assert "مركز الاتصال" in u


def test_latin_headline_regenerates_then_falls_back_no_latin():
    # The mock returns the SAME Latin headline every call -> retries exhausted -> grounded
    # Arabic fallback. The pipeline NEVER ships Latin on an Arabic brand.
    bad = _resp(headline="Faster connection", cta="Subscribe")
    c = build_creative_concept(_AR, caller=MockCaller({"poster_concept_brief": bad}), max_retries=1)
    assert not has_latin(c.headline)
    assert "fallback" in (c.note or "")


def test_arabic_brand_drops_latin_chips():
    c = build_creative_concept(
        _AR, caller=MockCaller({"poster_concept_brief": _resp(
            proof_points=["إنترنت أسرع", "vehicle tracking"])}))
    assert "إنترنت أسرع" in c.proof_points
    assert all(not has_latin(p) for p in c.proof_points)   # the Latin chip is dropped


def test_no_caller_grounded_fallback_is_arabic():
    c = build_creative_concept(_AR, caller=None)
    assert c.headline and not has_latin(c.headline) and c.language == "ar"


def test_english_brand_keeps_english():
    c = build_creative_concept(
        _EN, caller=MockCaller({"poster_concept_brief": _resp(
            headline="Built for builders", cta="Shop now", proof_points=["Fast", "Simple"])}))
    assert c.language == "en" and c.headline == "Built for builders"


def test_popup_manifest_raw_facts_reach_the_concept_prompt(monkeypatch):
    # Owner law (raw-data-first, 2026-07-12): popup-scraped course text lives in the manifest;
    # generation must read it VERBATIM. Fixture = the real coursesev.php?catID=205 popup family
    # (the modal-resolved modules/2631.html page + the single-word 'fiber' noise page that,
    # measured pre-fix, ate the whole raw-facts budget). Pins the full wiring so no refactor
    # silently re-orphans the manifest: manifest -> _product_raw_facts -> concept prompt.
    from pathlib import Path
    import dashboard.products as dp
    from dashboard.run import _product_raw_facts

    fx = Path(__file__).resolve().parent / "fixtures" / "nti_popup_manifest.json"
    monkeypatch.setattr(dp, "_latest_manifest", lambda slug, scrapes_dir="scrapes": fx)
    rf = _product_raw_facts("nti_sci_eg", "Fiber Networks Essentials")
    assert "Fiber Networks Essentials (72 Hours)" in rf                 # verbatim popup hours
    assert "Optical Fiber Network Planning course is designed" in rf   # verbatim description
    assert "Smart Village campus" not in rf   # 'fiber'-only noise no longer crowds the product out

    captured: dict = {}

    class _Rec:
        def __call__(self, system, user, response_model, group_name="", images=None):
            captured["user"] = user
            return _resp(), None

    build_creative_concept(_AR, caller=_Rec(), raw_facts=rf)
    u = captured["user"]
    assert "REAL PRODUCT-PAGE FACTS" in u                        # the raw-facts block is injected
    assert "Fiber Networks Essentials (72 Hours)" in u           # ...carrying the popup verbatim
    assert "Optical Fiber Network Planning course is designed" in u


def test_concept_prompt_carries_brand_coherence_rule():
    # Owner caught "بطل مذاكرة، ابدأ شغل" shipped for an EDUCATION institute — a hook that negates
    # the brand's own category. CS-120: assert the RUNTIME system prompt actually sent to the LLM
    # carries the hard rule (not just that the string exists somewhere in the module source — that
    # certified wording, not that the rule reaches the model).
    captured: dict = {}

    class _Rec:
        def __call__(self, system, user, response_model, group_name="", images=None):
            captured["system"] = system
            return _resp(), None

    build_creative_concept(_EN, caller=_Rec())
    sys_prompt = captured.get("system", "")
    assert "BRAND-COHERENCE" in sys_prompt
    assert "never disparages studying" in sys_prompt
