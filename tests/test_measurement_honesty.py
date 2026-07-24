# -*- coding: utf-8 -*-
"""Batch I / A6 (2026-07-18): measurement scripts must not LIE.
CS-67: the evidence-truncation meter scored an EMPTY scrape (0 filtered blocks) at 100% coverage —
a perfect grade for a scrape with NO content (the P-43 marasim brand). 0/0 is 0%, never 100%.
CS-51: the e-commerce coverage meter now reads the REAL live cap from scraper.config, not a stale 12."""
from __future__ import annotations


def test_empty_scrape_is_not_100pct_coverage():
    from benchmark.measure_evidence_truncation import coverage_pct
    assert coverage_pct(0, 0) == 0.0            # CS-67: an empty scrape is NOT fully covered
    assert coverage_pct(5, 10) == 50.0
    assert coverage_pct(10, 10) == 100.0        # a genuinely complete scrape still reads 100%


def test_ecom_coverage_reads_the_real_cap_not_a_stale_12():
    import benchmark.measure_ecom_coverage as m
    from scraper.config import ECOMMERCE_MAX_INTERNAL_PAGES
    assert m.CAP == ECOMMERCE_MAX_INTERNAL_PAGES     # CS-51: the live cap (50), not the old 12
    assert m.CAP > 12
