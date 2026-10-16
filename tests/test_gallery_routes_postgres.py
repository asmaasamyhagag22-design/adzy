# -*- coding: utf-8 -*-
"""The gallery served from PostgreSQL: real routes, real database, renders mocked.

§13-§20. Everything goes through FastAPI's own routing and the real
`PERSISTENCE_BACKEND=postgres` provider against a disposable PostgreSQL 16.15. Only the image
RENDER is replaced — it costs money and reaches the network, and §51 requires it outside any
transaction anyway, so mocking it hides no database interaction.

The question in every test is the same one the Job cutover established: do the writer and the
reader agree about which store owns this? A gallery write that lands in `history.json` while the
read comes from `gallery_entries` is not a smaller step, it is two authorities and one apparent
answer that neither store agrees with. `no_legacy_gallery` fails any test that touches the legacy
index at all.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import textwrap
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

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the gallery route proof")


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


@pytest.fixture()
def webapp_root(tmp_path):
    """Short and isolated. The managed blob path is `<root>/gallery/ab/<64 hex>.png`, so a deep
    root pushes it past Windows' 260-character limit — which `gallery_store` now names explicitly
    rather than surfacing as a mystifying ENOENT."""
    return tmp_path / "w"


@pytest.fixture()
def client(webapp_root, monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(webapp_root))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-gallery")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.main import app
    with TestClient(app) as test_client:
        yield test_client
    dispose()
    reset_persistence()


@pytest.fixture()
def no_legacy_gallery(monkeypatch):
    """§17: under postgres the legacy gallery index must be reached ZERO times, read or write."""
    import api.brand_store as bs

    calls: list[str] = []
    monkeypatch.setattr(bs, "record_history",
                        lambda *a, **k: calls.append("record_history"), raising=False)
    monkeypatch.setattr(bs, "read_history",
                        lambda *a, **k: calls.append("read_history") or [], raising=False)
    yield calls
    assert calls == [], f"postgres mode reached the legacy gallery index: {calls}"


@pytest.fixture()
def renders(monkeypatch):
    """Counts renders and hands back deterministic bytes. §12 needs the COUNT, not just the bytes."""
    from api.routes import generate as generate_route

    calls: list[dict] = []
    payloads: list[bytes] = []

    def fake_generate(brand, **kwargs):
        calls.append(dict(kwargs))
        seed = kwargs.get("headline") or f"render-{len(calls)}"
        image = payloads.pop(0) if payloads else _png(seed)
        return {"image_b64": base64.b64encode(image).decode("ascii"),
                "filename": f"{seed}.png", "headline": seed, "subtext": "", "cta": "",
                "is_preview": True, "trust_violation": False, "qa_status": "pass"}

    monkeypatch.setattr(generate_route.images, "generate_image", fake_generate)
    fake_generate.calls = calls          # type: ignore[attr-defined]
    fake_generate.payloads = payloads    # type: ignore[attr-defined]
    return fake_generate


def _account(client, email: str = "") -> tuple[dict, str]:
    address = email or f"gr{uuid.uuid4().hex[:8]}@test.local"
    response = client.post("/api/auth/signup",
                           json={"email": address, "password": "password123", "full_name": "GR"})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _brand(client, monkeypatch, headers) -> str:
    """A real Brand, made the way the app makes one: a scrape through the real route."""
    from api.brand_translate import domain_key_from_url
    from api.routes import scrape as scrape_route

    url = f"https://gal{uuid.uuid4().hex[:8]}.test/"

    def pipeline(job_id, _url, recorder, **kwargs):
        recorder.set_result_threadsafe(job_id, {"name": {"value": "Gallery Brand"},
                                                "primary_language": "en"})
        return {"ready_for_strategy": False, "profile_path": None}

    monkeypatch.setattr(scrape_route, "run_pipeline_job", pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    started = client.post("/api/scrape", json={"url": url}, headers=headers)
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]

    import time
    for _ in range(300):
        body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if body.get("status") in ("done", "error"):
            assert body["status"] == "done", body
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the brand scrape never finished")
    return domain_key_from_url(url)


def _generate(client, headers, domain_key: str, *, headline: str = "") -> dict:
    response = client.post("/api/generate", headers=headers, json={
        "domain_key": domain_key, "content_type": "post", "style": "bold",
        "headline": headline or f"h{uuid.uuid4().hex[:6]}"})
    assert response.status_code == 200, response.text
    return response.json()


def _history(client, headers, limit: int = 100) -> list[dict]:
    response = client.get(f"/api/history?limit={limit}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["history"]


def _entries(domain_key: str = "") -> list:
    from sqlalchemy import select

    from db.models import GalleryEntry
    from db.session import get_session_factory

    statement = select(GalleryEntry)
    if domain_key:
        statement = statement.where(GalleryEntry.domain_key == domain_key)
    with get_session_factory()() as session:
        return list(session.scalars(statement).all())


# =====================================================================
# §11/§19 — the three writers
# =====================================================================

def test_generating_an_image_lands_bytes_and_a_durable_entry(client, monkeypatch, renders,
                                                             no_legacy_gallery, webapp_root):
    from api import gallery_store

    headers, user_id = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    out = _generate(client, headers, domain_key, headline="first")
    assert out["image_b64"]

    rows = _entries(domain_key)
    assert len(rows) == 1, rows
    entry = rows[0]
    assert entry.origin == "live"
    assert str(entry.owner_user_id) == user_id
    assert entry.brand_id is not None, "a live entry knows the brand the request named"
    assert entry.legacy_seq is None and entry.imported_at is None

    blob = None
    from sqlalchemy import select

    from db.models import GalleryBlob
    from db.session import get_session_factory
    with get_session_factory()() as session:
        blob = session.get(GalleryBlob, entry.checksum)
    assert blob is not None and blob.mime == "image/png"
    payload = gallery_store.load(blob.storage_key)
    assert payload is not None
    assert hashlib.sha256(payload).hexdigest() == entry.checksum
    assert base64.b64decode(out["image_b64"]) == payload

    # And nothing was written into the legacy archive that `/api/generated/{name}` serves (§3).
    assert not (webapp_root / "generated").exists() or \
        not list((webapp_root / "generated").glob("*.png"))


def test_the_history_route_serves_what_was_generated(client, monkeypatch, renders,
                                                     no_legacy_gallery):
    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)
    out = _generate(client, headers, domain_key, headline="visible")

    history = _history(client, headers)
    mine = [h for h in history if h.get("domain_key") == domain_key]
    assert len(mine) == 1
    item = mine[0]
    assert item["image_b64"] == out["image_b64"], "the served bytes are not the generated bytes"
    assert item["filename"] == out["filename"]
    assert item["content_type"] == "post" and item["style"] == "bold"
    assert isinstance(item["created_at"], int) and item["created_at"] > 0
    # The disclosure travelled with the entry rather than being recomputed.
    assert item["is_preview"] is True and item["trust_violation"] is False
    assert item["qa_status"] == "pass"


def test_canonical_gallery_entry_is_backed_by_run_attempt_asset_and_strategy_dependency(
        client, monkeypatch, no_legacy_gallery):
    """The ids exposed as canonical lineage are relational facts, not UUID-shaped metadata."""
    from io import BytesIO

    from PIL import Image
    from sqlalchemy import select

    from api.persistence import get_persistence
    from db.models import Asset, Attempt, GalleryEntry, Run, RunDependency
    from db.services import LiveRunData
    from db.session import get_session_factory

    headers, user_id = _account(client)
    domain_key = _brand(client, monkeypatch, headers)
    provider = get_persistence()
    actor = provider._uuid(user_id)  # noqa: SLF001 — verifies the provider's relational crossing
    brand = provider._brand_row(domain_key=domain_key, user_id=user_id)  # noqa: SLF001
    strategy = provider._service.create_live_run(LiveRunData(  # noqa: SLF001
        brand_id=brand.id, user_id=actor, kind="strategy", status="succeeded",
        input_profile_id=brand.current_profile_id,
        output={"decision": {"selected_option_id": "pos_test"}}, output_schema_version=1))
    contract = {
        "creative_contract_id": "cc_relational_test",
        "strategy_version_id": str(strategy.id),
        "profile_snapshot_id": str(brand.current_profile_id),
    }
    execution = provider.begin_canonical_poster_run(
        domain_key=domain_key, user_id=user_id, contract=contract)

    buffer = BytesIO()
    unique_color = tuple(uuid.uuid4().bytes[:3])
    Image.new("RGB", (32, 24), unique_color).save(buffer, format="PNG")
    persisted = provider.record_gallery_entry(entry={
        "image_b64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "filename": "canonical.png", "domain_key": domain_key,
        "brand_name": "Gallery Brand", "content_type": "brand_poster",
        "style": "product_hero", "canonical": True, "strategy_grounded": True,
        "strategy_version_id": str(strategy.id),
        "creative_contract_id": contract["creative_contract_id"],
        "profile_snapshot_id": str(brand.current_profile_id),
        "generation_run_id": execution["run_id"],
        "generation_attempt_id": execution["attempt_id"],
        "strategy_verification": {"overall_pass": True},
        "visual_qa": {"overall_pass": True}, "trust_violation": False,
    }, user_id=user_id)

    with get_session_factory()() as session:
        run = session.get(Run, uuid.UUID(execution["run_id"]))
        attempt = session.get(Attempt, uuid.UUID(execution["attempt_id"]))
        asset = session.get(Asset, uuid.UUID(persisted["asset_id"]))
        dependency = session.scalar(select(RunDependency).where(
            RunDependency.run_id == run.id,
            RunDependency.depends_on_run_id == strategy.id))
        entry = session.scalar(select(GalleryEntry).where(
            GalleryEntry.domain_key == domain_key))

    assert run.status == "succeeded" and run.selected_attempt_id == attempt.id
    assert attempt.status == "succeeded" and attempt.asset_id == asset.id
    assert asset.checksum_sha256 and asset.width == 32 and asset.height == 24
    assert dependency.relation == "uses_strategy"
    assert entry.metadata_json["generation_run_id"] == execution["run_id"]
    assert entry.metadata_json["asset_id"] == persisted["asset_id"]

    failed_execution = provider.begin_canonical_poster_run(
        domain_key=domain_key, user_id=user_id, contract=contract)
    provider.fail_canonical_poster_run(
        run_id=failed_execution["run_id"],
        attempt_id=failed_execution["attempt_id"],
        user_id=user_id, error="provider did not return image bytes")
    with get_session_factory()() as session:
        failed_run = session.get(Run, uuid.UUID(failed_execution["run_id"]))
        failed_attempt = session.get(Attempt, uuid.UUID(failed_execution["attempt_id"]))
    assert failed_run.status == "failed"
    assert failed_attempt.status == "failed" and failed_attempt.qa_passed is False


def test_the_multipart_writers_are_durable_too(client, monkeypatch, renders, no_legacy_gallery):
    """`/generate/upload` and `/generate/reference` reach the same `_emit`, so the crossing covers
    all three — but "reaches the same helper" is a claim about source, and this checks behaviour."""
    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    upload = client.post("/api/generate/upload", headers=headers,
                         data={"domain_key": domain_key, "headline": "uploaded",
                               "content_type": "post", "style": "clean"},
                         files={"logo": ("logo.png", _png("logo"), "image/png")})
    assert upload.status_code == 200, upload.text

    reference = client.post("/api/generate/reference", headers=headers,
                            data={"domain_key": domain_key, "headline": "referenced",
                                  "content_type": "story", "style": "warm"},
                            files={"reference_image": ("ref.png", _png("ref"), "image/png")})
    assert reference.status_code == 200, reference.text

    rows = _entries(domain_key)
    assert len(rows) == 2, [r.filename for r in rows]
    assert {r.filename for r in rows} == {"uploaded.png", "referenced.png"}
    assert all(r.origin == "live" and r.brand_id is not None for r in rows)

    served = {h["filename"] for h in _history(client, headers)}
    assert {"uploaded.png", "referenced.png"} <= served


# =====================================================================
# §14 — identical bytes, two publications
# =====================================================================

def test_two_generations_with_identical_bytes_are_two_entries_and_one_blob(
        client, monkeypatch, renders, no_legacy_gallery):
    """The product records two publications; storage records one image. Both are true, and the
    user-visible history must not be collapsed to match the storage."""
    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    identical = _png("same-bytes-twice")
    renders.payloads.extend([identical, identical])
    first = _generate(client, headers, domain_key, headline="pub-one")
    second = _generate(client, headers, domain_key, headline="pub-two")
    assert first["image_b64"] == second["image_b64"]

    rows = _entries(domain_key)
    assert len(rows) == 2, "two publications collapsed into one history entry"
    assert len({r.checksum for r in rows}) == 1, "identical bytes were stored twice"

    from sqlalchemy import func, select

    from db.models import GalleryBlob
    from db.session import get_session_factory
    with get_session_factory()() as session:
        blobs = session.scalar(select(func.count()).select_from(GalleryBlob)
                               .where(GalleryBlob.checksum == rows[0].checksum))
    assert blobs == 1

    names = {h["filename"] for h in _history(client, headers)}
    assert {"pub-one.png", "pub-two.png"} <= names


# =====================================================================
# §15 — access control
# =====================================================================

def test_a_stranger_never_sees_another_accounts_gallery(client, monkeypatch, renders,
                                                        no_legacy_gallery):
    owner, _ = _account(client)
    domain_key = _brand(client, monkeypatch, owner)
    _generate(client, owner, domain_key, headline="private")

    stranger, _ = _account(client)
    visible = {h.get("filename") for h in _history(client, stranger)}
    assert "private.png" not in visible, "another account's gallery leaked"
    assert {h.get("filename") for h in _history(client, owner)} >= {"private.png"}


def test_unattributed_history_is_visible_to_everyone_and_assigned_to_nobody(
        client, monkeypatch, renders, no_legacy_gallery):
    """§15: the entries with no recorded owner follow the legacy contract — flagged `unclaimed`,
    shown to whoever asks, never handed to them."""
    from datetime import datetime, timezone

    from api import gallery_store
    from api.persistence import get_persistence

    headers, _ = _account(client)
    service = get_persistence()._service
    # Unique per run: `legacy_seq` is the import's DETERMINISTIC identity and the disposable
    # database is shared across the whole suite, so a fixed number collides with the previous run
    # — which is the uniqueness guarantee working, not a fixture problem.
    tag = uuid.uuid4().hex[:8]
    seq = 70_000 + int(tag[:4], 16)
    stored = gallery_store.store(_png(f"orphan-{tag}"))
    service.record_gallery_entry(
        checksum=stored.checksum, byte_size=stored.byte_size, mime=stored.mime,
        storage_key=stored.storage_key, filename=f"unowned-{tag}.png", owner_user_id=None,
        origin="legacy_import", legacy_seq=seq,
        imported_at=datetime.now(timezone.utc))

    item = next(h for h in _history(client, headers) if h["filename"] == f"unowned-{tag}.png")
    assert item.get("unclaimed") is True
    assert "owner_id" not in item

    other, _ = _account(client)
    assert any(h["filename"] == f"unowned-{tag}.png" for h in _history(client, other))

    rows = [r for r in _entries() if r.filename == f"unowned-{tag}.png"]
    assert rows and rows[0].owner_user_id is None, "a read assigned an owner"


# =====================================================================
# §12/§13 — spending, and failing after the spend
# =====================================================================

def test_an_invalid_target_never_reaches_the_renderer(client, monkeypatch, renders,
                                                      no_legacy_gallery):
    """§12: authorization and target resolution happen BEFORE the render."""
    headers, _ = _account(client)
    response = client.post("/api/generate", headers=headers,
                           json={"domain_key": "no_such_brand_com", "headline": "x"})
    assert response.status_code == 404, response.text
    assert renders.calls == [], "an unresolvable target still paid for a render"


def test_another_accounts_brand_never_reaches_the_renderer(client, monkeypatch, renders,
                                                           no_legacy_gallery):
    owner, _ = _account(client)
    domain_key = _brand(client, monkeypatch, owner)
    renders.calls.clear()

    stranger, _ = _account(client)
    response = client.post("/api/generate", headers=stranger,
                           json={"domain_key": domain_key, "headline": "theft"})
    assert response.status_code == 404
    assert renders.calls == [], "a foreign brand still paid for a render"


def test_a_database_failure_after_the_render_is_not_reported_as_success(
        client, monkeypatch, renders, no_legacy_gallery):
    """§13: the render succeeded and the bytes landed, but the row did not.

    The API must NOT return 200 with an image the gallery does not contain — the user would see a
    picture that vanishes on the next page load. Orphan bytes are acceptable and identifiable.
    """
    from api import gallery_store
    from db import services as db_services

    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    doomed = _png("doomed-render")
    renders.payloads.append(doomed)
    monkeypatch.setattr(db_services.PersistenceService, "record_gallery_entry",
                        lambda self, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("the transaction was rolled back")))

    response = client.post("/api/generate", headers=headers,
                           json={"domain_key": domain_key, "headline": "doomed"})
    assert response.status_code == 503, (
        f"a failed gallery write was reported to the client as {response.status_code}")
    detail = response.json()["detail"]
    assert "could not be saved to your gallery" in detail
    # The raw failure never reaches the client — `http_errors`' standing rule.
    for leak in ("rolled back", "RuntimeError", "psycopg", "Traceback", "gallery_entries"):
        assert leak not in detail, f"the client was shown {leak!r}: {detail}"

    assert _entries(domain_key) == [], "an entry survived a failed transaction"
    checksum = hashlib.sha256(doomed).hexdigest()
    orphan = gallery_store.path_for(gallery_store.storage_key_for(checksum, "png"))
    assert orphan.is_file(), "the bytes were not durable before the transaction was attempted"
    assert not any(h.get("filename") == "doomed.png" for h in _history(client, headers))


def test_an_ambiguous_calendar_target_never_reaches_the_renderer(client, monkeypatch, renders,
                                                                 no_legacy_gallery):
    """§12 on the calendar route: two posts on one date is a refusal, not a coin flip."""
    from api.persistence import AmbiguousCalendarDate
    from api.routes import generate as generate_route

    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)
    renders.calls.clear()

    from api import persistence as persistence_module
    monkeypatch.setattr(
        persistence_module.PostgresPersistence, "resolve_calendar_post",
        lambda self, **kwargs: (_ for _ in ()).throw(AmbiguousCalendarDate("2026-09-01", 2)))

    response = client.post("/api/calendar/generate-image", headers=headers,
                           json={"domain_key": domain_key, "date": "2026-09-01"})
    assert response.status_code == 409, response.text
    assert renders.calls == [], "an ambiguous calendar target still paid for a render"
    _ = generate_route


# =====================================================================
# §17 — zero legacy gallery authority
# =====================================================================

def test_the_history_route_never_reads_the_legacy_index_under_postgres(client, monkeypatch,
                                                                       renders,
                                                                       no_legacy_gallery,
                                                                       webapp_root):
    """And it does not fall back when there is nothing to show — an empty gallery is an answer."""
    headers, _ = _account(client)
    assert isinstance(_history(client, headers), list)

    legacy_index = webapp_root / "history.json"
    assert not legacy_index.exists(), "postgres mode created the legacy gallery index"

    domain_key = _brand(client, monkeypatch, headers)
    _generate(client, headers, domain_key, headline="only-durable")
    assert not legacy_index.exists()
    # `no_legacy_gallery` asserts the call counts on teardown.


# =====================================================================
# §20 — restart
# =====================================================================

def test_history_and_bytes_survive_a_process_restart(client, monkeypatch, renders,
                                                     no_legacy_gallery, webapp_root):
    """A SEPARATE interpreter, the same database and the same managed byte store.

    Nothing is shared but those two. `history.json` is asserted absent inside the child, so a pass
    cannot come from a legacy rescue that happened to be lying around.
    """
    headers, user_id = _account(client)
    domain_key = _brand(client, monkeypatch, headers)
    out = _generate(client, headers, domain_key, headline="survivor")
    expected_sha = hashlib.sha256(base64.b64decode(out["image_b64"])).hexdigest()

    source = textwrap.dedent(f"""
        import base64, hashlib, json, os, sys, types
        _f = types.ModuleType("playwright"); _fs = types.ModuleType("playwright.sync_api")
        class _S: ...
        for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
            setattr(_fs, _n, _S)
        _fs.TimeoutError = type("TimeoutError", (Exception,), {{}})
        _fs.Error = type("Error", (Exception,), {{}})
        sys.modules.setdefault("playwright", _f)
        sys.modules.setdefault("playwright.sync_api", _fs)
        sys.path.insert(0, {str(_ROOT)!r})
        os.environ["PERSISTENCE_BACKEND"] = "postgres"
        os.environ["BASEERA_DATABASE_URL"] = {_DSN!r}
        os.environ["BASEERA_WEBAPP_DIR"] = {str(webapp_root)!r}

        from api.user_auth import webapp_dir
        assert not (webapp_dir() / "history.json").exists(), "a legacy index was available"

        from api.persistence import get_persistence, reset_persistence
        reset_persistence()
        provider = get_persistence()
        items = provider.gallery_history(user_id={user_id!r}, limit=100)
        mine = [i for i in items if i.get("domain_key") == {domain_key!r}]
        assert len(mine) == 1, f"expected one entry, got {{len(mine)}}"
        digest = hashlib.sha256(base64.b64decode(mine[0]["image_b64"])).hexdigest()
        print("@@" + json.dumps({{"count": len(mine), "sha": digest,
                                  "filename": mine[0]["filename"]}}))
    """)
    proc = subprocess.run([sys.executable, "-c", source], cwd=str(_ROOT),
                          capture_output=True, text=True, timeout=180,
                          env={**os.environ, "PYTHONPATH": str(_ROOT)})
    assert proc.returncode == 0, f"the restarted process failed:\n{proc.stderr[-3000:]}"
    reported = json.loads(next(line[2:] for line in proc.stdout.splitlines()
                               if line.startswith("@@")))
    assert reported["count"] == 1
    assert reported["sha"] == expected_sha, "the bytes did not survive the restart"
    assert reported["filename"] == "survivor.png"
