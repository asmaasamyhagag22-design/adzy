# -*- coding: utf-8 -*-
"""Apify's pages must be written to disk as real PageRecords — not stuffed in as dicts.

MEASURED (pepsi.com, 2026-07-21): the fallback fired, Apify returned 17 usable pages in 79s — and
the run STILL printed the block message. `recover_blocked_scrape` was assigning raw Apify dicts to
`manifest.pages`, but a `PageRecord` (`scraper/schemas.py`) does not hold text at all: it holds
`html_path` and `clean_text_path` pointing at FILES the profile builder reads later. The assignment
failed validation, the broad `except` swallowed it, and a working fetch looked like a dead site.

So the recovery writes the same file layout the crawler writes (`raw/*.html`, `clean/*.txt`) and
builds PageRecords over it. Hermetic — tmp dirs, no network."""
from __future__ import annotations

from pathlib import Path

from competitor.full_run import recover_blocked_scrape


class _Meta:
    input_url = "https://www.pepsi.com/"


class _Manifest:
    def __init__(self):
        self.pages = []
        self.failures = []
        self.scrape_meta = _Meta()


def _res(n=3):
    return type("R", (), {
        "pages": [{"url": f"https://www.pepsi.com/p{i}", "text": f"real body {i}",
                   "html": f"<html><body>real body {i}</body></html>"} for i in range(n)],
        "source": "apify"})()


def test_pages_are_written_to_disk_and_recorded(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=lambda u: _res(3), scrape_dir=tmp_path)
    assert man is not None, "a successful Apify fetch must not read as a dead site"
    assert len(man.pages) == 3
    rec = man.pages[0]
    text_path = Path(getattr(rec, "clean_text_path"))
    assert text_path.is_file()
    assert "real body" in text_path.read_text(encoding="utf-8")
    assert Path(getattr(rec, "html_path")).is_file()


def test_the_record_shape_is_the_real_one(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=lambda u: _res(1), scrape_dir=tmp_path)
    rec = man.pages[0]
    for field in ("url", "html_path", "clean_text_path", "page_type", "tier"):
        assert getattr(rec, field, None) is not None, field


def test_the_first_page_is_typed_as_the_homepage(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=lambda u: _res(2), scrape_dir=tmp_path)
    pt = getattr(man.pages[0], "page_type")
    assert getattr(pt, "value", pt) == "homepage"   # enum, not its str() repr


def test_provenance_is_stamped(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=lambda u: _res(1), scrape_dir=tmp_path)
    assert getattr(man, "content_source", "") == "apify"


def test_a_failure_to_write_still_degrades_quietly(tmp_path: Path):
    bad = tmp_path / "nope" / "\0invalid"
    assert recover_blocked_scrape("https://x.example/", _Manifest(),
                                  fetch=lambda u: _res(1), scrape_dir=bad) is None
