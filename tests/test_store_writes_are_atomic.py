# -*- coding: utf-8 -*-
"""Concurrent writes must not silently eat each other.

Every persistence primitive in the web app is a read-modify-write with no lock:
`BrandStore.update` (get -> merge -> save), `record_history` (read -> insert -> write) and
`UserStore.create` (load -> check -> save). `Path.replace` is atomic, so the FILE never ends up
corrupt — which is precisely why the loss is silent: the last writer's complete snapshot simply
overwrites the other's. FastAPI runs the sync endpoints in a threadpool while brand jobs run on the
event loop, so two writers really are concurrent.

TWO DISTINCT DEFECTS LIVE HERE, and a lock only fixes the first:

  1. The SHORT window inside update(): two patches read the same version and one is dropped.
  2. The LONG window across a job: `_do_work` reads the brand at T0, the work function builds its
     patch from that stale copy — `webapp_influencer` literally does
     `ai = dict(brand.get("ai_influencer") or {})` then returns `{"ai_influencer": ai}` — and the
     write lands at T2 carrying a WHOLE SUBTREE from T0. Anything written to that subtree in
     between (a portrait approval) is overwritten by a job that never saw it. A lock cannot help:
     both writes are correctly serialised and the data is still lost. Merging one level deep does
     fix it, and is safe because every patch in this codebase is additive enrichment.

Also locked: the temp files. Two writers shared `<brand>.json.tmp`, so on Windows one could hit
PermissionError or publish half-written content — a worse failure than a lost update."""
from __future__ import annotations

import json
import threading

import pytest

from api.brand_store import BrandStore, read_history, record_history


def _run_together(fns):
    """Run `fns` on real threads, released from a barrier to maximise the overlap."""
    barrier = threading.Barrier(len(fns))
    errors: list = []

    def wrap(f):
        def inner():
            barrier.wait()
            try:
                f()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        return inner

    threads = [threading.Thread(target=wrap(f)) for f in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"a concurrent writer raised: {errors}"


# --- BrandStore.update ------------------------------------------------------------------------

def test_two_concurrent_patches_both_survive(tmp_path):
    store = BrandStore(base=tmp_path / "b")
    store.save({"domain_key": "x_com"})
    _run_together([
        lambda: store.update("x_com", {"swot_approved": True}),
        lambda: store.update("x_com", {"has_strategy": True}),
    ])
    brand = store.get("x_com")
    assert brand["swot_approved"] is True, "the SWOT approval was overwritten"
    assert brand["has_strategy"] is True, "the strategy flag was overwritten"


def test_many_concurrent_patches_all_survive(tmp_path):
    store = BrandStore(base=tmp_path / "b")
    store.save({"domain_key": "x_com"})
    _run_together([(lambda i=i: store.update("x_com", {f"k{i}": i})) for i in range(12)])
    brand = store.get("x_com")
    missing = [i for i in range(12) if brand.get(f"k{i}") != i]
    assert not missing, f"lost updates: {missing}"


def test_writers_do_not_share_a_temp_file(tmp_path):
    """A shared `<brand>.json.tmp` lets one writer publish another's half-written bytes."""
    store = BrandStore(base=tmp_path / "b")
    store.save({"domain_key": "x_com"})
    _run_together([(lambda i=i: store.update("x_com", {"big": "v" * 40000, "i": i}))
                   for i in range(8)])
    raw = (tmp_path / "b" / "x_com.json").read_text(encoding="utf-8")
    json.loads(raw)                        # must still parse: no interleaved bytes
    assert not list((tmp_path / "b").glob("*.tmp")), "a temp file was left behind"


# --- the stale-subtree clobber (a lock does NOT fix this one) ----------------------------------

def test_a_patch_built_from_a_stale_read_does_not_erase_a_newer_sibling_key(tmp_path):
    """The real influencer sequence: a long job reads at T0, an approval lands at T1, the job
    writes at T2. The approval must survive."""
    store = BrandStore(base=tmp_path / "b")
    store.save({"domain_key": "x_com", "ai_influencer": {"has_portrait": True}})

    stale = dict(store.get("x_com")["ai_influencer"])          # T0 — what the job saw
    store.update("x_com", {"ai_influencer": {"validated": True}})   # T1 — the user approves
    stale["poster_url"] = "p.png"
    store.update("x_com", {"ai_influencer": stale})                 # T2 — the job's patch lands

    ai = store.get("x_com")["ai_influencer"]
    assert ai["poster_url"] == "p.png", "the job's own result was lost"
    assert ai["validated"] is True, "the approval was erased by a job that never saw it"
    assert ai["has_portrait"] is True


def test_a_scalar_patch_still_replaces(tmp_path):
    """Merging is one level deep for dicts only — ordinary fields must still overwrite."""
    store = BrandStore(base=tmp_path / "b")
    store.save({"domain_key": "x_com", "readiness_score": 10})
    store.update("x_com", {"readiness_score": 90})
    assert store.get("x_com")["readiness_score"] == 90


def test_a_list_patch_replaces_rather_than_appends(tmp_path):
    """A regenerated calendar must REPLACE the old one, never accumulate onto it."""
    store = BrandStore(base=tmp_path / "b")
    store.save({"domain_key": "x_com", "content_calendar": {"calendar": [1, 2, 3]}})
    store.update("x_com", {"content_calendar": {"calendar": [9]}})
    assert store.get("x_com")["content_calendar"]["calendar"] == [9]


# --- record_history ---------------------------------------------------------------------------

def test_concurrent_history_writes_keep_every_image(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "w"))
    _run_together([(lambda i=i: record_history({"image_b64": "AA==", "filename": f"{i}.png"}))
                   for i in range(12)])
    names = {h["filename"] for h in read_history(500)}
    assert names == {f"{i}.png" for i in range(12)}, f"images lost from the gallery: {names}"


# --- UserStore.create -------------------------------------------------------------------------

def test_concurrent_signups_do_not_erase_each_other(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "w"))
    from api.user_auth import UserStore
    store = UserStore()
    _run_together([(lambda i=i: store.create(f"u{i}@t.co", "password123", f"U{i}"))
                   for i in range(10)])
    for i in range(10):
        assert store.get_by_email(f"u{i}@t.co") is not None, (
            f"u{i}@t.co signed up successfully but their account is gone")


def test_a_duplicate_email_is_still_refused_under_concurrency(tmp_path, monkeypatch):
    """Two simultaneous signups on the SAME address: exactly one may win."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "w"))
    from api.user_auth import UserStore
    store = UserStore()
    results: list = []
    lock = threading.Lock()

    def signup(pw):
        out = store.create("same@t.co", pw, "Same")
        with lock:
            results.append(out)

    _run_together([lambda: signup("password-aaa"), lambda: signup("password-bbb")])
    created = [r for r in results if r]
    assert len(created) == 1, f"the same email was registered twice: {results}"
    assert store.authenticate("same@t.co", "password-aaa") or \
        store.authenticate("same@t.co", "password-bbb"), "neither password works — the hash was lost"
