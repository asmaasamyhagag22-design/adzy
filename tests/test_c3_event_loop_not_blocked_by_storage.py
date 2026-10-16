# -*- coding: utf-8 -*-
"""C3: a storage wait must not become an event-loop freeze.

BrandStore is synchronous by design: real file I/O behind a threading.RLock AND a cross-process
OS file lock (F-09). Both are blocking OS-level waits. Calling one from the event-loop thread does
not merely make THAT request slow — it stops every other request, every SSE stream, and every
background job on the process, for as long as some other thread or process holds the lock.

"The GIL is released while waiting" does not help here. The event loop is a THREAD, and a thread
sitting in a blocking acquire is not running the loop. What matters is where the wait happens, not
whether other threads could theoretically run.

These tests measure the LOOP, not the storage call. A ticker coroutine samples the clock while a
second thread deliberately holds the brand's storage lock; the question is whether the ticker keeps
ticking. The storage operation is allowed to take just as long either way — that is the point. It
must simply stop taking the loop down with it.

Thresholds are calibrated from this machine by `test_calibrate...` below, not copied from anywhere.
No network, no renderer, no paid call.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from pathlib import Path

# Stub playwright BEFORE importing the api app (matches the other api tests).
_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")


class _Stub: ...


for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

import pytest  # noqa: E402

from api.brand_store import BrandStore  # noqa: E402

_OWNER = "user-alice"
_KEY = "example_com"
_URL = "https://example.com/"

# How long the competing writer holds the brand's storage lock. Long enough to dwarf scheduler
# noise, short enough to keep the suite quick.
LOCK_HOLD_S = 1.5
# The loop is "responsive" if no gap exceeds this. Calibrated below; generous so a loaded machine
# cannot make it flaky, yet ~5x smaller than LOCK_HOLD_S so a real freeze cannot hide under it.
RESPONSIVE_GAP_S = 0.30
# A freeze is only counted as such if the loop actually stalled for most of the hold.
FROZEN_GAP_S = LOCK_HOLD_S * 0.7


class Ticker:
    """Samples the clock on the event loop and remembers the worst gap between samples."""

    def __init__(self, interval: float = 0.005) -> None:
        self.interval = interval
        self.samples: list[float] = []
        self._stop = False

    async def run(self) -> None:
        self.samples.append(time.perf_counter())
        while not self._stop:
            await asyncio.sleep(self.interval)
            self.samples.append(time.perf_counter())

    def stop(self) -> None:
        self._stop = True

    @property
    def max_gap(self) -> float:
        return max((b - a for a, b in zip(self.samples, self.samples[1:])), default=0.0)


class LockHolder:
    """Holds the REAL storage lock for one brand, from another thread, for a fixed interval.

    Uses BrandStore.mutate so the lock acquired is exactly the one production takes — the module
    RLock plus the cross-process file lock — not a stand-in.
    """

    def __init__(self, base: Path, domain_key: str = _KEY, hold: float = LOCK_HOLD_S) -> None:
        self.base, self.domain_key, self.hold = base, domain_key, hold
        self.acquired = threading.Event()
        self.thread: threading.Thread | None = None

    def _hold(self) -> None:
        def blocking_mutator(latest: dict) -> dict:
            self.acquired.set()          # the lock is now HELD
            time.sleep(self.hold)        # ...and stays held for exactly this long
            return {**latest, "held": True}

        BrandStore(base=self.base).mutate(self.domain_key, blocking_mutator, for_user=_OWNER)

    def __enter__(self) -> "LockHolder":
        self.thread = threading.Thread(target=self._hold, daemon=True)
        self.thread.start()
        assert self.acquired.wait(10), "the competing writer never took the storage lock"
        return self

    def __exit__(self, *exc) -> None:
        if self.thread is not None:
            self.thread.join(20)


def _brand() -> dict:
    return {"domain_key": _KEY, "organization_name": "Example"}


async def _with_ticker(coro_factory):
    """Run `coro_factory()` while a ticker samples the loop. Returns (result, elapsed, max_gap)."""
    ticker = Ticker()
    task = asyncio.create_task(ticker.run())
    await asyncio.sleep(0.05)                      # let the ticker establish a baseline
    started = time.perf_counter()
    try:
        result = await coro_factory()
    finally:
        elapsed = time.perf_counter() - started
        ticker.stop()
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, elapsed, ticker.max_gap


# =====================================================================
# calibration — establish this machine's noise floor
# =====================================================================

def test_calibrate_idle_loop_noise_is_far_below_the_threshold():
    """An idle loop's worst gap must be small, or every threshold below is meaningless."""
    async def scenario():
        async def nothing():
            await asyncio.sleep(LOCK_HOLD_S)
            return None
        return await _with_ticker(nothing)

    _r, elapsed, gap = asyncio.run(scenario())
    assert elapsed >= LOCK_HOLD_S * 0.9
    assert gap < RESPONSIVE_GAP_S, (
        f"this machine's idle loop already stalls {gap:.3f}s; the C3 thresholds cannot "
        f"distinguish a freeze from noise here")
    print(f"\n[calibration] idle loop max gap = {gap * 1000:.1f} ms "
          f"(threshold {RESPONSIVE_GAP_S * 1000:.0f} ms)")


