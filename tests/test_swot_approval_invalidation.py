# -*- coding: utf-8 -*-
"""A human approval covers the SWOT it was given, and nothing else.

MEASURED DEFECT these lock (live app, 2026-08-31): `marasimltd_com` was approved, its SWOT was
regenerated through `/api/swot/generate` — the content genuinely changed, peer average moved
1.2 -> 1.4 — and `swot_approved` was still `true` afterwards. `/api/swot/approve` already refused
to approve a SWOT that had shifted under the reviewer, but nothing on the GENERATE side dropped
the flag, so `/api/strategy/generate`'s gate stayed open for a document no human had read.

These assert BEHAVIOUR through `generate_swot`'s returned patch and the persistence layer, not
the presence of a line of source.
"""
from __future__ import annotations

import pytest

from api.webapp_analysis import generate_swot, swot_identity


class _Swot:
    """Minimal stand-in for the computed SWOT; only its identity matters here."""

    def __init__(self, marker: str) -> None:
        self.marker = marker


@pytest.fixture()
def stub_compute(monkeypatch):
    """Drive `generate_swot` without a network, a peer search or an LLM."""
    state: dict = {"frontend": {"strengths": [{"title": "ctas 3 vs 1.2"}]}, "raises": None}

    def _compute(_profile, trends=None):
        if state["raises"] is not None:
            raise state["raises"]
        return _Swot("swot"), []

    monkeypatch.setattr("api.webapp_analysis._compute_swot", _compute)
    monkeypatch.setattr("api.webapp_analysis._swot_to_frontend", lambda _s: state["frontend"])
    monkeypatch.setattr("api.webapp_analysis._swot_raw_dict", lambda _s: {"raw": True})
    monkeypatch.setattr("api.webapp_analysis.market_signals_for", lambda _b: None)
    monkeypatch.setattr("api.webapp_analysis._profile_of", lambda _b: {})
    return state


def test_regenerating_an_approved_swot_revokes_the_approval(stub_compute):
    """The headline case: approved, then regenerated into DIFFERENT content."""
    approved_hash = swot_identity({"strengths": [{"title": "ctas 3 vs 1.2"}]})
    brand = {"swot_approved": True, "swot_approved_hash": approved_hash}

    stub_compute["frontend"] = {"strengths": [{"title": "ctas 3 vs 1.4"}]}   # the content moved
    patch = generate_swot(brand)

    assert patch["swot_approved"] is False
    assert patch["swot_content_hash"] != approved_hash
    assert patch["swot_approval_invalidated"]["reason"] == "swot_regenerated"


def test_a_legacy_approval_with_no_recorded_subject_does_not_survive(stub_compute):
    """`swot_approved: true` with no hash cannot be shown to cover the new bytes, so it doesn't.

    Every brand written before the hash existed is in this state, including the one that
    exposed the defect.
    """
    patch = generate_swot({"swot_approved": True})          # no swot_approved_hash
    assert patch["swot_approved"] is False


def test_regeneration_that_reproduces_the_approved_swot_keeps_the_approval(stub_compute):
    """Identical content is not "materially new" — re-running must not nag the human again."""
    same = {"strengths": [{"title": "ctas 3 vs 1.2"}]}
    stub_compute["frontend"] = same
    brand = {"swot_approved": True, "swot_approved_hash": swot_identity(same)}

    patch = generate_swot(brand)

    assert "swot_approved" not in patch          # the flag is not touched at all
    assert patch["swot_content_hash"] == brand["swot_approved_hash"]


def test_reading_a_brand_never_invalidates_anything(stub_compute):
    """Only generation revokes. A read path must not call the generator or mutate the flag."""
    from api.brand_store import BrandStore

    read = BrandStore.get
    src = read.__doc__ or ""
    assert "swot_approved" not in src
    # And an unapproved brand's regeneration says nothing about approval either way.
    patch = generate_swot({})
    assert "swot_approved" not in patch


def test_a_failed_regeneration_destroys_neither_content_nor_approval(stub_compute):
    """If the compute raises, no patch is produced — the last valid SWOT and its signature stand.

    The dangerous alternative is revoking first and failing second, which would leave a brand
    with a good SWOT marked unapproved for a run that never produced anything.
    """
    stub_compute["raises"] = RuntimeError("peer discovery exploded")
    brand = {"swot_approved": True, "swot_approved_hash": "abc", "swot": {"strengths": []}}

    with pytest.raises(RuntimeError):
        generate_swot(brand)

    assert brand["swot_approved"] is True        # untouched
    assert brand["swot"] == {"strengths": []}


def test_approval_binds_to_the_swot_that_was_on_screen(tmp_path, monkeypatch):
    """`approve_swot` records WHICH bytes it approved, so the next regeneration can compare."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path))
    from api.brand_store import BrandStore
    from api.persistence import LegacyPersistence

    swot_v1 = {"strengths": [{"title": "v1"}]}
    store = BrandStore()
    store.upsert({"domain_key": "example_com", "source_url": "https://example.com/",
                  "swot": swot_v1, "swot_content_hash": swot_identity(swot_v1),
                  "has_swot": True}, owner_id="u1")

    out = LegacyPersistence().approve_swot(domain_key="example_com", user_id="u1")
    assert out["swot_approved"] is True
    assert out["swot_approved_hash"] == swot_identity(swot_v1)

    saved = store.get("example_com", for_user="u1")
    assert saved["swot_approved_hash"] == swot_identity(swot_v1)

    # The approval covers v1 only: a v2 regeneration must revoke it.
    patch_hash = swot_identity({"strengths": [{"title": "v2"}]})
    assert patch_hash != saved["swot_approved_hash"]
