# -*- coding: utf-8 -*-
"""The same brand, through two doors, must get the same film.

Every defect in this file is the same shape: the DASHBOARD and the CLI disagreed about something,
and nobody noticed because each side was tested against itself.

1. AUDIENCE LANGUAGE. `reel/audience.py::_country_code` read a flat `country` field and a ccTLD;
   `poster/locale.py::country_of` reads the profile's own evidence — phone numbers, addresses,
   currency — and is what the poster and the seed casting already trust. MEASURED on AlFouad: the
   CLI's saved profile carries `country: "eg"` and resolved to Arabic, while the SAME BRAND as the
   dashboard stores it has no `country` field, a `.com` domain and `languages: ['en']` — and
   resolved to ENGLISH. That is the owner's July report verbatim: «دار الفوائد صيدلية في مصر
   وعندها عربي — عملت إنجليزي ليه؟». `country_of` answers "Egypt" for both.

2. SCENE COUNT. The browser posts a hardcoded `n_scenes: 5` and `/api/reel/from-profile` used it
   verbatim, while `reel/__main__.py::_scenes_for` derives it from the length the film is cut to
   and from whether the last beat is a rendered product insert. One product, two shapes, decided
   by which door the caller came through.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from reel.audience import audience_language_for


# =====================================================================
# one country resolver
# =====================================================================

def test_a_pharmacy_in_egypt_speaks_arabic_even_with_an_english_storefront():
    """The dashboard's stored shape: no `country` field, a .com domain, an English crawl."""
    lang, country, why = audience_language_for({
        "name": "AlFouad Pharmacies",
        "source_url": "https://alfouadpharmacies.com/",
        "languages": ["en"],
        "contact_channels": {"phones": [{"e164": "+201234567890"}]},
        "locations": [{"city": "Cairo", "country": "Egypt"}],
    })
    assert lang == "Arabic", why
    assert country == "eg"


def test_the_flat_country_field_still_wins_when_it_is_there():
    """The cheap lookup stays first — `country_of` is the fallback, not a replacement."""
    lang, country, _why = audience_language_for({"country": "sa", "languages": ["en"]})
    assert (lang, country) == ("Arabic", "sa")


def test_a_genuinely_global_com_is_not_forced_into_a_market():
    """The fallback must not invent a country. A brand with no market evidence has none."""
    lang, country, why = audience_language_for({
        "name": "Pepsi", "source_url": "https://pepsi.com/", "languages": ["en"]})
    assert country == ""
    assert lang == "English", why


def test_the_two_resolvers_agree_on_every_stored_brand():
    """The invariant, run over the real brand store: whatever `country_of` names, the audience
    resolver must not contradict by falling through to English."""
    from poster.locale import country_of
    try:
        from api.brand_store import BrandStore
        brands = BrandStore().list()
    except Exception:  # noqa: BLE001
        pytest.skip("no brand store on this machine")
    if not brands:
        pytest.skip("no brands stored")
    for b in brands:
        p = b.get("_profile") or {}
        named = str(country_of(p) or "").strip().lower()
        lang, code, why = audience_language_for(p)
        if "egypt" in named or "saudi" in named:
            assert lang == "Arabic", (
                f"{b.get('domain_key')}: country_of says {named!r} but the audience "
                f"resolver said {lang} ({why})")


def test_the_fallback_reads_the_evidence_resolver_not_a_second_table():
    import ast
    import reel.audience as a
    tree = ast.parse(inspect.getsource(a._country_code))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    assert "country_of" in ast.unparse(tree)


# =====================================================================
# one scene count
# =====================================================================

def test_the_reel_route_derives_the_count_instead_of_trusting_the_browser():
    import api.routes.reel as r
    src = inspect.getsource(r)
    assert "_scenes_for(" in src, "the route no longer derives the scene count"
    assert "n_scenes=max(3, request.n_scenes)" not in src, "the browser's hardcoded 5 is back"


def test_a_fifteen_second_reel_is_four_scenes_through_either_door():
    from reel.__main__ import _scenes_for
    from reel.schemas import ReelFromProfileRequest
    req = ReelFromProfileRequest(profile={})
    assert req.max_seconds == 15.0
    assert _scenes_for(req.max_seconds, req.n_scenes, has_insert=True) == 4
    assert _scenes_for(req.max_seconds, req.n_scenes, has_insert=False) == 3


def test_a_caller_asking_for_fewer_scenes_still_wins():
    from reel.__main__ import _scenes_for
    assert _scenes_for(15.0, 3, has_insert=True) == 3


# =====================================================================
# the preview costs nothing
# =====================================================================

def test_the_preview_never_calls_a_model():
    """A "see what it will cost before you spend" that itself spends is worse than nothing."""
    import ast
    import api.prompt_preview as pp
    tree = ast.parse(inspect.getsource(pp))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree).lower()
    for forbidden in ("design_creative_reel", "generate_oneshot", "default_caller",
                      "generate_content", "build_llm_concept_prompt"):
        assert forbidden not in code, forbidden


def test_the_preview_never_builds_a_brandbook():
    """`load_or_build_dna(profile, None)` with a None caller reads the cache and cannot build —
    building is a Serper + vision spend."""
    src = inspect.getsource(__import__("api.prompt_preview", fromlist=["x"])._dna_for)
    assert "load_or_build_dna(profile, None)" in src


def test_the_preview_says_it_is_free():
    from api.prompt_preview import poster_preview, reel_preview
    assert reel_preview({})["billable"] is False
    assert poster_preview({})["billable"] is False


def test_the_preview_survives_an_empty_profile():
    """It runs before anything is known; a brand with nothing stored must still get an answer."""
    from api.prompt_preview import reel_preview
    out = reel_preview({})
    assert isinstance(out.get("resolved"), dict)
    assert out["kind"] == "reel"


def test_the_preview_reports_the_cost_before_it_is_spent():
    from api.prompt_preview import reel_preview
    prof = Path("outputs/alfouad_profile.json")
    if not prof.is_file():
        pytest.skip("no saved profile")
    r = reel_preview(json.loads(prof.read_text(encoding="utf-8")))["resolved"]
    assert r.get("est_video_usd", 0) > 0
    assert r.get("scenes")
    assert r.get("scene_count_reason")


def test_the_preview_is_wired_to_a_route_and_a_button():
    import api.routes.analysis as a
    assert '@router.get("/prompt-preview/{domain_key}")' in inspect.getsource(a)
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "showPromptPreview()" in html
    assert "/api/prompt-preview/" in html
    assert 'id="prompt-preview"' in html