# =====================================================================
# the mechanism, isolated: same wait, two execution locations
# =====================================================================

def test_a_storage_wait_on_the_loop_thread_freezes_the_loop(tmp_path):
    """NEGATIVE CONTROL: the blocking call made directly by the loop thread stops the loop."""
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)

    async def scenario():
        with LockHolder(base):
            async def on_the_loop():
                # Exactly what a coroutine does when it calls BrandStore synchronously.
                return BrandStore(base=base).update(_KEY, {"field": "x"}, for_user=_OWNER)
            return await _with_ticker(on_the_loop)

    result, elapsed, gap = asyncio.run(scenario())
    assert result is not None, "the write itself must still succeed"
    assert elapsed >= FROZEN_GAP_S, f"the storage call did not actually wait ({elapsed:.3f}s)"
    assert gap >= FROZEN_GAP_S, (
        f"expected the loop to freeze for ~{LOCK_HOLD_S}s, but the worst gap was {gap:.3f}s")
    print(f"\n[on-loop]   storage wait {elapsed:.3f}s   loop max gap {gap:.3f}s  <-- FROZEN")


def test_the_same_storage_wait_off_thread_leaves_the_loop_responsive(tmp_path):
    """POSITIVE CONTROL: the wait is just as long, but the loop keeps running."""
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)

    async def scenario():
        with LockHolder(base):
            async def off_thread():
                return await asyncio.to_thread(
                    BrandStore(base=base).update, _KEY, {"field": "x"}, for_user=_OWNER)
            return await _with_ticker(off_thread)

    result, elapsed, gap = asyncio.run(scenario())
    assert result is not None
    assert elapsed >= FROZEN_GAP_S, (
        f"the storage call no longer waits ({elapsed:.3f}s) — the probe stopped measuring "
        f"contention, so a green result here would prove nothing")
    assert gap < RESPONSIVE_GAP_S, (
        f"the loop stalled {gap:.3f}s even off-thread")
    print(f"\n[off-thread] storage wait {elapsed:.3f}s   loop max gap {gap:.3f}s  <-- responsive")


# =====================================================================
# production boundaries
# =====================================================================

