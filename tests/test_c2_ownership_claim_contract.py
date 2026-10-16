# -*- coding: utf-8 -*-
"""C2: who may manage a Brand record, and when that management is assigned.

`owner_id` records the ACCOUNT MANAGING THE BRAND RECORD inside this product. It is not a claim
about who legally owns the real-world company, and nothing here verifies that.

Five cases make up the contract:

  A. brand absent            -> a successful authenticated scrape creates it and records the caller
  B. brand present, UNCLAIMED-> a successful authenticated re-scrape CLAIMS it, in the same locked
                                write that persists the result
  C. brand present, mine     -> update allowed, owner unchanged
  D. brand present, someone  -> refused BEFORE the crawl is paid for, and refused AGAIN at the
     else's                     final write, because the preflight is a cost gate and never the
                                concurrency authority
  E. two callers race for an -> exactly one wins; the loser must not overwrite the winner and must
     unclaimed brand            be told the truth

The regression this file exists for: the surviving tree refused case B as if it were case D, so a
brand created before accounts existed could never be re-scraped by anyone — the scrape ran, cost
what it costs, and then failed at the last step with "Brand profile could not be saved."

These tests drive the real BrandStore and the real route. No renderer, no network, no paid call.
"""
from __future__ import annotations

import json
import sys
import threading
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
from fastapi.testclient import TestClient  # noqa: E402

from api.brand_store import BrandStore  # noqa: E402

_ALICE = "user-alice"
_BOB = "user-bob"
_KEY = "example_com"
_URL = "https://example.com/"


def _brand(**extra) -> dict:
    return {"domain_key": _KEY, "organization_name": "Example", **extra}


def _unclaimed(store: BrandStore) -> dict:
    """A brand stored before accounts existed: present, readable, and owned by nobody."""
    saved = store.save(_brand(field_a="from-before-accounts"))
    assert not saved.get("owner_id"), "fixture must start UNCLAIMED"
    return saved


def _raw(store: BrandStore) -> dict:
    """The document as it sits on disk, past the store's presentation flags."""
    return json.loads(store._path(_KEY).read_text(encoding="utf-8"))


# =====================================================================
# store level — the final persistence boundary
# =====================================================================

def test_case_a_new_brand_records_the_scraping_account_as_owner(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    persisted = store.upsert(_brand(field_a="fresh"), owner_id=_ALICE)
    assert persisted is not None, "a brand nobody owns yet must be creatable"
    assert persisted["owner_id"] == _ALICE
    assert _raw(store)["owner_id"] == _ALICE, "the owner must be on disk, not only in the return"


def test_case_b_unclaimed_brand_is_claimed_by_a_successful_rescrape(tmp_path):
    """THE REGRESSION. An unclaimed brand was refused as if it belonged to someone else."""
    store = BrandStore(base=tmp_path / "brands")
    _unclaimed(store)

    persisted = store.upsert(_brand(field_a="rescraped"), owner_id=_ALICE)

    assert persisted is not None, "a re-scrape of an UNCLAIMED brand must not be refused"
    assert persisted["owner_id"] == _ALICE, "success must claim the brand for the caller"
    assert persisted["field_a"] == "rescraped", "the scrape result must actually be stored"
    on_disk = _raw(store)
    assert on_disk["owner_id"] == _ALICE
    assert on_disk["field_a"] == "rescraped"


def test_case_b_claim_and_result_are_one_write_not_two(tmp_path, monkeypatch):
    """The claim must ride the SAME locked write as the result — never a claim-then-persist pair.

    Two writes would leave a window where the brand is claimed but still holds the old result, and
    a crash between them would strand ownership without the work that earned it.
    """
    import api.brand_store as bs

    store = BrandStore(base=tmp_path / "brands")
    _unclaimed(store)

    writes: list[dict] = []
    original = bs._write_atomic

    def counting_write(path, payload):
        writes.append(json.loads(payload))
        return original(path, payload)

    monkeypatch.setattr(bs, "_write_atomic", counting_write)
    assert store.upsert(_brand(field_a="rescraped"), owner_id=_ALICE) is not None

    assert len(writes) == 1, f"claim-on-success must be ONE write, saw {len(writes)}"
    assert writes[0]["owner_id"] == _ALICE
    assert writes[0]["field_a"] == "rescraped", "the single write carries owner AND result"


def test_case_c_self_owned_rescrape_updates_and_keeps_the_owner(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(field_a="old"), owner_id=_ALICE)

    persisted = store.upsert(_brand(field_a="new"), owner_id=_ALICE)

    assert persisted is not None, "my own brand must be re-scrapable"
    assert persisted["field_a"] == "new"
    assert persisted["owner_id"] == _ALICE, "a re-scrape must not rewrite ownership"


def test_case_d_foreign_owner_is_refused_at_the_final_write(tmp_path):
    """Item 14: the store guard is authoritative even when a preflight let the caller through."""
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(field_a="bob-work"), owner_id=_BOB)

    assert store.upsert(_brand(field_a="alice-overwrite"), owner_id=_ALICE) is None

    on_disk = _raw(store)
    assert on_disk["owner_id"] == _BOB, "ownership must not transfer"
    assert on_disk["field_a"] == "bob-work", "the refused caller must not have written anything"


