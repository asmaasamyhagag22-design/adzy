# -*- coding: utf-8 -*-
"""The Apify-IG bridge (owner-authorized 2026-07-19): rows -> extractor signals, PII-free.
Hermetic — the pure converter only; the live pull is tripwired."""
from __future__ import annotations

from scripts.pull_instagram_apify import signals_from_apify_items


def test_signals_conversion_posts_and_comments_pii_free():
    items = [{"url": "https://instagram.com/p/AAA/",
              "caption": "خصم 20% على الليبوسومال الأسبوع ده",
              "latestComments": [
                  {"text": "الحديد عمل معايا نتيجة رهيبة", "ownerUsername": "SECRET_USER"},
                  {"text": "", "ownerUsername": "x"},
              ]},
             {"url": "https://instagram.com/p/BBB/", "caption": ""},
             "garbage"]
    sigs = signals_from_apify_items(items)
    assert len(sigs) == 2
    assert sigs[0].signal_kind == "post" and "خصم 20%" in sigs[0].text
    assert sigs[1].signal_kind == "comment" and "نتيجة رهيبة" in sigs[1].text
    assert all("SECRET_USER" not in s.text for s in sigs)     # author names never travel
    assert all(s.source_url.startswith("https://instagram.com/p/") for s in sigs)


def test_live_pull_refused_under_no_spend(monkeypatch):
    import sys

    import pytest

    import scripts.pull_instagram_apify as cli
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    monkeypatch.setattr(sys, "argv", ["x", "someone", "--brand-slug", "b"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "SPENDS REAL MONEY" in str(e.value)
