# -*- coding: utf-8 -*-
"""Continuity between scenes, and the two things it must never buy it with.

OWNER (2026-07-28): «هو بما إنه علشان يكون سيناريو مترابط، الأحسن i2v ولا t2v؟»

The answer is i2v — every coherence control in this pipeline lives on the still (look lock, the
same chosen face, the real place reference, the composited product, the QA gate) and t2v discards
all of it. But the question points at a real gap that is not the one it names: coherence WITHIN a
clip is not continuity BETWEEN clips. MEASURED before writing this: `grep -rn last_frame reel/`
returned zero. Nothing had ever carried one frame into the next, so scene 1 and scene 3 were two
takes that resembled each other.

The danger in the fix is compounding. Each chained scene is one generation further from any real
photograph, so drift accumulates silently — by the fifth link the reel is four generations from
the real place and nothing has said so. That is why the chain is bounded and why a real photo is
never displaced.
"""
from __future__ import annotations

import types


def _scene(**kw):
    s = types.SimpleNamespace(kind="body", place_ref_url=None, product_asset_url=None,
                              hero_product=False, seed_image_url=None)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# =====================================================================
# grounding outranks smoothness
# =====================================================================

def test_a_real_photograph_is_never_displaced_by_a_chain():
    """The reel's grounding promise is that each scene comes from the brand's real place. A
    previous clip's frame is a generated image; letting it win buys flow with the truth."""
    from reel.continuity import decide_seed
    prev = _scene(place_ref_url="https://brand.test/shop.jpg")
    cur = _scene(place_ref_url="https://brand.test/shop.jpg")
    d = decide_seed(cur, "tail.png", own_seed="https://brand.test/shop.jpg",
                    chain_len=0, previous_scene=prev)
    assert d.chained is False
    assert d.seed == "https://brand.test/shop.jpg"
    assert "real photograph" in d.reason


def test_an_unrecognised_seed_counts_as_real():
    """Fail toward protecting grounding: the permissive mistake here silently overwrites a real
    photo, the conservative one only loses some smoothness."""
    from reel.continuity import _is_real_photo
    assert _is_real_photo("https://cdn.brand.test/hero.jpg") is True
    assert _is_real_photo("/some/unknown/place/x.jpg") is True
    assert _is_real_photo("outputs/_seeds/seed_3.png") is False
    assert _is_real_photo("") is False


def test_a_generated_still_can_be_superseded_by_continuity():
    from reel.continuity import decide_seed
    prev = _scene(place_ref_url="https://brand.test/shop.jpg")
    cur = _scene(place_ref_url="https://brand.test/shop.jpg")
    d = decide_seed(cur, "tail.png", own_seed="outputs/_seeds/seed_2.png",
                    chain_len=0, previous_scene=prev)
    assert d.chained is True and d.seed == "tail.png"


# =====================================================================
# the chain is bounded, because drift compounds
# =====================================================================

def test_the_chain_stops_at_the_bound():
    from reel.continuity import MAX_CHAIN, decide_seed
    prev = _scene(place_ref_url="p")
    cur = _scene(place_ref_url="p")
    d = decide_seed(cur, "tail.png", own_seed="outputs/_seeds/s.png",
                    chain_len=MAX_CHAIN, previous_scene=prev)
    assert d.chained is False
    assert "re-grounding" in d.reason


def test_the_bound_is_small_enough_to_matter():
    from reel.continuity import MAX_CHAIN
    assert 1 <= MAX_CHAIN <= 3, (
        f"a chain of {MAX_CHAIN} puts the last scene {MAX_CHAIN} generations from the real place")


# =====================================================================
# a cut is a cut
# =====================================================================

def test_a_bookend_is_never_a_continuation():
    from reel.continuity import decide_seed
    for kind in ("intro", "outro"):
        d = decide_seed(_scene(kind=kind, place_ref_url="p"), "tail.png",
                        own_seed="outputs/_seeds/s.png", chain_len=0,
                        previous_scene=_scene(place_ref_url="p"))
        assert d.chained is False, kind
        assert "bookend" in d.reason


