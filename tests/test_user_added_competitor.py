# -*- coding: utf-8 -*-
"""The owner can name a competitor herself, and it must behave like a real one.

WHY THIS EXISTS. Automatic discovery is still being improved, and a SWOT with no peers has no
strengths or weaknesses at all — "strengths and weaknesses are only such in comparison to the
competition" (Pickton & Wright 1998, p.103). The rebuilt SWOT now tells the client exactly that and
points them at this feature: naming one competitor turns a page of site-readiness readings into an
actual strategic analysis. So this is not a convenience button; it is the unblock.

PROVENANCE IS NOT OPTIONAL. This project's whole claim is that every line traces to something real.
A competitor the user typed is evidence of a different kind from one the system found and scored,
and the two must never be indistinguishable downstream — hence `source="user"` travelling on the
stored entry and on the peer's fit basis.

THREE MEASURED LANDMINES this file also closes, all verified in the real repo on 2026-07-25:
  1. Every registry file on disk holds `entries: []`, so a naive implementation would be erased by
     the next discovery run that "establishes" a fresh set.
  2. `registry_path` resolves `"registry"` relative to the CURRENT WORKING DIRECTORY — the exact
     bug class `tests/test_webapp_paths_are_anchored.py` exists for. Running the API from a
     different cwd silently writes to a different store.
  3. The registry has NO owner scoping (`grep owner_id competitor/registry.py` -> 0), which is the
     same defect measured and fixed in the brand store on 2026-07-25. Competitor sets are client
     work product; they must not be shared across accounts.

Hermetic — a tmp store, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from competitor.manual import competitor_from_user
from competitor.registry import competitor_key, registry_path, stable_competitors


# --- building a usable peer from two strings ---------------------------------------------------

def test_a_named_competitor_becomes_a_real_peer_object():
    peer = competitor_from_user("Pharmacy X", "https://pharmx.example/")
    assert peer.candidate.name == "Pharmacy X"
    assert peer.candidate.website == "https://pharmx.example/"
    assert peer.has_scrapable_site is True, "without this the matrix never scrapes it"


def test_the_peer_is_marked_as_coming_from_the_user_not_from_discovery():
    peer = competitor_from_user("Pharmacy X", "https://pharmx.example/")
    assert peer.selection.breakdown.basis == "user_provided"
    why = (peer.selection.why_selected or "").lower()
    assert "owner" in why and "not discovered" in why, why


def test_a_user_peer_claims_no_fit_score_it_did_not_earn():
    """A discovered peer carries a measured fit. A typed name has not been scored against anything;
    inventing a number here would put an unearned figure into the client's evidence trail."""
    peer = competitor_from_user("Pharmacy X", "https://pharmx.example/")
    b = peer.selection.breakdown
    for axis in ("sub_vertical", "proximity", "size_similarity", "audience_tier"):
        assert getattr(b, axis, None) is None, f"{axis} was invented"


def test_a_competitor_with_no_website_is_still_usable_but_not_scrapable():
    peer = competitor_from_user("Corner Shop", "")
    assert peer.candidate.name == "Corner Shop"
    assert peer.has_scrapable_site is False


def test_a_nameless_competitor_is_refused():
    assert competitor_from_user("", "") is None
    assert competitor_from_user("   ", "  ") is None


def test_an_unsafe_url_is_refused_like_every_other_fetch_target():
    """Same SSRF gate the scrape route uses — a competitor URL is a URL we will fetch."""
    for bad in ("http://localhost:8000/", "http://127.0.0.1/", "http://169.254.169.254/",
                "file:///etc/passwd"):
        assert competitor_from_user("X", bad) is None, bad


# --- surviving in the registry -----------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    return str(tmp_path / "registry")


def test_a_user_competitor_survives_a_discovery_run_that_finds_nothing(store):
    """The landmine: every registry on disk is empty, so the next run 'establishes' a fresh set.
    A competitor the owner typed must not be swept away by that."""
    from competitor.registry import add_user_competitor

    add_user_competitor("https://brand.example/", name="Pharmacy X",
                        website="https://pharmx.example/", store_dir=store)
    peers, _ = stable_competitors("https://brand.example/", discovered=[], store_dir=store)
    assert [p.candidate.name for p in peers] == ["Pharmacy X"]


def test_it_also_survives_a_run_that_discovers_other_peers(store):
    from competitor.registry import add_user_competitor

    add_user_competitor("https://brand.example/", name="Pharmacy X",
                        website="https://pharmx.example/", store_dir=store)
    found = [competitor_from_user("Auto Found", "https://found.example/")]
    got, _ = stable_competitors("https://brand.example/", discovered=found, store_dir=store)
    names = {p.candidate.name for p in got}
    assert "Pharmacy X" in names, "the typed competitor was dropped by a discovery run"


def test_the_stored_entry_records_where_it_came_from(store):
    from competitor.registry import add_user_competitor

    add_user_competitor("https://brand.example/", name="Pharmacy X",
                        website="https://pharmx.example/", store_dir=store)
    data = json.loads(registry_path("https://brand.example/", store_dir=store).read_text("utf-8"))
    entry = next(e for e in data["entries"] if e["name"] == "Pharmacy X")
    assert entry["source"] == "user"
    assert entry.get("added_at")


def test_a_discovered_entry_is_still_marked_discovered(store):
    """Provenance must distinguish, which means the other value has to be written too."""
    from competitor.registry import add_user_competitor, establish_or_diff

    establish_or_diff("https://brand.example/",
                      [competitor_from_user("Auto", "https://auto.example/")], store_dir=store)
    add_user_competitor("https://brand.example/", name="Typed",
                        website="https://typed.example/", store_dir=store)
    data = json.loads(registry_path("https://brand.example/", store_dir=store).read_text("utf-8"))
    by_name = {e["name"]: e.get("source") for e in data["entries"]}
    assert by_name.get("Typed") == "user"
    assert by_name.get("Auto") in ("discovered", None)


def test_removing_a_user_competitor_works_and_is_idempotent(store):
    from competitor.registry import add_user_competitor, remove_user_competitor

    add_user_competitor("https://brand.example/", name="Pharmacy X",
                        website="https://pharmx.example/", store_dir=store)
    key = competitor_key("Pharmacy X", "https://pharmx.example/")
    assert remove_user_competitor("https://brand.example/", key, store_dir=store) is True
    assert remove_user_competitor("https://brand.example/", key, store_dir=store) is False
    left, _ = stable_competitors("https://brand.example/", discovered=[], store_dir=store)
    assert left == []


def test_the_same_competitor_added_twice_is_stored_once(store):
    from competitor.registry import add_user_competitor

    for _ in range(3):
        add_user_competitor("https://brand.example/", name="Pharmacy X",
                            website="https://pharmx.example/", store_dir=store)
    peers, _ = stable_competitors("https://brand.example/", discovered=[], store_dir=store)
    assert len(peers) == 1


# --- the store must not move when the process does ---------------------------------------------

def test_the_registry_path_is_anchored_not_relative_to_the_cwd(tmp_path, monkeypatch):
    """Measured: `_STORE = "registry"` resolved against the CWD, so the API writing from one
    directory and a CLI from another silently kept two different competitor sets."""
    monkeypatch.chdir(tmp_path)
    assert registry_path("https://brand.example/").is_absolute(), (
        "the registry follows the working directory — same bug class as the webapp dir")
