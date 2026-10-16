"""Hermetic security regressions for production operations configuration."""
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from ops import production_secrets
from ops.runtime_config import (DEFAULT_PRODUCTION_PROFILE, MissingProductionSecrets,
                                load_production_profile,
                                load_runtime_config, redact, scan_secret_material,
                                secret_profile_fields)
from ops.secrets import (PRODUCTION_SECRET_NAMES, PRODUCTION_SECRET_SERVICE,
                         WindowsKeyringSecretProvider)

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_PASSWORD = "pg-test-p@ss:/#safe"
SERVICE_KEY = "sb_secret_hermetic_service_role_value_123456"
AUTH_SECRET = "hermetic-auth-secret-value-987654"
POOLER_USERNAME = "postgres.cerpllikdnlibafnnppa"


class FakeProvider:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


class FakeKeyringModule:
    def __init__(self):
        self.values = {}
        self.calls = []

    def get_password(self, service, name):
        self.calls.append(("get", service, name))
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.calls.append(("set", service, name))
        self.values[(service, name)] = value


def complete_secrets():
    return {
        "POSTGRES_PASSWORD": POSTGRES_PASSWORD,
        "SUPABASE_SERVICE_ROLE_KEY": SERVICE_KEY,
        "BASEERA_AUTH_SECRET": AUTH_SECRET,
    }


