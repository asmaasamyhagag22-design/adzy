# -*- coding: utf-8 -*-
"""A transient image failure must not throw away the attempts that DID render.

MEASURED (Al Fouad, 2026-07-26 — the run the owner was watching):
    [bg] nano-banana REAL PRODUCT (1) + style (2)        <- attempt 0 rendered
    [qa] attempt=0 FAIL (image-fixable) -> regenerating
    [bg] nano-banana REAL PRODUCT (1) + style (2)        <- attempt 1 rendered
    [qa] attempt=1 FAIL (image-fixable) -> regenerating
    [bg] nano-banana background failed (RuntimeError: ... returned no image); -> legacy Imagen
    [bg] style failed (404 imagen-3.0-capability-001) -> outpaint
    [bg] outpaint failed (404) -> text-to-image
    ClientError: 404 imagen-4.0-generate-001
    real image generation failed; falling back to placeholder

Two defects in one trace:
  1. ONE transient no-image on the THIRD attempt discarded two posters that already existed.
  2. The fallback cascade is made of Vertex Imagen ids that ALL 404 on this project — they have
     been retired for days. It cannot rescue anything; it can only turn a recoverable failure
     into a total one, slowly.

The owner's word for the result was «العبث»: she waited minutes and got the free placeholder.
"""
from __future__ import annotations

import inspect


def test_the_dead_imagen_cascade_is_not_the_nano_fallback(monkeypatch):
    """Retrying a MEASURED-DEAD provider is not a fallback, it is a delay.

    Behavioural, not textual: the legacy cascade is still REACHABLE for the injected-`t2i`
    seam the offline tests use, so grepping the function proves nothing. What must hold is
    that a nano failure never CALLS it."""
    import poster.pipeline as pp

    monkeypatch.setattr(pp, "_generate_nano_background",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("returned no image")))
    legacy = {"n": 0}
    monkeypatch.setattr(pp, "_generate_imagen",
                        lambda *a, **k: (legacy.__setitem__("n", legacy["n"] + 1), ("x", "y"))[1])
    try:
        pp._generate_background({}, None, "a scene")
    except Exception:  # noqa: BLE001 — failing is correct; reaching the dead cascade is not
        pass
    assert legacy["n"] == 0, "a nano failure still falls through to the retired Imagen ids"


def test_nano_is_retried_once_before_giving_up(monkeypatch, tmp_path):
    """A no-image response is transient — the same call usually succeeds on a second try."""
    import poster.pipeline as pp

    calls = {"n": 0}

    def flaky(profile, brand_dna, prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one-shot poster: gemini-2.5-flash-image returned no image")
        return str(tmp_path / "bg.png"), "nano/bg"

    monkeypatch.setattr(pp, "_generate_nano_background", flaky)
    path, model = pp._generate_background({}, None, "a scene")
    assert calls["n"] == 2, f"nano was not retried: {calls['n']} call(s)"
    assert model == "nano/bg"


def test_a_failed_attempt_keeps_the_earlier_posters(monkeypatch, tmp_path):
    """The attempt loop must return its best EXISTING attempt when a later one cannot render."""
    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp.generate_poster).splitlines())
    assert "attempt_failed" in src or "results and " in src, (
        "a background failure mid-loop still aborts the whole render")