@pytest.fixture
def webapp(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-c3")
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    from api.user_auth import webapp_dir
    base = webapp_dir() / "brands"
    BrandStore().save(_brand(), owner_id=_OWNER)
    return base


def test_final_scrape_persistence_does_not_freeze_the_loop(webapp, monkeypatch):
    """PRODUCTION BOUNDARY: `_run_scrape_job` -> `_finish_scrape` -> `BrandStore.upsert`.

    The pipeline is replaced with a local fake, so nothing is crawled — but the BrandStore wait is
    the real one, against the real lock held by a real competing writer.
    """
    from api.jobs.recorder import JobRecorder
    from api.jobs.store import JobStore, serving_store
    from api.persistence import get_persistence
    from api.routes import scrape as scrape_mod

    def fake_pipeline(job_id, url, recorder, **kwargs):
        recorder.set_result_threadsafe(job_id, {"source_url": url})
        return {"ok": True}

    monkeypatch.setattr(scrape_mod, "run_pipeline_job", fake_pipeline)

    async def scenario():
        store = JobStore(ttl_seconds=3600)
        with serving_store(store):
            job = store.create(owner_principal="user:" + _OWNER)
            job.domain_key = _KEY
            store.attach_loop(job.job_id, asyncio.get_running_loop())
            with LockHolder(webapp):
                async def run_job():
                    await scrape_mod._run_scrape_job(
                        job_id=job.job_id, url=_URL,
                        recorder=JobRecorder(job_id=job.job_id, provider=get_persistence()),
                        domain_key=_KEY,
                        max_pages=1, max_depth=0, delay=0.0, owner_id=_OWNER)
                    return store.get(job.job_id)
                return await _with_ticker(run_job)

    job, elapsed, gap = asyncio.run(scenario())
    assert elapsed >= FROZEN_GAP_S, "the probe did not reach the real storage wait"
    print(f"\n[scrape]    storage wait {elapsed:.3f}s   loop max gap {gap:.3f}s")
    assert gap < RESPONSIVE_GAP_S, (
        f"the final scrape persistence froze the event loop for {gap:.3f}s")
    assert job.status.value == "succeeded", f"F-11: job must succeed, got {job.status}"


def test_webapp_brand_job_persistence_does_not_freeze_the_loop(webapp):
    """PRODUCTION BOUNDARY: `run_brand_job` -> `BrandStore.update` after generation."""
    from api.jobs.store import JobStore, serving_store
    from api.webapp_jobs import run_brand_job

    async def scenario():
        store = JobStore(ttl_seconds=3600)
        with serving_store(store):
            job = store.create(owner_principal="user:" + _OWNER)
            job.domain_key = _KEY
            store.attach_loop(job.job_id, asyncio.get_running_loop())
            with LockHolder(webapp):
                async def run_job():
                    await run_brand_job(job_id=job.job_id, domain_key=_KEY,
                                        work=lambda b: {"generated": "value"}, owner_id=_OWNER)
                    return store.get(job.job_id)
                return await _with_ticker(run_job)

    job, elapsed, gap = asyncio.run(scenario())
    assert elapsed >= FROZEN_GAP_S, "the probe did not reach the real storage wait"
    print(f"\n[brandjob]  storage wait {elapsed:.3f}s   loop max gap {gap:.3f}s")
    assert gap < RESPONSIVE_GAP_S, (
        f"the webapp brand job froze the event loop for {gap:.3f}s")
    assert job.status.value == "succeeded", f"F-11: job must succeed, got {job.status}"
    assert BrandStore().get(_KEY, for_user=_OWNER)["generated"] == "value"


def test_influencer_reset_does_not_freeze_the_loop(webapp, monkeypatch):
    """PRODUCTION BOUNDARY: `async create()` -> `_candidate_job` -> `BrandStore.replace_block`.

    A sync helper called straight from a coroutine is on the loop just as much as an inline call —
    which is why the audit had to follow the call chain, not just the enclosing `def`.
    """
    from api.jobs.store import JobStore, serving_store
    # Import in the same order as the application: `api.webapp_images` first. The
    # webapp_images <-> webapp_influencer cycle is a real, separate defect (C7) and importing the
    # route first trips it. Working around it here keeps this file about event-loop safety.
    from api import webapp_images  # noqa: F401
    from api.routes import influencer as inf

    class _Req:
        domain_key = _KEY

        def model_dump(self):
            return {"tone": "warm"}

    class _App:
        class state:  # noqa: N801
            store = None

    class _Request:
        app = _App

    async def scenario():
        _App.state.store = JobStore(ttl_seconds=3600)
        # Calling the route handler directly skips the middleware that binds the serving store.
        binding = serving_store(_App.state.store)
        binding.__enter__()
        with LockHolder(webapp):
            async def call_route():
                return await inf.create(_Req(), _Request(), {"id": _OWNER})
            return await _with_ticker(call_route)

    out, elapsed, gap = asyncio.run(scenario())
    assert elapsed >= FROZEN_GAP_S, "the probe did not reach the real storage wait"
    print(f"\n[influencer] storage wait {elapsed:.3f}s   loop max gap {gap:.3f}s")
    assert gap < RESPONSIVE_GAP_S, (
        f"the influencer reset froze the event loop for {gap:.3f}s")
    assert out.get("job_id"), "the route must still start its job"


def test_the_c2_preflight_read_does_not_block_on_a_competing_writer(tmp_path):
    """R1's `owner_of()` runs on the loop in `async scrape()`. Prove that is SAFE, don't assume it.

    Reads in this store are lock-FREE by construction: the cross-process lock is taken on a
    separate `.lock` file, never on the brand JSON, and writes publish by atomic replace. So a
    reader never queues behind a writer and never observes a half-written document. That is why the
    preflight can stay inline — measured here rather than argued.
    """
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)

    async def scenario():
        with LockHolder(base):
            async def preflight():
                return BrandStore(base=base).owner_of(_KEY)
            return await _with_ticker(preflight)

    owner, elapsed, gap = asyncio.run(scenario())
    assert owner == _OWNER, "the preflight must still answer correctly under contention"
    print(f"\n[preflight] read {elapsed * 1000:.1f} ms   loop max gap {gap * 1000:.1f} ms")
    assert elapsed < RESPONSIVE_GAP_S, (
        f"owner_of() waited {elapsed:.3f}s — it is NOT lock-free and must be offloaded too")
    assert gap < RESPONSIVE_GAP_S


