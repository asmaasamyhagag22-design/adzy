# -*- coding: utf-8 -*-
"""The agency reel: creative by default, i2v guaranteed, casting derived, never silent.

OWNER (2026-07-25), verbatim anchors for every decision here:
  «عايزة البرفكت ريل زي ما الوكالات الكبيرة بتعمل … كل صورة كأنها سيناريو لوحدها، ومفيش معنى
   ولا تسويق» · «هنود معرفش ليه … فين المنتجات … فين السيناريو فين المخرج» · «مش فارقة أنا عايزة
   الأصح» · «نانو بنانا لايت كوالتي وحشة خليك برو»

MEASURED CAUSES (runs/reel_abuauf.log + exploration, all file:line-verified):

  * My CLI run took the TEMPLATE path — `python -m reel` without --creative runs
    `build_storyboard`, a field-presence template where every seeded scene shares ONE identical
    prompt (`_i2v_motion_prompt` takes only the brief). The DIRECTOR the owner asked about exists
    (`creative_director.py`, AD SPINE hook→what→benefit→proof→CTA) and was simply not the default.
  * The photo gate rejected 1/1 images → `selected=[]` → the creative path returned None → silent
    fall to the template → no products, no story.
  * `compositor.py:110` hands a reference image to intro/outro only → 3/5 scenes went to
    TEXT-to-video, silently — the owner's whole plan is stills animated by the video model.
  * `voiceover.py:341` logs only the exception CLASS of an ElevenLabs failure and the explicit
    `REEL_TTS_BACKEND=elevenlabs` has no fallback → a silent reel and no way to know why.

Hermetic throughout: every model/provider/network call is stubbed at its existing seam.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest


# =====================================================================
# 1) creative is the default; failure is loud, never a silent downgrade
# =====================================================================

def test_the_reel_cli_defaults_to_the_creative_director():
    import reel.__main__ as m
    src = inspect.getsource(m)
    assert "--template" in src, "no explicit way to ask for the old template path"
    assert "args.template" in src, (
        "the default is still the template storyboard; the director runs only on --creative")


def test_creative_failure_exits_loudly_instead_of_shipping_a_template_reel():
    """A weak reel that LOOKS directed is worse than a failed run: the owner reads the output,
    not the logs. Falling back silently is how the Abu Auf reel shipped."""
    import reel.__main__ as m
    src = inspect.getsource(m)
    assert "allow-template-fallback" in src, "no explicit opt-in for the degraded path"


def test_the_fallback_is_stamped_into_the_audit():
    import reel.__main__ as m
    src = inspect.getsource(m)
    assert "template_fallback" in src, (
        "a template reel can still masquerade as a directed one in the audit")


# =====================================================================
# 2) the ungated reference pool — identity for nano, never display
# =====================================================================

def _png_bytes(w=300, h=400):
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (w, h), (180, 120, 60)).save(buf, "PNG")
    return buf.getvalue()


def test_a_small_photo_still_enters_the_reference_pool():
    """The gate's _MIN_SHORT_SIDE=500 protects DISPLAY quality. A nano reference needs the
    product's identity, not 1080p — rejecting a 300px product shot from conditioning is how the
    model ended up inventing products."""
    from reel.image_quality import reference_pool
    profile = {"visual": {"hero_image_url": "https://x.test/hero.jpg",
                          "content_images": ["https://x.test/small.jpg"]}}
    pool = reference_pool(profile, fetch=lambda u: _png_bytes(300, 400))
    assert pool, "an existing real photo was withheld from conditioning entirely"


def test_collages_and_qr_codes_stay_out_of_the_pool():
    from reel.image_quality import is_collage_bytes, reference_pool
    profile = {"visual": {"content_images": ["https://x.test/a.jpg"]}}
    pool = reference_pool(profile, fetch=lambda u: b"not an image at all")
    assert pool == [], "undecodable bytes entered the reference pool"


def test_the_pool_is_conditioning_only_never_a_seed_image():
    """A below-gate photo may GUIDE generation; it must never be SHOWN. The returned display ref
    for a pool-conditioned seed stays None."""
    import reel.seed_gen as sg
    sig = inspect.signature(sg.generate_scene_seeds)
    assert "ref_pool" in sig.parameters, "seed generation cannot receive the pool"


# =====================================================================
# 3) zero photos no longer kills the director
# =====================================================================

def test_the_director_designs_from_identity_when_no_photo_survives():
    import reel.creative_director as cd
    src = inspect.getsource(cd.design_creative_reel)
    assert "no real photos; skipping" not in src, (
        "zero usable photos still aborts the director — the exact Abu Auf failure")


def test_the_brand_dna_is_actually_loaded():
    """`creative.py` passed brand_dna=None while outputs/brandbooks/<slug>_dna.json sat on disk —
    the seed prompts' dna_lines were always empty."""
    import reel.creative as c
    src = inspect.getsource(c)
    # Strip comments first — the fix's own explanation quotes the old code, and this suite has
    # already tripped over exactly that four times today (once per comment syntax).
    code = " ".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "brand_dna=None" not in code, "the DNA on disk is still ignored"


