# -*- coding: utf-8 -*-
"""F-06: language, market, and Arabic register are independent facts.

Hermetic only: injected model callers and the local FastAPI TestClient.  No provider, browser,
network, paid image, or reel execution.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _profile(*, language: str, country: str = "", source_url: str = "https://brand.example/") -> dict:
    locations = [{"country": country}] if country else []
    return {
        "name": {"value": "علامة تجريبية" if language == "ar" else "Synthetic Brand"},
        "tagline": {"value": "أفكار لكل يوم" if language == "ar" else "Ideas for every day"},
        "description": {"value": "مساحة للإبداع" if language == "ar" else "A creative space"},
        "languages": [language],
        "locations": locations,
        "source_url": source_url,
        "offerings": [],
        "value_propositions": [],
    }


class _CaptionCaller:
    def __init__(self):
        self.system = ""

    def __call__(self, system, user, response_model, **kwargs):
        self.system = system
        return response_model(
            hook="لحظة ملهمة",
            body="أفكار لكل يوم.",
            cta="اكتشف المزيد",
            hashtags=["#علامة_تجريبية"],
        ), {"total_tokens": 0}


@pytest.mark.parametrize(
    ("country", "source_url", "expected", "forbidden"),
    [
        ("Saudi Arabia", "https://brand.example/", "Khaleeji", "Egyptian"),
        ("the UAE", "https://brand.example/", "Khaleeji", "Egyptian"),
        ("Egypt", "https://brand.example/", "Egyptian", "Khaleeji"),
        ("", "https://brand.example/", "conversational Arabic", "Egyptian"),
    ],
)
def test_social_caption_prompt_keeps_arabic_register_separate_from_country(
    country, source_url, expected, forbidden
):
    """Capture the real social-caption model boundary, not a helper implementation detail."""
    from api.webapp_social_copy import social_caption

    caller = _CaptionCaller()
    out = social_caption(
        _profile(language="ar", country=country, source_url=source_url),
        language="ar",
        caller=caller,
    )

    assert out["language"] == "ar"
    assert expected.lower() in caller.system.lower()
    assert forbidden.lower() not in caller.system.lower()


@pytest.mark.parametrize("country", ["Egypt", "Saudi Arabia"])
def test_explicit_english_remains_english_in_an_arabic_market(country):
    from api.webapp_social_copy import social_caption

    caller = _CaptionCaller()
    out = social_caption(_profile(language="ar", country=country), language="en", caller=caller)

    assert out["language"] == "en"
    assert "English" in caller.system
    assert "Egyptian" not in caller.system and "Khaleeji" not in caller.system


def test_country_resolver_uses_structured_location_without_inferring_from_arabic():
    from poster.locale import country_of

    assert country_of(_profile(language="ar", country="Saudi Arabia")) == "Saudi Arabia"
    assert country_of(_profile(language="ar", country="the UAE")) == "the UAE"
    assert country_of(_profile(language="ar", country="Egypt")) == "Egypt"
    assert country_of(_profile(language="ar")) == ""


def test_script_and_minority_language_do_not_override_the_measured_content_language():
    from poster.concept import brand_is_arabic

    # The first measured language is dominant. Arabic in a bilingual name or minority page does
    # not silently switch the complete creative to Arabic.
    bilingual = _profile(language="en", country="Saudi Arabia")
    bilingual["languages"] = ["en", "ar"]
    bilingual["name"] = {"value": "علامة Synthetic"}
    assert brand_is_arabic(bilingual) is False

    # An old artifact with Arabic glyphs but no language measurement remains unknown; script is
    # not a locale declaration and, critically, cannot become an Egypt signal.
    old = {"name": {"value": "علامة Synthetic"}, "description": {"value": "quoted: أهلاً"}}
    assert brand_is_arabic(old) is False

    dominant_ar = _profile(language="ar", country="Saudi Arabia")
    dominant_ar["languages"] = ["ar", "en"]
    assert brand_is_arabic(dominant_ar) is True

    declared = {"name": {"value": "Synthetic Brand"}, "declared_languages": ["ar"]}
    assert brand_is_arabic(declared) is True

    ambiguous_editions = {
        "name": {"value": "Synthetic Brand"},
        "declared_languages": ["ar", "en"],
    }
    assert brand_is_arabic(ambiguous_editions) is False


@pytest.mark.parametrize("country", ["Egypt", "Saudi Arabia"])
def test_auto_language_uses_dominant_content_not_market_or_incidental_script(country):
    from api.webapp_social_copy import social_caption

    profile = _profile(language="en", country=country)
    profile["name"] = {"value": "علامة Synthetic"}
    profile["description"] = {"value": "English copy quoting أهلاً once"}
    profile["languages"] = ["en", "ar"]
    caller = _CaptionCaller()

    out = social_caption(profile, language="auto", caller=caller)

    assert out["language"] == "en"
    assert "English" in caller.system
    assert "Egyptian" not in caller.system and "Khaleeji" not in caller.system


def test_explicit_arabic_overrides_inferred_english_but_not_the_saudi_market():
    from api.webapp_social_copy import social_caption

    caller = _CaptionCaller()
    out = social_caption(
        _profile(language="en", country="Saudi Arabia"),
        language="ar",
        caller=caller,
    )

    assert out["language"] == "ar"
    assert "Khaleeji" in caller.system and "Egyptian" not in caller.system


@pytest.mark.parametrize(
    ("profile", "expected", "forbidden"),
    [
        (_profile(language="en", country="Egypt", source_url="https://brand.eg/"),
         "LANGUAGE: English", "write EVERY topic, angle and hook in Arabic"),
        (_profile(language="ar", country="Saudi Arabia", source_url="https://brand.sa/"),
         "Khaleeji", "Egyptian"),
        (_profile(language="ar", source_url="https://brand.example/"),
         "conversational Arabic", "Egyptian"),
    ],
)
def test_strategy_prompt_keeps_content_language_independent_from_market(profile, expected, forbidden):
    from strategy.builder import _llm_plan

    captured = {"system": ""}

    def caller(system, user, response_model, **kwargs):
        captured["system"] = system
        return response_model(items=[]), {"total_tokens": 0}

    _llm_plan(profile, caller, days=7, platforms=["instagram"], trends=[], target=1)

    assert expected in captured["system"]
    assert forbidden not in captured["system"]


def test_strategy_old_artifact_with_incidental_arabic_script_does_not_become_egyptian():
    from strategy.builder import _llm_plan

    captured = {"system": ""}

    def caller(system, user, response_model, **kwargs):
        captured["system"] = system
        return response_model(items=[]), {"total_tokens": 0}

    _llm_plan(
        {"name": {"value": "Synthetic علامة"}, "description": {"value": "Quote: أهلاً"}},
        caller,
        days=7,
        platforms=["instagram"],
        trends=[],
        target=1,
    )

    assert "LANGUAGE: English" in captured["system"]
    assert "Egyptian" not in captured["system"]


def _concept_response():
    from poster.concept import _ConceptResponse

    return _ConceptResponse(
        audience="عملاء",
        single_message="فكرة واضحة",
        core_benefit="بساطة",
        emotional_tone="دافئ",
        visual_idea="minimal warm studio scene",
        proof_points=["فكرة يومية"],
        headline="فكرة كل يوم",
        subheadline="مساحة للإبداع",
        cta="اكتشف المزيد",
    )


@pytest.mark.parametrize(
    ("country", "source_url", "expected", "forbidden"),
    [
        ("Saudi Arabia", "https://brand.sa/", "Khaleeji", "Egyptian"),
        ("the UAE", "https://brand.ae/", "Khaleeji", "Egyptian"),
        ("Egypt", "https://brand.eg/", "Egyptian", "Khaleeji"),
        ("", "https://brand.example/", "conversational Arabic", "Egyptian"),
    ],
)
def test_poster_copy_qa_cannot_reinfer_egypt_after_the_concept_prompt(
    monkeypatch, country, source_url, expected, forbidden
):
    """Both actual model boundaries must receive the same evidenced/neutral register."""
    from poster.concept import build_creative_concept
    import business_profile.llm as llm

    captured = {"concept": "", "copy_sense": ""}

    def concept_caller(system, user, response_model, **kwargs):
        captured["concept"] = system
        return _concept_response(), {"total_tokens": 0}

    def sense_caller(system, user, response_model, **kwargs):
        captured["copy_sense"] = system
        fields = [
            {"field": name, "natural": True, "issue": "", "rewrite": ""}
            for name in ("headline", "subheadline", "cta")
        ]
        return response_model(fields=fields), {"total_tokens": 0}

    monkeypatch.setattr(llm, "default_caller", lambda **kwargs: sense_caller)
    build_creative_concept(
        _profile(language="ar", country=country, source_url=source_url),
        caller=concept_caller,
        arabic=True,
        max_retries=0,
    )

    for boundary in ("concept", "copy_sense"):
        assert expected.lower() in captured[boundary].lower(), boundary
        assert forbidden.lower() not in captured[boundary].lower(), boundary


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "f06-test-secret")
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)
    import api.user_auth as ua

    ua._SECRET_CACHE = None
    from api.main import app

    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path", ["/api/generate/upload", "/api/generate/reference"])
def test_explicit_owner_language_survives_multipart_api_paths(local_client, monkeypatch, path):
    """The observable route contract: a selected language reaches the generation boundary."""
    from api.brand_store import BrandStore
    import api.routes.generate as route

    token = local_client.post(
        "/api/auth/signup",
        json={"email": "f06@example.test", "password": "password123"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    owner_id = local_client.get("/api/auth/me", headers=headers).json()["id"]
    BrandStore().save(
        {
            "domain_key": "synthetic_test",
            "organization_name": "علامة تجريبية",
            "_profile": _profile(language="ar", country="Egypt"),
        },
        owner_id=owner_id,
    )

    monkeypatch.setattr(
        route,
        "_emit",
        lambda brand, **kwargs: {"language_at_generation": kwargs.get("language")},
    )
    response = local_client.post(
        path,
        data={"domain_key": "synthetic_test", "language": "en"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["language_at_generation"] == "en"
