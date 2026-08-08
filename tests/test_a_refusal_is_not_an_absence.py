"""A refused Places lookup and a brand with no listing must not look identical.

MEASURED 2026-08-02: every SWOT on the web path had an empty Threats quadrant. The stored
explanation was "this brand has no Places listing". The truth, found by calling the API
directly, was a 403 — the key is suspended, the same Google account problem that stopped the
video and text models. Nobody could tell the two apart, because `find_subject_places` caught
every exception and returned the same `None` an honest empty result returns. An investigation
concluded the branch was dead in the CODE; the code was fine.

Second finding from the same call: the 403 body Google returns ECHOES THE API KEY back, and
that body went verbatim into the exception message — so the module docstring's promise that
"the key is never logged" held for our code and failed for our errors.

These tests lock both: the refusal speaks, and it speaks without the credential.
"""
from __future__ import annotations

import logging

import pytest

from competitor.discovery import find_subject_places
from competitor.places_client import PlacesError, _redact_secrets

# Shaped like a real Google key, and NOT a real one: the prefix and length are what the
# redactor keys on, so a synthetic value exercises the same path a live leak would.
FAKE_KEY = "AIzaSyC" + "0" * 32
_403_BODY = (
    '{\n  "error": {\n    "code": 403,\n    "message": "Permission denied: Consumer '
    f"'api_key:{FAKE_KEY}' has been suspended.\",\n    \"status\": \"PERMISSION_DENIED\"\n" + "  }\n}"
)


class _Refusing:
    """A client whose lookup is REFUSED — the state we could not previously see."""

    def search_text(self, query, max_results=5):
        raise PlacesError(f"403 from https://places.googleapis.com/v1/places:searchText: {_403_BODY}")


class _Empty:
    """A client that answered fine and found nothing — the state we always reported."""

    def search_text(self, query, max_results=5):
        return []


PROFILE = {"name": "صيدلية دار الفؤاد", "physical_address": "القاهرة"}


# --- the credential never reaches a log line ------------------------------------------

def test_the_redactor_removes_a_key_google_handed_back():
    out = _redact_secrets(_403_BODY)
    assert FAKE_KEY not in out
    assert "[REDACTED]" in out
    # It redacts the SECRET, not the diagnosis — the reason must survive or the log is useless.
    assert "has been suspended" in out
    assert "403" in out


def test_the_redactor_catches_a_key_that_reached_a_query_string():
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address=x&key={FAKE_KEY}"
    assert FAKE_KEY not in _redact_secrets(url)


def test_the_exception_message_does_not_carry_the_key(caplog):
    with caplog.at_level(logging.WARNING):
        find_subject_places(PROFILE, _Refusing())
    assert FAKE_KEY not in caplog.text, "a live credential was written to the log"


# --- the two states are distinguishable ------------------------------------------------

def test_a_refusal_is_logged(caplog):
    with caplog.at_level(logging.WARNING):
        result = find_subject_places(PROFILE, _Refusing())
    assert result is None, "the contract holds: a lookup failure never breaks the SWOT"
    assert "[places]" in caplog.text
    assert "403" in caplog.text, "the log must carry the REASON, not just the fact of failure"


def test_an_honest_empty_result_is_silent(caplog):
    """The control. Without this, 'we now log' proves nothing — logging BOTH states would
    leave them exactly as indistinguishable as returning None for both did."""
    with caplog.at_level(logging.WARNING):
        result = find_subject_places(PROFILE, _Empty())
    assert result is None
    assert "[places]" not in caplog.text, "an empty result is not a failure and must not read as one"


@pytest.mark.parametrize("client,expected_noise", [(_Refusing(), True), (_Empty(), False)])
def test_the_caller_can_tell_them_apart(client, expected_noise, caplog):
    with caplog.at_level(logging.WARNING):
        find_subject_places(PROFILE, client)
    assert bool("[places]" in caplog.text) is expected_noise
