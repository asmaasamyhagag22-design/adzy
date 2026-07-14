# -*- coding: utf-8 -*-
"""Render #3 §5 — the G2 blocking gate. Hermetic (fake vision caller; PIL images).

The binding law: PASS is computed IN CODE from the booleans; a failing set triggers
targeted regen <=3 then RuntimeError — Veo can never fire on a failing seed set."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from reel.render3.g2 import G2Verdict, build_contact_sheet, g2_check, run_g2_loop


def _png(path, color):
    img = Image.new("RGB", (120, 200), color)
    img.save(path, "PNG")
    return str(path)


def _ref_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), (200, 180, 160)).save(buf, "PNG")
    return buf.getvalue()


class _Judge:
    """Scripted verdicts, records what it saw."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = 0

    def __call__(self, system, user, model, group_name="", images=None):
        assert group_name == "render3_g2" and len(images) == 2   # ref + sheet
        assert "harsh QA gate" in system
        self.calls += 1
        return self.verdicts.pop(0), None


def test_contact_sheet_grid(tmp_path):
    seeds = [_png(tmp_path / f"s{i}.png", (i * 40, 90, 120)) for i in range(5)]
    data = build_contact_sheet(seeds, out_path=tmp_path / "sheet.png")
    img = Image.open(io.BytesIO(data))
    assert img.width > 3 * 100 and img.height > 200          # 3-col grid, 2 rows
    assert (tmp_path / "sheet.png").is_file()


def test_g2_verdict_computed_in_code_never_self_passed(tmp_path):
    seeds = [_png(tmp_path / f"s{i}.png", (100, 100, 100)) for i in range(3)]
    sheet = build_contact_sheet(seeds)
    # the model claims PASS while flagging a junk screen -> code says FAIL
    optimist = _Judge([G2Verdict(same_person=True, same_world_grade=True,
                                 junk_screens=[2], arc_readable=True, verdict="PASS")])
    v = g2_check(sheet, _ref_png(), n=3, caller=optimist)
    assert v.verdict == "FAIL"
    # and the honest clean verdict passes
    clean = _Judge([G2Verdict(same_person=True, same_world_grade=True,
                              junk_screens=[], arc_readable=True, verdict="FAIL")])
    v2 = g2_check(sheet, _ref_png(), n=3, caller=clean)
    assert v2.verdict == "PASS"                              # code conjunction wins both ways


def test_loop_targeted_regen_then_pass(tmp_path):
    seeds = [_png(tmp_path / f"s{i}.png", (90, 90, 90)) for i in range(3)]
    regens = []

    def regen(k):
        regens.append(k)
        return _png(tmp_path / f"s{k}_v2.png", (10, 200, 10))

    judge = _Judge([
        G2Verdict(same_person=False,
                  identity_offenders=[{"frame": 2, "reason": "different woman"}],
                  same_world_grade=True, junk_screens=[3], arc_readable=True),
        G2Verdict(same_person=True, same_world_grade=True, junk_screens=[],
                  arc_readable=True),
    ])
    v = run_g2_loop(seeds, _ref_png(), regen=regen, caller=judge)
    assert v.verdict == "PASS" and judge.calls == 2
    assert regens == [1, 2]                                  # frames 2,3 -> 0-based 1,2
    assert seeds[1].endswith("s1_v2.png") and seeds[2].endswith("s2_v2.png")
    assert seeds[0].endswith("s0.png")                       # untouched frame stays


def test_arc_failure_without_offenders_fails_fast_no_regen(tmp_path):
    # M4: an unreadable arc with NO named frame can't be fixed by re-rolling seeds -> the loop
    # must fail closed immediately, spending zero regens (not blind-regen everything max_retries x).
    seeds = [_png(tmp_path / f"s{i}.png", (90, 90, 90)) for i in range(3)]
    regens = []
    arc_fail = G2Verdict(same_person=True, same_world_grade=True, junk_screens=[],
                         arc_readable=False)                    # story unreadable, no frame blamed
    judge = _Judge([arc_fail])
    with pytest.raises(RuntimeError, match="treatment-level"):
        run_g2_loop(seeds, _ref_png(), regen=lambda k: regens.append(k) or seeds[k],
                    caller=judge, max_retries=3)
    assert regens == []                                        # ZERO wasted image spend
    assert judge.calls == 1                                    # no blind re-gate loop either


def test_loop_exhaustion_blocks_veo(tmp_path):
    seeds = [_png(tmp_path / f"s{i}.png", (90, 90, 90)) for i in range(2)]
    bad = G2Verdict(same_person=False,
                    identity_offenders=[{"frame": 1, "reason": "drift"}],
                    same_world_grade=True, junk_screens=[], arc_readable=True)
    judge = _Judge([bad, bad, bad])
    with pytest.raises(RuntimeError, match="Veo blocked"):
        run_g2_loop(seeds, _ref_png(), regen=lambda k: seeds[k], caller=judge,
                    max_retries=3)
    assert judge.calls == 3
