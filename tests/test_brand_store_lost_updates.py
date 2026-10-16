# -*- coding: utf-8 -*-
"""F-09: BrandStore updates preserve unrelated committed state.

These tests separate logical update atomicity from JSON-file atomicity.  They use the real
BrandStore, a spawned second process, and the real calendar-image persistence route.  Image work is
replaced with a local fake; no renderer, network, or paid service is reached.
"""
from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path

import pytest

from api.brand_store import BrandStore


_OWNER = "owner-a"
_OTHER = "owner-b"


def _brand() -> dict:
    return {
        "domain_key": "x_com",
        "owner_id": _OWNER,
        "field_a": "old",
        "field_b": "old",
        "analysis": {"swot": {"approved": False}, "scores": {"quality": 1}},
        "creative": {"assets": {"logo": "old-logo.png"}},
        "content_calendar": {
            "calendar": [
                {"date": "2026-08-10", "topic": "A safe local topic", "poster": None},
            ]
        },
    }


def _process_update(
    base: str,
    patch: dict,
    pause_before_write: bool,
    reached_write,
    release_write,
    finished,
) -> None:
    """Process target that can expose the old read -> write window deterministically."""
    import api.brand_store as brand_store

    if pause_before_write:
        original = brand_store._write_atomic

        def paused_write(path, payload):
            reached_write.set()
            if not release_write.wait(15):
                raise TimeoutError("test did not release the paused BrandStore writer")
            return original(path, payload)

        brand_store._write_atomic = paused_write

    try:
        BrandStore(base=Path(base)).update("x_com", patch, for_user=_OWNER)
    finally:
        finished.set()


def _process_delete(base: str, started, finished) -> None:
    started.set()
    try:
        BrandStore(base=Path(base)).delete("x_com", for_user=_OWNER)
    finally:
        finished.set()


def test_two_processes_cannot_lose_independent_top_level_updates(tmp_path):
    """P1 pauses after reading; P2 commits; P1 must not publish P1's stale full snapshot."""
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)
    ctx = multiprocessing.get_context("spawn")
    reached_write = ctx.Event()
    release_write = ctx.Event()
    p1_done = ctx.Event()
    p2_done = ctx.Event()

    p1 = ctx.Process(
        target=_process_update,
        args=(str(base), {"field_a": "from-p1"}, True, reached_write, release_write, p1_done),
    )
    p2 = ctx.Process(
        target=_process_update,
        args=(str(base), {"field_b": "from-p2"}, False, ctx.Event(), ctx.Event(), p2_done),
    )
    p1.start()
    try:
        assert reached_write.wait(15), "P1 never reached the real storage write"
        p2.start()
        # Before the fix P2 completes while P1 is paused.  After a storage-boundary lock P2 waits
        # for P1; either way releasing P1 now makes the final commit order deterministic.
        p2_done.wait(2)
        release_write.set()
        p1.join(15)
        p2.join(15)
        assert p1.exitcode == 0
        assert p2.exitcode == 0
    finally:
        release_write.set()
        if p1.is_alive():
            p1.terminate()
            p1.join(5)
        if p2.pid is not None and p2.is_alive():
            p2.terminate()
            p2.join(5)

    stored = BrandStore(base=base).get("x_com", for_user=_OWNER)
    assert stored["field_a"] == "from-p1"
    assert stored["field_b"] == "from-p2", "P1's stale document erased P2's committed field"


def test_independent_nested_fields_and_mapping_keys_survive(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_OWNER)

    store.update("x_com", {"analysis": {"swot": {"approved": True}}}, for_user=_OWNER)
    store.update("x_com", {"analysis": {"scores": {"evidence": 9}}}, for_user=_OWNER)
    store.update("x_com", {"creative": {"assets": {"hero": "hero.png"}}}, for_user=_OWNER)

    stored = store.get("x_com", for_user=_OWNER)
    assert stored["analysis"]["swot"]["approved"] is True
    assert stored["analysis"]["scores"] == {"quality": 1, "evidence": 9}
    assert stored["creative"]["assets"] == {
        "logo": "old-logo.png",
        "hero": "hero.png",
    }


