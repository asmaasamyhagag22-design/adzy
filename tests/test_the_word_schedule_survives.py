# -*- coding: utf-8 -*-
"""The word schedule must land next to the finished track, not inside a temp directory.

MEASURED on the first live run of the word-sync feature (Nano Care, 2026-07-27): the render made
its ElevenLabs call, the words came back — and `find . -name "*.words.json"` returned NOTHING.

`synth_voiceover` synthesises into `raw = tmpd / "vo_full.mp3"` inside a
`tempfile.TemporaryDirectory`, and `tts_with_timestamps` writes its sidecar beside whatever path it
was handed. So the schedule was written and then deleted with the temp directory, and
`creative.py` — which looks beside the FINAL `.vo.m4a` — found nothing and every caption silently
fell back to the designed stagger.

Produced, serialized, never reaches. I wrote this one myself, an hour after writing the tests that
catch it in other people's code.
"""
from __future__ import annotations

import inspect
import json
import re


def test_the_sidecar_is_written_beside_the_final_track_not_the_temp_file():
    """Pinned on the source: the write must key off `out_path`, which outlives the tempdir."""
    import reel.voiceover as vo
    src = inspect.getsource(vo.synth_voiceover)
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    m = re.search(r'Path\(str\((\w+)\)\s*\+\s*"\.words\.json"\)\.write_text', body)
    assert m, "nothing writes a .words.json beside a durable path"
    assert m.group(1) == "out_path", (
        f"the schedule is written beside {m.group(1)!r} — if that is the temp `raw`, it dies "
        "with the TemporaryDirectory")


def test_the_reader_looks_where_the_writer_writes():
    """Writer and reader must agree on the path, or the feature is silently inert."""
    import reel.creative as rc
    src = inspect.getsource(rc)
    assert '.words.json' in src, "creative.py never looks for a schedule"
    assert 'str(vo_path) + ".words.json"' in src, (
        "the reader does not use the same `<track>.words.json` shape the writer produces")


def test_a_schedule_on_disk_reaches_the_scenes(tmp_path):
    """End to end on the mapping itself: a sidecar beside the track becomes per-scene delays."""
    from reel.word_sync import caption_delays

    words = [{"word": "طاقتك", "start": 0.2, "end": 0.6},
             {"word": "كاملة", "start": 0.8, "end": 1.2},
             {"word": "اسألي", "start": 4.4, "end": 4.9}]
    p = tmp_path / "reel.vo.m4a.words.json"
    p.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    loaded = json.loads(p.read_text(encoding="utf-8"))
    first, cursor = caption_delays("طاقتك كاملة", loaded, scene_start_s=0.0)
    assert first == (0.2, 0.8)
    second, _ = caption_delays("اسألي", loaded, scene_start_s=4.0, search_from=cursor)
    assert second == (0.4,)
