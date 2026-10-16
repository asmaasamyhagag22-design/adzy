# -*- coding: utf-8 -*-
"""The Trend toggle and the social caption — the owner's posting workflow.

OWNER (2026-07-25):
  «عايزاه يبقى اوبشن — عايزة بوستر او ريل متأثر بالترند اضغط قبلها على ترند»
  «عايزة فوق الريل والبوست يبقى مكتوب كلام زي السوشيال ميديا بالظبط لأن خلاص هبدأ أبوست»
  «ويبقى في اوبشن للكونتنت بس من غير ريل ولا صورة»

WHAT ALREADY EXISTS AND WAS UNPLUGGED (the day's recurring shape):
  * `generate_poster(trend_context=)` has flowed into the concept + research prompts all along
    (poster/pipeline.py:1094 -> concept.py:324 -> brand_research.py:183). NOTHING in the webapp
    passes it — the trends were serialized into `swot.trends` earlier today, with sources, and
    stop there.
  * Captions: the only "social copy" in the product is one naive hashtag in the calendar
    (webapp_analysis.py: `#<brandname>`).

Hermetic: renderer and model stubbed; no network, no spend.
"""
from __future__ import annotations

import inspect

import pytest


# =====================================================================
# 1) the Trend toggle
# =====================================================================

@pytest.fixture
def _brand():
    return {"domain_key": "shop_test", "organization_name": "Shop",
            "swot": {"trends": [
                {"title": "نمو سوق المكملات في مصر", "url": "https://reuters.test/a",
                 "matched_terms": ["مكملات", "مصر"]},
                {"title": "Second trend", "url": "https://x.test/b", "matched_terms": ["x"]},
            ]},
            "_profile": {"name": "Shop", "category": "ecommerce",
                         "source_url": "https://shop.test/"}}


@pytest.fixture
def _capture(monkeypatch):
    seen = {}

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = None

    import poster.pipeline as pp
    monkeypatch.setattr(pp, "generate_poster", lambda profile, **kw: (seen.update(kw), _Res())[1])
    import api.webapp_images as wi
    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **k: object(), raising=False)
    return seen


def test_the_toggle_passes_the_stored_trend_to_the_poster(_brand, _capture):
    import api.webapp_images as wi
    wi.generate_image(_brand, headline="h", trend=True)
    ctx = _capture.get("trend_context") or ""
    assert "نمو سوق المكملات" in ctx, f"the stored trend never reached the renderer: {ctx!r}"


def test_the_response_names_the_trend_that_was_used(_brand, _capture):
    """«اتأثر بإيه بالظبط» — the owner must see WHICH signal shaped the creative, with its source."""
    import api.webapp_images as wi
    out = wi.generate_image(_brand, headline="h", trend=True)
    used = out.get("trend_used") or {}
    assert used.get("title") == "نمو سوق المكملات في مصر"
    assert used.get("url", "").startswith("http")


def test_no_toggle_means_no_trend(_brand, _capture):
    import api.webapp_images as wi
    out = wi.generate_image(_brand, headline="h")
    assert not _capture.get("trend_context")
    assert not out.get("trend_used")


def test_a_brand_with_no_stored_trends_says_so_instead_of_inventing(_capture):
    import api.webapp_images as wi
    brand = {"domain_key": "x", "organization_name": "X", "_profile": {"name": "X"}}
    out = wi.generate_image(brand, headline="h", trend=True)
    assert not _capture.get("trend_context"), "a trend was invented from nothing"
    assert out.get("trend_note"), "the toggle silently did nothing — the owner deserves a reason"


def test_the_route_accepts_the_toggle():
    import api.routes.generate as g
    src = inspect.getsource(g)
    assert "trend" in src.lower()


def test_the_frontend_has_the_trend_toggle():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "studio-trend" in html, "no Trend control on the page"


# =====================================================================
# 2) the social caption + content-only mode
# =====================================================================

def _fake_caller(**responses):
    def caller(system, user, schema, **kw):
        return schema(**responses), {"total_tokens": 10}
    return caller


