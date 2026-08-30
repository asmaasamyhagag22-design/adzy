# -*- coding: utf-8 -*-
"""A generated sound effect must reach the mix — and a one-word cue can never be product-specific.

The owner, 2026-07-26: «ElevenLabs Sound Effects: بتكتبي فيها بالضبط (مثلاً: Crisp chocolate bar
bite with crunchy nuts) فبتطلعلك صوت قرمشة إعلاني ممتاز جداً … ما دي تنفع؟»

Yes — and the module for it was committed the same day the account was funded, then never called.
MEASURED before this file was written:

  * `grep -rn "generate_sfx|describe_cue" --include=*.py` over the repo -> only `reel/sfx.py`'s own
    definitions and its unit test. ZERO production callers.
  * What actually ships in the SFX slot is `reel/sound.py`, pure ffmpeg synthesis:
        impact -> `sine=frequency=62:duration=0.5`      (a 62 Hz tone)
        whoosh -> `anoisesrc=c=pink` band-passed 320-5200 Hz
        chime  -> `aevalsrc=0.5*sin(2*PI*880*t)+0.4*sin(2*PI*1318.51*t)`
    Signal generators, not sound design.
  * The deeper bottleneck is NOT the renderer. `reel/creative_director.py:536` tells the model to
    write one of `whoosh | impact | chime`, and `_CUES` maps that token to a generic description.
    "Crisp chocolate bar bite with crunchy nuts" cannot come out of the token `crunch`; the best a
    token can buy is a stock crunch that fits any brand. The cue has to carry the description.
  * `reel/sound.py:134-148` dispatches cues by ENGLISH substring, so a cue authored in any other
    language always renders as the whoosh sweep — silently.
"""
from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------
# the cue must be able to carry a real description
# ---------------------------------------------------------------------

def test_a_written_description_survives_instead_of_being_reframed():
    """The owner's own example must reach the sound model unchanged. `describe_cue` used to wrap
    ANY unknown cue as "<cue> sound effect, short, clean, close mic, no music" — which mangles a
    sentence that was already a full description."""
    from reel.sfx import describe_cue
    written = "Crisp chocolate bar bite with crunchy nuts"
    assert describe_cue(written) == written


def test_a_bare_token_still_expands_to_a_usable_description():
    """Backward compatible: an old one-word plan must not regress to a one-word prompt."""
    from reel.sfx import describe_cue
    out = describe_cue("whoosh")
    assert len(out.split()) >= 4 and "whoosh" in out.lower()


def test_an_unknown_single_token_is_still_framed():
    from reel.sfx import describe_cue
    assert describe_cue("kettle").lower().startswith("kettle")
    assert len(describe_cue("kettle").split()) >= 4


# ---------------------------------------------------------------------
# the paid call must be cached and metered
# ---------------------------------------------------------------------

def test_the_same_cue_is_paid_for_once(tmp_path, monkeypatch):
    """A reel is re-rendered many times while the owner reviews it. Paying for the same whoosh on
    every render is how a $228 bill happens."""
    import reel.sfx as sfx

    calls = []

    def _fake(description, out_path, *, seconds=2.0, **kw):
        calls.append(description)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"ID3fake-mp3-bytes")
        return Path(out_path)

    monkeypatch.setattr(sfx, "generate_sfx", _fake)
    a = sfx.sfx_clip("crunch", seconds=1.5, cache_dir=tmp_path)
    b = sfx.sfx_clip("crunch", seconds=1.5, cache_dir=tmp_path)
    assert a and b and Path(a) == Path(b)
    assert len(calls) == 1, f"the cache did not hold — paid {len(calls)} times for one cue"


def test_a_different_description_is_a_different_cache_entry(tmp_path, monkeypatch):
    import reel.sfx as sfx

    def _fake(description, out_path, *, seconds=2.0, **kw):
        Path(out_path).write_bytes(b"x" * 32)
        return Path(out_path)

    monkeypatch.setattr(sfx, "generate_sfx", _fake)
    a = sfx.sfx_clip("crisp chocolate bar bite", seconds=1.5, cache_dir=tmp_path)
    b = sfx.sfx_clip("wrapper tearing open", seconds=1.5, cache_dir=tmp_path)
    assert a and b and Path(a) != Path(b)


def test_a_failed_generation_returns_none_and_is_not_cached(tmp_path, monkeypatch):
    """A failure must never be cached as a success — the next render would inherit the silence."""
    import reel.sfx as sfx
    monkeypatch.setattr(sfx, "generate_sfx", lambda *a, **k: None)
    assert sfx.sfx_clip("whoosh", seconds=1.0, cache_dir=tmp_path) is None
    assert not list(Path(tmp_path).glob("*.mp3"))


# ---------------------------------------------------------------------
# it has to reach the mix, and fail down to the synth rather than to silence
# ---------------------------------------------------------------------

