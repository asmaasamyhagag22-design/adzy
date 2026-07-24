# -*- coding: utf-8 -*-
"""Local competitor search must actually use the brand's location.

MEASURED (2026-07-21, across all 22 saved profiles): **zero** carry `physical_address`, `lat` or
`lng` — the three fields `competitor/discovery.py:110-117` reads. They are not on
`business_profile/schemas.py` at all; the schema has `locations` (:495), `service_areas` (:496) and
`contact_channels.physical_addresses` (:192), and `service_areas` holds the evidence in 13 of 22.

So `is_online_only` was **unconditionally True for every brand ever analysed**, and the whole
geocode→nearby-search branch (`discovery.py:274-300`) — which is fully implemented — never ran once
in production. A Doha café was searched with no coordinates and no location bias, and the run
logged the honest but misleading note "proximity dropped".

This pins the address accessor to the fields that actually exist. Hermetic."""
from __future__ import annotations

from competitor.discovery import _subject_address


def test_reads_service_areas_the_field_that_usually_holds_it():
    p = {"service_areas": [{"value": "Qanat Quartier"}, {"value": "The Pearl Qatar"},
                           {"value": "Qatar"}]}
    addr = _subject_address(p)
    assert addr and "Qatar" in addr
    assert "Qanat Quartier" in addr          # the specific part survives for geocoding


def test_prefers_a_real_location_over_a_service_area():
    p = {"locations": [{"address": "6B Boardwalk, The Pearl, Doha, Qatar"}],
         "service_areas": [{"value": "Qatar"}]}
    assert "Boardwalk" in _subject_address(p)


def test_reads_contact_channel_addresses():
    p = {"contact_channels": {"physical_addresses": ["12 Nile St, Giza, Egypt"]}}
    assert "Nile St" in _subject_address(p)


def test_a_genuinely_online_brand_has_no_address():
    assert _subject_address({}) == ""
    assert _subject_address({"service_areas": []}) == ""
    assert _subject_address({"offerings": [{"name": "x"}]}) == ""


def test_plain_string_shapes_are_accepted():
    # profiles are inconsistent: some fields are {"value": ...}, some bare strings
    assert "Doha" in _subject_address({"service_areas": ["Doha"]})
    assert "Doha" in _subject_address({"locations": ["Doha, Qatar"]})


def test_criteria_marks_a_located_brand_as_not_online_only():
    from competitor.discovery import build_match_criteria
    crit = build_match_criteria({"name": {"value": "Volume Cafe"}, "category": {"value": "cafe"},
                           "service_areas": [{"value": "The Pearl Qatar"}]})
    assert crit.is_online_only is False
    assert crit.address and "Pearl" in crit.address


def test_criteria_keeps_a_truly_online_brand_online():
    from competitor.discovery import build_match_criteria
    crit = build_match_criteria({"name": {"value": "Shop"}, "category": {"value": "ecommerce"}})
    assert crit.is_online_only is True