def _ef(value, quote=None):
    return {
        "value": value,
        "evidence": [{
            "block_id": "synthetic_1",
            "page_url": "https://synthetic.test/about",
            "quote": quote or value,
            "extractor": "llm",
        }],
        "confidence": "high",
        "source_type": "extracted",
    }


def _grounded_profile():
    return {
        "source_url": "https://synthetic.test/",
        "name": _ef("Synthetic Studio"),
        "tagline": _ef("Ideas for everyday moments"),
        "description": _ef("We serve 500 customers every year"),
        "languages": ["en"],
        "value_propositions": [],
        "offerings": [],
    }


def test_a_caption_reads_like_social_media(monkeypatch):
    from api.webapp_social_copy import social_caption
    out = social_caption(
        {"name": "Shop", "category": "ecommerce",
         "value_propositions": ["Fast delivery"], "source_url": "https://shop.test/"},
        language="ar",
        caller=_fake_caller(hook="خلّي يومك أحلى 🔥", body="توصيل سريع لحد باب البيت.",
                            cta="اطلب دلوقتي", hashtags=["#شوب", "#توصيل_سريع"]))
    text = out.get("caption") or ""
    assert "خلّي يومك أحلى" in text and "اطلب دلوقتي" in text
    assert any(h.startswith("#") for h in out.get("hashtags") or [])


def test_unsupported_hard_claim_in_hook_is_removed_from_final_caption():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="The only certified synthetic studio",
            body="Make space for better ideas.",
            cta="Discover more",
            hashtags=["#SyntheticStudio"],
        ),
    )
    assert "only" not in out["hook"].lower()
    assert "certified" not in out["hook"].lower()
    assert "only" not in out["caption"].lower()
    assert "certified" not in out["caption"].lower()


def test_unsupported_body_sentence_is_removed_but_creative_sentence_remains():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="Make today feel brighter",
            body="Create something memorable. Save 77% today.",
            cta="Discover more",
            hashtags=["#SyntheticStudio"],
        ),
    )
    assert "Create something memorable" in out["body"]
    assert "77%" not in out["body"]
    assert "77%" not in out["caption"]


def test_unsupported_hard_claim_in_cta_is_removed_from_final_caption():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="Make today feel brighter",
            body="Create something memorable.",
            cta="Join 9000 customers today",
            hashtags=["#SyntheticStudio"],
        ),
    )
    assert "9000" not in out["cta"]
    assert "9000" not in out["caption"]


def test_claims_distributed_across_every_generated_field_do_not_survive():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="#1 synthetic studio",
            body="Save 77% today.",
            cta="Join 9000 customers today",
            hashtags=["#Certified", "#SyntheticStudio"],
        ),
    )
    rejected_tokens = ("#1", "77%", "9000", "certified")
    assert all(token not in out["caption"].lower() for token in rejected_tokens)
    assert all(token not in " ".join(out["hashtags"]).lower() for token in rejected_tokens)


def test_supported_factual_claim_remains_in_final_caption():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="Make today feel brighter",
            body="Serving 500 customers every year.",
            cta="Discover more",
            hashtags=["#SyntheticStudio"],
        ),
    )
    assert "500 customers" in out["body"]
    assert "500 customers" in out["caption"]


def test_harmless_creative_copy_and_small_non_claim_number_remain():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="Make today feel brighter",
            body="Three moods, one creative spark. 3 ideas to explore.",
            cta="See what inspires you",
            hashtags=["#SyntheticStudio", "#EverydayIdeas"],
        ),
    )
    assert out["hook"] == "Make today feel brighter"
    assert "3 ideas to explore" in out["body"]
    assert out["cta"] == "See what inspires you"