def test_owner_cannot_be_reassigned_or_cleared_through_ordinary_writes(tmp_path):
    """F-09 immutability: only unclaimed -> A is allowed. Never A -> B, never A -> nothing."""
    store = BrandStore(base=tmp_path / "brands")
    store.save(_brand(), owner_id=_ALICE)

    with pytest.raises(ValueError, match="owner_id"):
        store.update(_KEY, {"owner_id": _BOB}, for_user=_ALICE)
    with pytest.raises(ValueError, match="owner_id"):
        store.mutate(_KEY, lambda latest: {**latest, "owner_id": ""}, for_user=_ALICE)

    assert _raw(store)["owner_id"] == _ALICE


# =====================================================================
# case E — the race, against the real locking
# =====================================================================

def test_case_e_concurrent_claim_of_one_unclaimed_brand_has_exactly_one_winner(tmp_path):
    """Both callers see 'unclaimed', both try to claim. The storage lock decides.

    Coordination is a Barrier, not a sleep: both threads are released into `upsert` together and the
    store's own lock serialises them, so the outcome is decided by the code under test.
    """
    base = tmp_path / "brands"
    _unclaimed(BrandStore(base=base))

    start = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def claim(user: str) -> None:
        try:
            start.wait(timeout=15)
            results[user] = BrandStore(base=base).upsert(
                _brand(field_a=f"result-from-{user}"), owner_id=user)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=claim, args=(u,)) for u in (_ALICE, _BOB)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert not errors, f"the race raised instead of resolving: {errors}"
    assert not any(t.is_alive() for t in threads), "a claimant deadlocked"

    winners = [u for u, r in results.items() if r is not None]
    losers = [u for u, r in results.items() if r is None]
    assert len(winners) == 1, f"exactly one winner required, got winners={winners} losers={losers}"
    assert len(losers) == 1, "the loser must be told it lost, not silently succeed"

    winner = winners[0]
    on_disk = _raw(BrandStore(base=base))          # also proves the file is still valid JSON
    assert on_disk["owner_id"] == winner, "the persisted owner must be the caller that won"
    assert on_disk["field_a"] == f"result-from-{winner}", "the loser overwrote the winner's result"
    assert results[winner]["owner_id"] == winner


def test_a_preflight_that_saw_unclaimed_still_loses_the_final_write(tmp_path):
    """Item 11: no TOCTOU. Passing the preflight is not permission to overwrite the winner."""
    store = BrandStore(base=tmp_path / "brands")
    _unclaimed(store)

    # Both callers preflight while the brand is genuinely unclaimed.
    assert store.owner_of(_KEY) == "", "preflight sees an unclaimed brand"
    assert store.owner_of(_KEY) == ""

    assert store.upsert(_brand(field_a="alice-won"), owner_id=_ALICE) is not None
    # Bob's preflight is now stale. The final write must re-read the latest owner and refuse.
    assert store.upsert(_brand(field_a="bob-late"), owner_id=_BOB) is None

    on_disk = _raw(store)
    assert on_disk["owner_id"] == _ALICE
    assert on_disk["field_a"] == "alice-won"


