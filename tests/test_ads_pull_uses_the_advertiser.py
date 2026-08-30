# -*- coding: utf-8 -*-
"""§11.2 — 260 ads on disk, zero of them a competitor's. The fix was written and left unplugged.

MEASURED across every saved snapshot in `runs/ad_snapshots/`:

    body_building_house   200 ads  ->  Military Training · Pages of Pure Love · Novels Lover
    joe_s_cafe             43 ads  ->  Goodnovel Lovers · Shortical - Top Drama TV
    vodafone_eg             1 ad   ->  Vodafone Foundation

Not one row belongs to the competitor it was pulled for. The relevance gate downstream drops all
of them and is working correctly — the DATA is wrong, because the query asked the wrong question:
`search_terms` searches AD COPY, so it returns every advertiser whose text happens to contain the
words, and never the advertiser itself.

THE PART THAT MATTERS FOR WHOEVER PICKS THIS UP: the correct query is already implemented.
`ads_intel/live.py:75 iter_competitor_ads` resolves the advertiser's page and pulls THAT page's
ads (`search_page_ids`), falling back to keywords only when no page can be identified, and tags
every batch with the `basis` that produced it. It has `resolve_page_id`, it has
`build_page_query_url`, and `grep` finds NO production caller — both real callers
(`ads_intel/orchestrator.py:91`, `scripts/pull_meta_ads.py:54`) still call the keyword-only
`iter_ad_pages`. Same shape as the geocode branch this project already found once: the good code
written, tested, and never connected.

SECOND HALF OF THE FIX: the snapshots already on disk carry `query`/`country`/`pulled_at` and
NOTHING that says which query shape produced them. So even after the puller is corrected, an old
keyword-era file is indistinguishable from a new page-id one, and the noise keeps counting. A
snapshot has to state its own basis.

Hermetic: a fake transport, no network, no token.
"""
from __future__ import annotations

import json

from ads_intel.live import iter_competitor_ads


class _FakeTransport:
    """Answers page-id lookups and page-scoped ad queries; records what was asked."""

    def __init__(self, *, page_id="998877", page_ads=None, keyword_ads=None):
        self.page_id = page_id
        self.page_ads = page_ads if page_ads is not None else [{"id": "a1", "page_name": "Joe Cafe"}]
        self.keyword_ads = keyword_ads or [{"id": "x9", "page_name": "Goodnovel Lovers"}]
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        if "search_page_ids" in url:
            return {"data": list(self.page_ads)}
        # a page-id resolution probe returns rows carrying the advertiser's page id
        if "search_terms" in url and "page_id" in url:
            return {"data": [{"page_id": self.page_id, "page_name": "Joe Cafe"}]}
        return {"data": list(self.keyword_ads)}


def _pull(transport, name="Joe Cafe"):
    return list(iter_competitor_ads(name, country="EG", token="T",
                                    fields="id,page_id,page_name", transport=transport))


# --- the pull must ask for the ADVERTISER, not for their words -----------------------------------

def test_the_advertisers_own_page_is_queried_when_it_can_be_resolved():
    t = _FakeTransport()
    pages = _pull(t)
    assert pages, "no ads yielded at all"
    assert any("search_page_ids" in u for u in t.urls), (
        "the pull never asked for the advertiser's page — it is still searching ad copy")


def test_every_row_says_which_question_produced_it():
    """`basis` travels WITH the rows so "their ads" and "ads mentioning them" cannot be confused
    downstream — which is exactly what happened to the 260 rows already on disk."""
    _rows, basis = _pull(_FakeTransport())[0]
    assert basis.get("basis") == "page_id"
    assert basis.get("page_id")


def test_an_identified_advertiser_running_nothing_does_not_fall_back_to_strangers():
    """"They are not advertising" is a real finding. Replacing it with other advertisers' ads
    turns a true answer into a false one.

    ASSERTION CORRECTED while writing this file: the first version expected `[]`. The real
    behaviour is one EMPTY page carrying the basis, and that is the better design — it tells the
    caller "we identified them and they are running nothing", which `[]` alone cannot distinguish
    from "we never found their page". Only one of those two is a fact about the competitor."""
    pages = _pull(_FakeTransport(page_ads=[]))
    rows = [r for page, _basis in pages for r in page]
    assert rows == [], f"strangers' ads filled an identified advertiser's silence: {rows}"
    assert pages and pages[0][1].get("basis") == "page_id"
    assert "Goodnovel" not in json.dumps(pages)


def test_keywords_are_used_only_when_no_page_can_be_identified():
    class _NoPage(_FakeTransport):
        def fetch(self, url):
            self.urls.append(url)
            if "search_page_ids" in url:
                return {"data": []}
            return {"data": list(self.keyword_ads)}
    t = _NoPage()
    pages = _pull(t)
    assert pages, "the keyword fallback did not fire"
    _rows, basis = pages[0]
    assert basis.get("basis") != "page_id", basis


# --- the wiring: the correct function must actually be the one production calls -------------------

def test_the_orchestrator_pulls_by_advertiser():
    """The defect was never that the fix was hard — it was that nothing called it."""
    import inspect
    import ads_intel.orchestrator as orch
    src = inspect.getsource(orch)
    assert "iter_competitor_ads" in src, (
        "the orchestrator still pulls by keyword; the page-id path stays dead code")


def test_the_puller_script_pulls_by_advertiser():
    from pathlib import Path
    src = Path("scripts/pull_meta_ads.py").read_text(encoding="utf-8")
    assert "iter_competitor_ads" in src, "the CLI puller still pulls by keyword"


# --- the snapshots on disk must declare their own basis -------------------------------------------

def test_a_new_snapshot_records_the_basis_it_was_pulled_with():
    from ads_intel.orchestrator import snapshot_basis
    assert snapshot_basis({"basis": "page_id", "page_id": "1"}) == "page_id"


def test_a_snapshot_with_no_basis_is_treated_as_keyword_era_noise():
    """MEASURED: every file in `runs/ad_snapshots/` predates the fix and carries no basis field.
    Absent must read as "keyword" — the shape we know produced strangers — never as trustworthy."""
    from ads_intel.orchestrator import snapshot_basis
    assert snapshot_basis({}) == "keyword_legacy"
    assert snapshot_basis(None) == "keyword_legacy"


def test_the_stored_snapshots_are_all_flagged_as_legacy():
    from pathlib import Path
    from ads_intel.orchestrator import snapshot_basis
    files = sorted(Path("runs/ad_snapshots").glob("*.json"))
    if not files:                                     # nothing stored on this machine
        return
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                             # noqa: BLE001
            continue
        if d.get("basis") in (None, "keyword", "keyword_legacy"):
            assert snapshot_basis(d) != "page_id", f.name
