# -*- coding: utf-8 -*-
"""Candidates accumulate; they never overwrite each other.

The owner asked for "3 people, pick one" — paid ONE AT A TIME: "واحد الأول، والتاني والتالت بطلب".
That makes the candidate set something built up across several separate jobs, which walks straight
into a trap this codebase already met once today.

THE TRAP, from `api/brand_store._merge_one_level`: the merge is ONE LEVEL DEEP. `ai_influencer` is
merged, but anything INSIDE it is replaced wholesale. Worse, each enrichment job builds its patch
from a read taken at T0 (`webapp_jobs._do_work`) — so two "add a candidate" jobs each carry the
whole `ai_influencer` subtree as it looked before the other ran, and the second write erases the
first candidate. The owner would pay for a portrait, see it, ask for another, and silently lose the
first one she was meant to be choosing between.

So candidates are a DICT keyed by id (not a list), and appending happens under the store's write
lock rather than being reconstructed inside a job. Hermetic."""
from __future__ import annotations

import threading

import pytest

from api.brand_store import BrandStore


@pytest.fixture()
def store(tmp_path):
    s = BrandStore(base=tmp_path / "b")
    s.save({"domain_key": "x_com", "ai_influencer": {"candidates": {}, "config": {}}},
           owner_id="u1")
    return s


def _cand(i):
    return {"url": f"/api/generated/c{i}.png", "persona": f"persona {i}"}


# --- the locked append primitive ----------------------------------------------------------------

def test_appending_a_candidate_keeps_the_ones_already_there(store):
    store.add_nested("x_com", "ai_influencer", "candidates", "c1", _cand(1), for_user="u1")
    store.add_nested("x_com", "ai_influencer", "candidates", "c2", _cand(2), for_user="u1")
    cands = store.get("x_com", for_user="u1")["ai_influencer"]["candidates"]
    assert set(cands) == {"c1", "c2"}, f"a candidate was lost: {list(cands)}"


def test_concurrent_appends_all_survive(store):
    """The real shape of the bug: two jobs finishing at once."""
    barrier = threading.Barrier(3)

    def add(i):
        barrier.wait()
        store.add_nested("x_com", "ai_influencer", "candidates", f"c{i}", _cand(i),
                         for_user="u1")

    threads = [threading.Thread(target=add, args=(i,)) for i in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    cands = store.get("x_com", for_user="u1")["ai_influencer"]["candidates"]
    assert set(cands) == {"c1", "c2", "c3"}, f"lost candidates: {sorted(cands)}"


def test_a_sibling_key_written_meanwhile_is_not_erased(store):
    """Selecting a face while another candidate lands must not undo the selection."""
    store.add_nested("x_com", "ai_influencer", "candidates", "c1", _cand(1), for_user="u1")
    store.update("x_com", {"ai_influencer": {"selected_id": "c1"}}, for_user="u1")
    store.add_nested("x_com", "ai_influencer", "candidates", "c2", _cand(2), for_user="u1")
    ai = store.get("x_com", for_user="u1")["ai_influencer"]
    assert ai["selected_id"] == "c1", "the selection was erased by a later candidate"
    assert set(ai["candidates"]) == {"c1", "c2"}


def test_it_refuses_on_a_brand_that_is_not_yours(store):
    assert store.add_nested("x_com", "ai_influencer", "candidates", "c9", _cand(9),
                            for_user="someone-else") is None


def test_it_creates_the_containers_when_absent(store):
    store.save({"domain_key": "fresh_com"}, owner_id="u1")
    store.add_nested("fresh_com", "ai_influencer", "candidates", "c1", _cand(1), for_user="u1")
    assert store.get("fresh_com", for_user="u1")["ai_influencer"]["candidates"]["c1"]["url"]
