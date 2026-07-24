# -*- coding: utf-8 -*-
"""P-21/P-22 (2026-07-18): the seed/scene gates in generate.py and compositor.py were fail-OPEN at
the call site — `if v.checked and not v.overall_pass` regenerates/drops ONLY on an explicit FAIL, so
a NOT_CHECKED verdict (no caller / unreadable frames / a call timeout -> overall_pass=None) shipped
the raw clip straight to a PAID Veo render. seed_gen.py:172 already fixed its twin (`overall_pass is
True`); the other two gates now route through ONE shared fail-CLOSED predicate so they cannot drift
open again. Hermetic — pure verdict logic + a stubbed image provider; NO Veo, NO network, NO render."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_seed_may_animate_is_the_one_fail_closed_rule():
    from reel.scene_qa import SceneQAVerdict, SeedVerdict, seed_may_animate
    # only an explicit PASS spends Veo money
    assert seed_may_animate(SeedVerdict(overall_pass=True, checked=True)) is True
    assert seed_may_animate(SceneQAVerdict(overall_pass=True, checked=True)) is True
    # an explicit FAIL never animates
    assert seed_may_animate(SeedVerdict(overall_pass=False, checked=True)) is False
    # NOT_CHECKED (no caller / unreadable / timeout) is NOT a pass — the fail-open hole
    assert seed_may_animate(SeedVerdict(checked=False)) is False
    assert seed_may_animate(SceneQAVerdict(checked=False)) is False
    assert seed_may_animate(None) is False


def test_an_unvetted_seed_never_reaches_veo_in_the_generated_reel(monkeypatch, tmp_path):
    # build_brand_generated_reel with a gate that CANNOT run (checked=False on every seed): every
    # scene must drop and the run must raise BEFORE the Veo stage — not one unvetted seed animates.
    import reel.generate as gen
    from reel.scene_qa import SeedVerdict

    made = {"gen": 0}

    class _FakeImagen:
        name = "fake-imagen"

        def generate(self, prompt, out_dir=None, aspect_ratio=None, **kw):
            made["gen"] += 1
            p = Path(out_dir) / f"seed_{made['gen']}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
            return p

    class _NoVeo:
        def __init__(self, *a, **k):
            raise AssertionError("Veo was reached — an unvetted seed animated (fail-open regression)")

    monkeypatch.setattr("poster.imagen_provider.VertexImagenProvider", _FakeImagen)
    monkeypatch.setattr("reel.video_provider.VeoProvider", _NoVeo)
    # the exact shape scene_qa returns with no caller / unreadable / a call failure
    monkeypatch.setattr("reel.scene_qa.check_seed_frame",
                        lambda p, caller=None, brand_hint="", log=None:
                        SeedVerdict(checked=False, reason="no vision caller: seed gate skipped"))

    with pytest.raises(RuntimeError):
        gen.build_brand_generated_reel({"name": {"value": "NTI"}}, caller=None,
                                       out_path=tmp_path / "r.mp4", n_scenes=3, log=lambda *a: None)
    assert made["gen"] > 0, "the image provider should have been exercised"