# =====================================================================
# 4) i2v is guaranteed; t2v is a refusal, not a silent downgrade
# =====================================================================

class _UnusedFakeTransport:
    def __init__(self):
        self.calls = []

    def post(self, url, *, headers=None, json_body=None):
        self.calls.append(url)
        return {"data": {"task_id": "t1"}}

    def get(self, url, *, headers=None):
        return {"data": {"task_status": "succeed",
                         "task_result": {"videos": [{"url": "https://cdn.test/v.mp4"}]}}}

    def download(self, url, dest):
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypmp42x")
        return dest


@pytest.fixture
def _veo(monkeypatch):
    """A VeoProvider whose submit is captured, so no network and no $0.40/second."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "")
    from reel.video_provider import VeoProvider
    return VeoProvider


def test_veo_refuses_text_to_video(_veo, tmp_path):
    """OWNER 2026-07-28: «عايزه VEO ... بس مجرد إنه بيحرك الصور اللي نانو بانانا بيديهاله».

    A clip with no seed frame is the model INVENTING footage, which is the opposite of the
    stills-animated plan — and the compositor already catches a provider error and falls back to
    Ken Burns over the real still, so refusing costs nothing real. Migrated from the provider
    that was removed: the guarantee outlived the vendor."""
    with pytest.raises(Exception) as exc:
        _veo().generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                        width=1080, height=1920, reference_image=None)
    msg = str(exc.value).lower()
    assert "image-to-video" in msg or "seed" in msg, msg


def test_veo_does_not_silently_downgrade_when_the_seed_is_unusable(_veo, tmp_path):
    """The fallback that used to sit here re-submitted WITHOUT the image on any i2v rejection —
    so a broken seed quietly became invented footage that still looked like a successful render."""
    import inspect

    import reel.video_provider as vp
    src = inspect.getsource(vp.VeoProvider.generate)
    assert "_submit(None)" not in src, (
        "an i2v rejection still falls back to text-to-video behind the caller's back")
    with pytest.raises(Exception) as exc:
        _veo().generate("x", out_path=tmp_path / "c.mp4", duration_s=5, width=1080,
                        height=1920, reference_image="https://nope.invalid/missing.jpg")
    assert "image-to-video" in str(exc.value).lower()


# =====================================================================
# 5) casting reaches the director (D-8: derived, never hardcoded)
# =====================================================================

def test_the_directors_system_prompt_carries_the_measured_market():
    """«هنود معرفش ليه» — the seeds already receive brand_locale; the veo_prompts the director
    writes did not, and the video model animates people INTO scenes from those prompts."""
    import reel.creative_director as cd
    sig = inspect.signature(cd._system_prompt)
    assert "locale_primer" in sig.parameters, "the director cannot be told the brand's market"


def test_coherence_lint_flags_a_person_with_no_locale():
    from reel.coherence import coherence_lint
    src = inspect.getsource(coherence_lint)
    assert "locale" in src.lower() or "country" in src.lower(), (
        "nothing catches a person-bearing prompt that ignores the brand's market")


# =====================================================================
# 6) the reel is never silent
# =====================================================================

def test_an_elevenlabs_http_error_is_logged_with_its_code_and_body(caplog):
    """`HTTPError` alone cannot distinguish wrong-tier from bad-key from quota. The body names
    the cause; throwing it away is why this failure stayed undiagnosed."""
    import io as _io
    import urllib.error

    import reel.voiceover as vo

    def _boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                     _io.BytesIO(b'{"detail":{"status":"voice_not_found"}}'))
    import logging
    caplog.set_level(logging.WARNING)
    ok = vo._elevenlabs_segment("hello", Path("nul"), voice="v", model="m",
                                api_key="k", _urlopen=_boom)
    assert ok is False
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "401" in joined and "voice_not_found" in joined


def test_a_failed_paid_backend_falls_to_edge_instead_of_silence(monkeypatch, tmp_path):
    """FIXTURE CORRECTED after a TypeError: the first version invented a signature
    (`synth_voiceover(text, out_dir=...)`); the real one is (lines, durations, out_path).
    ffmpeg post-processing is stubbed — the test is about the FALLBACK decision, not audio."""
    import reel.voiceover as vo
    monkeypatch.setenv("REEL_TTS_BACKEND", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(vo, "_elevenlabs_segment", lambda *a, **k: False)
    made = {}

    def _fake_edge(script, out, *, voice, rate="", pitch="", **kw):
        made["voice"] = voice
        Path(out).write_bytes(b"ID3" + b"0" * 64)
        return True
    monkeypatch.setattr(vo, "_edge_segment", _fake_edge)
    monkeypatch.setattr(vo, "_audio_dur", lambda p: 5.0)
    monkeypatch.setattr(vo, "run_ffmpeg", lambda args: Path(args[-1]).write_bytes(b"aac"))
    out = vo.synth_voiceover(["أهلاً بيكم في أبو عوف"], [5.0], tmp_path / "vo.m4a")
    assert out is not None, "the paid backend failed and the reel went silent anyway"
    assert made, "edge was never tried"


# =====================================================================
# 7) honest ledgers + the pro lane
# =====================================================================

def test_the_ledger_prices_the_model_that_actually_served(monkeypatch, tmp_path):
    """MEASURED bug: nano.py priced the REQUESTED lane and recorded the SERVED model — pro 404 →
    flash serves → ledger says flash at $0.12, a 3x over-report."""
    import vertex_spend
    import reel.render3.nano as nano
    rows = []
    monkeypatch.setattr(vertex_spend, "record",
                        lambda kind, n, usd, **kw: rows.append({"usd": usd, **kw}))
    monkeypatch.setattr(vertex_spend, "guard_image", lambda *a, **k: 0.12)

    class _Client:
        class models:
            @staticmethod
            def generate_content(*a, **k):
                model = k.get("model") or a[0]
                if "pro" in str(model):
                    raise RuntimeError("404 NOT_FOUND")

                class _Part:
                    inline_data = type("D", (), {"data": _png_bytes(), "mime_type": "image/png"})()

                class _Resp:
                    candidates = [type("C", (), {"content": type("Ct", (), {"parts": [_Part()]})()})()]
                return _Resp()
    monkeypatch.setattr(nano, "_client", lambda loc: _Client())
    nano.generate_with_refs("a scene", tmp_path / "s.png", pro=True)
    assert rows, "nothing was recorded"
    assert "flash" in str(rows[-1].get("model", "")), rows[-1]
    assert rows[-1]["usd"] == pytest.approx(0.04), (
        f"the flash lane was billed at the pro price: {rows[-1]}")


def test_image_lane_pro_puts_pro_first(monkeypatch):
    import poster.oneshot as oneshot
    monkeypatch.setenv("IMAGE_LANE", "pro")
    assert "pro-image" in oneshot.resolve_oneshot_model()
    monkeypatch.delenv("IMAGE_LANE", raising=False)
    assert oneshot.resolve_oneshot_model() == oneshot.DEFAULT_ONESHOT_MODEL


# =====================================================================
# 7) the seed gate vs the REAL product label
#    LIVE FINDING (Abu Auf run 2026-07-25, runs/reel_abuauf_agency.log): the gate rejected
#    EVERY pool-conditioned seed with "the product packaging contains clear, readable text
#    which will be garbled" — 13 paid attempts, ~all scenes seedless. The no_text rule was
#    calibrated for VEO-era invented signage; it now kills exactly what the owner ordered:
#    «عايزة المنتج يجي زي ما هو بالظبط». Real label text from an attached reference is the
#    DELIVERABLE; invented pseudo-text is still the defect.
# =====================================================================

def _seed_gate_probe(real_refs: bool):
    from reel.scene_qa import check_seed_frame
    seen = {}

    def caller(system, user, schema, group_name=None, images=None):
        seen["system"] = system

        class _R:
            anatomy_ok = True; lighting_real = True; composition_clean = True
            ad_grade = True; no_text = True; score = 9; reason = "clean"
        return _R(), {}

    check_seed_frame(b"fakepng", caller=caller, brand_hint="Abu Auf",
                     real_product_refs=real_refs)
    return seen["system"]


def test_the_gate_allows_the_real_label_when_refs_were_attached():
    """real_product_refs=True: readable text that faithfully reproduces the ATTACHED product's
    real packaging must be acceptable; invented/garbled text must still fail."""
    system = _seed_gate_probe(True)
    low = system.lower()
    assert "no readable or pseudo-text of any kind" not in low, (
        "the blanket Veo-era text ban is still active even though the seed was conditioned "
        "on the brand's real product reference")
    assert "packaging" in low and ("faithful" in low or "real product" in low), system
    assert "garbled" in low or "invented" in low, (
        "pseudo-text lost its ban entirely — the gate swung from too strict to absent")


def test_the_gate_keeps_the_blanket_ban_for_unconditioned_seeds():
    """No real refs attached -> any readable text is still invented text -> still banned."""
    system = _seed_gate_probe(False)
    assert "no readable or pseudo-text of any kind" in system.lower()


def test_seed_gen_tells_the_gate_when_real_refs_were_attached(monkeypatch, tmp_path):
    """The wiring: an attempt that attached real product refs must call the gate with
    real_product_refs=True; the unconditioned attempt 3 must not."""
    import reel.seed_gen as sg
    import reel.scene_qa as sq

    calls = []

    def fake_gate(image, *, caller=None, brand_hint="", real_product_refs=False, **kw):
        calls.append(bool(real_product_refs))

        class _V:
            overall_pass = False if len(calls) < 3 else True
            checked = True; reason = "probe"
        return _V()

    import poster.oneshot as oneshot
    monkeypatch.setattr(sq, "check_seed_frame", fake_gate)
    monkeypatch.setattr(oneshot, "generate_oneshot_poster",
                        lambda prompt, path, **kw: Path(path).write_bytes(b"png"))
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: b"refbytes")

    class _Scene:
        veo_prompt = "a hand lifts the pack"
        image_index = 0
        hero_product = True     # v3 motion policy: only a hero scene carries the real pack

    class _Creative:
        scenes = [_Scene()]

    profile = {"name": "Abu Auf", "source_url": "https://abuauf.test/"}
    sg.generate_scene_seeds(_Creative(), [], profile=profile, out_dir=tmp_path,
                            caller=object(), ref_pool=["https://abuauf.test/p.jpg"],
                            log=lambda *a, **k: None)
    assert calls, "the gate was never called"
    # attempts 1-2 attach the pool (no gated photo) -> True; attempt 3 is unconditioned -> False
    assert calls[0] is True, f"pool-conditioned attempt hid the refs from the gate: {calls}"
    assert False in calls[:3], f"the unconditioned attempt still claimed real refs: {calls}"
