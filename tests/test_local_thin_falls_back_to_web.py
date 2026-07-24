# -*- coding: utf-8 -*-
"""A local brand with a thin peer set must still get the web engine.

MEASURED (2026-07-21): `competitor/router.py:132-133` routes LOCAL to Places and returns — the web
engine is constructed and passed in but never touched, there is no merge and no retry.
`discovery.py` computes `thin_peer_set` and nothing anywhere acts on it.

The comparison is decisive: nanocare (HYBRID) found 4 competitors and its saved note reads
"HYBRID merge: 0 Places + 4 web -> 4 after dedup" — **every one came from the web engine, its
Places path returned zero**. Volume Qatar was the only brand locked to a single engine, and it drew
the weaker one: 1 peer, a catering company.

So a thin Places result now widens to the web engine and merges with the SAME dedup the HYBRID
branch already uses. A HEALTHY Places result never triggers it, so brands that work today are
byte-identical. Hermetic — both engines are injected."""
from __future__ import annotations

from competitor.models import (Candidate, CompetitorProfile, PeerFitBreakdown,
                               PeerMatchResult, SelectionRecord)
from competitor.router import route_discovery


class _WebEngine:
    name = "fake-web"

    def __init__(self, peers):
        self.peers = peers
        self.calls = 0

    def discover(self, profile, manifest=None, **kw):
        self.calls += 1
        return list(self.peers)


def _cand(name):
    """A selected peer in the shape both engines really return (router._dedup keys on
    `.candidate.place_id`, so a bare Candidate would never dedupe)."""
    return CompetitorProfile(
        candidate=Candidate(place_id=name, name=name, types=["cafe"], rating=4.4,
                            review_count=50),
        selection=SelectionRecord(place_id=name, name=name, website=None,
                                  peer_fit_score=0.8, breakdown=PeerFitBreakdown(),
                                  why_selected="test"))


def _profile():
    return {"name": {"value": "Volume Cafe"}, "category": {"value": "cafe"},
            "source_url": {"value": "https://volumeqatar.com/"},
            "service_areas": [{"value": "The Pearl Qatar"}]}


def _patch_places(monkeypatch, peers):
    def fake_places(profile, client, base, **kw):
        return PeerMatchResult(list(peers), len(peers) < 4, len(peers), len(peers),
                               [base, f"places: {len(peers)} peer(s)"], [])
    monkeypatch.setattr("competitor.router._places", fake_places)


def test_a_thin_places_result_pulls_in_the_web_engine(monkeypatch):
    _patch_places(monkeypatch, [_cand("Only Peer")])
    web = _WebEngine([_cand("Web Cafe A"), _cand("Web Cafe B")])
    res = route_discovery(_profile(), places_client=object(), web_engine=web)
    assert web.calls == 1
    names = {c.candidate.name for c in res.competitors}
    assert {"Only Peer", "Web Cafe A", "Web Cafe B"} <= names
    assert any("web" in n.lower() for n in res.notes), res.notes   # disclosed, not silent


def test_a_healthy_places_result_is_left_alone(monkeypatch):
    peers = [_cand(f"Peer {i}") for i in range(4)]
    _patch_places(monkeypatch, peers)
    web = _WebEngine([_cand("Web Cafe")])
    res = route_discovery(_profile(), places_client=object(), web_engine=web)
    assert web.calls == 0                                   # no widening when Places delivered
    assert len(res.competitors) == 4


def test_duplicates_across_engines_are_merged_once(monkeypatch):
    _patch_places(monkeypatch, [_cand("Shared Cafe")])
    web = _WebEngine([_cand("Shared Cafe"), _cand("Web Only")])
    res = route_discovery(_profile(), places_client=object(), web_engine=web)
    names = [c.candidate.name for c in res.competitors]
    assert names.count("Shared Cafe") == 1, names


def test_a_failing_web_engine_keeps_the_places_result(monkeypatch):
    class _Boom:
        name = "boom"

        def discover(self, *a, **k):
            raise RuntimeError("serp down")
    _patch_places(monkeypatch, [_cand("Only Peer")])
    res = route_discovery(_profile(), places_client=object(), web_engine=_Boom())
    assert [c.candidate.name for c in res.competitors] == ["Only Peer"]
