# -*- coding: utf-8 -*-
"""CS-13/P-08 (2026-07-18): art_director had its OWN 18-entry ccTLD table (a subset of
poster.locale's 60) and resolved the brand country from the source-URL ccTLD ONLY. A .com brand
(with a Saudi service-area or a +966 phone) resolved to "" and lost its region anchor. Route it
through the single, richer resolver poster.locale.country_of so both poster engines agree. Hermetic."""
from __future__ import annotations

from poster.art_director import _country


def test_country_resolves_a_dot_com_brand_via_evidence_not_only_cctld():
    # a .com Saudi brand: the old ccTLD-only lookup returned "" — country_of finds it via the
    # evidenced service_area (and would also via a +966 phone).
    prof = {"source_url": "https://brand.com/", "service_areas": [{"value": "Riyadh, Saudi Arabia"}]}
    assert _country(prof) == "Saudi Arabia"


def test_country_still_reads_the_cctld():
    assert _country({"source_url": "https://brand.com.eg/"}) == "Egypt"
    assert _country({"source_url": "https://brand.qa/"}) == "Qatar"


def test_no_duplicate_cctld_table_remains_in_art_director():
    # the drift-prone private table is gone — one country->name map lives in poster.locale.
    import poster.art_director as ad
    assert not hasattr(ad, "_CCTLD_COUNTRY"), "the duplicate ccTLD table should be deleted (CS-13)"
