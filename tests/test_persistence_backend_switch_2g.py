# -*- coding: utf-8 -*-
"""The backend switch: legacy by default, PostgreSQL only when asked, and never both.

Three properties carry the whole cutover, and each is here because the alternative is a real
failure mode rather than a hypothetical one:

  DEFAULT LEGACY   — a configured `BASEERA_DATABASE_URL` means "a database is reachable", not "the
                     database is in charge". CI, migration rehearsals and this very suite all set a
                     DSN while the application is still legacy-authoritative. Inferring authority
                     from a DSN would hand production to PostgreSQL the first time somebody
                     exported one.

  FAIL CLOSED      — selecting `postgres` against a database that is missing, unreachable or at the
                     wrong migration head must RAISE. Falling back to the file stores would mean
                     writes landing in whichever store answered, with no later way to tell which
                     rows were authoritative.

  NO DUAL WRITE    — one backend owns an operation. A "write legacy too, just to be safe" shadow
                     write manufactures two divergent truths.

The PostgreSQL tests here need a real disposable database and skip without one; the default-legacy
and fail-closed tests need no database at all, which is the point.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from api.persistence import (
    ENV_BACKEND,
    LEGACY,
    POSTGRES,
    BackendMisconfigured,
    CapabilityNotWired,
    LegacyPersistence,
    build_persistence,
    reset_persistence,
    selected_backend,
)

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_selection():
    reset_persistence()
    yield
    reset_persistence()


# =====================================================================
# §38 — unset means legacy, and a DSN does not change that
# =====================================================================

def test_an_unset_switch_selects_legacy(monkeypatch):
    monkeypatch.delenv(ENV_BACKEND, raising=False)
    assert selected_backend() == LEGACY


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_switch_selects_legacy(monkeypatch, blank):
    monkeypatch.setenv(ENV_BACKEND, blank)
    assert selected_backend() == LEGACY


def test_a_configured_dsn_does_not_promote_postgres_to_authority(monkeypatch):
    """THE accident this guard exists for."""
    monkeypatch.delenv(ENV_BACKEND, raising=False)
    monkeypatch.setenv("BASEERA_DATABASE_URL",
                       "postgresql+psycopg://someone:secret@localhost:5432/prod")
    assert selected_backend() == LEGACY
    assert isinstance(build_persistence(), LegacyPersistence)


def test_an_unknown_backend_name_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setenv(ENV_BACKEND, "postgre")          # a plausible typo
    with pytest.raises(BackendMisconfigured, match="not one of"):
        selected_backend()


def test_the_default_backend_opens_no_database_connection():
    """A fresh process, no switch, a DSN pointing nowhere: legacy must not care."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", str(_ROOT)),
        # A DSN that would fail instantly if anything tried to use it.
        "BASEERA_DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/nope",
    }
    proc = subprocess.run(
        [sys.executable, "-s", "-c",
         "from api.persistence import build_persistence;"
         "backend = build_persistence();"
         "print('BACKEND=' + backend.name)"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "BACKEND=legacy" in proc.stdout


# =====================================================================
# §40 — fail closed
# =====================================================================

def test_postgres_without_a_dsn_raises_instead_of_using_the_file_stores(monkeypatch):
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.delenv("BASEERA_DATABASE_URL", raising=False)
    with pytest.raises(BackendMisconfigured, match="no database is configured"):
        build_persistence()


def test_postgres_with_an_unreachable_database_raises_instead_of_falling_back(monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL",
                       "postgresql+psycopg://nobody:nothing@127.0.0.1:1/absent")
    with pytest.raises(BackendMisconfigured) as excinfo:
        build_persistence()
    message = str(excinfo.value)
    assert "could not be verified" in message
    assert "Refusing to fall back" in message
    assert "nothing" not in message, "the DSN password leaked into the error"


def test_a_failed_postgres_selection_never_returns_a_legacy_backend(monkeypatch):
    """The failure mode stated as an outcome, not just as an exception type."""
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL",
                       "postgresql+psycopg://nobody:nothing@127.0.0.1:1/absent")
    result = None
    try:
        result = build_persistence()
    except BackendMisconfigured:
        pass
    assert result is None, f"a failed postgres selection produced {result!r}"


# =====================================================================
# §5 / §46 — no dual write, no leaked dict mutation
# =====================================================================

def test_the_provider_returns_exactly_one_backend(monkeypatch):
    monkeypatch.delenv(ENV_BACKEND, raising=False)
    backend = build_persistence()
    assert backend.name == LEGACY
    assert not hasattr(backend, "backends"), "the provider is composing more than one store"


def test_the_contract_exposes_no_generic_document_mutation():
    """`update(key, dict)` / `mutate` / `replace_block` must not reappear as the public shape."""
    from api import persistence as module

    banned = {"update", "mutate", "replace_block", "add_nested", "patch", "set_block",
              "update_fields"}
    for cls in (module.LegacyPersistence, module.PostgresPersistence):
        exposed = {n for n in dir(cls) if not n.startswith("_")}
        assert not (exposed & banned), (
            f"{cls.__name__} exposes generic document mutation: {sorted(exposed & banned)}")


def test_routes_do_not_import_the_database_layer_directly():
    """Routes may depend on the provider; they may not reach past it."""
    import ast

    offenders = {}
    for path in list((_ROOT / "api" / "routes").rglob("*.py")) + [_ROOT / "api" / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = set()
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name in ("sqlalchemy", "alembic") or name.startswith(
                        ("sqlalchemy.", "alembic.", "db.repositories", "db.models",
                         "db.migration_tools", "db.session", "db.uow")):
                    bad.add(name)
        if bad:
            offenders[path.name] = sorted(bad)
    assert not offenders, f"routes reach past the provider into persistence internals: {offenders}"


# =====================================================================
# §39 — the explicit switch, against a real database
# =====================================================================

_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

postgres = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the postgres-backend proof")


def _migrate_to_head() -> None:
    """Bring the disposable database to head as an OPERATOR action, before selecting the backend.

    This is not belt-and-braces. The migration suites legitimately run `downgrade base` against this
    same disposable database, so by the time the full suite reaches this file the schema may be
    gone. Asking the application to repair that would contradict §9 — `verify_postgres_ready` must
    refuse a schema it was not built against and must never migrate. So the TEST provisions the
    schema, exactly as a deploy would, and the application is still only ever asked to verify.

    PYTHONPATH is extended rather than replaced: the DB dependencies may live on an inherited
    isolated install path.
    """
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, (
        f"could not provision the disposable database:\n{proc.stderr[-2000:]}")


@pytest.fixture()
def pg_backend(monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate_to_head()
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.session import dispose
    dispose()
    backend = build_persistence()
    yield backend
    dispose()


@postgres
def test_the_explicit_switch_selects_postgres(pg_backend):
    assert pg_backend.name == POSTGRES


@postgres
def test_a_user_round_trips_through_the_postgres_backend(pg_backend):
    import uuid

    email = f"switch{uuid.uuid4().hex[:8]}@test.local"
    created = pg_backend.create_user(email=email, password="password123", full_name="Switch")
    assert created and created["email"] == email
    assert "hashed_password" not in created and "password_hash" not in created

    assert pg_backend.authenticate(email=email, password="password123")
    assert pg_backend.authenticate(email=email, password="wrong") is None
    assert pg_backend.user_by_email(email)["id"] == created["id"]
    assert pg_backend.user_by_id(created["id"])["email"] == email


@postgres
def test_the_postgres_backend_writes_nothing_to_the_legacy_stores(pg_backend, monkeypatch):
    """§39/§5: with postgres selected, the file stores must receive ZERO write calls."""
    import uuid

    import api.brand_store as bs
    import api.user_auth as ua

    calls: list[str] = []
    for module, cls, methods in (
            (bs, "BrandStore", ("save", "upsert", "update", "mutate", "replace_block",
                                "add_nested", "delete")),
            (ua, "UserStore", ("create", "_save")),
    ):
        target = getattr(module, cls)
        for method in methods:
            if hasattr(target, method):
                monkeypatch.setattr(
                    target, method,
                    (lambda name: lambda *a, **k: calls.append(name))(f"{cls}.{method}"),
                    raising=False)

    pg_backend.create_user(email=f"nolegacy{uuid.uuid4().hex[:8]}@test.local",
                           password="password123")
    pg_backend.owner_of_domain("some_domain_com")

    assert calls == [], f"the postgres backend also wrote to the legacy stores: {calls}"


@postgres
def test_a_legacy_jwt_subject_resolves_and_a_malformed_one_does_not(pg_backend):
    """§10/§46.18 — pre-cutover tokens keep working; arbitrary strings do not become a user."""
    import uuid

    from db.session import get_session_factory
    from db.services import PersistenceService
    from db.uow import unit_of_work_factory

    from api.user_auth import hash_password

    legacy_id = uuid.uuid4().hex[:16]
    service = PersistenceService(unit_of_work_factory(get_session_factory()))
    record = service.create_user(
        email=f"legacy{uuid.uuid4().hex[:8]}@test.local",
        hashed_password=hash_password("password123"), full_name="Legacy",
        legacy_user_id=legacy_id)

    assert pg_backend.user_for_token_subject(str(record.id))["id"] == str(record.id)
    assert pg_backend.user_for_token_subject(legacy_id)["id"] == str(record.id)

    for malformed in ("", "   ", "not-a-uuid", "zzzzzzzzzzzzzzzz", legacy_id.upper(),
                      legacy_id[:15], legacy_id + "0", "' OR 1=1 --"):
        assert pg_backend.user_for_token_subject(malformed) is None, (
            f"{malformed!r} was accepted as a user identity")


@postgres
def test_the_postgres_backend_holds_no_route_back_to_the_legacy_stores(pg_backend):
    """§4 at the object level: a gap must never RESOLVE to the other store.

    Until DB2G-B this test named two capabilities that raised `CapabilityNotWired` — brand reads,
    which that phase implemented. Asserting they still raise would now be asserting the feature is
    missing. The durable property was never those two methods; it was that the PostgreSQL backend
    has no path to the file stores at all, so a future gap cannot quietly become a legacy read.

    `CapabilityNotWired` is kept and checked as a live contract, because the next unwired capability
    must raise it rather than improvise.
    """
    from api.persistence import LegacyPersistence

    assert not isinstance(pg_backend, LegacyPersistence)
    assert not any(isinstance(v, LegacyPersistence) for v in vars(pg_backend).values()), (
        "the postgres backend holds a legacy adapter — a fallback is one attribute lookup away")
    assert LegacyPersistence not in type(pg_backend).__mro__

    # An operation nobody implemented is an AttributeError, never a silent success.
    with pytest.raises(AttributeError):
        pg_backend.some_capability_nobody_wired()

    assert issubclass(CapabilityNotWired, RuntimeError)


@postgres
def test_every_provider_capability_exists_on_both_backends(pg_backend):
    """One contract, two implementations — a method on only one of them is a split API.

    A route calling a legacy-only method would work in the default backend and 500 under postgres,
    which is the same failure as a silent fallback wearing a different hat.
    """
    from api.persistence import LegacyPersistence, PersistenceProvider

    contract = {n for n in dir(PersistenceProvider)
                if not n.startswith("_") and n != "name"}
    for backend in (LegacyPersistence, type(pg_backend)):
        missing = sorted(n for n in contract if not hasattr(backend, n))
        assert not missing, f"{backend.__name__} does not implement {missing}"


@postgres
def test_a_wrong_migration_head_is_refused(pg_backend, monkeypatch):
    """§9: the application refuses a schema it was not built against, and never migrates."""
    import api.persistence as module

    monkeypatch.setattr(module, "EXPECTED_SCHEMA_HEAD", "9999_not_this_one")
    with pytest.raises(BackendMisconfigured, match="expects"):
        module.verify_postgres_ready()
