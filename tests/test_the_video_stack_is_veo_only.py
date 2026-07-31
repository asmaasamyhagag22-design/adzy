# -*- coding: utf-8 -*-
"""One video provider, seeded by a still, carrying the motion contract.

OWNER, 2026-07-28, two rulings in one sitting:

    «اشتغل بـVeo مش كلينج، شيل أي أثر لكلينج»
    «عايزه VEO من غير صور في الخلفية بس، مجرد إنه بيحرك الصور اللي نانو بانانا بيديهاله»

Both were stated with the cost in front of her: Veo bills $0.40/SECOND, so a 20-second reel is
roughly $8 a render, against the per-clip price of the provider being removed.

THE REMOVAL WAS NOT COSMETIC. Two things were wired only into the provider that left:

  * THE MOTION CONTRACT. `reel/motion_contract.py` opens by explaining that it is a module and not
    a string inside a provider class, because "a contract written inside one class is a contract
    the next provider ships without". It was then applied in exactly one place — inside that class
    — and Veo received neither the positive clause nor the negative one. Every Veo render since
    was generated with nothing forbidding a locked-off camera: the slideshow the contract exists
    to prevent, and the owner's rule 1. Deleting the provider without moving the contract would
    have shipped that defect as the default.
  * THE REFUSAL TO INVENT FOOTAGE. The removed provider refused text-to-video by design. Veo did
    the opposite: it fell back to text-to-video whenever the image seed was missing OR rejected,
    which is a fallback that quietly changes what is being generated.

So this file pins the guarantees, not the vendor.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest


# =====================================================================
# the vendor is gone from the code, not just from the default
# =====================================================================

_CODE_ROOTS = ("reel", "poster", "api", "frontend", "tests")


def _mentions(path: Path) -> int:
    try:
        return len(re.findall(r"(?i)\bkling\b|KLING_", path.read_text(encoding="utf-8",
                                                                     errors="ignore")))
    except Exception:  # noqa: BLE001
        return 0


def test_no_module_imports_or_names_the_removed_provider():
    """Historical prose is allowed to record WHY a rule exists; live code is not. The one
    permitted mention is the ruling itself, recorded verbatim in the resolver's docstring."""
    offenders = []
    for root in _CODE_ROOTS:
        for p in Path(root).rglob("*"):
            if p.suffix not in (".py", ".html") or not _mentions(p):
                continue
            if p.as_posix() in ("reel/video_provider.py",          # the ruling, recorded verbatim
                                "tests/" + Path(__file__).name):   # this guard's own pattern
                continue
            offenders.append(p.as_posix())
    assert not offenders, f"the removed provider is still named in: {offenders}"


def test_the_provider_class_is_gone():
    import reel.video_provider as vp
    assert not hasattr(vp, "KlingProvider")
    assert not hasattr(vp, "_kling_jwt")
    src = inspect.getsource(vp)
    assert "api-singapore.klingai.com" not in src


def test_the_resolver_can_only_reach_veo_aiml_or_the_stub():
    import reel.video_provider as vp
    src = inspect.getsource(vp.default_video_provider)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "KlingProvider" not in code
    assert "KLING_" not in code


def test_auto_resolves_to_veo(monkeypatch):
    monkeypatch.delenv("REEL_VIDEO_BACKEND", raising=False)
    monkeypatch.delenv("REEL_FORCE_STUB", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    from reel.video_provider import default_video_provider
    assert default_video_provider().name == "veo"


def test_a_stale_third_party_key_cannot_hijack_the_pipeline(monkeypatch):
    """Runway did exactly this on 2026-07-05 — a stale key silently overrode Veo and returned 400
    on every scene, so the reel fell back to Ken Burns stills."""
    monkeypatch.delenv("REEL_VIDEO_BACKEND", raising=False)
    monkeypatch.delenv("REEL_FORCE_STUB", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("KLING_API_KEY", "a-stale-key-someone-left-behind")
    monkeypatch.setenv("AIML_API_KEY", "another-one")
    from reel.video_provider import default_video_provider
    assert default_video_provider().name == "veo"


# =====================================================================
# Veo animates a still — it never invents one
# =====================================================================

def _veo(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    from reel.video_provider import VeoProvider
    return VeoProvider()


def test_no_seed_frame_is_refused(monkeypatch, tmp_path):
    with pytest.raises(Exception) as exc:
        _veo(monkeypatch).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                   width=1080, height=1920, reference_image=None)
    assert "image-to-video" in str(exc.value).lower()


def test_an_unloadable_seed_is_refused_not_silently_downgraded(monkeypatch, tmp_path):
    with pytest.raises(Exception) as exc:
        _veo(monkeypatch).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                   width=1080, height=1920,
                                   reference_image="https://nope.invalid/missing.jpg")
    msg = str(exc.value).lower()
    assert "image-to-video" in msg and "could not be loaded" in msg


def test_the_text_to_video_fallback_is_gone():
    """It re-submitted WITHOUT the image on any i2v rejection, so a broken seed became invented
    footage that still returned a file and still looked like a successful render."""
    import reel.video_provider as vp
    src = inspect.getsource(vp.VeoProvider.generate)
    assert "_submit(None)" not in src
    assert "falling back to text-to-video" not in src


# =====================================================================
# the contract Veo used to ship without
# =====================================================================

def test_veo_receives_the_negative_prompt():
    from google.genai import types

    from reel.video_provider import _videos_config
    neg = str(getattr(_videos_config(types, 6), "negative_prompt", "") or "").lower()
    assert neg, "Veo is still configured with no negative prompt at all"
    for banned in ("slideshow", "static camera", "still photo"):
        assert banned in neg, f"'{banned}' is not forbidden on the Veo request"


def test_veo_receives_the_positive_clause():
    import reel.video_provider as vp
    src = inspect.getsource(vp.VeoProvider.generate)
    assert "apply_motion_contract" in src
    assert "prompt=moving_prompt" in src, (
        "the contract is computed and the RAW prompt is submitted anyway")


def test_the_config_degrades_instead_of_dying_on_an_older_sdk():
    """`negative_prompt` and `person_generation` are both optional across SDK builds; a pinned
    older build must lose the field, not the render."""
    import reel.video_provider as vp

    class _Types:
        class GenerateVideosConfig:
            def __init__(self, **kw):
                if "negative_prompt" in kw:
                    raise TypeError("unexpected keyword argument 'negative_prompt'")
                self.__dict__.update(kw)

    cfg = vp._videos_config(_Types, 6)
    assert cfg.person_generation == "ALLOW_ADULT"
    assert cfg.duration_seconds == 6


def test_the_spend_guard_still_meters_every_second():
    """Veo is the most expensive operation in this repo and now the only video path."""
    import reel.video_provider as vp
    src = inspect.getsource(vp.VeoProvider.generate)
    assert "guard_video" in src, "a $0.40/second render is no longer metered before it runs"