def test_two_threads_using_separate_instances_preserve_independent_nested_keys(tmp_path):
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)
    barrier = threading.Barrier(2)
    errors = []

    def write(patch):
        try:
            barrier.wait()
            BrandStore(base=base).update("x_com", patch, for_user=_OWNER)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=({"analysis": {"scores": {"thread-a": 7}}},)),
        threading.Thread(target=write, args=({"analysis": {"scores": {"thread-b": 8}}},)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    scores = BrandStore(base=base).get("x_com", for_user=_OWNER)["analysis"]["scores"]
    assert scores["thread-a"] == 7
    assert scores["thread-b"] == 8


def test_same_field_is_last_committed_writer_wins(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_OWNER)
    store.update("x_com", {"field_a": "first-commit"}, for_user=_OWNER)
    store.update("x_com", {"field_a": "last-commit"}, for_user=_OWNER)
    assert store.get("x_com", for_user=_OWNER)["field_a"] == "last-commit"


def test_same_field_conflict_is_serialized_and_second_writer_wins(tmp_path):
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)
    ctx = multiprocessing.get_context("spawn")
    reached_write = ctx.Event()
    release_write = ctx.Event()
    p1_done = ctx.Event()
    p2_done = ctx.Event()
    p1 = ctx.Process(
        target=_process_update,
        args=(str(base), {"field_a": "p1"}, True, reached_write, release_write, p1_done),
    )
    p2 = ctx.Process(
        target=_process_update,
        args=(str(base), {"field_a": "p2"}, False, ctx.Event(), ctx.Event(), p2_done),
    )
    p1.start()
    try:
        assert reached_write.wait(15)
        p2.start()
        p2_done.wait(2)
        release_write.set()
        p1.join(15)
        p2.join(15)
        assert p1.exitcode == p2.exitcode == 0
    finally:
        release_write.set()
        for process in (p1, p2):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
    assert BrandStore(base=base).get("x_com", for_user=_OWNER)["field_a"] == "p2"


def test_delete_waits_for_inflight_update_and_does_not_allow_resurrection(tmp_path):
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)
    ctx = multiprocessing.get_context("spawn")
    reached_write = ctx.Event()
    release_write = ctx.Event()
    update_done = ctx.Event()
    delete_started = ctx.Event()
    delete_done = ctx.Event()
    updater = ctx.Process(
        target=_process_update,
        args=(str(base), {"field_a": "updated"}, True, reached_write, release_write, update_done),
    )
    deleter = ctx.Process(target=_process_delete, args=(str(base), delete_started, delete_done))
    updater.start()
    try:
        assert reached_write.wait(15)
        deleter.start()
        assert delete_started.wait(15)
        delete_done.wait(2)
        release_write.set()
        updater.join(15)
        deleter.join(15)
        assert updater.exitcode == deleter.exitcode == 0
    finally:
        release_write.set()
        for process in (updater, deleter):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
    assert BrandStore(base=base).get("x_com", for_user=_OWNER) is None


def test_update_after_delete_does_not_resurrect_a_deleted_brand(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_OWNER)
    assert store.delete("x_com", for_user=_OWNER) == "deleted"
    assert store.update("x_com", {"field_a": "resurrected"}, for_user=_OWNER) is None
    assert store.get("x_com", for_user=_OWNER) is None


def test_atomic_mutation_enforces_owner_and_accepts_schema_free_fields(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_OWNER)

    assert store.mutate(
        "x_com", lambda latest: {**latest, "future_enrichment": {"status": "ready"}},
        for_user=_OTHER,
    ) is None
    updated = store.mutate(
        "x_com", lambda latest: {**latest, "future_enrichment": {"status": "ready"}},
        for_user=_OWNER,
    )
    assert updated["future_enrichment"] == {"status": "ready"}
    assert updated["owner_id"] == _OWNER

    with pytest.raises(ValueError, match="owner_id"):
        store.update("x_com", {"owner_id": _OTHER}, for_user=_OWNER)
    with pytest.raises(ValueError, match="domain_key"):
        store.mutate("x_com", lambda latest: {**latest, "domain_key": "other_com"},
                     for_user=_OWNER)


