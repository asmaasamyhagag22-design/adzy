# -*- coding: utf-8 -*-
"""The voice and the sound design — now that the ElevenLabs account is funded.

OWNER (2026-07-26): «شحنتلها ELEVEN LAB، اظبط بقا الأصوات والفويس أوفر استخدمه صح علشان تطلع
بكوالتي تحفة من الناحية دي».

MEASURED LIVE the moment it was funded (the same key returned 402 `paid_plan_required` all day
until then):
  * tier `payg`, 23,736 characters available
  * FOUR professional Egyptian-Arabic voices in the library, all male:
        QvNF0qyyt1Tuy1YAmnzH  Mostafa — Bold and Spirited
        5REPlS2Ja1VZ7zNA0ykn  Ziad — Dynamic and Vibrant
        Ojb0nFbyzZn95u0i5a5p  Marco Nady — naturally earns your trust
        VqHyN6PYNu3uNKGdbxKs  ELareef
  * `POST /v1/sound-generation` works — SFX from a text description, which is the layer
    §5 of the reel directive calls for (wrapper crinkle, crunch, whoosh, riser, room tone)
    and which this project has never had at all.

What this pins: the voice is CHOSEN from real ids by brand register (not a hardcoded name), the
delivery is steered per line, and the SFX layer exists as code with a spend record.
Hermetic — the HTTP client is injected.
"""
from __future__ import annotations

import json


class _FakeHTTP:
    """Records requests, returns bytes."""

    def __init__(self, payload=b"ID3audio", fail_code=None):
        self.calls = []
        self.payload = payload
        self.fail_code = fail_code

    def __call__(self, req, timeout=None):
        body = req.data.decode("utf-8") if req.data else ""
        self.calls.append({"url": req.full_url, "headers": dict(req.headers),
                           "body": json.loads(body) if body else {}})
        if self.fail_code:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, self.fail_code, "nope", {},
                                         __import__("io").BytesIO(b'{"detail":"x"}'))

        class _R:
            def __init__(self, data):
                self._d = data

            def read(self):
                return self._d

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _R(self.payload)


# --- the voice is chosen, not hardcoded --------------------------------------------------------

def test_an_egyptian_brand_gets_an_egyptian_voice():
    from reel.voices import pick_voice
    v = pick_voice(dialect="Egyptian Arabic", tone="energetic")
    assert v["voice_id"] in {"QvNF0qyyt1Tuy1YAmnzH", "5REPlS2Ja1VZ7zNA0ykn"}, v
    assert v["accent"] == "egyptian"


def test_a_trust_led_brand_gets_the_trust_voice():
    from reel.voices import pick_voice
    v = pick_voice(dialect="Egyptian Arabic", tone="professional trustworthy clinic")
    assert v["voice_id"] == "Ojb0nFbyzZn95u0i5a5p", v


def test_a_non_arabic_brand_is_not_given_an_egyptian_voice():
    """D-8: the voice follows the brand's own measured register, never a default market."""
    from reel.voices import pick_voice
    v = pick_voice(dialect="", tone="energetic")
    assert v.get("accent") != "egyptian", v


def test_an_explicit_env_override_wins():
    import os

    from reel.voices import pick_voice
    os.environ["REEL_TTS_VOICE"] = "ZZZcustom"
    try:
        assert pick_voice(dialect="Egyptian Arabic", tone="x")["voice_id"] == "ZZZcustom"
    finally:
        os.environ.pop("REEL_TTS_VOICE", None)


# --- the request that actually ships ------------------------------------------------------------

def test_the_tts_request_carries_the_multilingual_model_and_steering(monkeypatch):
    import reel.voiceover as vo
    http = _FakeHTTP()
    ok = vo._elevenlabs_segment("اطلب دلوقتي!", __import__("pathlib").Path("x.mp3"),
                                voice="QvNF0qyyt1Tuy1YAmnzH", model="eleven_multilingual_v2",
                                api_key="k", _urlopen=http, delivery="excited, urgent CTA")
    assert ok is True or http.calls
    body = http.calls[0]["body"]
    assert body["model_id"] == "eleven_multilingual_v2"
    vs = body["voice_settings"]
    assert 0.0 <= vs["stability"] <= 0.6, vs        # low stability = expressive, not flat
    assert vs["style"] > 0.3, vs
    assert vs.get("use_speaker_boost") is True


