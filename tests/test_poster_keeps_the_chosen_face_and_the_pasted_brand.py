# -*- coding: utf-8 -*-
"""Two roots the La Roche poster exposed (2026-07-26, Al Fouad run).

OWNER: «الوش والشعر الكيرلي اللي انا اخترته اتغيروا — انا عايزة الصورة اللي اخترتها تطلع بالظبط»
        «الكلام أبيض مش باين»

MEASURED on that render's server log:

  1. THE FACE REACHES ONE PATH ONLY. `generate_poster(identity_image=)` is consumed inside the
     ONESHOT branch. The log reads `[oneshot] gate not passed -> classic overlay pipeline` and
     then `[bg] nano-banana REAL PRODUCT (1) + style (2)` — the classic path, which never saw the
     identity. That path is the DEFAULT for Arabic brands, so the face the owner picked was
     dropped on the exact route her posters take.

  2. THE THIRD-PARTY GATE FIRES ON THE PRODUCT SHE ASKED FOR. Final verdict: "The ad is for a
     pharmacy but is dominated by a third-party product brand, which is a hard-gate violation."
     The pharmacy SELLS La Roche-Posay and the owner pasted that exact product link. The gate
     was written for an invented Nike swoosh, not for a retailer advertising its own stock — a
     true rule answering the wrong question, so the poster shipped stamped untrustworthy.

Hermetic: no renders, no network.
"""
from __future__ import annotations

import inspect


def test_the_classic_background_accepts_and_forwards_an_identity_ref():
    """The nano background builder must be able to carry the chosen person."""
    import poster.pipeline as pp
    sig = inspect.signature(pp._generate_nano_background)
    assert "identity_ref" in sig.parameters, (
        "the classic path cannot carry a face at all — identity dies with the oneshot gate")


def test_the_identity_travels_all_the_way_to_the_image_call(monkeypatch, tmp_path):
    import poster.oneshot as oneshot
    import poster.pipeline as pp

    seen = {}

    def fake_gen(prompt, out_path, **kw):
        seen.update(kw)
        from PIL import Image
        Image.new("RGB", (64, 96), (200, 200, 200)).save(out_path)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(pp, "_image_bytes_from_url", lambda u: (b"IMG", "image/jpeg"))
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: [])
    pp._generate_nano_background({}, None, "a scene", out_dir=str(tmp_path),
                                 identity_ref=(b"FACEBYTES", "image/png"))
    assert seen.get("identity_ref") and seen["identity_ref"][0] == b"FACEBYTES", (
        f"the face never reached the image call: {list(seen)}")


def test_the_background_prompt_says_the_person_is_the_attached_one(monkeypatch, tmp_path):
    """A reference the model treats as 'inspiration' produces a different woman — the same
    failure the product attachment had, and the same cure: say it is THE person."""
    import poster.oneshot as oneshot
    import poster.pipeline as pp

    seen = {}

    def fake_gen(prompt, out_path, **kw):
        seen["prompt"] = prompt
        from PIL import Image
        Image.new("RGB", (64, 96), (200, 200, 200)).save(out_path)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(pp, "_image_bytes_from_url", lambda u: (b"IMG", "image/jpeg"))
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: [])
    pp._generate_nano_background({}, None, "a scene", out_dir=str(tmp_path),
                                 identity_ref=(b"FACE", "image/png"))
    p = (seen.get("prompt") or "").lower()
    assert "same person" in p or "identical face" in p, (
        f"the prompt never pins the person to the attachment: {p[-300:]!r}")


def test_the_pipeline_hands_the_identity_to_the_classic_path():
    """Structural, with comments AND docstrings stripped so this file's own prose cannot pass it."""
    import poster.pipeline as pp
    src = inspect.getsource(pp)
    tree_src = "\n".join(l.split("#")[0] for l in src.splitlines())
    i = tree_src.find("_generate_nano_background(profile, brand_dna, prompt")
    assert i > 0, "the classic call site moved"
    assert "identity_ref" in tree_src[i:i + 400], (
        "generate_poster's identity_image is not forwarded into the classic background")


# --- the third-party gate vs the product the owner pasted -----------------------------------

def test_a_pasted_products_own_brand_is_not_a_third_party_violation():
    from poster.vision_qa import third_party_is_expected
    assert third_party_is_expected(
        "The ad is dominated by a third-party product brand (La Roche-Posay).",
        product_name="La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+ 50ml") is True


def test_an_unrelated_brand_is_still_a_violation():
    from poster.vision_qa import third_party_is_expected
    assert third_party_is_expected(
        "A Nike swoosh is rendered on the model's shirt.",
        product_name="La Roche-Posay Anthelios Fluid") is False
    assert third_party_is_expected("A Nike swoosh appears.", product_name="") is False


def test_the_pipeline_clears_the_flag_when_the_mark_is_the_pasted_product():
    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "third_party_is_expected" in src, (
        "the pharmacy's own stocked brand still trips the invented-logo gate")


# --- «الكلام أبيض مش باين» ------------------------------------------------------------------

def _stops(css: str):
    """[(alpha, position%)] of the scrim gradient, in CSS order."""
    import re
    return [(float(a), float(pos)) for a, pos in
            re.findall(r"rgba\(\s*\d+,\s*\d+,\s*\d+,\s*([\d.]+)\s*\)\s+([\d.]+)%", css)]


def _alpha_at(stops, pos: float) -> float:
    """Linear-interpolate the gradient's alpha at `pos`% up the text block."""
    stops = sorted(stops, key=lambda s: s[1])
    if pos <= stops[0][1]:
        return stops[0][0]
    for (a0, p0), (a1, p1) in zip(stops, stops[1:]):
        if p0 <= pos <= p1:
            t = 0.0 if p1 == p0 else (pos - p0) / (p1 - p0)
            return a0 + (a1 - a0) * t
    return stops[-1][0]


def test_the_scrim_actually_covers_where_the_headline_sits():
    """MEASURED on the La Roche poster: the pixels behind the white headline average 185/255
    (white on 185 grey is ~1.7:1 — unreadable). The scrim WAS applied, but as a gradient that
    reaches full strength at the very BOTTOM edge and fades out going up, while the block's
    130px top padding puts the biggest type near the TOP — where the alpha had already fallen
    to almost nothing. A legibility backing has to be under the text, not under the margin."""
    from poster.template import _lower_css
    stops = _stops(_lower_css("bottom_band", 0.82, "right", True))
    assert stops
    a75 = _alpha_at(stops, 75.0)
    assert a75 >= 0.45, (
        f"at 75% up the text block the scrim is only {a75:.2f} — that is where the headline "
        f"sits and why white type vanished on a bright scene: {stops}")


def test_a_dark_scene_is_not_flattened_into_a_black_box():
    """The other half of the rule: over-darkening a night scene destroys the photograph."""
    from poster.template import _lower_css
    stops = _stops(_lower_css("bottom_band", 0.25, "right", True))
    assert max(a for a, _ in stops) <= 0.45, stops
    assert min(a for a, _ in stops) == 0.0, f"the gradient must still fade out on top: {stops}"


def test_every_text_layout_backs_its_copy():
    from poster.template import _lower_css
    for layout in ("bottom_band", "magazine_hero", "top_anchor"):
        stops = _stops(_lower_css(layout, 0.82, "right", True))
        assert _alpha_at(stops, 75.0) >= 0.45, f"{layout}: {stops}"