def test_scrape_upsert_preserves_enrichment_and_refuses_another_owner(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_OWNER)
    store.update("x_com", {"has_strategy": True,
                           "marketing_strategy": {"status": "approved"}}, for_user=_OWNER)

    refreshed = store.upsert(
        {
            "domain_key": "x_com",
            "field_a": "fresh scrape",
            "_profile": {"fresh": True},
            # profile_to_brand emits these defaults on every scrape.  They are not an instruction
            # to erase analysis that completed while this scrape was running.
            "has_strategy": False,
            "marketing_strategy": None,
            "content_calendar": None,
        },
        owner_id=_OWNER,
        preserve_existing={"has_strategy", "marketing_strategy", "content_calendar"},
    )
    assert refreshed["field_a"] == "fresh scrape"
    assert refreshed["has_strategy"] is True
    assert refreshed["marketing_strategy"] == {"status": "approved"}

    assert store.upsert(
        {"domain_key": "x_com", "field_a": "stolen"}, owner_id=_OTHER,
    ) is None
    stored = store.get("x_com", for_user=_OWNER)
    assert stored["field_a"] == "fresh scrape"
    assert stored["owner_id"] == _OWNER


def test_failed_mutation_is_surfaced_and_preserves_previous_json(tmp_path, monkeypatch):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_OWNER)
    path = store._path("x_com")
    before = path.read_bytes()
    original_replace = Path.replace

    def fail_publish(self, target):
        if Path(target) == path:
            raise OSError("synthetic disk failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(OSError, match="synthetic disk failure"):
        store.mutate("x_com", lambda latest: {**latest, "field_a": "not-durable"},
                     for_user=_OWNER)

    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp")), "failed publication left a staging file"
    assert json.loads(before)["field_a"] == "old"


def test_malformed_existing_file_is_not_overwritten_by_update(tmp_path):
    from api.brand_store import CorruptBrandError

    store = BrandStore(base=tmp_path / "brands")
    path = store._path("x_com")
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CorruptBrandError, match="unreadable"):
        store.update("x_com", {"field_a": "new"}, for_user=_OWNER)
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_calendar_image_persistence_does_not_erase_concurrent_enrichment(tmp_path, monkeypatch):
    """Real composition: creative metadata finishes while a background analysis patch lands."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api.routes import generate

    store = BrandStore()
    store.save(_brand(), owner_id=_OWNER)

    def fake_generate_image(_stale_brand, **_kwargs):
        # The route has already loaded its old snapshot.  Model a background strategy/enrichment
        # job committing while local image work is in progress.
        BrandStore().update(
            "x_com",
            {"marketing_strategy": {"status": "committed-during-render"}},
            for_user=_OWNER,
        )
        return {
            "image_b64": "UE5H",
            # Match generate_image's real identity + F-08 disclosure contract.  Omitting filename
            # creates a state the production generator cannot produce and prevents this test from
            # reaching the concurrent BrandStore mutation it exists to exercise.
            "filename": "x_com_calendar_fake.png",
            "is_preview": True,
            "degraded_reason": "local fake",
            "trust_violation": False,
            "trust_note_ar": "",
            "qa_status": "not_checked",
            "qa_failed": False,
            "qa_note": "local fake was not checked",
        }

    monkeypatch.setattr(generate.images, "generate_image", fake_generate_image)
    monkeypatch.setattr(generate.images, "save_generated_png", lambda _png, _name: "/generated/p.png")

    generate.calendar_generate_image(
        generate.CalendarImageRequest(domain_key="x_com", date="2026-08-10"),
        user={"id": _OWNER},
    )

    stored = BrandStore().get("x_com", for_user=_OWNER)
    assert stored["marketing_strategy"] == {"status": "committed-during-render"}
    poster = stored["content_calendar"]["calendar"][0]["poster"]
    assert poster["url"] == "/generated/p.png"
    assert poster["filename"] == "x_com_calendar_fake.png"
    assert poster["qa_status"] == "not_checked"
    # STALE ASSERTION UPDATED 2026-08-31. This pinned the stub's own free text, but the calendar
    # path no longer echoes a caller-supplied note: it derives the CANONICAL disclosure from
    # `poster_acceptance_from_output`, so an unchecked render always discloses the same sentence
    # rather than whatever the producer happened to write. `test_f08_qa_disclosure_continuity`
    # asserts that contract directly. Production behaviour is deliberate and unchanged here; only
    # this expectation was outdated. The test's real subject — that the concurrent
    # `marketing_strategy` write above is not lost — is untouched.
    assert "No authoritative visual-QA result" in poster["qa_note"]


def test_owner_input_recompute_only_commits_media_plan_v2(tmp_path, monkeypatch):
    """The recompute must not replay the rest of its stale _full_result snapshot."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api.routes import brands
    import media_plan_v2

    store = BrandStore()
    stored = _brand()
    stored["_profile"] = {"name": {"value": "Synthetic"}}
    stored["_full_result"] = {
        "analysis_version": "old",
        "media_plan_v2": {"version": "old-plan"},
    }
    store.save(stored, owner_id=_OWNER)

    class Recomputed:
        def model_dump(self, **_kwargs):
            return {"version": "recomputed"}

    def fake_build(*_args, **_kwargs):
        # set_owner_inputs loaded _full_result before reaching this function.
        BrandStore().update(
            "x_com", {"_full_result": {"analysis_version": "new-concurrent"}},
            for_user=_OWNER,
        )
        return Recomputed()

    monkeypatch.setattr(media_plan_v2, "build_media_plan_v2", fake_build)
    out = brands.set_owner_inputs(
        "x_com", brands.OwnerInputs(monthly_budget=5000), user={"id": _OWNER},
    )
    assert out["plan_recomputed"] is True
    latest = BrandStore().get("x_com", for_user=_OWNER)["_full_result"]
    assert latest["analysis_version"] == "new-concurrent"
    assert latest["media_plan_v2"] == {"version": "recomputed"}


