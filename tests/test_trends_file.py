# -*- coding: utf-8 -*-
"""Every dataset gets its OWN file — the trends included.

Owner (2026-07-20): "أي حاجة مش موجودة طلعها في فايل اللي قولتلك خليه في الأوتبوت." The trend items
lived only inside the big analysis blob, so there was no artifact to open, diff or hand over — and
no record of WHAT was searched, in WHICH market, or how noisy a single SERP snapshot is.

`outputs/<slug>_trends.json` carries the items plus a `_meta` block that makes the pull auditable:
the seed keywords, the market (gl/hl), the count, and the measured noise disclosure. Hermetic."""
from __future__ import annotations

import json
from pathlib import Path

from trends.artifact import write_trends_file


def _items():
    return [{"title": "سيروم فيتامين سي", "url": "https://x.example/a", "source": "serper",
             "match_score": 0.8},
            {"title": "روتين العناية", "url": "https://y.example/b", "source": "serper"}]


def test_writes_items_and_auditable_meta(tmp_path: Path):
    p = write_trends_file("nanocareegypt_com", _items(), out_dir=str(tmp_path),
                          keywords=["سيروم", "جلوتاثيون"], gl="eg", hl="ar")
    assert p and Path(p).name == "nanocareegypt_com_trends.json"
    doc = json.loads(Path(p).read_text(encoding="utf-8"))
    assert len(doc["items"]) == 2
    meta = doc["_meta"]
    assert meta["seed_keywords"] == ["سيروم", "جلوتاثيون"]
    assert meta["gl"] == "eg" and meta["hl"] == "ar"
    assert meta["count"] == 2
    from trends.engine import NOISE_DISCLOSURE
    assert meta["disclosure"] == NOISE_DISCLOSURE          # the honesty travels WITH the data
    assert meta.get("generated_at")


def test_no_market_resolved_is_recorded_not_faked(tmp_path: Path):
    # gl/hl empty means the evidence was silent about the market — say that, don't imply a country.
    p = write_trends_file("brand", _items(), out_dir=str(tmp_path), keywords=["k"], gl="", hl="")
    meta = json.loads(Path(p).read_text(encoding="utf-8"))["_meta"]
    assert meta["gl"] == "" and "غير محدد" in meta["market_note"]


def test_empty_trends_still_writes_the_file_with_the_reason(tmp_path: Path):
    # An empty pull is INFORMATION (no on-topic signal found), not a reason to leave no artifact.
    p = write_trends_file("brand", [], out_dir=str(tmp_path), keywords=[], gl="eg", hl="ar")
    doc = json.loads(Path(p).read_text(encoding="utf-8"))
    assert doc["items"] == [] and doc["_meta"]["count"] == 0
    assert doc["_meta"]["empty_reason"]


def test_a_backfilled_file_says_so_instead_of_claiming_no_market(tmp_path: Path):
    # Files rebuilt from an older saved analysis have no record of the market/keywords used at pull
    # time. Reporting that as "no market was resolved" would be a true sentence answering a
    # different question — the pull HAD a market, we just didn't save it.
    p = write_trends_file("brand", _items(), out_dir=str(tmp_path), keywords=[], gl="", hl="",
                          backfilled_from="brand_result.json")
    meta = json.loads(Path(p).read_text(encoding="utf-8"))["_meta"]
    assert meta["backfilled_from"] == "brand_result.json"
    assert "غير محدد" not in meta["market_note"]
    assert "غير مسجّل" in meta["market_note"] or "مش مسجل" in meta["market_note"]


def test_write_never_raises_on_a_bad_path():
    assert write_trends_file("b", _items(), out_dir="\0bad", keywords=[], gl="", hl="") is None
