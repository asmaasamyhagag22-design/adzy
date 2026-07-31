# -*- coding: utf-8 -*-
"""Reel v3 — the six fixes after the owner's v2 verdict (2026-07-26).

OWNER: «الريل لقطات قليلة اللي بتتحرك، صور ساكنة مبوّظة الفيديو زي لقطة الجيم، الاند مش في
الفيديو، الصوت بشع برود، متركب كأنه صور ولا بعض مفيش سيناريو... ويلا اشتغل في الاصلاحات
ومتنساش ولا تسقط حاجة».

Measured roots this file pins (each test names its measurement):
  1. Seeds ship in ANY aspect (114 on disk: 768x1376, 1120x928, 1024x1024) -> landscape clips
     letterboxed inside the 9:16 canvas = the "broken static" gym look.
  2. No protagonist lock without a dashboard-selected influencer -> 5 different men in 8 scenes.
  3. Endcard xfade failed on a dims mismatch (reel 772x1372 vs card 768x1366), returned False
     "best-effort" silently, 0-byte _with_endcard.mp4 left on disk, [ok] printed anyway.
  4. The VO joiner STRIPS terminal !/؟ (rstrip " ،.!؟?") and the whole reel is read in ONE
     flat prosody — the deliveries the director writes never reach the free voice.
  5. The video model garbles label text it is asked to MOVE; the director never separates "static pack,
     camera moves" scenes from "free motion, no text in frame" scenes.
  6. Nobody watches the assembled film: parts pass, the whole ships broken.

Hermetic: no network, no paid calls; ffmpeg = the bundled imageio binary.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _png(path: Path, w: int, h: int, color=(120, 90, 60)) -> Path:
    Image.new("RGB", (w, h), color).save(path)
    return path


# =====================================================================
# 1) 9:16 is ENFORCED on seeds — not politely requested in prose
# =====================================================================

def test_oneshot_forwards_the_aspect_to_the_model_config(monkeypatch, tmp_path):
    """The prompt already says "VERTICAL 9:16" and the model ignores it (measured: 1120x928
    seeds on disk). The aspect must travel in image_config, not prose."""
    import poster.oneshot as oneshot
    seen = {}

    class _Models:
        @staticmethod
        def generate_content(*a, **k):
            seen["config"] = k.get("config")

            class _Part:
                inline_data = type("D", (), {
                    "data": _png_bytes_portrait(), "mime_type": "image/png"})()

            class _Resp:
                candidates = [type("C", (), {
                    "content": type("Ct", (), {"parts": [_Part()]})()})()]
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr(oneshot, "_client", lambda *a, **k: _Client())
    monkeypatch.setattr(oneshot, "_record_image_spend", lambda *a, **k: None, raising=False)
    import vertex_spend
    monkeypatch.setattr(vertex_spend, "guard_image", lambda *a, **k: 0.04)
    monkeypatch.setattr(vertex_spend, "record", lambda *a, **k: None)
    oneshot.generate_oneshot_poster("a frame", tmp_path / "s.png", logo_bytes=None,
                                    aspect="9:16")
    cfg = seen.get("config")
    ic = getattr(cfg, "image_config", None)
    assert ic is not None and getattr(ic, "aspect_ratio", "") == "9:16", (
        f"aspect never reached the model config: {cfg!r}")


def _png_bytes_portrait() -> bytes:
    import io as _io
    buf = _io.BytesIO()
    Image.new("RGB", (72, 128), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_a_landscape_seed_is_made_portrait_before_it_ships(monkeypatch, tmp_path):
    """Belt-and-braces: if the model still returns 1120x928, the seed that leaves seed_gen
    must be portrait (center-cropped), never a future letterbox."""
    import reel.seed_gen as sg
    import reel.scene_qa as sq
    import poster.oneshot as oneshot

    def fake_gen(prompt, path, **kw):
        _png(Path(path), 1120, 928)          # the measured landscape offender
        return Path(path)

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: (b"ref", "image/jpeg"))
    monkeypatch.setattr(sq, "check_seed_frame",
                        lambda p, **kw: type("V", (), {"overall_pass": True, "checked": True,
                                                       "reason": "clean"})())

    class _Scene:
        veo_prompt = "wide gym establishing shot"
        image_index = 0
        hero_product = True

    class _Creative:
        scenes = [_Scene()]

    out = sg.generate_scene_seeds(_Creative(), [], profile={"name": "Abu Auf"},
                                  out_dir=tmp_path, caller=object(),
                                  ref_pool=["https://x/p.jpg"], log=lambda *a, **k: None)
    seed = out[0][0]
    assert seed, "no seed came back at all"
    w, h = Image.open(seed).size
    assert h > w, f"a landscape seed shipped: {w}x{h}"
    assert abs((h / w) - (16 / 9)) < 0.08, f"not 9:16 after enforcement: {w}x{h}"


# =====================================================================
# 2) ONE protagonist — the first cast face locks the rest (self-cast)
# =====================================================================

def test_the_first_protagonist_becomes_the_identity_ref_for_later_scenes(monkeypatch, tmp_path):
    """v2 measured: 5 different men in 8 scenes («متركب كأنه صور ولا بعض»). With no selected
    influencer, the FIRST person seed that passes the gate must become identity_ref for every
    following scene."""
    import reel.seed_gen as sg
    import reel.scene_qa as sq
    import poster.oneshot as oneshot

    id_refs = []

    def fake_gen(prompt, path, **kw):
        id_refs.append(kw.get("identity_ref"))
        _png(Path(path), 768, 1376)
        return Path(path)

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: (b"ref", "image/jpeg"))
    monkeypatch.setattr(sq, "check_seed_frame",
                        lambda p, **kw: type("V", (), {"overall_pass": True, "checked": True,
                                                       "reason": "clean"})())

    def _scene(prompt):
        return type("S", (), {"veo_prompt": prompt, "image_index": 0, "hero_product": True})()

    class _Creative:
        scenes = [_scene("an Egyptian man opens the pack"),
                  _scene("the same man smiles and takes a bite")]

    sg.generate_scene_seeds(_Creative(), [], profile={"name": "Abu Auf"}, out_dir=tmp_path,
                            caller=object(), ref_pool=["https://x/p.jpg"],
                            identity_ref=None, log=lambda *a, **k: None)
    assert id_refs[0] is None, "scene 1 should have no identity yet"
    assert id_refs[-1] is not None, (
        "scene 2 was cast with NOBODY — the protagonist lock never engaged")
    assert id_refs[-1][0], "the self-cast identity ref carries no bytes"


def test_a_scene_with_no_person_does_not_seed_the_cast(monkeypatch, tmp_path):
    """A product-only macro shot must not become the 'face' of the reel."""
    import reel.seed_gen as sg
    import reel.scene_qa as sq
    import poster.oneshot as oneshot

    id_refs = []

    def fake_gen(prompt, path, **kw):
        id_refs.append(kw.get("identity_ref"))
        _png(Path(path), 768, 1376)
        return Path(path)

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: (b"ref", "image/jpeg"))
    monkeypatch.setattr(sq, "check_seed_frame",
                        lambda p, **kw: type("V", (), {"overall_pass": True, "checked": True,
                                                       "reason": "clean"})())

    def _scene(prompt):
        return type("S", (), {"veo_prompt": prompt, "image_index": 0, "hero_product": True})()

    class _Creative:
        scenes = [_scene("macro: roasted nuts cascade in golden light"),
                  _scene("granola bar rotates on a marble slab")]

    sg.generate_scene_seeds(_Creative(), [], profile={"name": "Abu Auf"}, out_dir=tmp_path,
                            caller=object(), ref_pool=["https://x/p.jpg"],
                            identity_ref=None, log=lambda *a, **k: None)
    assert all(r is None for r in id_refs), "a person-free scene polluted the cast lock"


# =====================================================================
# 3) the endcard — dims follow the REEL, and failure is LOUD
# =====================================================================

def test_the_endcard_is_rendered_at_the_reels_own_dimensions(monkeypatch, tmp_path):
    """v2 measured: reel 772x1372, card 768x1366 -> xfade refused -> no endcard. The card must
    be rendered at the probed dims of the video it joins."""
    import reel.endcard as ec

    seen = {}

    def fake_render(profile, out_png, *, width, height):
        seen["dims"] = (width, height)
        return None                      # stop the flow right after the probe we care about

    monkeypatch.setattr(ec, "render_endcard", fake_render)
    monkeypatch.setattr(ec, "_video_dims", lambda p: (772, 1372), raising=False)
    vid = tmp_path / "reel.mp4"
    vid.write_bytes(b"fake")
    ec.append_endcard_to_reel({"name": "Abu Auf"}, vid)
    assert seen.get("dims") == (772, 1372), (
        f"the card ignored the reel's real dims: {seen.get('dims')}")


def test_a_failed_endcard_is_stamped_into_the_sidecar_not_swallowed(monkeypatch, tmp_path):
    """v2 shipped [ok] with a 0-byte _with_endcard.mp4 on disk. The wrapper must stamp the
    third state into the reel's sidecar so the failure reaches every later reader."""
    import reel.__main__ as rm

    monkeypatch.setattr("reel.endcard.append_endcard_to_reel", lambda *a, **k: False)
    vid = tmp_path / "reel.mp4"
    vid.write_bytes(b"fake")
    rm._append_brand_endcard({"name": "Abu Auf"}, vid, enabled=True)
    side = vid.with_suffix(".path.json")
    assert side.is_file(), "no sidecar was written — the failure evaporated again"
    data = json.loads(side.read_text(encoding="utf-8"))
    assert data.get("endcard") == "failed", data