def test_influencer_metadata_patch_does_not_replay_stale_config_or_candidates(
        tmp_path, monkeypatch):
    """A poster job updates its own metadata, not the whole ai block it read before rendering."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    # Import in the same order as the application to avoid the subsystem's pre-existing cycle.
    from api import webapp_images  # noqa: F401
    from api import webapp_influencer as influencer

    store = BrandStore()
    stored = _brand()
    stored["ai_influencer"] = {
        "candidates": {"old": {"url": "/old.png", "persona": "Old"}},
        "selected_id": "old",
        "config": {"tone": "old", "influencer_persona": "Old"},
    }
    store.save(stored, owner_id=_OWNER)
    stale = store.get("x_com", for_user=_OWNER)

    monkeypatch.setattr(influencer.images, "paid_images_enabled", lambda: False)
    monkeypatch.setattr(influencer, "_card", lambda *_args, **_kwargs: b"PNG")
    monkeypatch.setattr(influencer.images, "save_generated_png",
                        lambda *_args, **_kwargs: "/generated/poster.png")
    patch = influencer.generate_influencer_poster(
        stale, product_hint="local product", scene_description="local scene")

    # A user reset commits while the poster job is finishing.  The job may write poster metadata,
    # but must not resurrect the candidate/config snapshot it happened to read at T0.
    store.replace_block(
        "x_com", "ai_influencer",
        {"candidates": {}, "selected_id": "", "config": {"tone": "new"}},
        for_user=_OWNER,
    )
    store.update("x_com", patch, for_user=_OWNER)
    latest = store.get("x_com", for_user=_OWNER)["ai_influencer"]
    assert latest["candidates"] == {}
    assert latest["selected_id"] == ""
    assert latest["config"]["tone"] == "new"
    assert latest["config"]["product_hint"] == "local product"
    assert latest["poster_url"] == "/generated/poster.png"


def test_select_candidate_patch_does_not_replay_unrelated_stale_config(tmp_path):
    from api import webapp_images  # noqa: F401
    from api import webapp_influencer as influencer

    store = BrandStore(base=tmp_path / "brands")
    stored = _brand()
    stored["ai_influencer"] = {
        "candidates": {"c1": {"url": "/c1.png", "persona": "Persona"}},
        "config": {"tone": "old"},
    }
    store.save(stored, owner_id=_OWNER)
    stale = store.get("x_com", for_user=_OWNER)
    patch = influencer.select_candidate(stale, "c1")
    store.update("x_com", {"ai_influencer": {"config": {"tone": "new"}}},
                 for_user=_OWNER)
    store.update("x_com", patch, for_user=_OWNER)
    config = store.get("x_com", for_user=_OWNER)["ai_influencer"]["config"]
    assert config["tone"] == "new"
    assert config["influencer_persona"] == "Persona"
