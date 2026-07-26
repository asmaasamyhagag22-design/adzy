# -*- coding: utf-8 -*-
"""ONE camera, ONE light, across every shot — pinned before generation, not graded after.

OWNER (2026-07-26): «لازم تطلبي من الأيجنت يثبت كلمات وصف الإضاءة والألوان في كل البرومبتات اللي
بيبعتها لموديل التوليد… تثبيت العبارة دي بيجبر الـAI إنه يولد المشاهد كلها بنفس عدسة الكاميرا
ونفس جو الإضاءة تقريباً».

MEASURED on abuauf_agency_v2: every shot arrived with its own lens and its own light — a warm
sun-lit kitchen next to a cold gym next to a flat studio — which is the largest single reason
eight good frames read as eight unrelated clips.

THE RULE: one LOOK string, derived once per brand, appended verbatim to EVERY generation
prompt — the still seeds, the motion prompts and the poster background alike. Identical text is
the whole point: two shots that differ by a word differ by a grade.

D-8: the look is DERIVED from the brand's own learned DNA (a pharmacy is clinical and cool, a
snack brand is warm and golden). The owner's example string is the DEFAULT for a brand with no
DNA yet, not a house style stamped on everyone.
"""
from __future__ import annotations


class _DNA:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# --- the string itself -------------------------------------------------------------------------

def test_the_look_carries_lens_light_and_grade():
    from brand.look import look_lock
    s = look_lock()
    low = s.lower()
    assert "lens" in low or "mm" in low
    assert "light" in low or "lighting" in low
    assert "grade" in low or "palette" in low or "grain" in low
    assert len(s.split()) >= 8, s


def test_the_same_brand_gets_the_identical_string_every_time():
    """Two shots that differ by a word differ by a grade — stability IS the feature."""
    from brand.look import look_lock
    dna = _DNA(mood="warm, energetic", imagery_style="sunlit lifestyle photography")
    assert look_lock(brand_dna=dna) == look_lock(brand_dna=dna)


def test_a_clinical_brand_does_not_get_a_golden_hour_look():
    """D-8 on the aesthetic axis: a pharmacy is not a snack brand."""
    from brand.look import look_lock
    warm = look_lock(brand_dna=_DNA(mood="warm, appetising, golden",
                                    imagery_style="sunlit kitchen"))
    clinical = look_lock(brand_dna=_DNA(mood="clean, clinical, trustworthy",
                                        imagery_style="bright white pharmacy studio"))
    assert warm != clinical
    assert "golden" in warm.lower()
    assert "golden" not in clinical.lower()


def test_no_dna_falls_back_to_the_owners_default():
    from brand.look import look_lock
    s = look_lock().lower()
    assert "cinematic" in s and "consistent" in s


# --- every prompt carries it ---------------------------------------------------------------------

def test_the_seed_prompt_ends_with_the_look():
    from brand.look import look_lock
    from reel.seed_gen import build_seed_prompt
    look = look_lock()
    p = build_seed_prompt("a woman opens the pack", brand_name="Abu Auf", look=look)
    assert p.rstrip().endswith(look.rstrip()), p[-200:]


def test_the_motion_prompt_carries_the_same_look():
    """The still and the clip it becomes must share one look, or the animation regrades it."""
    from brand.look import look_lock
    from reel.creative import _motion_directive
    scene = type("S", (), {"hero_product": True, "veo_prompt": "he lifts the pack"})()
    d = _motion_directive(scene, look=look_lock())
    assert look_lock() in d


def test_the_poster_background_prompt_carries_it(monkeypatch, tmp_path):
    from PIL import Image

    import poster.oneshot as oneshot
    import poster.pipeline as pp

    seen = {}

    def fake_gen(prompt, out_path, **kw):
        seen["prompt"] = prompt
        Image.new("RGB", (64, 96), (200, 200, 200)).save(out_path)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: [])
    pp._generate_nano_background({}, None, "a scene", out_dir=str(tmp_path))
    from brand.look import look_lock
    assert look_lock() in (seen.get("prompt") or ""), (seen.get("prompt") or "")[-300:]


def test_the_creative_reel_pins_one_look_for_the_whole_run():
    """Structural: the look is resolved ONCE and reused, not recomputed per scene where a
    changed profile field could silently split the film in two."""
    import inspect

    import reel.creative as rc
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(rc).splitlines())
    assert src.count("look_lock(") <= 1, "the look is recomputed per scene"
    assert "look" in src
