# -*- coding: utf-8 -*-
"""The caption word pops when the voice says it — real timestamps, not a fixed stagger."""
from __future__ import annotations

import base64
import io
import json


def test_alignment_groups_on_any_whitespace_not_just_space():
    """The reference snippet split on " " only — a newline welds two words and shifts every
    timestamp after it."""
    from reel.word_sync import words_from_alignment
    chars = list("طاقتك\nكاملة")
    starts = [i * 0.1 for i in range(len(chars))]
    ends = [s + 0.1 for s in starts]
    words = words_from_alignment(chars, starts, ends)
    assert [w["word"] for w in words] == ["طاقتك", "كاملة"]
    import pytest
    assert words[1]["start"] == pytest.approx(0.6)


def test_a_trailing_word_is_not_dropped():
    from reel.word_sync import words_from_alignment
    words = words_from_alignment(list("ab c"), [0, .1, .2, .3], [.1, .2, .3, .4])
    assert [w["word"] for w in words] == ["ab", "c"]


def test_tts_writes_audio_and_sidecar(tmp_path):
    from reel.word_sync import tts_with_timestamps
    payload = {"audio_base64": base64.b64encode(b"ID3fake").decode(),
               "alignment": {"characters": list("hi go"),
                             "character_start_times_seconds": [0, .1, .2, .3, .4],
                             "character_end_times_seconds": [.1, .2, .3, .4, .5]}}

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    words = tts_with_timestamps(
        "hi go", tmp_path / "vo.mp3", voice="v", model="m", api_key="k",
        voice_settings={"stability": 0.4},
        _urlopen=lambda req, timeout=None: _R(json.dumps(payload).encode()))
    assert [w["word"] for w in words] == ["hi", "go"]
    assert (tmp_path / "vo.mp3").read_bytes() == b"ID3fake"
    side = json.loads((tmp_path / "vo.mp3.words.json").read_text(encoding="utf-8"))
    assert side[1]["start"] == 0.3


def test_tts_failure_returns_none_and_writes_nothing(tmp_path):
    from reel.word_sync import tts_with_timestamps

    def _boom(req, timeout=None):
        raise OSError("down")

    assert tts_with_timestamps("x", tmp_path / "vo.mp3", voice="v", model="m",
                               api_key="k", voice_settings={}, _urlopen=_boom) is None
    assert not (tmp_path / "vo.mp3").exists()


def test_caption_delays_come_from_the_schedule():
    from reel.word_sync import caption_delays
    words = [{"word": "طاقتك", "start": 2.4, "end": 2.9},
             {"word": "كاملة", "start": 3.0, "end": 3.5},
             {"word": "ومعدتك", "start": 3.6, "end": 4.1}]
    got = caption_delays("طاقتك كاملة", words, scene_start_s=2.0)
    assert got is not None
    delays, nxt = got
    assert delays == (0.4, 1.0)
    assert nxt == 2


def test_a_caption_the_voice_never_says_keeps_the_designed_stagger():
    from reel.word_sync import caption_delays
    words = [{"word": "اسألي", "start": 1.0, "end": 1.4}]
    assert caption_delays("عرض خاص", words, scene_start_s=0.0) is None


def test_audio_leading_a_cut_clamps_to_zero_not_negative():
    """The J-cut: the voice may start a breath before the video cut lands."""
    from reel.word_sync import caption_delays
    words = [{"word": "جديد", "start": 4.8, "end": 5.2}]
    delays, _ = caption_delays("جديد", words, scene_start_s=5.0)
    assert delays == (0.0,)


def test_repeated_words_land_on_the_right_occurrence():
    from reel.word_sync import caption_delays
    words = [{"word": "جديد", "start": 1.0, "end": 1.2},
             {"word": "جديد", "start": 6.0, "end": 6.2}]
    _d1, nxt = caption_delays("جديد", words, scene_start_s=0.0)
    delays2, _ = caption_delays("جديد", words, scene_start_s=5.0, search_from=nxt)
    assert delays2 == (1.0,)