def test_a_calm_line_is_steered_differently_from_an_urgent_one(monkeypatch):
    import reel.voiceover as vo
    a, b = _FakeHTTP(), _FakeHTTP()
    p = __import__("pathlib").Path("x.mp3")
    vo._elevenlabs_segment("اطلب!", p, voice="v", model="m", api_key="k", _urlopen=a,
                           delivery="excited, urgent")
    vo._elevenlabs_segment("اهلا بيك", p, voice="v", model="m", api_key="k", _urlopen=b,
                           delivery="calm, warm")
    assert a.calls[0]["body"]["voice_settings"] != b.calls[0]["body"]["voice_settings"]


# --- the SFX layer ------------------------------------------------------------------------------

def test_a_sound_effect_is_generated_from_a_description(tmp_path):
    from reel.sfx import generate_sfx
    http = _FakeHTTP(payload=b"SFXBYTES")
    out = generate_sfx("crisp bite and crunch of a nut bar", tmp_path / "crunch.mp3",
                       seconds=2.0, api_key="k", _urlopen=http)
    assert out and out.read_bytes() == b"SFXBYTES"
    call = http.calls[0]
    assert "sound-generation" in call["url"]
    assert call["body"]["duration_seconds"] == 2.0


def test_sfx_failure_returns_none_and_never_raises(tmp_path):
    from reel.sfx import generate_sfx
    assert generate_sfx("x", tmp_path / "a.mp3", api_key="k",
                        _urlopen=_FakeHTTP(fail_code=402)) is None
    assert generate_sfx("x", tmp_path / "b.mp3", api_key="") is None


def test_every_sfx_call_is_metered(tmp_path, monkeypatch):
    """Sound generation is billed. A layer with no spend record is how the Veo scare happened."""
    import reel.sfx as sfx
    rows = []
    monkeypatch.setattr(sfx, "_record_spend", lambda **kw: rows.append(kw))
    sfx.generate_sfx("whoosh", tmp_path / "w.mp3", seconds=1.5, api_key="k",
                     _urlopen=_FakeHTTP())
    assert rows and rows[0]["seconds"] == 1.5


def test_the_scene_cue_maps_to_a_real_description():
    """The director already writes `sfx: whoosh|impact|chime`. Those tokens must become sound
    descriptions, not be passed through as one-word prompts."""
    from reel.sfx import describe_cue
    d = describe_cue("whoosh")
    assert len(d.split()) >= 4 and "whoosh" in d.lower()
    assert describe_cue("") == ""


def test_the_vocalized_script_is_what_elevenlabs_receives(monkeypatch, tmp_path):
    """OWNER: «ومتستغناش عن حوار التشكيل المنطوق علشان نتجنب أي مشاكل». The tashkeel pass is
    NOT an edge-voice workaround — a native voice reads «عُوف» and «عَوْف» differently too, and
    the owner's ear caught exactly that. It must reach the paid path as well."""
    import reel.voiceover as vo

    heard = {}
    monkeypatch.setattr(vo, "_vocalize_for_tts", lambda lines, **kw: ["مُشَكَّل::" + lines[0]])
    monkeypatch.setattr(vo, "_elevenlabs_segment",
                        lambda text, out, **kw: (heard.__setitem__("text", text),
                                                 out.write_bytes(b"mp3"), True)[2])
    monkeypatch.setattr(vo, "_audio_dur", lambda p: 4.0)
    monkeypatch.setattr(vo, "run_ffmpeg", lambda args: __import__("pathlib").Path(args[-1]).write_bytes(b"a"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    out = vo.synth_voiceover(["اطلب دلوقتي"], [3.0], tmp_path / "vo.m4a", backend="elevenlabs",
                             voice="QvNF0qyyt1Tuy1YAmnzH")
    assert out is not None
    assert "مُشَكَّل::" in heard.get("text", ""), (
        f"the paid voice received the RAW spelling: {heard.get('text')!r}")
