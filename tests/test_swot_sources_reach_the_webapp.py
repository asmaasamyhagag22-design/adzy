# -*- coding: utf-8 -*-
"""The dashboard's SWOT must be fed from the same sources as the CLI's.

MEASURED (2026-07-25): `competitor/full_run.py` calls
`synthesize_swot(matrix, themes=…, unique_insights=…, profile=…, trends=…, ad_presence=…)` while
`api/webapp_analysis.py` calls `synthesize_swot(matrix, themes=…, profile=…)`. So the SWOT the
owner actually looks at in the dashboard is missing THREE inputs that already work on another
path — and nothing anywhere said so.

Worse, one level up: `api/webapp_analysis.py` routes discovery WITHOUT a web engine, so it falls to
`NullWebDiscoveryEngine`. nanocare's own stored run reads "0 Places + 4 web" — every one of its
competitors came from the web engine — which means in the dashboard that brand had NO peers at all,
and with no peers there is no SWOT worth the name.

And a real bug behind it: `full_run.py` derived the ad-snapshot refs with
`getattr(c, "name", "")` on a `CompetitorProfile`, whose fields are
candidate/selection/reviews/has_scrapable_site/is_local — there IS no `.name`. Every ref was the
empty string, so `ad_presence` has been empty on every run since it was written, while
`ads_intel/orchestrator.py` does the same thing correctly a few files away.

These tests assert on the ARGUMENTS the analysis is invoked with, because that is the defect: the
functions all work, and the dashboard simply never handed them anything. Hermetic — no network."""
from __future__ import annotations

import pytest

from competitor.models import Candidate, CompetitorProfile, PeerFitBreakdown, SelectionRecord


def _peer(name: str) -> CompetitorProfile:
    return CompetitorProfile(
        candidate=Candidate(place_id="", name=name, website=f"https://{name}.example/"),
        selection=SelectionRecord(place_id="", name=name, website=None, peer_fit_score=0.0,
                                  breakdown=PeerFitBreakdown(total=0.0), why_selected=""),
        reviews=[], has_scrapable_site=True, is_local=False)


# --- the ad-snapshot ref bug --------------------------------------------------------------------

def test_the_competitor_name_is_read_from_where_it_actually_lives():
    """`CompetitorProfile` has no `.name`; reading one yields '' for every peer."""
    from competitor.full_run import competitor_ref_slugs

    assert competitor_ref_slugs([_peer("Joe Cafe"), _peer("iFit")]) == ["joe_cafe", "ifit"]


def test_a_nameless_peer_is_skipped_not_turned_into_an_empty_ref():
    from competitor.full_run import competitor_ref_slugs

    bare = CompetitorProfile(
        candidate=Candidate(place_id="", name="", website=None),
        selection=SelectionRecord(place_id="", name="", website=None, peer_fit_score=0.0,
                                  breakdown=PeerFitBreakdown(total=0.0), why_selected=""),
        reviews=[], has_scrapable_site=False, is_local=False)
    assert competitor_ref_slugs([bare]) == []


def test_it_accepts_a_plain_dict_too():
    """Stored runs hold competitors as dicts; the helper must read both shapes."""
    from competitor.full_run import competitor_ref_slugs

    assert competitor_ref_slugs([{"candidate": {"name": "Joe Cafe"}}]) == ["joe_cafe"]


# --- the webapp hands the analysis everything it has --------------------------------------------

@pytest.fixture
def spy(monkeypatch):
    """Capture what `_compute_swot` passes to `synthesize_swot`, without running any of it."""
    seen: dict = {}
    import competitor as comp_pkg

    def fake_synthesize(matrix, **kwargs):
        seen.update(kwargs)
        seen["matrix"] = matrix
        from competitor.swot import SWOT
        return SWOT()

    monkeypatch.setattr(comp_pkg, "synthesize_swot", fake_synthesize, raising=False)
    return seen


def test_the_webapp_passes_every_source_the_cli_passes(spy, monkeypatch):
    import api.webapp_analysis as wa

    monkeypatch.setattr(wa, "_places_client", lambda: None, raising=False)
    brand = {"domain_key": "x_com", "_profile": {"name": {"value": "X"},
                                                 "source_url": {"value": "https://x.com/"}}}
    wa._compute_swot(brand.get("_profile"))
    for key in ("themes", "profile", "unique_insights", "trends", "ad_presence"):
        assert key in spy, f"the dashboard still withholds `{key}` from the analysis"


def test_discovery_in_the_webapp_gets_a_web_engine(monkeypatch):
    """Without one, an ecommerce brand gets zero competitors — nanocare's own peers were all web.

    CORRECTED while writing this file: the first version patched `route_discovery` on the analysis
    module, but `_compute_swot` imports it from the `competitor` package INSIDE the function, so the
    patch never intercepted anything and the test would have passed on unchanged code."""
    seen: dict = {}
    import competitor as comp_pkg
    import api.webapp_analysis as wa

    def fake_route(profile, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here — only the arguments matter")

    monkeypatch.setattr(comp_pkg, "route_discovery", fake_route, raising=False)
    monkeypatch.setattr(wa, "synthesize_swot_standalone", lambda p: (None, []), raising=False)
    wa._compute_swot({"name": {"value": "X"}, "source_url": {"value": "https://x.com/"}})
    assert "web_engine" in seen, \
        "discovery runs with NullWebDiscoveryEngine — ecommerce brands get no peers"


def test_a_failing_source_never_breaks_the_swot(monkeypatch):
    """The CLI's contract: every optional source is wrapped so the SWOT still ships."""
    import api.webapp_analysis as wa
    from trends import engine as trends_engine

    def boom(*a, **k):
        raise RuntimeError("trends provider exploded")

    monkeypatch.setattr(trends_engine, "top_trends_bounded", boom, raising=False)
    assert wa._trends_for({"name": {"value": "X"},
                           "market_category": {"value": "supplements"}}) == []
