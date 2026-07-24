# -*- coding: utf-8 -*-
"""CS-104: the scrape-QA 'Products' count must use the SAME rule as the studio picker.

Before the fix, `scrape_qa_for_slug` called `products_from_manifest` WITHOUT the profile, so a
brand whose products come from its extracted OFFERINGS (education/services/clinics with no Shopify
URL slugs) reported 0 products in QA while the picker offered a full list (22/90 brands). Hermetic.
"""
from __future__ import annotations

import json
import os


def _setup(tmp_path):
    slug = "acmeedu"
    scrapes = tmp_path / "scrapes"
    out = tmp_path / "outputs"
    (scrapes / f"{slug}_20260101_000000").mkdir(parents=True)
    out.mkdir()
    # a manifest with NO Shopify product URLs (an education site) — the old rule finds 0 here
    manifest = {"scrape_meta": {"final_url": "https://acmeedu/", "pages_attempted": 3,
                                "pages_succeeded": 3, "duration_ms": 1000},
                "readiness": {"ready_for_extraction": True},
                "pages": [{"url": "https://acmeedu/", "text_blocks": [{"text": "Welcome"}]}],
                "images_of_interest": [], "notes": [], "failures": []}
    (scrapes / f"{slug}_20260101_000000" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    # a profile whose OFFERINGS are the real products (source #1 the QA count used to ignore)
    profile = {"profile": {"name": {"value": "Acme Edu"}, "category": {"value": "education"},
                           "offerings": [{"name": "Python Bootcamp", "price_text": "$300"},
                                         {"name": "Data Science Track", "price_text": "$500"},
                                         {"name": "UX Design Course"}]}}
    (out / f"{slug}_profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return slug, str(scrapes), str(out)


def test_qa_product_count_matches_the_picker(tmp_path):
    from dashboard.products import scrape_qa_for_slug, products_for_slug
    slug, scrapes, out = _setup(tmp_path)

    picker = products_for_slug(slug, scrapes_dir=scrapes, out_dir=out)
    qa = scrape_qa_for_slug(slug, scrapes_dir=scrapes, out_dir=out)

    assert picker, "the picker should find offering-based products for an education brand"
    assert qa is not None
    # the QA count now reflects the SAME products the picker offers — not 0
    assert qa["products"] == len(picker)
    assert qa["products"] >= 3
