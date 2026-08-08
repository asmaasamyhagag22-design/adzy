# -*- coding: utf-8 -*-
"""Competitor Registry (owner-caught non-determinism) — hermetic.

First discovery establishes a persisted baseline; later runs DIFF against it
(present/disappeared/new-pending) and NEVER silently replace. SWOT computes against the
approved set. Identity changes are the owner's (this module never auto-approves)."""
from __future__ import annotations

import pytest

from competitor.registry import (
    approved_competitors, competitor_key, diff_competitors, establish_or_diff, load_registry,
)


def _c(name, site):
    return {"selection": {"name": name, "website": site}, "candidate": {"name": name}}


def test_key_is_stable_by_domain():
    assert competitor_key("Foo", "https://www.foo-clinic.com/x?y=1") == "foo-clinic.com"
    assert competitor_key("Foo", "http://foo-clinic.com") == "foo-clinic.com"   # same rival
    assert competitor_key("مطعم الدهان", "") == "مطعمالدهان"                     # name fallback


def test_first_run_establishes_baseline(tmp_path):
    disc = [_c("A", "https://a.com"), _c("B", "https://b.com")]
    out = establish_or_diff("nti", disc, store_dir=str(tmp_path), now="2026-07-12")
    assert out["established"] is True
    assert sorted(out["approved_keys"]) == ["a.com", "b.com"]
    reg = load_registry("nti", store_dir=str(tmp_path))
    assert {e["key"] for e in reg["entries"]} == {"a.com", "b.com"}
    assert all(e["status"] == "approved" for e in reg["entries"])


def test_later_run_diffs_without_silent_replacement(tmp_path):
    establish_or_diff("nti", [_c("A", "https://a.com"), _c("B", "https://b.com")],
                      store_dir=str(tmp_path), now="d1")
    # next run: A stays, B gone, C is new
    out = establish_or_diff("nti", [_c("A", "https://a.com"), _c("C", "https://c.com")],
                            store_dir=str(tmp_path), now="d2")
    assert out["established"] is False
    d = out["diff"]
    assert [e["key"] for e in d["still_present"]] == ["a.com"]
    assert [e["key"] for e in d["disappeared"]] == ["b.com"]     # flagged, not deleted
    assert [e["key"] for e in d["new_pending"]] == ["c.com"]     # pending, NOT auto-added
    # the persisted baseline is UNCHANGED (no silent replacement)
    reg = load_registry("nti", store_dir=str(tmp_path))
    assert {e["key"] for e in reg["entries"]} == {"a.com", "b.com"}
    # SWOT computes only against the approved baseline (a.com, b.com) — C is excluded
    approved = approved_competitors([_c("A", "https://a.com"), _c("C", "https://c.com")],
                                    out["approved_keys"])
    assert [_c_key(c) for c in approved] == ["a.com"]            # only the still-present approved


def _c_key(c):
    from competitor.registry import _entry_of
    return _entry_of(c)["key"]


def test_removed_entry_drops_from_approved(tmp_path):
    establish_or_diff("x", [_c("A", "https://a.com")], store_dir=str(tmp_path), now="d1")
    reg = load_registry("x", store_dir=str(tmp_path))
    reg["entries"][0]["status"] = "removed"                     # owner removed it (HITL)
    from competitor.registry import save_registry
    save_registry(reg, store_dir=str(tmp_path))
    out = establish_or_diff("x", [_c("A", "https://a.com")], store_dir=str(tmp_path), now="d2")
    assert out["approved_keys"] == []                           # a removed rival is not approved
    assert out["diff"]["still_present"] == []                   # and not counted present


def test_diff_is_pure_no_mutation():
    reg = {"brand_ref": "x", "entries": [{"key": "a.com", "name": "A", "status": "approved"}]}
    diff_competitors(reg, [_c("B", "https://b.com")])
    assert reg["entries"] == [{"key": "a.com", "name": "A", "status": "approved"}]


# --------------------------------------------------------------------------------------------
# P6 #11 — "the fix you believe shipped but didn't"
# --------------------------------------------------------------------------------------------

def test_rejecting_EVERY_peer_does_not_resurrect_the_raw_pool(tmp_path):
    """P6 #11(b) — `if approved:` was the bug.

    Remove every competitor via HITL and `approved == []`, which is FALSY, so full_run kept
    matrix_competitors at its raw-pool value and the rival the owner explicitly rejected
    reappeared in the next SWOT — under a comment asserting the opposite.
    """
    from competitor.registry import save_registry, stable_competitors
    pool = [_c("Rival A", "https://a.com"), _c("Rival B", "https://b.com")]
    # a baseline EXISTS and the owner has removed everything in it
    save_registry({"brand_ref": "acme", "entries": [
        {"key": competitor_key("Rival A", "https://a.com"), "name": "Rival A",
         "website": "https://a.com", "status": "removed"},
        {"key": competitor_key("Rival B", "https://b.com"), "name": "Rival B",
         "website": "https://b.com", "status": "removed"},
    ]}, store_dir=str(tmp_path))
    comps, _diff = stable_competitors("acme", pool, store_dir=str(tmp_path), now="2026-07-15")
    assert comps == [], "the raw live pool came back after the owner rejected every peer"