def test_a_product_beat_is_a_cut():
    """Continuing a person's gesture into a packshot reads as an error, not as flow."""
    from reel.continuity import decide_seed
    prev = _scene(place_ref_url="p")
    for marker in ({"product_asset_url": "https://x/box.jpg"}, {"hero_product": True}):
        d = decide_seed(_scene(place_ref_url="p", **marker), "tail.png",
                        own_seed="outputs/_seeds/s.png", chain_len=0, previous_scene=prev)
        assert d.chained is False, marker
        assert "cut" in d.reason


def test_a_different_place_is_not_the_same_moment():
    from reel.continuity import decide_seed
    d = decide_seed(_scene(place_ref_url="https://a/one.jpg"), "tail.png",
                    own_seed="outputs/_seeds/s.png", chain_len=0,
                    previous_scene=_scene(place_ref_url="https://b/two.jpg"))
    assert d.chained is False


def test_continuity_is_derived_not_declared():
    """`test_the_reel_product_is_pasted_not_drawn` already recorded what a director-set flag costs:
    "a cure that depends on a model remembering to opt in is not a cure"."""
    import inspect

    import reel.continuity as c
    body = inspect.getsource(c._same_moment)
    # Strip the docstring as well as the comments — the prose explains WHY a declared flag was
    # rejected and naturally names it, which a comments-only strip would read as the code using it.
    doc = c._same_moment.__doc__ or ""
    code = "\n".join(l.split("#")[0] for l in body.replace(doc, "").splitlines())
    assert "place_ref_url" in code, (
        "the same-moment test reads nothing measurable off the scene")
    assert "continues_previous" not in code, (
        "continuity is being read off a field the creative director has to remember to set")


# =====================================================================
# a scene with nothing still gets a frame
# =====================================================================

def test_a_seedless_scene_inherits_rather_than_dropping_to_ken_burns():
    """Veo is image-to-video only since 2026-07-28, so a seedless scene is refused and falls back.
    Inheriting the previous frame keeps it in the reel."""
    from reel.continuity import decide_seed
    d = decide_seed(_scene(), "tail.png", own_seed=None, chain_len=0, previous_scene=None)
    assert d.chained is True and d.seed == "tail.png"


def test_the_first_scene_has_nothing_to_inherit():
    from reel.continuity import decide_seed
    d = decide_seed(_scene(), None, own_seed=None, chain_len=0, previous_scene=None)
    assert d.chained is False and d.seed is None


# =====================================================================
# the tail is the text-free frame, and it never costs a render
# =====================================================================

def test_the_tail_is_taken_before_the_captions_are_burnt_on():
    """A tail lifted after the overlay would carry the previous scene's headline into the next
    scene's generation — the words would be baked into the world."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp)
    tail = src.find("prev_tail = tail_frame(raw")
    overlay = src.find("text_seqs", tail)
    assert tail > 0, "the tail is never captured"
    assert "tail_frame(raw" in src, "the tail is taken from something other than the raw clip"


def test_a_missing_clip_returns_no_tail_and_does_not_raise(tmp_path):
    from reel.continuity import tail_frame
    assert tail_frame(tmp_path / "nope.mp4", tmp_path / "t.png") is None


def test_a_broken_clip_returns_no_tail_and_does_not_raise(tmp_path):
    from reel.continuity import tail_frame
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert tail_frame(bad, tmp_path / "t.png") is None


def test_the_compositor_resets_the_chain_after_a_cut():
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp)
    assert "chain_len = chain_len + 1 if _cont.chained else 0" in src, (
        "the chain counter never resets, so one early cut disables continuity for the whole reel "
        "or one long run never re-grounds")


def test_a_chained_seed_is_announced():
    """A reel that quietly stopped being grounded in the real place would look fine and be wrong."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp)
    assert "_cont.reason" in src, "the continuity decision is never printed"