@pytest.fixture(scope="module")
def closure_module():
    path = ROOT / "scratchpad" / "hosted" / "gate1_closure.py"
    spec = importlib.util.spec_from_file_location("gate1_closure_security_tests", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_with(values=None, environ=None):
    return load_runtime_config(provider=FakeProvider(values or complete_secrets()),
                               environ={} if environ is None else environ)


def test_windows_keyring_provider_retrieves_from_namespaced_store():
    keyring = FakeKeyringModule()
    keyring.values[(PRODUCTION_SECRET_SERVICE, "POSTGRES_PASSWORD")] = POSTGRES_PASSWORD
    provider = WindowsKeyringSecretProvider(keyring_module=keyring)

    assert provider.get("POSTGRES_PASSWORD") == POSTGRES_PASSWORD
    assert keyring.calls == [("get", PRODUCTION_SECRET_SERVICE, "POSTGRES_PASSWORD")]


def test_missing_secret_fails_closed_without_legacy_or_value_disclosure():
    values = complete_secrets()
    values.pop("BASEERA_AUTH_SECRET")

    with pytest.raises(MissingProductionSecrets) as captured:
        load_with(values)

    assert captured.value.names == ("BASEERA_AUTH_SECRET",)
    assert "legacy" not in str(captured.value).lower()
    assert POSTGRES_PASSWORD not in str(captured.value)
    assert SERVICE_KEY not in str(captured.value)


def test_profile_and_sqlalchemy_connection_retain_exact_pooler_username():
    profile = load_production_profile()
    config = load_with()
    dsn = config.postgres.sqlalchemy_dsn

    assert profile.postgres_username == POOLER_USERNAME
    assert config.postgres.username == POOLER_USERNAME
    assert config.postgres.sqlalchemy_url.username == POOLER_USERNAME
    assert make_url(dsn).username == POOLER_USERNAME
    assert dsn.startswith("postgresql+psycopg://%s:" % POOLER_USERNAME)
    assert "@127.0.0.1:15432/postgres?sslmode=require" in dsn
    assert "pg-test-p%40ss%3A%2F%23safe" in dsn
    assert config.postgres.username != "postgres"


def test_real_sqlalchemy_dialect_parameters_retain_pooler_username(closure_module):
    config = load_with()
    engine = create_engine(config.postgres.sqlalchemy_url, future=True)
    try:
        cargs, cparams = engine.dialect.create_connect_args(engine.url)
        assert closure_module._assert_sql_connection_parameters(
            config, cargs, cparams) == POOLER_USERNAME
        replaced = dict(cparams, user="postgres")
        with pytest.raises(closure_module.ClosureFailed):
            closure_module._assert_sql_connection_parameters(config, cargs, replaced)
    finally:
        engine.dispose()


def test_safe_summary_and_repr_contain_no_secrets_or_complete_dsn():
    config = load_with()
    safe = config.safe_summary()
    rendered = repr(config)

    assert safe == (
        "backend=postgres host=127.0.0.1 port=15432 database=postgres "
        "blob_backend=supabase bucket=baseera-gallery")
    for secret in config.known_secret_values():
        assert secret not in safe
        assert secret not in rendered
    assert "postgresql" not in safe


@pytest.mark.parametrize("scheme", ["postgresql", "postgresql+psycopg"])
def test_redaction_removes_credential_bearing_postgres_dsns(scheme):
    raw = "%s://dbuser:db-password@db.example/postgres?sslmode=require" % scheme
    safe = redact("failed to connect to %s" % raw)

    assert "db-password" not in safe
    assert "dbuser" not in safe
    assert "db.example" not in safe
    assert "credentials-redacted" in safe


def test_redaction_and_scan_catch_known_loaded_secret_values():
    raw = "opaque=%s" % AUTH_SECRET

    assert AUTH_SECRET not in redact(raw, [AUTH_SECRET])
    assert "known-loaded-secret" in scan_secret_material(raw, [AUTH_SECRET])


def test_environment_override_is_deliberate_but_not_required():
    stored = complete_secrets()
    provider = FakeProvider(stored)
    override = "ephemeral-override-password"
    config = load_runtime_config(
        provider=provider, environ={"POSTGRES_PASSWORD": override})

    assert config.postgres.password == override
    assert "POSTGRES_PASSWORD" not in provider.reads
    assert set(provider.reads) == {"SUPABASE_SERVICE_ROLE_KEY", "BASEERA_AUTH_SECRET"}


def test_ambient_process_environment_does_not_override_keyring_implicitly(monkeypatch):
    provider = FakeProvider(complete_secrets())
    monkeypatch.setenv("POSTGRES_PASSWORD", "stale-process-local-password")

    config = load_runtime_config(provider=provider)

    assert config.postgres.password == POSTGRES_PASSWORD
    assert "POSTGRES_PASSWORD" in provider.reads


def test_production_profile_contains_only_the_exact_non_secret_shape():
    raw = DEFAULT_PRODUCTION_PROFILE.read_text(encoding="utf-8")
    document = tomllib.loads(raw)
    profile = load_production_profile()

    assert secret_profile_fields(document) == ()
    assert "password" not in raw.casefold()
    assert "service_role" not in raw.casefold()
    assert "auth_secret" not in raw.casefold()
    assert profile.persistence_backend == "postgres"
    assert profile.blob_backend == "supabase"
    assert profile.postgres_username == POOLER_USERNAME


def good_closure_reports(module):
    postgres = {
        "dump_size": 4096,
        "dump_sha256": "a" * 64,
        "custom_format": True,
        "pg_restore_list_exit_code": 0,
        "toc_entries": 40,
        "missing_application_tables": [],
        "alembic_version_table_present": True,
        "alembic_head": module.EXPECTED_HEAD,
    }
    storage = {
        "bucket_public": False,
        "missing_count": 0,
        "recursive_object_count": 29,
        "exported_object_count": 29,
        "manifest_digest": "b" * 64,
        "exported_manifest_digest": "b" * 64,
    }
    return postgres, storage


def test_closure_cannot_pass_with_zero_byte_dump(closure_module):
    postgres, storage = good_closure_reports(closure_module)
    postgres.update(dump_size=0, dump_sha256=closure_module.EMPTY_SHA256)

    assert not closure_module._closure_can_pass(postgres, storage, audit_clean=True)


def test_closure_cannot_pass_when_pg_restore_list_fails(closure_module):
    postgres, storage = good_closure_reports(closure_module)
    postgres["pg_restore_list_exit_code"] = 1

    assert not closure_module._closure_can_pass(postgres, storage, audit_clean=True)


def test_closure_cannot_pass_when_secret_leak_audit_fails(closure_module):
    postgres, storage = good_closure_reports(closure_module)

    assert not closure_module._closure_can_pass(postgres, storage, audit_clean=False)


def test_pg_dump_password_uses_stdin_and_never_docker_argv(
        closure_module, monkeypatch, tmp_path, capsys):
    postgres_dir = tmp_path / "postgres-evidence"
    dump = postgres_dir / "post_cutover_pg.dump"
    dump_part = postgres_dir / "post_cutover_pg.dump.part"
    report_path = postgres_dir / "postgres_backup_evidence.json"
    calls = []

    def fake_docker(args, *, stdin_bytes=None, timeout=1800):
        calls.append((list(args), stdin_bytes, timeout))
        dump_part.write_bytes(b"PGDMP" + b"x" * 64)
        proof = closure_module._PASSWORD_TRANSPORT_PROOF + __import__("hashlib").sha256(
            stdin_bytes).hexdigest() + "\n"
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": proof})()

    monkeypatch.setattr(closure_module, "PG_EVIDENCE", postgres_dir)
    monkeypatch.setattr(closure_module, "ROOT", tmp_path)
    monkeypatch.setattr(closure_module, "DUMP", dump)
    monkeypatch.setattr(closure_module, "DUMP_PART", dump_part)
    monkeypatch.setattr(closure_module, "PG_REPORT", report_path)
    monkeypatch.setattr(closure_module, "_docker", fake_docker)
    monkeypatch.setattr(closure_module, "_verify_archive", lambda: {
        "pg_dump_version": "pg_dump (PostgreSQL) 17.11",
        "pg_restore_list_exit_code": 0,
        "toc_entries": 40,
        "expected_application_tables": ["alembic_version"],
        "missing_application_tables": [],
        "alembic_version_table_present": True,
    })
    config = load_with()
    database = {"database": "postgres", "server_version": "17.6",
                "alembic_head": closure_module.EXPECTED_HEAD}

    result = closure_module._postgres_backup(config, database)

    assert result["dump_size"] > 0
    assert len(calls) == 1
    argv, stdin_value, _ = calls[0]
    rendered_argv = " ".join(argv)
    assert config.postgres.password not in rendered_argv
    assert "--username=%s" % POOLER_USERNAME in argv
    assert "--username=postgres" not in argv
    assert "--env" not in argv
    assert "PGPASSWORD=" not in rendered_argv
    assert stdin_value == config.postgres.password.encode("utf-8")
    assert not stdin_value.endswith(b"\n")
    output = capsys.readouterr().out
    assert "PG_DUMP_START\n" in output
    assert "PG_DUMP_EXIT_CODE=0\n" in output
    assert "PG_DUMP_PASSWORD_TRANSPORT=PASS\n" in output
    assert "PG_DUMP_RESULT=PASS\n" in output
    assert "host=host.docker.internal port=15432 database=postgres" in output
    assert "username=%s sslmode=require" % POOLER_USERNAME in output
    assert config.postgres.password not in output


def test_pg_dump_transport_proof_requires_exact_opaque_password_bytes(closure_module):
    password = " leading $p@ss;`quoted`\\value# trailing "
    digest = __import__("hashlib").sha256(password.encode("utf-8")).hexdigest()
    stderr = "%s%s\npg_dump: safe redacted diagnostic\n" % (
        closure_module._PASSWORD_TRANSPORT_PROOF, digest)

    assert closure_module._consume_password_transport_proof(stderr, password) == (
        "pg_dump: safe redacted diagnostic\n")
    with pytest.raises(closure_module.ClosureFailed, match="transport proof failed"):
        closure_module._consume_password_transport_proof(stderr, password + "x")


def test_post_probe_pg_dump_failure_is_not_reclassified_as_password_mismatch(
        closure_module):
    failure = closure_module.ClosureFailed(
        "pg_dump failed: password authentication failed for user tenant")

    assert not closure_module._is_pre_probe_password_auth_failure(
        failure, connection_identity_proven=True, sql_probe_passed=True)
    assert closure_module._is_pre_probe_password_auth_failure(
        failure, connection_identity_proven=True, sql_probe_passed=False)


def test_real_closure_preflight_reports_one_consistent_safe_username(
        closure_module, monkeypatch, tmp_path, capsys):
    config = load_with()
    monkeypatch.setattr(closure_module, "PG_EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(closure_module, "DUMP_PART", tmp_path / "evidence" / "dump.part")

    arguments = closure_module._connection_preflight(config)
    output = capsys.readouterr().out.splitlines()

    assert output == [
        "CONFIG_USERNAME=%s" % POOLER_USERNAME,
        "SQL_PROBE_USERNAME=%s" % POOLER_USERNAME,
        "PG_DUMP_USERNAME=%s" % POOLER_USERNAME,
    ]
    assert "--username=%s" % POOLER_USERNAME in arguments
    assert "--username=postgres" not in arguments
    assert config.postgres.password not in " ".join(arguments)


def test_bootstrap_prompts_only_for_missing_and_never_prints_values(monkeypatch, capsys):
    provider = FakeProvider({"POSTGRES_PASSWORD": POSTGRES_PASSWORD})
    prompted = []

    def hidden(name):
        prompted.append(name)
        return complete_secrets()[name]

    monkeypatch.setattr(production_secrets, "_hidden_value", hidden)
    assert production_secrets._bootstrap(provider) == 0
    output = capsys.readouterr().out

    assert prompted == ["SUPABASE_SERVICE_ROLE_KEY", "BASEERA_AUTH_SECRET"]
    assert all("%s: READY" % name in output for name in PRODUCTION_SECRET_NAMES)
    assert not any(secret in output for secret in complete_secrets().values())