def test_a_successful_endcard_is_stamped_too(monkeypatch, tmp_path):
    import reel.__main__ as rm

    monkeypatch.setattr("reel.endcard.append_endcard_to_reel", lambda *a, **k: True)
    vid = tmp_path / "reel.mp4"
    vid.write_bytes(b"fake")
    rm._append_brand_endcard({"name": "Abu Auf"}, vid, enabled=True)
    data = json.loads(vid.with_suffix(".path.json").read_text(encoding="utf-8"))
    assert data.get("endcard") == "appended", data


# =====================================================================
# 4) the voice — emotive punctuation survives, deliveries reach the read
# =====================================================================

def test_the_joined_script_keeps_exclamation_and_question_marks():
    """Measured root of «برود»: rstrip(" ،.!؟?") deletes the very marks that carry the
    performance. «متتنسيش!» must reach the voice as «متتنسيش!»."""
    from reel.voiceover import _join_script
    script = _join_script(["طعم وجودة متتنسيش!", "جرب النهاردة؟", "اطلب دلوقتي"])
    assert "!" in script, f"the exclamation was stripped: {script!r}"
    assert "؟" in script, f"the question mark was stripped: {script!r}"


def test_deliveries_group_the_edge_read_with_different_prosody(monkeypatch, tmp_path):
    """One flat call for the whole reel is the measured monotone. Distinct deliveries must
    produce distinct rate/pitch groups (same voice, shifted energy)."""
    import reel.voiceover as vo

    calls = []

    def fake_edge(text, out_mp3, *, voice, rate=None, pitch=None):
        calls.append((text, rate, pitch))
        Path(out_mp3).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(vo, "_edge_segment", fake_edge)
    monkeypatch.setattr(vo, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(vo, "run_ffmpeg", lambda args: Path(args[-1]).write_bytes(b"aac"))
    for k in ("REEL_TTS_EDGE_RATE", "REEL_TTS_EDGE_PITCH"):
        monkeypatch.delenv(k, raising=False)
    out = vo.synth_voiceover(
        ["افتح الباكيت!", "استمتع بالطعم", "اطلب دلوقتي!"], [3.0, 3.0, 3.0],
        tmp_path / "vo.m4a", backend="edge",
        deliveries=["excited, high energy", "warm, relaxed", "excited, urgent CTA"])
    assert out is not None
    assert len(calls) >= 2, "everything was read in ONE flat call again"
    rates = {c[1] for c in calls}
    assert len(rates) >= 2, f"groups exist but share one prosody: {rates}"


def test_uniform_deliveries_stay_a_single_continuous_read(monkeypatch, tmp_path):
    """The one-call design fixed real bugs (changing voice / mid-word cuts). When every line
    carries the same energy there is nothing to group — one call, no regression."""
    import reel.voiceover as vo

    calls = []

    def fake_edge(text, out_mp3, *, voice, rate=None, pitch=None):
        calls.append(text)
        Path(out_mp3).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(vo, "_edge_segment", fake_edge)
    monkeypatch.setattr(vo, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(vo, "run_ffmpeg", lambda args: Path(args[-1]).write_bytes(b"aac"))
    out = vo.synth_voiceover(["سطر واحد", "سطر تاني"], [3.0, 3.0], tmp_path / "vo.m4a",
                             backend="edge", deliveries=["warm", "warm"])
    assert out is not None
    assert len(calls) == 1, f"uniform energy split into {len(calls)} calls"


def test_a_failed_verbatik_falls_to_edge_never_to_silence(monkeypatch, tmp_path):
    """The owner's ordered backend (VERBATIK_API_KEY) is 401 today — account side. Until she
    fixes the plan/key, verbatik must degrade to edge exactly like elevenlabs does."""
    import reel.voiceover as vo

    monkeypatch.setenv("VERBATIK_API_KEY", "vbk_bad")
    monkeypatch.setenv("REEL_TTS_BACKEND", "verbatik")
    monkeypatch.setattr(vo, "_verbatik_segment", lambda *a, **k: False)
    edge_called = {}

    def fake_edge(text, out_mp3, *, voice, rate=None, pitch=None):
        edge_called["yes"] = True
        Path(out_mp3).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(vo, "_edge_segment", fake_edge)
    monkeypatch.setattr(vo, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(vo, "run_ffmpeg", lambda args: Path(args[-1]).write_bytes(b"aac"))
    out = vo.synth_voiceover(["اهلا بيك"], [3.0], tmp_path / "vo.m4a")
    assert out is not None and edge_called.get("yes"), "verbatik failure cost the reel its voice"


def test_verbatik_ssml_carries_prosody_when_deliveries_exist(monkeypatch):
    """When the key works, the paid read must not be flat either: the SSML sent to Verbatik
    wraps the script in prosody derived from the deliveries."""
    import reel.voiceover as vo
    ssml = vo._verbatik_ssml("افتح الباكيت", voice="salma-ar-eg",
                             delivery="excited, high energy")
    assert "<prosody" in ssml, f"no prosody in the paid read: {ssml!r}"


# =====================================================================
# 5) motion strategy — static pack vs free motion, decided per scene
# =====================================================================

def test_a_hero_product_scene_pins_the_pack_and_moves_the_camera():
    from reel.creative import _motion_directive
    s = type("S", (), {"hero_product": True, "veo_prompt": "he lifts the pack"})()
    d = _motion_directive(s)
    assert "static" in d.lower() and "camera" in d.lower(), d
    assert "text" in d.lower() or "label" in d.lower(), (
        "the directive never protects the label from being redrawn")


def test_a_free_scene_bans_readable_text_from_the_frame():
    from reel.creative import _motion_directive
    s = type("S", (), {"hero_product": False, "veo_prompt": "gym wide shot"})()
    d = _motion_directive(s)
    assert "no readable text" in d.lower() or "no text" in d.lower(), d


def test_free_scenes_do_not_attach_product_refs(monkeypatch, tmp_path):
    """Attaching the pack to a gym wide shot is how text sneaks into free-motion frames."""
    import reel.seed_gen as sg
    import reel.scene_qa as sq
    import poster.oneshot as oneshot

    attach_per_scene = []

    def fake_gen(prompt, path, **kw):
        attach_per_scene.append(kw.get("product_images"))
        _png(Path(path), 768, 1376)
        return Path(path)

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: (b"ref", "image/jpeg"))
    monkeypatch.setattr(sq, "check_seed_frame",
                        lambda p, **kw: type("V", (), {"overall_pass": True, "checked": True,
                                                       "reason": "clean"})())

    def _scene(prompt, hero):
        return type("S", (), {"veo_prompt": prompt, "image_index": 0, "hero_product": hero})()

    class _Creative:
        scenes = [_scene("he holds the pack", True),
                  _scene("gym wide shot, high energy", False)]

    sg.generate_scene_seeds(_Creative(), [], profile={"name": "Abu Auf"}, out_dir=tmp_path,
                            caller=object(), ref_pool=["https://x/p.jpg"],
                            log=lambda *a, **k: None)
    assert attach_per_scene[0], "the hero scene lost its real product refs"
    assert not attach_per_scene[-1], (
        f"a free-motion scene still got the pack attached: {attach_per_scene[-1]!r}")


# =====================================================================
# 6) the final screening — somebody watches the FILM, not the parts
# =====================================================================

def _make_clip(tmp: Path, name: str, *, vf: str, silent: bool) -> Path:
    out = tmp / name
    args = [_ffmpeg(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x8a6f4d:s=90x160:d=1.0"]
    if silent:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    else:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.0"]
    args += ["-t", "1.0", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(out)]
    subprocess.run(args, check=True, capture_output=True)
    return out


def test_letterboxed_footage_is_flagged(tmp_path):
    from reel.final_screening import screen_reel
    clip = _make_clip(tmp_path, "banded.mp4",
                      vf="scale=90:90,pad=90:160:0:35:black", silent=False)
    v = screen_reel(clip)
    assert v.checked is True
    assert any("letterbox" in i.lower() or "band" in i.lower() for i in v.issues), v.issues


def test_full_frame_footage_with_voice_passes_the_code_checks(tmp_path):
    from reel.final_screening import screen_reel
    clip = _make_clip(tmp_path, "clean.mp4", vf="scale=90:160", silent=False)
    v = screen_reel(clip)
    assert v.checked is True
    assert not v.issues, v.issues


def test_a_silent_reel_is_flagged_not_shipped(tmp_path):
    from reel.final_screening import screen_reel
    clip = _make_clip(tmp_path, "silent.mp4", vf="scale=90:160", silent=True)
    v = screen_reel(clip)
    assert any("silent" in i.lower() or "audio" in i.lower() for i in v.issues), v.issues


def test_an_unreadable_file_is_could_not_verify_not_a_pass(tmp_path):
    from reel.final_screening import screen_reel
    bad = tmp_path / "junk.mp4"
    bad.write_bytes(b"not a video")
    v = screen_reel(bad)
    assert v.checked is False, "an unscreenable reel read as screened"
    assert v.passed is not True, "absence read as success — again"


def test_the_cli_refuses_to_ship_a_failed_screening(monkeypatch):
    """Exit path: screening fail -> nonzero exit unless explicitly allowed. Static check on
    the wiring (comments stripped so our own prose cannot satisfy it)."""
    import inspect
    import reel.__main__ as rm
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(rm).splitlines())
    assert "screen_reel" in src, "the screening gate is not wired into the shipping surface"
    assert "REEL_ALLOW_BELOW_GRADE" in src, "no explicit override — or no enforcement at all"


# =====================================================================
# 7) TTS orthography — «طريقة كتابة الكلام بتخلي الصوت يقوله صح»
#    (the owner's engineer, quoted twice). A TTS voice reads unvocalized
#    dialect Arabic with MSA guesses: wrong vowels, wrong stress, dead
#    rhythm. The lines must be REWRITTEN AS SPOKEN (full tashkeel,
#    phonetic Egyptian spelling) before any engine sees them.
# =====================================================================

def _voc_caller(returned_lines):
    def caller(system, user, schema, **kw):
        class _R:
            lines = returned_lines
        return _R(), {"total_tokens": 5}
    return caller


def test_lines_are_vocalized_before_synthesis():
    from reel.voiceover import _vocalize_for_tts
    voc = ["نُصُّ اليُومْ وِطَاقْتَكْ خِلْصِتْ؟"]
    out = _vocalize_for_tts(["نص اليوم وطاقتك خلصت؟"], caller=_voc_caller(voc))
    assert out == voc


def test_a_wrong_line_count_falls_back_to_the_originals():
    """Durations align per line — a rewrite that merges/splits lines would desync the edit."""
    from reel.voiceover import _vocalize_for_tts
    orig = ["سطر أول", "سطر تاني"]
    out = _vocalize_for_tts(orig, caller=_voc_caller(["سَطْر وَاحِد بس"]))
    assert out == orig


def test_no_caller_means_original_lines_not_a_crash():
    from reel.voiceover import _vocalize_for_tts
    orig = ["اطلب دلوقتي!"]
    assert _vocalize_for_tts(orig, caller=None) == orig


def test_synth_voiceover_feeds_the_vocalized_text_to_the_engine(monkeypatch, tmp_path):
    import reel.voiceover as vo

    monkeypatch.setattr(vo, "_vocalize_for_tts",
                        lambda lines, **kw: [f"مُشَكَّل::{l}" for l in lines])
    heard = []

    def fake_edge(text, out_mp3, *, voice, rate=None, pitch=None):
        heard.append(text)
        Path(out_mp3).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(vo, "_edge_segment", fake_edge)
    monkeypatch.setattr(vo, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(vo, "run_ffmpeg", lambda args: Path(args[-1]).write_bytes(b"aac"))
    out = vo.synth_voiceover(["اطلب دلوقتي!"], [3.0], tmp_path / "vo.m4a", backend="edge")
    assert out is not None
    assert heard and "مُشَكَّل::" in heard[0], (
        f"the engine heard the raw spelling, not the spoken one: {heard!r}")


def test_high_energy_deliveries_flip_the_read_into_the_ratified_hype():
    """Owner listen test 2026-07-26: sample D's street-smart hype is the approved register
    when the director's deliveries lean high-energy. A luxury brand keeps its poise."""
    from reel.voiceover import _instructions_for
    hype = _instructions_for(["اطلب!"], ["excited, urgent", "excited", "warm"], tone="friendly")
    assert "best friend" in hype and "NEVER a polite narrator" in hype
    lux = _instructions_for(["line"], ["excited", "excited"], tone="luxury elegant")
    assert "best friend" not in lux, "a luxury brand got the street-hype read"
    calm = _instructions_for(["line"], ["warm", "warm", "warm"], tone="friendly")
    assert "best friend" not in calm, "an all-warm arc still got hyped"


def test_the_director_is_told_hero_product_marks_every_product_scene():
    """v3 made hero_product the MOTION SWITCH (refs + static-pack contract). The prompt used to
    ask for it on "at least ONE scene" — which would strip the real products from every other
    scene, reproducing the v2 complaint «فين منتجات أبو عوف». Comments stripped so this file's
    own prose cannot satisfy the assertion."""
    import inspect
    import reel.creative_director as cd
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(cd).splitlines())
    i = src.find("- hero_product:")
    assert i > 0, "the hero_product contract vanished from the prompt"
    block = src[i:i + 420]
    assert "EVERY scene" in block, f"still asks for one hero shot only: {block[:200]!r}"
    assert "at least ONE scene that concretely" not in src


def test_a_genuinely_dark_scene_is_not_mistaken_for_letterboxing(tmp_path):
    """MEASURED false positive (2026-07-26, before the v3 render): a fully dark night frame
    tripped the letterbox check 5/5 — the gate would have exited 3 on a GOOD reel after the
    money was spent. A real letterbox is a DISCONTINUITY: dark bands around a brighter image."""
    from reel.final_screening import screen_reel
    clip = _make_clip(tmp_path, "night.mp4", vf="scale=90:160,eq=brightness=-0.42", silent=False)
    v = screen_reel(clip)
    assert not any("letterbox" in i.lower() or "band" in i.lower() for i in v.issues), v.issues


def test_one_sidecar_file_carries_both_the_path_and_the_endcard_state(monkeypatch, tmp_path):
    """MEASURED split-brain (2026-07-26): the template branch wrote `<reel>.mp4.path.json`
    while the endcard stamp and the screening read `<reel>.path.json` — two files, so the
    screening could never see a path stamp and a reader of one file never saw the other's
    state. ONE sidecar, merged keys."""
    import reel.__main__ as rm

    vid = tmp_path / "reel.mp4"
    vid.write_bytes(b"fake")
    rm._write_reel_sidecar(vid, {"path": "template_fallback"})
    monkeypatch.setattr("reel.endcard.append_endcard_to_reel", lambda *a, **k: True)
    rm._append_brand_endcard({"name": "X"}, vid, enabled=True)

    assert not (tmp_path / "reel.mp4.path.json").exists(), "the second sidecar is still written"
    data = json.loads((tmp_path / "reel.path.json").read_text(encoding="utf-8"))
    assert data.get("path") == "template_fallback", f"the path stamp was clobbered: {data}"
    assert data.get("endcard") == "appended", f"the endcard state is missing: {data}"


def test_odd_reel_dimensions_are_evened_before_the_card_encode(monkeypatch, tmp_path):
    """libx264 rejects odd dimensions: an odd-width reel would fail the card encode and cost
    the endcard a second time, in a different way than the v2 dims mismatch."""
    import reel.endcard as ec
    seen = {}
    monkeypatch.setattr(ec, "_video_dims", lambda p: (773, 1371))
    monkeypatch.setattr(ec, "render_endcard",
                        lambda profile, out_png, *, width, height: seen.update(
                            dims=(width, height)) or None)
    vid = tmp_path / "reel.mp4"
    vid.write_bytes(b"fake")
    ec.append_endcard_to_reel({"name": "X"}, vid)
    assert seen["dims"] == (772, 1370), seen
