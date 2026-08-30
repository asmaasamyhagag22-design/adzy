"""P-29: a client deliverable must never carry a raw Python repr — not an `AudienceType.MIXED`
enum, an `EvidencedField(value=...)` model repr, nor an `<obj at 0x..>` object address.

CS-05: the first version of this scrubber was SCALAR-only, so a list-typed field
(`service_areas: list[EvidencedField[str]]`) went straight through `str()` and shipped
`serves: [{'value': 'Cairo', 'evidence': [{'page_url': ...}]}]` — a raw list-of-dicts with the
evidence internals — as a client-facing persona claim. It must clean lists, and whatever it cannot
clean it must DROP LOUDLY: a scrubber that silently passes what it can't clean is the failure class.
"""
from __future__ import annotations

import logging

from business_profile.schemas import AudienceType
from media_plan.builder import _clean_value, _field_ref


def test_enum_is_unwrapped_to_its_human_value():
    # str(AudienceType.MIXED) is the repr "AudienceType.MIXED" on 3.11 — must become "mixed".
    assert _clean_value(AudienceType.MIXED) == "mixed"
    assert _clean_value({"value": AudienceType.B2C}) == "B2C"


def test_object_repr_is_dropped_not_shipped():
    class _Weird:
        def __str__(self):
            return "<_Weird object at 0x7ffab0>"
    assert _clean_value(_Weird()) == ""
    assert _clean_value("EvidencedField(value='x', evidence=[])") == ""


def test_plain_text_survives():
    assert _clean_value("young families in Cairo") == "young families in Cairo"
    assert _clean_value({"value": "B2B buyers"}) == "B2B buyers"


def test_field_ref_claim_has_no_enum_repr():
    profile = {"audience_type": {"value": AudienceType.MIXED,
                                 "evidence": [{"page_url": "https://brand.eg/about", "quote": "..."}]}}
    ref = _field_ref(profile, "audience_type", "the brand's audience")
    assert ref is not None
    assert "AudienceType" not in ref.claim          # the leak is gone
    assert ref.claim == "the brand's audience: mixed"


# ---- CS-05: list-typed fields (the scalar-only scrubber's blind spot) ----

def test_plain_list_becomes_human_text():
    assert _clean_value(["Cairo", "Giza"]) == "Cairo، Giza"


def test_list_of_evidenced_fields_is_unwrapped():
    assert _clean_value([{"value": "Cairo", "evidence": []},
                         {"value": "Giza", "evidence": []}]) == "Cairo، Giza"


def test_evidenced_field_whose_value_is_a_list():
    assert _clean_value({"value": ["Cairo", "Giza"], "evidence": []}) == "Cairo، Giza"


def test_service_areas_claim_ships_no_dict_repr():
    """The exact leak: service_areas is `list[EvidencedField[str]]`, and the claim used to carry the
    whole list-of-dicts INCLUDING each item's evidence (page_url + quote)."""
    profile = {"service_areas": [
        {"value": "Cairo", "evidence": [{"page_url": "https://brand.eg/", "quote": "نخدم القاهرة"}]},
        {"value": "Giza", "evidence": []},
    ]}
    ref = _field_ref(profile, "service_areas", "serves")
    assert ref is not None
    assert ref.claim == "serves: Cairo، Giza"
    for leak in ("{'value'", "[{", "evidence", "page_url", "https://"):
        assert leak not in ref.claim


def test_unscrubbable_value_is_dropped_LOUDLY(caplog):
    """It must not ship, and it must not go quietly — silence is how the leak survived."""
    class _Weird:
        def __str__(self):
            return "<_Weird object at 0x7ffab0>"

    with caplog.at_level(logging.WARNING, logger="media_plan.builder"):
        assert _clean_value(_Weird()) == ""
    assert any("dropped an un-scrubbable value" in r.message for r in caplog.records), \
        "the scrubber dropped a value silently — CS-05's failure class"
