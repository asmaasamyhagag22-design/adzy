# -*- coding: utf-8 -*-
"""The trends FILE must be reachable from the deck.

Owner's rule: every dataset gets a file — and a file she can't find from the page is not an
artifact, it's a secret. The market page names the file (and its item count) under the pulse, so
she can open the full pull instead of only the 8 shown. Hermetic."""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.war_room import _page_market


def _result():
    return {"trends": [{"title": "سيروم فيتامين سي", "url": "https://x.example/a",
                        "trend_score": 0.8, "source": "serper"}],
            "competitors": []}


def test_market_page_names_the_trends_file_when_it_exists(tmp_path: Path):
    (tmp_path / "brand_trends.json").write_text(
        json.dumps({"items": [1, 2, 3, 4, 5, 6], "_meta": {"count": 6}}), encoding="utf-8")
    html = _page_market(_result(), slug="brand", out_dir=str(tmp_path))
    assert "brand_trends.json" in html
    assert "6" in html                                     # the full count, not just the 8 shown


def test_no_file_means_no_dead_link(tmp_path: Path):
    html = _page_market(_result(), slug="brand", out_dir=str(tmp_path))
    assert "_trends.json" not in html


def test_market_page_still_works_without_the_new_arguments():
    # back-compat: older callers pass only the result dict
    assert "<section" in _page_market(_result())
