# -*- coding: utf-8 -*-
"""Never tell the model to erase the product's own printed label.

MEASURED A/B (Nano Care, 2026-07-27) — same model (gemini-3-pro-image-preview), same reference
packshot, same scene, ONE difference: the ABSOLUTE RULES block.

  A, the rule the pipeline ships today
     "no text, letters, numbers, logos, watermarks or signage anywhere in the frame"
     -> the brand mark survives but the sub-line degrades and the box flattens into a
        generic carton

  B, the same rule with the product's own label explicitly protected
     -> the box matches the real packshot's cube form and layout, and the printed label
        reads cleanly

The owner's point, and it holds: «انا بستخدم نانو بانانا ... ولما بستخدمه من جوجل بطلع نتايج تحفه
يبقا المشكله من عندك». The model is capable. We were instructing it to remove the one thing that
makes a product recognisable, and it complied by inventing label-shaped marks — which the scene-QA
gate then correctly reported as "the brand name replaced by garbled, illegible text". We were
manufacturing the defect we were rejecting.

THE BAN ITSELF IS STILL RIGHT — for INVENTED text. Generated signage, captions and logos garble,
which is why every word in this product is composited afterwards. The fix is to ban ADDED text
while protecting text that belongs to the real object.

HONEST LIMIT, recorded so nobody over-reads it: n=1 per arm, and B still mis-set one Arabic letter
(«جديد» for «حديد») and drifted the red toward pink. Generation alone is NOT exact enough for a
client deliverable — the composite stays the reliable path. This fix makes the plate better; it
does not replace pasting the real photograph.
"""
from __future__ import annotations


def test_a_product_scene_protects_the_real_label():
    from reel.seed_gen import build_seed_prompt
    p = build_seed_prompt("hands lifting the box off a counter", product_named=True).lower()
    assert "no text, letters, numbers, logos" not in p, (
        "the blanket ban is still applied to a product scene — the model is being told to erase "
        "the packaging's own printing")
    assert "own printed label" in p or "product's own" in p


def test_a_product_scene_still_bans_INVENTED_text():
    """The ban exists because generated signage garbles; only the real label is exempt."""
    from reel.seed_gen import build_seed_prompt
    p = build_seed_prompt("hands lifting the box", product_named=True).lower()
    assert "added" in p or "invented" in p
    for banned in ("signage", "watermark"):
        assert banned in p, f"{banned} is no longer forbidden"


def test_a_scene_with_no_product_keeps_the_blanket_ban():
    """A location or lifestyle shot has no label to protect, and generated text there is a defect."""
    from reel.seed_gen import build_seed_prompt
    p = build_seed_prompt("a pharmacist walking through the shop").lower()
    assert "no text, letters, numbers, logos" in p


def test_the_default_is_unchanged():
    """Callers that never pass the flag must behave exactly as before."""
    from reel.seed_gen import build_seed_prompt
    assert build_seed_prompt("x") == build_seed_prompt("x", product_named=False)


def test_the_seed_generator_passes_the_flag_through():
    """Written is not wired — the third time this session that mattered."""
    import inspect

    import reel.seed_gen as sg
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(sg).splitlines())
    body = src.split("def build_seed_prompt", 1)[-1]
    body = body.split("def generate_scene_seeds", 1)[-1]
    assert "product_named=" in body, (
        "generate_scene_seeds never tells build_seed_prompt whether the scene features a product")