def test_owner_of_reports_absent_unclaimed_and_owned_without_leaking_the_document(tmp_path):
    store = BrandStore(base=tmp_path / "brands")
    assert store.owner_of(_KEY) is None, "absent brand"

    _unclaimed(store)
    assert store.owner_of(_KEY) == "", "present but unclaimed"

    store.save(_brand(secret="bob-private-analysis"), owner_id=_BOB)
    assert store.owner_of(_KEY) == _BOB, "present and owned"


# =====================================================================
# route level — the preflight is a COST gate
# =====================================================================

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-c2")
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    yield


@pytest.fixture
def client(isolated):
    from api.main import app
    # The context manager runs the lifespan, which creates `app.state.store`.
    with TestClient(app) as c:
        yield c


def _account(client, email: str) -> tuple[dict, str]:
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "password123", "full_name": email})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["id"]


@pytest.fixture
def counted_pipeline(monkeypatch):
    """Count entries into the expensive boundary. Never let the real crawler run."""
    calls: list[str] = []

    def fake_pipeline(job_id, url, store, **kwargs):
        calls.append(url)
        raise AssertionError("the pipeline must not be reached in these tests")

    monkeypatch.setattr("api.routes.scrape.run_pipeline_job", fake_pipeline)
    return calls


def test_foreign_owned_brand_is_refused_before_the_pipeline_is_reached(client, counted_pipeline):
    """CASE D. Bob owns it; Alice must be stopped at the door, not after paying for the crawl."""
    _bob_headers, bob_id = _account(client, "bob@example.com")
    BrandStore().save(_brand(field_a="bob-work"), owner_id=bob_id)
    alice_headers, _alice_id = _account(client, "alice@example.com")

    r = client.post("/api/scrape", json={"url": _URL}, headers=alice_headers)

    assert r.status_code == 409, r.text
    assert "job_id" not in r.json(), "a refused scrape must not hand back a job to poll"
    assert counted_pipeline == [], "expensive work ran for a request that was never authorised"
    detail = str(r.json().get("detail", ""))
    assert "bob@example.com" not in detail and "bob-work" not in detail, \
        "the refusal must not disclose the other account or its data"


def test_unclaimed_brand_passes_the_preflight(client, counted_pipeline):
    """CASE B at the route: the preflight must not re-introduce the bug it is guarding."""
    alice_headers, _ = _account(client, "alice@example.com")
    BrandStore().save(_brand(field_a="from-before-accounts"))

    r = client.post("/api/scrape", json={"url": _URL}, headers=alice_headers)

    assert r.status_code == 200, r.text
    assert r.json().get("job_id"), "an unclaimed brand must be re-scrapable"


def test_absent_brand_passes_the_preflight(client, counted_pipeline):
    alice_headers, _ = _account(client, "alice@example.com")
    r = client.post("/api/scrape", json={"url": _URL}, headers=alice_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("job_id")


def test_self_owned_brand_passes_the_preflight(client, counted_pipeline):
    alice_headers, alice_id = _account(client, "alice@example.com")
    BrandStore().save(_brand(field_a="mine"), owner_id=alice_id)

    r = client.post("/api/scrape", json={"url": _URL}, headers=alice_headers)

    assert r.status_code == 200, r.text
    assert r.json().get("job_id")


def test_preflight_refusal_creates_no_job(client, counted_pipeline):
    """Item 17: a refused scrape must not leave a job that could later report success."""
    _bob_headers, bob_id = _account(client, "bob@example.com")
    BrandStore().save(_brand(), owner_id=bob_id)
    alice_headers, _ = _account(client, "alice@example.com")

    from api.main import app
    before = len(app.state.store.all_job_ids())
    r = client.post("/api/scrape", json={"url": _URL}, headers=alice_headers)

    assert r.status_code == 409, r.text
    assert len(app.state.store.all_job_ids()) == before, "a refused scrape created a job anyway"
