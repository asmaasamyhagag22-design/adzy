"""P-81 (D-8): competitor discovery via Places must search the BRAND'S market, not a hardcoded
EG/ar default. The old PlacesClient defaulted region_code="EG", language_code="ar", and BOTH live
entrypoints instantiated PlacesClient() with no args — so a Saudi/Qatari/US brand's peers were
searched in the Egyptian market in Arabic. Now the default is neutral (omitted from the request),
and the caller passes the brand's evidenced (gl, hl).

Hermetic: no network — we inspect the request body the client would send, via _locale_body().
"""
from __future__ import annotations

import pytest

from competitor.places_client import PlacesClient, PlacesError


def _client(**kw):
    # Provide a dummy key so construction doesn't raise for missing creds.
    return PlacesClient(api_key="TEST_KEY", **kw)


def test_neutral_default_omits_market_fields():
    c = _client()
    assert c.region_code == "" and c.language_code == ""
    assert c._locale_body() == {}, "empty market must be OMITTED, not sent as a guess"


def test_brand_market_flows_into_the_request_body():
    c = _client(region_code="SA", language_code="ar")
    assert c._locale_body() == {"languageCode": "ar", "regionCode": "SA"}


def test_partial_market_only_sends_what_is_known():
    assert _client(region_code="QA")._locale_body() == {"regionCode": "QA"}
    assert _client(language_code="en")._locale_body() == {"languageCode": "en"}


def test_default_is_not_egypt_arabic():
    # The specific regression: the class must NOT silently bias every search to Egypt/Arabic.
    c = _client()
    assert c.region_code != "EG"
    assert c.language_code != "ar"
