# -*- coding: utf-8 -*-
"""Recovered pages must carry a real <head>, or every rule-based extractor reads nothing.

MEASURED (pepsi.com, 2026-07-21). The Apify fallback delivered 12 pages of genuine Pepsi content
and the profile still came out `name=None, category=None, 0 offerings` — which then surfaced as
eight FALSE weaknesses about the brand ("No clear brand tagline", "Narrow offering range").

The reason is in the bytes: Apify's website-content-crawler returns READABILITY-EXTRACTED html —

    <div id="readability-content"><h1>Pepsi Official Site | Refreshing the World with Pepsi</h1>...

no `<head>`, no `<title>`, no `<meta>`, no og: tags. Our extractors read exactly those for the
brand name, description and visual identity, so they found nothing — while the name sat in plain
sight in the H1 and in Apify's own `metadata.title`.

So the recovery rebuilds a minimal, HONEST head from the metadata Apify actually returned. Nothing
is invented: every value comes from the fetched item. Hermetic."""
from __future__ import annotations

from pathlib import Path

from competitor.full_run import recover_blocked_scrape


class _Manifest:
    def __init__(self):
        self.pages = []
        self.failures = []


def _apify_item():
    return {
        "url": "https://www.pepsi.com/",
        "metadata": {"title": "Pepsi Official Site | Refreshing the World",
                     "description": "The official home of Pepsi®.",
                     "languageCode": "en"},
        "text": "Pepsi Zero Sugar Wild Cherry",
        "html": '<div id="readability-content"><h1>Pepsi Official Site</h1></div>',
    }


def _fetch(items):
    return lambda url: type("R", (), {"pages": items, "source": "apify"})()


def test_the_title_survives_into_the_saved_html(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=_fetch([_apify_item()]), scrape_dir=tmp_path)
    html = Path(getattr(man.pages[0], "html_path")).read_text(encoding="utf-8")
    assert "<title>" in html.lower()
    assert "Pepsi Official Site" in html


def test_the_description_becomes_a_meta_tag(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=_fetch([_apify_item()]), scrape_dir=tmp_path)
    html = Path(getattr(man.pages[0], "html_path")).read_text(encoding="utf-8")
    assert 'name="description"' in html and "official home of Pepsi" in html


def test_the_body_content_is_kept_verbatim(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=_fetch([_apify_item()]), scrape_dir=tmp_path)
    html = Path(getattr(man.pages[0], "html_path")).read_text(encoding="utf-8")
    assert "readability-content" in html          # nothing thrown away


def test_nothing_is_invented_when_metadata_is_absent(tmp_path: Path):
    bare = {"url": "https://x.example/", "text": "some body", "html": "<p>some body</p>"}
    man = recover_blocked_scrape("https://x.example/", _Manifest(),
                                 fetch=_fetch([bare]), scrape_dir=tmp_path)
    html = Path(getattr(man.pages[0], "html_path")).read_text(encoding="utf-8")
    assert "<title></title>" not in html          # an empty title tag is worse than none
    assert "some body" in html


def test_an_h1_is_used_when_apify_gives_no_title(tmp_path: Path):
    item = {"url": "https://x.example/", "text": "b",
            "html": "<div><h1>Acme Coffee Roasters</h1></div>"}
    man = recover_blocked_scrape("https://x.example/", _Manifest(),
                                 fetch=_fetch([item]), scrape_dir=tmp_path)
    html = Path(getattr(man.pages[0], "html_path")).read_text(encoding="utf-8")
    assert "Acme Coffee Roasters" in html and "<title>" in html.lower()