@pytest.mark.parametrize(
    ("field", "claim", "rejected_token"),
    [
        ("hook", "Save ٨٨٪ today", "٨٨"),
        ("body", "انضم إلى ٩٠٠٠ عميل اليوم.", "٩٠٠٠"),
        ("cta", "Join 9000 customers اليوم", "9000"),
        ("hook", "The best synthetic studio", "best"),
        ("cta", "The only certified synthetic studio", "certified"),
    ],
)
def test_adversarial_hard_claim_variants_do_not_survive(field, claim, rejected_token):
    from api.webapp_social_copy import social_caption
    generated = {
        "hook": "Make today feel brighter",
        "body": "Create something memorable.",
        "cta": "Discover more",
        "hashtags": ["#SyntheticStudio"],
    }
    generated[field] = claim

    out = social_caption(
        _grounded_profile(), language="en", caller=_fake_caller(**generated),
    )

    assert rejected_token.lower() not in out["caption"].lower()


def test_hard_claim_cannot_hide_inside_compound_hashtag():
    from api.webapp_social_copy import social_caption
    out = social_caption(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="Make today feel brighter",
            body="Create something memorable.",
            cta="Discover more",
            hashtags=["#OnlyCertified", "#Best_Synthetic_Studio", "#SyntheticStudio"],
        ),
    )

    lowered = out["caption"].lower()
    assert "onlycertified" not in lowered
    assert "best_synthetic_studio" not in lowered


def test_no_model_caller_uses_trusted_profile_fallback(monkeypatch):
    import api.webapp_social_copy as sc
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **kwargs: None)

    out = sc.social_caption(_grounded_profile(), language="en", caller=None)

    assert out["caption"] == "Ideas for everyday moments"
    assert out["body"] == "Ideas for everyday moments"
    assert out["hook"] == "" and out["cta"] == ""


def test_final_assembled_caption_contains_no_rejected_hard_claim():
    from api.webapp_social_copy import social_caption
    from grounding import EvidenceLedger
    profile = _grounded_profile()
    out = social_caption(
        profile, language="en",
        caller=_fake_caller(
            hook="The best and only certified studio",
            body="Save 77% today. Create with confidence.",
            cta="Join 9000 customers",
            hashtags=["#1", "#SyntheticStudio"],
        ),
    )

    verdicts = EvidenceLedger.from_profile(profile).audit_text(out["caption"])
    assert not any(not verdict.sourced for verdict in verdicts)


def test_content_only_customer_response_uses_the_same_grounded_caption():
    import api.webapp_social_copy as sc
    out = sc.content_only(
        _grounded_profile(), language="en",
        caller=_fake_caller(
            hook="The only certified studio",
            body="Save 77% today.",
            cta="Join 9000 customers",
            hashtags=["#1", "#SyntheticStudio"],
        ),
    )
    assert out["content_only"] is True
    assert all(token not in out["caption"].lower()
               for token in ("only", "certified", "77%", "9000", "#1"))


def test_content_only_produces_no_image_and_spends_nothing_on_render(monkeypatch):
    import api.webapp_social_copy as sc
    called = {"render": 0}
    import poster.pipeline as pp
    monkeypatch.setattr(pp, "generate_poster",
                        lambda *a, **k: called.__setitem__("render", called["render"] + 1))
    out = sc.content_only(
        {"name": "Shop", "category": "ecommerce", "source_url": "https://shop.test/"},
        language="ar",
        caller=_fake_caller(hook="هوك", body="جسم", cta="سي تي ايه", hashtags=["#تاج"]))
    assert out.get("caption")
    assert "image_b64" not in out
    assert called["render"] == 0, "content-only rendered an image anyway"


def test_every_generated_image_carries_a_caption(monkeypatch, _brand, _capture):
    """«فوق الريل والبوست يبقى مكتوب كلام زي السوشيال» — the caption rides WITH the creative."""
    import api.webapp_images as wi
    import api.webapp_social_copy as sc
    monkeypatch.setattr(sc, "social_caption",
                        lambda profile, **k: {"caption": "كابشن جاهز", "hashtags": ["#براند"]})
    out = wi.generate_image(_brand, headline="h")
    assert out.get("social_caption"), "the poster arrived with no caption to post it with"


def test_the_frontend_shows_the_caption_with_a_copy_button():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "gen-caption" in html, "no caption block above the creative"
    assert "copyCaption" in html, "no copy button — the owner is about to start posting"


def test_the_frontend_has_a_content_only_mode():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "content_only" in html or "contentOnly" in html
