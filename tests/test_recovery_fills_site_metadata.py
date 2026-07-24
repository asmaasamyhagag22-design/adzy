# -*- coding: utf-8 -*-
"""Recovery must fill `manifest.site_metadata` — that is where the brand's identity is read from.

MEASURED (pepsi.com, 2026-07-21), the last layer of this bug. After the fallback fetched 12 pages
AND each saved page carried a proper `<title>`, extraction STILL returned `name=None`. The reason:
`business_profile/rules/from_metadata.py:165` reads `manifest.site_metadata` — a manifest-level
object the crawler populates — not the per-page HTML. The recovery rebuilt pages and left that
object empty, so every metadata rule had nothing to read.

Four layers, each hiding the next: dicts-not-PageRecords → hollow profile shipped as findings →
readability HTML with no head → site_metadata never filled. Hermetic."""
from __future__ import annotations

from pathlib import Path

from competitor.full_run import recover_blocked_scrape


class _SiteMeta:
    def __init__(self):
        self.title = None
        self.description = None
        self.og = {}
        self.schema_org = []


class _Manifest:
    def __init__(self):
        self.pages = []
        self.failures = []
        self.site_metadata = _SiteMeta()


def _fetch(item):
    return lambda url: type("R", (), {"pages": [item], "source": "apify"})()


def _pepsi_item():
    return {"url": "https://www.pepsi.com/",
            "metadata": {"title": "Pepsi Official Site | Refreshing the World",
                         "description": "The official home of Pepsi®."},
            "text": "Pepsi Zero Sugar", "html": "<div><h1>Pepsi</h1></div>"}


def test_the_title_reaches_site_metadata(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=_fetch(_pepsi_item()), scrape_dir=tmp_path)
    assert man is not None
    assert "Pepsi" in (man.site_metadata.title or "")


def test_the_description_reaches_site_metadata(tmp_path: Path):
    man = recover_blocked_scrape("https://www.pepsi.com/", _Manifest(),
                                 fetch=_fetch(_pepsi_item()), scrape_dir=tmp_path)
    assert "official home of Pepsi" in (man.site_metadata.description or "")


def test_existing_metadata_is_never_overwritten(tmp_path: Path):
    man = _Manifest()
    man.site_metadata.title = "Already known"
    out = recover_blocked_scrape("https://www.pepsi.com/", man,
                                 fetch=_fetch(_pepsi_item()), scrape_dir=tmp_path)
    assert out.site_metadata.title == "Already known"      # the crawler's own read wins


def test_missing_metadata_leaves_the_field_alone(tmp_path: Path):
    bare = {"url": "https://x.example/", "text": "body", "html": "<p>body</p>"}
    man = recover_blocked_scrape("https://x.example/", _Manifest(),
                                 fetch=_fetch(bare), scrape_dir=tmp_path)
    assert not (man.site_metadata.title or "")             # nothing invented


def test_a_manifest_without_site_metadata_does_not_crash(tmp_path: Path):
    class _Bare:
        def __init__(self):
            self.pages = []
            self.failures = []
    assert recover_blocked_scrape("https://x.example/", _Bare(),
                                  fetch=_fetch(_pepsi_item()), scrape_dir=tmp_path) is not None