def test_the_shared_module_rlock_is_why_the_gallery_path_counts(tmp_path):
    """MECHANISM: `record_history` takes BrandStore's module RLock, so a brand write stalls it.

    `_storage_lock` is `_WRITE_LOCK` + the file lock. The gallery append takes `_WRITE_LOCK` alone,
    which is enough to queue behind any in-flight brand write. This is why `/generate/upload` and
    `/generate/reference` — both `async def`, both reaching `record_history` through `_emit` —
    belong in C3's scope and not only in the image path's.
    """
    from api.brand_store import _WRITE_LOCK

    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)

    async def scenario():
        with LockHolder(base):
            async def take_the_same_lock():
                started = time.perf_counter()
                with _WRITE_LOCK:                      # exactly what record_history() does
                    pass
                return time.perf_counter() - started
            return await _with_ticker(take_the_same_lock)

    waited, _elapsed, _gap = asyncio.run(scenario())
    print(f"\n[rlock]     shared-RLock wait {waited:.3f}s (mechanism)")
    assert waited >= FROZEN_GAP_S, (
        "record_history no longer shares BrandStore's lock — the premise of the multipart-route "
        "test below no longer holds and it must be re-derived, not trusted")


def test_multipart_generate_route_does_not_freeze_the_loop(webapp, monkeypatch):
    """PRODUCTION BOUNDARY: `async generate_with_logo()` -> `_emit` -> `record_history`.

    The renderer is faked — it is irrelevant here and would only add cost. The gallery append,
    and therefore the real lock wait, is left completely alone.
    """
    from api.routes import generate as gen

    def fake_generate_image(_brand, **_kwargs):
        return {"image_b64": "UE5H", "filename": "x.png", "is_preview": True,
                "degraded_reason": "local fake", "trust_violation": False, "trust_note_ar": "",
                "qa_status": "not_checked", "qa_failed": False, "qa_note": "local fake"}

    monkeypatch.setattr(gen.images, "generate_image", fake_generate_image)

    async def scenario():
        with LockHolder(webapp):
            async def call_route():
                return await gen.generate_with_logo(
                    domain_key=_KEY, content_type="post", style="", headline="H",
                    subtext="", cta="", product_url="", language="auto",
                    logo=None, user={"id": _OWNER})
            return await _with_ticker(call_route)

    out, elapsed, gap = asyncio.run(scenario())
    assert out["filename"] == "x.png", "the route must still return its render"
    assert elapsed >= FROZEN_GAP_S, "the probe did not reach the real gallery lock wait"
    print(f"\n[multipart] storage wait {elapsed:.3f}s   loop max gap {gap:.3f}s")
    assert gap < RESPONSIVE_GAP_S, (
        f"the multipart generate route froze the event loop for {gap:.3f}s")


# =====================================================================
# saturation — many concurrent offloads must stay correct AND responsive
# =====================================================================

def test_concurrent_offloaded_writes_stay_correct_and_the_loop_stays_responsive(tmp_path):
    """Default executor under bounded stress: no corruption, no lost update, no deadlock."""
    base = tmp_path / "brands"
    BrandStore(base=base).save(_brand(), owner_id=_OWNER)
    n = 12

    async def scenario():
        async def storm():
            return await asyncio.gather(*[
                asyncio.to_thread(
                    lambda i=i: BrandStore(base=base).update(
                        _KEY, {"counters": {f"w{i}": i}}, for_user=_OWNER))
                for i in range(n)
            ])
        return await _with_ticker(storm)

    results, elapsed, gap = asyncio.run(scenario())
    assert all(r is not None for r in results), "an offloaded write was refused"
    stored = BrandStore(base=base).get(_KEY, for_user=_OWNER)
    assert stored["counters"] == {f"w{i}": i for i in range(n)}, (
        "a concurrent offloaded write was lost — F-09 read-latest-under-lock did not hold")
    print(f"\n[saturation] {n} concurrent writes in {elapsed:.3f}s   loop max gap {gap:.3f}s")
    assert gap < RESPONSIVE_GAP_S, f"the loop stalled {gap:.3f}s under offload saturation"
