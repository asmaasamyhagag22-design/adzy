# -*- coding: utf-8 -*-
"""A parameter accepted and never used, and a guard that could not see it.

`generate_scene_seeds(..., look="")` declared the LOOK LOCK on its signature and its body never
referenced it. Both `build_seed_prompt(...)` calls omitted `look=`, so the look block inside that
builder was unreachable and EVERY generated still in EVERY film was made with no shared
camera/lighting/grade sentence. `brand/look.py` opens by explaining why that is the expensive one:
no colour pass after the fact repairs a scene that was LIT differently — the shadows are already in
the pixels.

The existing guard (`tests/test_one_look_across_every_shot.py`) called `build_seed_prompt` DIRECTLY
and asserted the look appeared in its return value. It did, always. The bug lived one layer up, at
the seam the guard never crossed.

So these tests capture the prompts where they actually leave us — at the `generate_oneshot_poster`
boundary, with the image generator stubbed — and assert the look is in every one. That is the only
place the question "did the look reach the model" has an honest answer.

The second half covers the other end of the same seam: an owner-APPROVED plan renders verbatim, and
verbatim used to drop the look too, because the look was glued to the motion contract in one string.
"""
from __future__ import annotations

import pytest


# =====================================================================
# the seed generator — captured at the boundary
# =====================================================================

def _fake_scene(prompt="a woman pours tea in a bright kitchen", hero=False):
    import types
    return types.SimpleNamespace(veo_prompt=prompt, hero_product=hero, image_index=0)


def _capture_seed_prompts(monkeypatch, tmp_path, look, n_scenes=2):
    """Run `generate_scene_seeds` with the image model stubbed, returning every prompt it sent."""
    import types

    from PIL import Image

    import poster.oneshot as oneshot
    import reel.scene_qa as scene_qa
    import reel.seed_gen as sg

    sent: list[str] = []

    def _fake_generate(prompt, out, *, logo_bytes=None, model=None, product_images=None,
                       identity_ref=None, aspect="9:16"):
        sent.append(str(prompt))
        Image.new("RGB", (108, 192), (120, 120, 120)).save(out)
        return str(out)

    # `generate_scene_seeds` imports these INSIDE the function, so the module that owns them is
    # what has to be patched — patching `sg.<name>` binds an attribute the function never reads.
    monkeypatch.setattr(oneshot, "generate_oneshot_poster", _fake_generate)
    monkeypatch.setattr(scene_qa, "check_seed_frame",
                        lambda *a, **k: types.SimpleNamespace(overall_pass=True, reason="ok",
                                                              checked=True))
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: None)
    monkeypatch.setattr(sg, "_ensure_portrait", lambda p, ratio=16 / 9: None)

    creative = types.SimpleNamespace(scenes=[_fake_scene(f"scene {i}") for i in range(n_scenes)])
    sg.generate_scene_seeds(creative, [], profile={"name": "B"}, out_dir=tmp_path,
                            look=look, log=lambda *a, **k: None)
    return sent


LOOK = "soft north window light, warm 3200K, shallow depth of field, gentle film grain"


def test_every_seed_prompt_carries_the_look(monkeypatch, tmp_path):
    """The assertion the old guard could not make: EVERY prompt that leaves us, not the return
    value of a helper nobody was calling correctly."""
    sent = _capture_seed_prompts(monkeypatch, tmp_path, LOOK)
    assert sent, "no prompts reached the image model"
    missing = [p[:60] for p in sent if LOOK not in p]
    assert not missing, f"{len(missing)}/{len(sent)} seed prompts have no look lock: {missing}"


def test_the_look_reaches_the_unconditioned_retry_too(monkeypatch, tmp_path):
    """Attempt 3 drops the photo conditioning entirely, so the look sentence is the ONLY thing
    keeping that shot in the same film — it needs the lock more than the others, not less."""
    import inspect

    import reel.seed_gen as sg
    src = inspect.getsource(sg.generate_scene_seeds)
    at = src.find("prompt_plain = build_seed_prompt")
    assert at > 0
    assert "look=look" in src[at:at + 400], "the unconditioned attempt drops the look"


def test_no_look_means_no_clause_rather_than_an_empty_one(monkeypatch, tmp_path):
    sent = _capture_seed_prompts(monkeypatch, tmp_path, "")
    assert sent
    assert not any("THE LOOK" in p for p in sent)


def test_the_parameter_is_actually_referenced_in_the_body():
    """The shape of the original defect: declared on the signature, absent from the body. A
    parameter nothing reads is indistinguishable from one that was never added."""
    import ast
    import inspect

    import reel.seed_gen as sg
    tree = ast.parse(inspect.getsource(sg.generate_scene_seeds))
    fn = tree.body[0]
    uses = [n for n in ast.walk(fn)
            if isinstance(n, ast.Name) and n.id == "look" and isinstance(n.ctx, ast.Load)]
    assert len(uses) >= 2, "the look parameter is accepted and never read"


# =====================================================================
# an approved plan keeps its look
# =====================================================================

def test_a_verbatim_plan_still_carries_the_look():
    """`verbatim` protects CREATIVE choices — what happens, how it moves, what is said. The look
    is technical, like the no-text ban the compositor appends to every prompt regardless."""
    from reel.creative import _look_clause
    assert LOOK in _look_clause("a woman pours tea", LOOK)


def test_the_look_is_never_applied_twice():
    """An owner who wrote the look into her own prompt must not be given it again, and re-running
    the builder on a built prompt must be a no-op."""
    from reel.creative import _look_clause, _motion_directive
    import types
    built = _motion_directive(types.SimpleNamespace(hero_product=True), look=LOOK)
    assert LOOK in built
    assert _look_clause(built, LOOK) == ""
    assert _look_clause("a woman pours. " + LOOK, LOOK) == ""


def test_an_empty_look_adds_nothing():
    from reel.creative import _look_clause
    assert _look_clause("a woman pours tea", "") == ""
    assert _look_clause("a woman pours tea", "   ") == ""


def test_the_verbatim_path_does_not_smuggle_in_the_motion_contract():
    """The reason the two were split. An approved plan must keep its own motion description."""
    import inspect

    import reel.creative as c
    src = inspect.getsource(c.build_creative_storyboard)
    at = src.find("if verbatim_prompts")
    assert at > 0
    head = src[max(0, at - 260):at]
    assert "_look_clause" in head, "the verbatim branch no longer carries the look"
    assert "_motion_directive" not in head, "the verbatim branch smuggles in the motion contract"


def test_the_storyboard_built_from_an_approved_plan_has_the_look():
    """End to end through the real builder, not through a helper."""
    from poster.schemas import PosterBrief
    from reel.creative import build_creative_storyboard
    from reel.creative_director import CreativeReel, CreativeScene

    reel = CreativeReel(language="en", images=["https://x/a.jpg"], scenes=[
        CreativeScene(image_index=0, veo_prompt="wide of the kitchen", duration_s=4),
        CreativeScene(image_index=0, veo_prompt="close on her hands", duration_s=4),
    ])
    sb = build_creative_storyboard(reel, PosterBrief(business_name="Brand", headline="Brand"),
                                   verbatim_prompts=True, look=LOOK)
    assert sb.scenes, "no scenes built"
    assert all(LOOK in s.visual_prompt for s in sb.scenes)
    # and the creative content is untouched — that is what verbatim protects
    assert sb.scenes[0].visual_prompt.startswith("wide of the kitchen")