def test_a_real_clip_is_mixed_in_as_a_file_not_synthesized(tmp_path, monkeypatch):
    """The whole point: the ffmpeg graph must take the generated mp3 as an INPUT. Until now the
    cue only ever chose between three built-in generators."""
    import reel.sound as snd

    clip = tmp_path / "real.mp3"
    clip.write_bytes(b"ID3" + b"\0" * 64)
    monkeypatch.setattr(snd, "sfx_clip", lambda *a, **k: clip, raising=False)

    seen = {}

    def _fake_run(args):
        seen["args"] = list(args)
        Path(args[-1]).write_bytes(b"m4a")

    monkeypatch.setattr(snd, "run_ffmpeg", _fake_run)
    out = snd.synth_sound_bed(tmp_path / "bed.m4a", 8.0, cut_times=[3.0],
                              sfx_beats=[(3.0, "Crisp chocolate bar bite with crunchy nuts")],
                              real_sfx=True)
    assert out is not None
    args = seen["args"]
    assert str(clip) in args, "the generated clip never became an ffmpeg input"
    graph = args[args.index("-filter_complex") + 1]
    assert "adelay=3000|3000" in graph, "the real clip was not placed at its beat"


def test_when_generation_fails_the_synth_still_covers_the_beat(tmp_path, monkeypatch):
    """Fail DOWN, never to silence: the reel keeps today's behaviour when the API is unreachable."""
    import reel.sound as snd
    monkeypatch.setattr(snd, "sfx_clip", lambda *a, **k: None, raising=False)

    seen = {}

    def _fake_run(args):
        seen["args"] = list(args)
        Path(args[-1]).write_bytes(b"m4a")

    monkeypatch.setattr(snd, "run_ffmpeg", _fake_run)
    out = snd.synth_sound_bed(tmp_path / "bed.m4a", 8.0, sfx_beats=[(3.0, "impact")],
                              real_sfx=True)
    assert out is not None
    graph = seen["args"][seen["args"].index("-filter_complex") + 1]
    assert "sine=frequency=62" in " ".join(seen["args"]) or "lowpass=f=180" in graph, (
        "the beat fell through to silence instead of the synthesized impact")


def test_real_sfx_is_off_when_the_caller_says_so(tmp_path, monkeypatch):
    """No paid call may happen behind the caller's back."""
    import reel.sound as snd
    called = []
    monkeypatch.setattr(snd, "sfx_clip", lambda *a, **k: called.append(1), raising=False)
    monkeypatch.setattr(snd, "run_ffmpeg", lambda args: Path(args[-1]).write_bytes(b"m4a"))
    snd.synth_sound_bed(tmp_path / "bed.m4a", 6.0, sfx_beats=[(2.0, "whoosh")], real_sfx=False)
    assert not called


# ---------------------------------------------------------------------
# universality
# ---------------------------------------------------------------------

def _prompt_text() -> str:
    """The director's PROMPT STRINGS only — comments and docstrings stripped.

    Recurring trap in this repo (5th time): a test greps the module and the prose explaining the
    change satisfies the grep. The instruction has to be in the code the model actually receives.
    """
    import ast
    import inspect

    import reel.creative_director as cd

    src = inspect.getsource(cd)
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body or not isinstance(body[0], ast.Expr):
            continue
        val = body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            doc_lines.update(range(val.lineno, (val.end_lineno or val.lineno) + 1))
    code = "\n".join(l.split("#")[0] for i, l in enumerate(src.splitlines(), 1)
                     if i not in doc_lines)
    return "\n".join(
        n.value for n in ast.walk(ast.parse(code))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)).lower()


def test_the_director_asks_for_the_cue_in_english_because_it_is_a_machine_parameter():
    """D-8: the sound description is a parameter for an audio model, not audience-facing copy.
    An Arabic cue silently fell through every English substring test in `sound.py` and rendered as
    a whoosh. The prompt must name the language so the cue is usable for ANY brand's language."""
    text = _prompt_text()
    sfx_lines = [l for l in text.splitlines() if "sfx" in l]
    assert sfx_lines, "no prompt line mentions sfx at all"
    assert any("english" in l for l in sfx_lines), (
        "the director is never told the sfx cue must be English — a non-English cue renders as a "
        "generic whoosh with no error anywhere")


def test_the_director_is_told_to_describe_the_real_thing_not_pick_a_token():
    """A token cannot be product-specific. The prompt has to ask for a description."""
    text = _prompt_text()
    sfx_lines = [l for l in text.splitlines() if "sfx" in l]
    assert any("describe" in l for l in sfx_lines), (
        "the sfx instruction still only offers a closed token list")


# ---------------------------------------------------------------------
# spend is recorded
# ---------------------------------------------------------------------

def test_every_generated_second_lands_in_the_ledger(tmp_path, monkeypatch):
    import reel.sfx as sfx
    ledger = tmp_path / "spend.jsonl"
    monkeypatch.setenv("ELEVENLABS_SPEND_LEDGER", str(ledger))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ID3" + b"\0" * 100

    sfx.generate_sfx("a crisp bite", tmp_path / "o.mp3", seconds=1.25,
                     _urlopen=lambda *a, **k: _R())
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows and rows[-1]["seconds"] == 1.25 and rows[-1]["provider"] == "elevenlabs"
