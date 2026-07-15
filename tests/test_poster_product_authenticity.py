# -*- coding: utf-8 -*-
"""Poster product authenticity (owner-caught, topshoes 2026-07-12: the poster showed an
INVENTED sneaker with a swoosh — a false product claim invisible to every text gate).

Three links of the chain, each pinned:
1. thumbnail-starved photos: a too-small CDN thumbnail upgrades to its param-stripped
   ORIGINAL instead of being dropped (the exact topshoes mechanism);
2. the n_products=0 prompt BANS inventing a hero product + ALL third-party marks;
3. the vision QA carries invented_product / third_party_mark as code-level hard gates.
"""
from __future__ import annotations

import io as _io

from poster.oneshot import build_oneshot_prompt
from poster.vision_qa import PosterQAVerdict, _QAResponse, poster_vision_qa
from reel.image_quality import filter_usable_photos, original_variant


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 80)).save(buf, format="PNG")
    return buf.getvalue()


def test_thumbnail_upgrades_to_param_stripped_original():
    thumb = "https://cdn.shop.example/files/shoe.jpg?v=123&width=330"
    assert original_variant(thumb) == "https://cdn.shop.example/files/shoe.jpg?v=123"
    assert original_variant("https://x.example/photo.jpg") is None      # nothing to strip

    small, big = _png(330, 330), _png(1024, 1024)

    def fetch(url: str):
        return big if "width" not in url else small

    kept = filter_usable_photos([thumb], fetch=fetch)
    assert kept == ["https://cdn.shop.example/files/shoe.jpg?v=123"]    # UPGRADED url kept
    # and a small photo with no size param still drops honestly
    assert filter_usable_photos(["https://x.example/tiny.jpg"], fetch=lambda u: small) == []


def test_prompt_without_real_products_bans_invention_and_marks():
    p0 = build_oneshot_prompt({"headline": "س"}, brand_name="Top Shoes", palette_names=["red"],
                              dna_lines="", rtl=True, has_logo=True, heading_font="",
                              body_font="", single_message="", visual_idea="سنيكر من عالم آخر",
                              n_products=0, n_places=0, locale_line="")
    assert "do NOT invent a hero product" in p0
    assert "third-party logo" in p0 and "swooshes" in p0
    # with real product photos attached, the REAL-props contract still governs
    p2 = build_oneshot_prompt({"headline": "س"}, brand_name="Top Shoes", palette_names=["red"],
                              dna_lines="", rtl=True, has_logo=True, heading_font="",
                              body_font="", single_message="", visual_idea="x",
                              n_products=2, n_places=0, locale_line="")
    assert "REAL PRODUCT PROPS" in p2 and "do NOT invent a hero product" not in p2
    assert "third-party logo" in p2                     # the marks ban is unconditional


def test_vision_qa_hard_gates_invented_product_and_marks():
    class _Optimist:
        def __call__(self, system, user, model, group_name="", images=None):
            assert "invented_product" in system         # the question reached the judge
            assert "third_party_mark" in system
            return model(has_latin_text=False, cta_clipped=False, on_brand_color=True,
                         candid_violation=False, invented_product=True,
                         overall_pass=True, reason="model says fine"), None

    v = poster_vision_qa(b"\x89PNG\r\n\x1a\n" + b"0" * 64, caller=_Optimist(),
                         expect_real_products=False)
    assert v.checked and v.invented_product is True
    assert v.overall_pass is False                      # code hard gate wins

    class _Swoosh:
        def __call__(self, system, user, model, group_name="", images=None):
            return model(has_latin_text=False, cta_clipped=False, on_brand_color=True,
                         candid_violation=False, third_party_mark=True,
                         overall_pass=True, reason="nike-like mark"), None

    v2 = poster_vision_qa(b"\x89PNG\r\n\x1a\n" + b"0" * 64, caller=_Swoosh())
    assert v2.overall_pass is False and v2.third_party_mark is True
    # defaults keep old callers/tests valid
    assert PosterQAVerdict().invented_product is False and _QAResponse(
        has_latin_text=False, cta_clipped=False, on_brand_color=True,
        candid_violation=False, overall_pass=True, reason="").third_party_mark is False


def test_the_invented_product_question_is_ASKED_when_realness_is_unknown():
    """P6 #9 — the hard gate the model was never asked about on the classic path.

    The instruction was emitted only `if expect_real_products is False`. poster/pipeline.py calls
    QA WITHOUT that argument => None => `None is False` is False => the instruction was omitted =>
    invented_product kept its permissive default => the gate could never fire. So the owner-caught
    topshoes failure (a fabricated hero sneaker presented as the brand's merchandise — a false
    product claim invisible to every text gate) shipped on EVERY classic poster while
    overall_pass=True certified it clean. The existing tests pass because the stub SETS the field;
    they never exercise the prompt assembly that decides whether it can be set at all.

    Fail-closed: ask unless we KNOW real product photos were supplied.
    """
    from poster.vision_qa import poster_vision_qa
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    def _capture(box):
        def _caller(system, user, model, group_name="", images=None):
            box["system"] = system
            return model(), None
        return _caller

    # NB: the token "invented_product" appears in the response SCHEMA regardless, so its presence
    # proves nothing. Assert on the distinctive INSTRUCTION text — the thing that actually asks.
    ASK = "fabricated shoe/bottle/device/dish"

    seen: dict = {}
    poster_vision_qa(png, caller=_capture(seen))                              # the classic path
    assert ASK in seen["system"], (
        "the hard gate is never ASKED when realness is unknown -> it can never fire")

    seen_false: dict = {}
    poster_vision_qa(png, caller=_capture(seen_false), expect_real_products=False)
    assert ASK in seen_false["system"]                                         # unchanged

    seen_true: dict = {}
    poster_vision_qa(png, caller=_capture(seen_true), expect_real_products=True)
    assert ASK not in seen_true["system"]                  # real photos supplied -> hero IS theirs
