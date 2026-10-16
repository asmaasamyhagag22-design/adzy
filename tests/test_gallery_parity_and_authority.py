# -*- coding: utf-8 -*-
"""`/api/history` parity across backends (§10), and the full PostgreSQL authority gate (§22).

TWO CLAIMS, AND THEY ARE DIFFERENT.

Parity: the same workflow driven twice, once per backend, with the two responses diffed key by key.
A gallery that returns a slightly different shape under PostgreSQL does not fail anything — the SPA
just renders a blank card, on one backend only, with neither store disagreeing with itself. That is
the class of defect no single-backend test can see.

Authority: with `postgres` selected, the legacy stores must be reached ZERO times for business
state. Not "mostly", not "except the gallery" — the whole point of a single authority is that the
count is zero, and a count is checkable where a promise is not. `/api/generated/{name}` is excluded
by name and by reason: it serves bytes that already exist on disk and is classified
BACKEND_INDEPENDENT, which is a file server, not a source of truth.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path

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

from api.persistence import ENV_BACKEND, LEGACY, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required to compare the two backends")

#: Keys that MUST match exactly between the backends.
_PARITY_KEYS = ("filename", "brand_name", "domain_key", "content_type", "style",
                "image_b64", "is_preview", "trust_violation", "qa_status", "qa_failed",
                "qa_note")

#: The ONE intentional difference, stated rather than normalised away. Each backend answers with
#: the owner id IT knows: the file store keeps the legacy 16-hex user id, PostgreSQL keeps the
#: user's UUID. They are the same person under two identity schemes, and rewriting one to look
#: like the other would be inventing an id that store never issued.
_INTENTIONALLY_DIFFERENT = ("owner_id",)


def _png(seed: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + seed.encode("utf-8").ljust(32, b"\x00")


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


def _drive(backend: str, tmp_path: Path, monkeypatch, *, url: str, headline: str) -> dict:
    """Scrape a brand, generate an image, read the gallery back — over real HTTP, on `backend`."""
    import time

    import api.user_auth as ua
    from api.routes import generate as generate_route
    from api.routes import scrape as scrape_route
    from db.session import dispose

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / backend))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-gparity")
    monkeypatch.setenv(ENV_BACKEND, backend)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    ua._SECRET_CACHE = None
    dispose()
    reset_persistence()

    image = _png(headline)

    def fake_generate(brand, **kwargs):
        return {"image_b64": base64.b64encode(image).decode("ascii"),
                "filename": f"{headline}.png", "headline": headline, "subtext": "", "cta": "",
                "is_preview": True, "trust_violation": False, "qa_status": "pass"}

    def pipeline(job_id, _url, recorder, **kwargs):
        recorder.set_result_threadsafe(job_id, {"name": {"value": "Parity Brand"},
                                                "primary_language": "en"})
        return {"ready_for_strategy": False, "profile_path": None}

    monkeypatch.setattr(generate_route.images, "generate_image", fake_generate)
    monkeypatch.setattr(scrape_route, "run_pipeline_job", pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)

    from api.brand_translate import domain_key_from_url
    from api.main import app

    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={
            "email": f"gy{uuid.uuid4().hex[:8]}@test.local",
            "password": "password123", "full_name": "GY"})
        assert signup.status_code == 200, signup.text
        headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

        started = client.post("/api/scrape", json={"url": url}, headers=headers)
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]
        for _ in range(400):
            body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
            if body.get("status") in ("done", "error"):
                assert body["status"] == "done", body
                break
            time.sleep(0.03)
        else:
            raise AssertionError(f"[{backend}] the brand scrape never finished")

        domain_key = domain_key_from_url(url)
        generated = client.post("/api/generate", headers=headers, json={
            "domain_key": domain_key, "content_type": "post", "style": "bold",
            "headline": headline})
        assert generated.status_code == 200, generated.text

        history = client.get("/api/history?limit=100", headers=headers)
        assert history.status_code == 200, history.text
        items = history.json()["history"]

        stranger = client.post("/api/auth/signup", json={
            "email": f"gz{uuid.uuid4().hex[:8]}@test.local",
            "password": "password123", "full_name": "GZ"})
        stranger_headers = {"Authorization": f"Bearer {stranger.json()['access_token']}"}
        foreign = client.get("/api/history?limit=100", headers=stranger_headers).json()["history"]
        # Scoped to THIS run's brand. The disposable database is shared across the suite and the
        # unattributed historical entries are visible to everyone BY DESIGN, so an unscoped count
        # would measure the neighbours rather than the scoping rule.
        foreign = [i for i in foreign if i.get("domain_key") == domain_key_from_url(url)]
        unauthenticated = client.get("/api/history?limit=100").status_code

    dispose()
    reset_persistence()
    mine = [i for i in items if i.get("domain_key") == domain_key]
    return {
        "count": len(mine),
        "item": mine[0] if mine else {},
        "keys": sorted(mine[0].keys()) if mine else [],
        "foreign_sees": [i.get("filename") for i in foreign],
        "unauthenticated_status": unauthenticated,
        "generated": generated.json(),
    }


@pytest.fixture()
def both(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()

    def run() -> tuple[dict, dict]:
        url = f"https://gpar{uuid.uuid4().hex[:8]}.test/"
        headline = f"parity{uuid.uuid4().hex[:6]}"
        return (_drive(LEGACY, tmp_path, monkeypatch, url=url, headline=headline),
                _drive(POSTGRES, tmp_path, monkeypatch, url=url, headline=headline))

    return run


# =====================================================================
# §10 — history parity
# =====================================================================

def test_the_history_entry_reads_the_same_on_both_backends(both):
    legacy, postgres = both()

    assert legacy["count"] == postgres["count"] == 1
    for key in _PARITY_KEYS:
        assert legacy["item"].get(key) == postgres["item"].get(key), (
            f"'{key}' differs: legacy={legacy['item'].get(key)!r} "
            f"postgres={postgres['item'].get(key)!r}")

    # The timestamp is a real epoch on both, generated seconds apart — the SHAPE must match, the
    # value obviously cannot.
    for side in (legacy, postgres):
        assert isinstance(side["item"]["created_at"], int) and side["item"]["created_at"] > 0


def test_the_only_shape_difference_is_the_declared_one(both):
    """Any NEW divergence fails here rather than being discovered by a blank card in the UI."""
    legacy, postgres = both()
    difference = set(legacy["keys"]) ^ set(postgres["keys"])
    assert difference <= set(_INTENTIONALLY_DIFFERENT), (
        f"undeclared shape difference between the backends: {sorted(difference)}")

    # And the declared one is a difference in VALUE, not in presence: both name an owner.
    assert legacy["item"].get("owner_id") and postgres["item"].get("owner_id")
    assert legacy["item"]["owner_id"] != postgres["item"]["owner_id"]


def test_scoping_behaves_identically_on_both_backends(both):
    legacy, postgres = both()
    assert legacy["foreign_sees"] == postgres["foreign_sees"] == []
    assert legacy["unauthenticated_status"] == postgres["unauthenticated_status"]
    assert legacy["unauthenticated_status"] in (401, 403)


def test_the_generate_response_is_unchanged_by_the_crossing(both):
    """The image route's own response shape is a separate public contract from the gallery's."""
    legacy, postgres = both()
    assert set(legacy["generated"].keys()) == set(postgres["generated"].keys())
    assert legacy["generated"]["image_b64"] == postgres["generated"]["image_b64"]


# =====================================================================
# §22 — the full authority gate
# =====================================================================

def test_a_full_postgres_workflow_reaches_no_legacy_store_at_all(tmp_path, monkeypatch):
    """Brand, user, job AND gallery: every legacy business authority counted, all must be zero.

    The JobStore is checked by OBJECT rather than by patching: it is created by the lifespan on
    both backends, so "was it constructed" proves nothing and "does it hold any job" is the
    question that matters.
    """
    import time

    import api.brand_store as bs
    import api.user_auth as ua
    from api.routes import generate as generate_route
    from api.routes import scrape as scrape_route
    from db.session import dispose

    _migrate()
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-gate")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    ua._SECRET_CACHE = None
    dispose()
    reset_persistence()

    touched: list[str] = []
    for module, holder, methods in (
            (bs, "BrandStore", ("save", "upsert", "update", "mutate", "replace_block", "delete",
                                "get", "all", "_persist_locked")),
            (ua, "UserStore", ("create", "_save", "by_email", "by_id")),
    ):
        target = getattr(module, holder)
        for method in methods:
            if hasattr(target, method):
                monkeypatch.setattr(
                    target, method,
                    (lambda n: lambda *a, **k: touched.append(n))(f"{holder}.{method}"),
                    raising=False)
    for name in ("record_history", "read_history"):
        monkeypatch.setattr(bs, name,
                            (lambda n: lambda *a, **k: touched.append(n) or [])(name),
                            raising=False)

    image = _png("gate")

    monkeypatch.setattr(generate_route.images, "generate_image", lambda brand, **kw: {
        "image_b64": base64.b64encode(image).decode("ascii"), "filename": "gate.png",
        "headline": "gate", "subtext": "", "cta": "", "is_preview": False,
        "trust_violation": False, "qa_status": "pass"})

    def pipeline(job_id, _url, recorder, **kwargs):
        recorder.set_result_threadsafe(job_id, {"name": {"value": "Gate"},
                                                "primary_language": "en"})
        return {"ready_for_strategy": False, "profile_path": None}

    monkeypatch.setattr(scrape_route, "run_pipeline_job", pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)

    from api.brand_translate import domain_key_from_url
    from api.main import app

    url = f"https://gate{uuid.uuid4().hex[:8]}.test/"
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={
            "email": f"gg{uuid.uuid4().hex[:8]}@test.local",
            "password": "password123", "full_name": "GG"})
        assert signup.status_code == 200, signup.text
        headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        started = client.post("/api/scrape", json={"url": url}, headers=headers)
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]
        for _ in range(400):
            body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
            if body.get("status") in ("done", "error"):
                assert body["status"] == "done", body
                break
            time.sleep(0.03)
        else:
            raise AssertionError("the scrape never finished")

        domain_key = domain_key_from_url(url)
        assert client.get(f"/api/brands/{domain_key}", headers=headers).status_code == 200
        assert client.post("/api/generate", headers=headers, json={
            "domain_key": domain_key, "headline": "gate"}).status_code == 200
        served = client.get("/api/history", headers=headers).json()["history"]
        # Scoped to THIS run's brand: the disposable database is shared across the suite and the
        # unattributed historical entries are visible to every caller by design.
        mine = [i for i in served if i.get("domain_key") == domain_key]
        assert len(mine) == 1, [i.get("filename") for i in served]

        # The in-memory JobStore is MOUNTED on both backends. Under postgres it must hold nothing.
        assert client.app.state.store.all_job_ids() == [], (
            f"JobStore held job state under postgres: {client.app.state.store.all_job_ids()}")

    dispose()
    reset_persistence()

    assert touched == [], f"postgres mode reached the legacy stores: {sorted(set(touched))}"
    assert not (tmp_path / "gate" / "history.json").exists(), "the legacy gallery index was written"
    assert not (tmp_path / "gate" / "brands").exists(), "the legacy brand store was written"


def test_the_route_matrix_has_no_core_domain_left_unwired():
    """§18/§22, derived from the live application rather than from a maintained number."""
    sys.path.insert(0, str(_ROOT / "tests"))
    import test_route_persistence_classification_2gb as matrix

    live = matrix._pairs()
    framework = matrix._classified(matrix.FRAMEWORK)
    wired = matrix._classified(matrix.POSTGRES_WIRED)
    independent = matrix._classified(matrix.BACKEND_INDEPENDENT)
    unavailable = matrix._classified(matrix.UNAVAILABLE)

    assert len(framework) + len(wired) + len(independent) + len(unavailable) == len(live)
    blocked = {d for _p, (k, d, _n) in matrix.MATRIX.items()
               if k == matrix.UNAVAILABLE and d in matrix.CORE_DOMAINS}
    assert blocked == set(), f"these CORE domains are still unwired: {sorted(blocked)}"
    assert unavailable == set(), f"routes still unavailable: {sorted(unavailable)}"


def test_the_frozen_generated_archive_is_still_backend_independent():
    """§3: `/api/generated/{name}` serves bytes that already exist and keeps its own contract.

    Excluded from the authority gate deliberately, and the exclusion is written down here so it is
    a decision rather than an omission: a file server is not a source of truth, and the frozen
    legacy orphans have no managed row by design.
    """
    sys.path.insert(0, str(_ROOT / "tests"))
    import test_route_persistence_classification_2gb as matrix

    kind, domain, note = matrix.MATRIX[("GET", "/api/generated/{name}")]
    assert kind == matrix.BACKEND_INDEPENDENT and domain == "ASSETS"
    assert note.strip()

    # And the managed gallery store must not be that directory — an orphan blob and a legacy
    # orphan file have to stay tellable apart.
    from api import gallery_store
    assert gallery_store.ROOT_NAME != "generated"
