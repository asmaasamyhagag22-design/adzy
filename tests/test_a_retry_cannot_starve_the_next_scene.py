# -*- coding: utf-8 -*-
"""One scene's re-roll may not spend the footage another scene has not asked for yet.

MEASURED 2026-08-06 on the live NTI reel. Scene 0 failed its QA twice, so it bought three clips.
The run hit its per-run ceiling on scene 0, and scenes 1, 2 AND 3 were then refused their FIRST
clip and fell back to Ken Burns:

    [reel][budget] scene 1: per-run Veo budget reached - remaining scenes use 'kenburns'
    [reel][budget] scene 2: per-run Veo budget reached - remaining scenes use 'kenburns'
    [reel][budget] scene 3: per-run Veo budget reached - remaining scenes use 'kenburns'

Three quarters of a four-scene film became panned stills because one shot was re-rolled — the
exact thing the owner rejected («انا مش عايزه Ken Burns في الريل»). The CEILING was right. Spending
it first-come-first-served was not.

Replayed on the guard, four 4-second scenes with scene 0 retried twice, at the code's own $8
default:

    first-come-first-served   ['Veo','Veo','Veo','KenBurns']   $8.00   2 retries bought   3/4 move
    later scenes reserved     ['Veo','Veo','Veo','Veo']        $8.00   1 retry bought     4/4 move

Same money. One more scene that moves, because the SECOND re-roll was refused instead of the
fourth scene's only clip.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import vertex_spend as vs

CAP = 8.0
CLIP = 1.60          # a 4-second Veo clip


@pytest.fixture()
def ledger(tmp_path):
    p = tmp_path / "spend.jsonl"
    p.write_text("", encoding="utf-8")
    return p


def _spend(ledger, seconds=4, reserve=0.0):
    est = vs.guard_video(seconds, ledger=ledger, max_usd=CAP, reserve_usd=reserve)
    vs.record("veo", seconds, est, ledger=ledger)
    return est


def _run(ledger, *, reserved: bool, scenes=4, retries_on_scene_0=2):
    got = []
    for i in range(scenes):
        reserve = round(CLIP * (scenes - 1 - i), 2) if reserved else 0.0
        ok = False
        for _a in range(1 + (retries_on_scene_0 if i == 0 else 0)):
            try:
                _spend(ledger, reserve=reserve)
                ok = True
            except vs.SpendRefused:
                break
        got.append(ok)
    return got


def test_the_measured_failure_reproduces_without_a_reserve(ledger):
    """The bug, as data: the last scene is starved by an earlier scene's retries."""
    assert _run(ledger, reserved=False) == [True, True, True, False]


def test_reserving_the_later_scenes_gives_every_scene_its_clip(ledger):
    """Same cap, same retries, same money — one more scene that actually moves."""
    assert _run(ledger, reserved=True) == [True, True, True, True]
    assert vs.run_spend_usd("veo", ledger=ledger) == pytest.approx(CAP, abs=0.01)


def test_the_reserve_refuses_the_retry_not_the_base_clip(ledger):
    """The point is WHICH call is refused. A first attempt must always be affordable while the
    budget covers the base clips; only the extra roll gets turned down."""
    # scene 0's base clip, with three scenes still owed
    assert _spend(ledger, reserve=3 * CLIP) == pytest.approx(CLIP)
    # its first retry still fits
    assert _spend(ledger, reserve=3 * CLIP) == pytest.approx(CLIP)
    # its second does not — that money belongs to scenes 1-3
    with pytest.raises(vs.SpendRefused):
        _spend(ledger, reserve=3 * CLIP)


def test_the_refusal_says_what_is_being_held_and_why(ledger):
    _spend(ledger, reserve=3 * CLIP)
    _spend(ledger, reserve=3 * CLIP)
    with pytest.raises(vs.SpendRefused) as e:
        _spend(ledger, reserve=3 * CLIP)
    msg = str(e.value)
    assert "held back" in msg and "not been shot yet" in msg, msg


def test_the_guard_compares_money_in_cents(ledger):
    """MEASURED while adding the reserve: it puts this comparison on the boundary constantly, and
    `3.2 + 1.6 == 4.800000000000001` in binary floating point — a scene lost its only clip to a
    rounding error of one ten-trillionth of a dollar."""
    _spend(ledger, reserve=0.0)
    _spend(ledger, reserve=0.0)                       # spent is now exactly 3.20
    assert vs.run_spend_usd("veo", ledger=ledger) == pytest.approx(3.20, abs=1e-9)
    # 3.20 + 1.60 lands exactly on an allowance of 4.80 — it must be ALLOWED, not refused
    assert _spend(ledger, reserve=CAP - 4.80) == pytest.approx(CLIP)


def test_no_reserve_is_byte_identical_to_the_old_behaviour(ledger):
    """`reserve_usd` defaults to 0.0, so every caller that has not been taught about it — and the
    monthly cap path — behaves exactly as before."""
    import inspect
    assert inspect.signature(vs.guard_video).parameters["reserve_usd"].default == 0.0
    for _ in range(5):
        _spend(ledger)
    with pytest.raises(vs.SpendRefused):
        _spend(ledger)


def test_the_compositor_reserves_the_scenes_that_come_after(tmp_path):
    """Wired, not merely written: the compositor is the only caller that knows the scene list."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    assert "_reserve_for" in src and "reserve_usd" in src
    assert "storyboard.scenes[idx + 1:]" in src, "the reserve is not the scenes still to come"
    body = src.split("def _reserve_for", 1)[1].split("\n        for i, scene in", 1)[0]
    assert "insert_product_urls" in body, \
        "a rendered insert costs no Veo seconds and must not be reserved for"
