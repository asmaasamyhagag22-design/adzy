# -*- coding: utf-8 -*-
"""The real product must be the one in the ad, and the language must be the owner's choice.

OWNER, 2026-07-25:
  «عايزه المنتج يجي ذي ما هو بالظبط ع الويب سايت دي مفيهاش لعب … علشان كدا بستعمل نانو باننا
   لانه مميز ف كدا»
  «يبقا متوفر اوبشن اختيار عربي ولا انجليزي»

1. THE REAL PRODUCT NEVER REACHED THE IMAGE MODEL ON THE PATH THAT ACTUALLY RUNS.

   `grep product_images poster/pipeline.py` returns two call sites, and both are inside
   `_try_oneshot`. The classic overlay path — now the default for every brand, and the only path
   for Arabic — calls `_generate_nano_background`, which attaches brand STYLE references and no
   product at all. So the model was asked for a scene containing a product it had never seen, and
   invented one. Measured, repeatedly, by the pipeline's own critic:

       "The poster features clearly fake, AI-generated products with garbled text"
       "a fabricated product with nonsensical, garbled text on its label"
       "The secondary script on the shopping bag is garbled and illegible"

   Nano Banana is chosen precisely because it PRESERVES a reference image's detail. It cannot
   preserve what it was never given.

2. LANGUAGE WAS INFERRED AND NOT CHOOSABLE. `generate_poster(language=...)` has accepted
   "ar"/"en"/"auto" all along; nothing above it ever passed anything but the default. Al Fouad's
   profile reads `languages: ['en']`, so its poster came out in English while the brand's own
   customers are addressed in Arabic elsewhere. "auto" stays the default — the inference is a good
   default, it just must not be the only option.

Hermetic: the image generator is stubbed; no network, no spend.
"""
from __future__ import annotations

import inspect

import pytest


# --- 1) the real product reaches the model that draws the scene ---------------------------------

def test_the_classic_background_accepts_a_real_product_image():
    import poster.pipeline as pp
    sig = inspect.signature(pp._generate_nano_background)
    assert "product_images" in sig.parameters, (
        "the background generator cannot be given the real product, so it invents one")


def _stub_fetch(monkeypatch, pp):
    """URLs become (bytes, mime) before they are attached, and the suite blocks the network — so
    without this nothing is ever attached and the test measures the net guard, not the wiring.
    The bytes carry the URL so the assertions can tell WHICH image arrived, and in what order."""
    monkeypatch.setattr(pp, "_image_bytes_from_url",
                        lambda u, **k: (str(u).encode("utf-8"), "image/jpeg"))


def test_the_real_product_is_forwarded_to_the_image_model(monkeypatch, tmp_path):
    """The whole point of Nano Banana here is detail preservation from a reference."""
    import poster.oneshot as oneshot
    import poster.pipeline as pp
    _stub_fetch(monkeypatch, pp)
    seen = {}

    def _fake(prompt, out_path, **kw):
        seen.update(kw)
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG" + b"0" * 32)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", _fake)
    pp._generate_nano_background({"name": "X"}, None, "a scene", out_dir=str(tmp_path),
                                 product_images=["https://shop.test/p.jpg"])
    refs = seen.get("product_images") or []
    assert any("shop.test/p.jpg" in str(r) for r in refs), (
        f"the real product was not attached: {refs}")


def test_the_product_leads_the_reference_list(monkeypatch, tmp_path):
    """`poster/oneshot.py` caps attachments at two. A brand style swatch must never push the
    actual product out of the list."""
    import poster.oneshot as oneshot
    import poster.pipeline as pp
    _stub_fetch(monkeypatch, pp)
    seen = {}

    def _fake(prompt, out_path, **kw):
        seen.update(kw)
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG" + b"0" * 32)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", _fake)
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: ["style1.jpg", "style2.jpg"])
    pp._generate_nano_background({"name": "X"}, None, "a scene", out_dir=str(tmp_path),
                                 product_images=["https://shop.test/real.jpg"])
    refs = [str(r) for r in (seen.get("product_images") or [])]
    assert refs and "real.jpg" in refs[0], f"a style swatch outranked the real product: {refs}"


def test_the_prompt_tells_the_model_to_keep_the_product_exact(monkeypatch, tmp_path):
    """«دي مفيهاش لعب» — the attached item is the product, not inspiration for one."""
    import poster.oneshot as oneshot
    import poster.pipeline as pp
    _stub_fetch(monkeypatch, pp)
    seen = {}

    def _fake(prompt, out_path, **kw):
        seen["prompt"] = prompt
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG" + b"0" * 32)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", _fake)
    pp._generate_nano_background({"name": "X"}, None, "a scene", out_dir=str(tmp_path),
                                 product_images=["https://shop.test/p.jpg"])
    p = seen.get("prompt", "").lower()
    assert "exact" in p or "unchanged" in p or "do not redesign" in p, (
        "nothing tells the model the attached product must survive intact")


def test_no_product_keeps_the_old_behaviour(monkeypatch, tmp_path):
    import poster.oneshot as oneshot
    import poster.pipeline as pp
    seen = {}

    def _fake(prompt, out_path, **kw):
        seen.update(kw)
        from pathlib import Path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG" + b"0" * 32)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", _fake)
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: [])
    pp._generate_nano_background({"name": "X"}, None, "a scene", out_dir=str(tmp_path))
    assert not (seen.get("product_images") or [])


# --- 2) the owner picks the language ------------------------------------------------------------

def test_the_webapp_accepts_a_language_choice():
    import api.webapp_images as wi
    assert "language" in inspect.signature(wi.generate_image).parameters


def test_the_chosen_language_reaches_the_renderer(monkeypatch):
    import api.webapp_images as wi
    seen = {}

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = None

    import poster.pipeline as pp

    def _fake_generate_poster(profile, **kw):
        seen.update(kw)
        return _Res()

    monkeypatch.setattr(pp, "generate_poster", _fake_generate_poster)
    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **k: object(), raising=False)
    wi.generate_image({"domain_key": "x", "_profile": {"name": "X"}}, headline="h", language="ar")
    assert seen.get("language") == "ar"


def test_auto_stays_the_default(monkeypatch):
    import api.webapp_images as wi
    assert inspect.signature(wi.generate_image).parameters["language"].default == "auto"


def test_the_route_carries_the_choice():
    import api.routes.generate as g
    src = inspect.getsource(g)
    assert "language" in src, "the client has no way to ask for a language"


def test_the_frontend_offers_the_choice():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "studio-language" in html, "no Arabic/English control on the page"


@pytest.mark.parametrize("lang", ["ar", "en", "auto"])
def test_every_accepted_value_passes_through(monkeypatch, lang):
    import api.webapp_images as wi
    seen = {}

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = None

    import poster.pipeline as pp
    monkeypatch.setattr(pp, "generate_poster", lambda profile, **kw: (seen.update(kw), _Res())[1])
    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **k: object(), raising=False)
    wi.generate_image({"domain_key": "x", "_profile": {"name": "X"}}, headline="h", language=lang)
    assert seen.get("language") == lang