def test_a_CORRUPT_registry_never_silently_rebaselines_and_auto_approves(tmp_path):
    """P6 #11(c) — a corrupt file was indistinguishable from a missing one, so establish_or_diff
    treated it as "first run, trust everything", AUTO-APPROVED every live peer and OVERWROTE the
    owner's file — the one thing registry.py promises it never does."""
    from competitor.registry import RegistryCorrupt, registry_path, stable_competitors
    p = registry_path("acme", store_dir=str(tmp_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"brand_ref": "acme", "entries": [{"key": "rival_a"', encoding="utf-8")  # truncated
    before = p.read_text(encoding="utf-8")
    with pytest.raises(RegistryCorrupt):
        stable_competitors("acme", [_c("Rival A", "https://a.com")], store_dir=str(tmp_path),
                           now="2026-07-15")
    assert p.read_text(encoding="utf-8") == before, "a corrupt registry was overwritten"


def test_BOTH_entrypoints_route_their_peer_set_through_the_registry():
    """P6 #11(a) — api/routes/swot.py never called the registry, so the PAID dashboard computed
    SWOT against the raw live pool while the CLI used the approved baseline. Static check: the
    two entrypoints must both go through the one helper (a behavioural test would need a live
    scrape). approved_competitors' own docstring: SWOT computes against the approved baseline,
    "never the raw live pool"."""
    import inspect

    import api.routes.swot as api_swot
    import competitor.full_run as cli
    for mod in (api_swot, cli):
        src = inspect.getsource(mod)
        assert "stable_competitors" in src, f"{mod.__name__} bypasses the competitor registry"


def test_client_facing_swot_outputs_use_the_approved_set_not_raw_pool():
    """P-79: the OLD guard above only greps for `stable_competitors` — it passed while review
    THEMES, the client competitor LIST, and the COUNT still came from the raw `result.competitors`
    pool, so the set shown to the client drifted every run even though the matrix was stable. This
    pins the actual wiring: the client-facing outputs must reference the APPROVED variable, and the
    raw-pool anti-patterns must be gone.

    CS-91: the greps below are the WIRING pin (cheap tripwire); the BEHAVIOR itself is executed
    by test_stable_competitors_behavior_survives_pool_drift — production code, not source text."""
    import inspect

    import api.routes.swot as api_swot
    import competitor.full_run as cli
    api_src = inspect.getsource(api_swot)
    cli_src = inspect.getsource(cli)

    # API route: themes, client list, and count all use `peers` (the approved set).
    assert "extractor(peers)" in api_src
    assert "for c in peers:" in api_src
    assert "competitor_count=len(peers)" in api_src
    assert "extractor(result.competitors)" not in api_src, "themes reverted to the raw pool"

    # CLI: themes + output list + count use `matrix_competitors` (the approved set).
    assert "ReviewThemeExtractor()(matrix_competitors)" in cli_src
    assert "for c in matrix_competitors" in cli_src
    assert '"competitor_count": len(matrix_competitors)' in cli_src
    assert "ReviewThemeExtractor()(result.competitors)" not in cli_src, "themes reverted to raw pool"
    assert "for c in result.competitors]" not in cli_src, "output list reverted to the raw pool"


def test_stable_competitors_behavior_survives_pool_drift(tmp_path):
    """CS-91 (behavioral, executes production code): run 1 establishes the baseline from the
    live pool; run 2 arrives with a DRIFTED pool — the client-facing set must be the APPROVED
    baseline's members, not the new strangers. This is the invariant the source-greps above
    could only gesture at."""
    from competitor.registry import stable_competitors

    def _peer(name, website):
        return {"candidate": {"name": name, "website": website},
                "selection": {"name": name, "website": website}}

    run1 = [_peer("Alpha Clinic", "https://alpha.example/"),
            _peer("Beta Clinic", "https://beta.example/")]
    got1, diff1 = stable_competitors("brand_x", run1, store_dir=str(tmp_path), now="t1")
    assert [p["candidate"]["name"] for p in got1] == ["Alpha Clinic", "Beta Clinic"]

    run2 = [_peer("Alpha Clinic", "https://alpha.example/"),
            _peer("Stranger Co", "https://stranger.example/")]   # drift: Beta gone, Stranger in
    got2, diff2 = stable_competitors("brand_x", run2, store_dir=str(tmp_path), now="t2")

    # A re-hydrated peer comes back as a real CompetitorProfile (built from the registry entry),
    # while one the live pool supplied is whatever the pool passed in — here a dict. Read both.
    def _name(p):
        return p["candidate"]["name"] if isinstance(p, dict) else p.candidate.name

    names2 = [_name(p) for p in got2]
    assert "Stranger Co" not in names2            # a newcomer NEVER auto-joins the client set
    assert "Alpha Clinic" in names2               # the approved member persists
    # AND BETA COMES BACK (2026-08-01). This is the invariant the docstring above always claimed —
    # "the client-facing set must be the APPROVED baseline's members" — and which the code did not
    # keep: `approved_competitors` is a FILTER over the live pool, so an approved peer the pool
    # stopped returning was silently dropped. Measured on AlFouad: three approved peers, discovery
    # returned zero (the Google project was billing-blocked), SWOT peers=0, weaknesses=0.
    assert "Beta Clinic" in names2, "an approved peer vanished because the live pool forgot it"
