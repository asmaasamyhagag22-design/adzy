# -*- coding: utf-8 -*-
"""Round-3 reviewer findings (2026-07-26) — every one measured, every one right.

  1. NO TOLERANCE KEYS THIS ASSET. Measured on the banner: ground p5/median/p95 = 124/216/250,
     bottle body = 91/202/247. Fourteen levels apart, ranges fully overlapped, and the tolerance
     has a CLIFF (tol 20 -> 4% of the bottle eaten; tol 28 -> 38%). Refusing is correct — and
     refusal alone left the poster with no product. A named fallback is required: use the shot
     FULL-BLEED as the scene, where its own ground and reflection are the design instead of a
     seam.
  2. RELOCATION MAKES CONTRAST WORSE. The cleanest band is also the BRIGHTEST (mean 231 ->
     1.24:1 for white), and NO band on that poster clears 4.5:1 in white. The measured remedy
     order is: flip the ink first, then relocate, then scrim as the guaranteed stop.
  3. THE SOFTENER IS MORPHOLOGICALLY LEAKY. The ع-ل-ج root surfaces as معالجة / علاجي / للعلاج
     and a substring rule misses them; worse, blind replacement breaks grammar («علاج البقع» ->
     «يقلل ظهور البقع»). Broken Arabic is worse than a rejection, so an unrecognised form must
     be REPORTED for rewrite, not auto-patched. Two whole categories were missing: absolutes
     (100% / مضمون / الأفضل) and operational promises («يوصلك النهاردة» is a delivery claim).
  4. A TRADEMARK DESIGNATION STAYS LATIN. "SPF 50+" and "UVMUNE 400" are printed marks; a rule
     that says "write the spec in the copy's language" would produce «إس بي إف ٥٠+».
  5. THE EXEMPTION IS PRINTED. A CTA passing at 3.89:1 under the large-text rule is a DECISION;
     it must appear in the audit, not slip through in silence.
"""
from __future__ import annotations


# --- 1) the named fallback ----------------------------------------------------------------------

def test_an_unkeyable_asset_becomes_the_scene_not_a_panel(tmp_path, monkeypatch):
    """OPT-IN since the owner saw it live: the store's asset was a multi-product BANNER, so
    full-bleed put a wall of other products on the poster and discarded the generated scene.
    The measured argument holds for a single-product studio shot, which is what this pins."""
    monkeypatch.setenv("POSTER_ALLOW_FULL_BLEED", "1")
    import io

    from PIL import Image

    from poster.product_composite import compose_or_background
    im = Image.new("RGB", (800, 1000))
    for y in range(1000):                       # gradient studio ground
        g = 200 + int(45 * y / 1000)
        for x in range(800):
            im.putpixel((x, y), (g, g, g + 2))
    for y in range(300, 760):                   # a white bottle on white
        for x in range(320, 500):
            im.putpixel((x, y), (243, 243, 246))
    buf = io.BytesIO(); im.save(buf, format="PNG")

    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (40, 42, 48)).save(bg)
    path, mode = compose_or_background(bg, buf.getvalue())
    assert mode == "full_bleed", mode
    out = Image.open(path).convert("RGB")
    assert out.size == (1080, 1350)
    # the scene IS the shot: its own light ground fills the canvas, no dark generated bg left
    assert out.getpixel((40, 40))[0] > 150, out.getpixel((40, 40))


def test_a_keyable_asset_still_composites(tmp_path):
    import io

    from PIL import Image

    from poster.product_composite import compose_or_background
    im = Image.new("RGBA", (300, 600), (0, 0, 0, 0))
    for y in range(60, 540):
        for x in range(90, 210):
            im.putpixel((x, y), (30, 90, 200, 255))
    buf = io.BytesIO(); im.save(buf, format="PNG")
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (40, 42, 48)).save(bg)
    _p, mode = compose_or_background(bg, buf.getvalue())
    assert mode == "composited", mode


# --- 2) ink first, then move, then scrim --------------------------------------------------------

def test_the_remedy_order_is_ink_then_move_then_scrim():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    i_ink = src.find("ink_for_background")
    i_move = src.find("clearest_copy_zone")
    assert i_ink > 0 and i_move > 0, (i_ink, i_move)
    assert i_ink < i_move, "the copy is moved before its colour is even reconsidered"


def test_dark_ink_wins_on_the_measured_marble(tmp_path):
    from PIL import Image

    from poster.contrast_gate import ink_for_background, wcag_ratio
    p = tmp_path / "bg.png"
    Image.new("RGB", (400, 500), (231, 231, 233)).save(p)      # the measured mean
    ink = ink_for_background(p, zones=[(0.05, 0.55, 0.9, 0.3)])
    assert ink != "#ffffff"
    assert wcag_ratio((14, 18, 25), (231, 231, 233)) > 12


# --- 3) claim morphology: catch the root, refuse what you cannot rewrite -------------------------

def test_every_derivation_of_the_root_is_caught():
    from poster.copy_rules import claim_risks
    for text in ("معالجة البقع الداكنة", "فعالية علاجية مثبتة", "للعلاج السريع",
                 "بيعالج البقع", "علاج نهائي"):
        risks = claim_risks(text)
        assert any(r["kind"] == "therapeutic" for r in risks), (text, risks)


def test_a_known_verb_form_is_rewritten_but_a_noun_form_is_refused():
    from poster.copy_rules import claim_risks, soften_therapeutic_claims
    out, changed = soften_therapeutic_claims("بيعالج البقع الداكنة")
    assert changed and "بيعالج" not in out
    risks = claim_risks("علاج البقع الداكنة")
    therapeutic = [r for r in risks if r["kind"] == "therapeutic"]
    assert therapeutic and therapeutic[0]["action"] == "rewrite_required", risks


def test_absolutes_are_flagged():
    from poster.copy_rules import claim_risks
    for text in ("حماية 100%", "نتيجة مضمونة", "الأفضل في مصر", "آمن تماماً", "مفعول فوري"):
        assert any(r["kind"] == "absolute" for r in claim_risks(text)), text


def test_operational_promises_are_flagged():
    from poster.copy_rules import claim_risks
    for text in ("اطلبيه ويوصلك النهاردة", "توصيل في نفس اليوم", "delivered today"):
        assert any(r["kind"] == "operational" for r in claim_risks(text)), text


def test_clean_copy_has_no_risks():
    from poster.copy_rules import claim_risks
    assert claim_risks("بيحمي بشرتك من الشمس") == []


# --- 4) a printed designation stays printed ------------------------------------------------------

def test_trademark_designations_are_never_transliterated():
    from poster.concept import product_facts_block
    b = product_facts_block(name="Anthelios UVMUNE 400 SPF50+", description="", price="",
                            currency="")
    low = b.lower()
    assert "latin" in low or "as printed" in low or "never transliterate" in low, b


# --- 5) the exemption is printed ------------------------------------------------------------------

def test_the_large_text_exemption_is_reported_not_silent(tmp_path):
    from PIL import Image

    from poster.contrast_gate import check_copy_contrast
    p = tmp_path / "p.png"
    Image.new("RGB", (1000, 1000), (222, 30, 40)).save(p)      # the brand red
    v = check_copy_contrast(p, zones=[(0.05, 0.8, 0.5, 0.12)], ink="#ffffff",
                            large_text_zones=[0])
    assert v.zones[0].get("exemption") == "large_text", v.zones
    assert "large" in (v.reason or "").lower() or v.passed is True
