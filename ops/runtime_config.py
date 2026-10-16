"""Canonical, typed production configuration for operational consumers."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from sqlalchemy.engine import URL

from .secrets import (PRODUCTION_SECRET_NAMES, SecretProvider,
                      WindowsKeyringSecretProvider, validate_secret_value)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTION_PROFILE = ROOT / "config" / "runtime.production.toml"

_PROFILE_SECTIONS = {
    "persistence": {"backend"},
    "blob": {"backend"},
    "postgres": {"host", "port", "database", "username", "sslmode"},
    "supabase": {"url", "bucket"},
}
_FORBIDDEN_PROFILE_KEY = re.compile(
    r"(?i)(password|secret|token|credential|private.?key|signing|dsn|database.?url)")
_CREDENTIAL_DSN = re.compile(
    r"(?i)\b(postgres(?:ql)?(?:\+[a-z0-9_.-]+)?://)[^\s/@:]+:[^\s@]+@[^\s\"'<>]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_SUPABASE_SECRET = re.compile(r"\bsb_secret_[A-Za-z0-9_-]{12,}\b", re.IGNORECASE)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----", re.DOTALL)
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)(^\s*(?:\$env:|export\s+)?(?:POSTGRES_PASSWORD|SUPABASE_SERVICE_ROLE_KEY|"
    r"BASEERA_AUTH_SECRET|BASEERA_DATABASE_URL|PGPASSWORD)\s*=)\s*[^\r\n]*")


class RuntimeConfigError(RuntimeError):
    """Production configuration is missing, malformed, or unsafe."""


class MissingProductionSecrets(RuntimeConfigError):
    def __init__(self, names: Sequence[str]) -> None:
        self.names = tuple(names)
        super().__init__("required production secrets are missing: %s" % ", ".join(self.names))


@dataclass(frozen=True)
class ProductionProfile:
    profile: str
    persistence_backend: str
    blob_backend: str
    postgres_host: str
    postgres_port: int
    postgres_database: str
    postgres_username: str
    postgres_sslmode: str
    supabase_url: str
    supabase_bucket: str


@dataclass(frozen=True)
class PostgresRuntimeConfig:
    host: str
    port: int
    database: str
    username: str
    sslmode: str
    password: str = field(repr=False)

    @property
    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg", username=self.username, password=self.password,
            host=self.host, port=self.port, database=self.database,
            query={"sslmode": self.sslmode},
        )

    @property
    def sqlalchemy_dsn(self) -> str:
        return self.sqlalchemy_url.render_as_string(hide_password=False)


@dataclass(frozen=True)
class SupabaseRuntimeConfig:
    url: str
    bucket: str
    service_role_key: str = field(repr=False)


@dataclass(frozen=True)
class RuntimeConfig:
    profile: str
    persistence_backend: str
    blob_backend: str
    postgres: PostgresRuntimeConfig
    supabase: SupabaseRuntimeConfig
    auth_secret: str = field(repr=False)

    def known_secret_values(self) -> tuple[str, ...]:
        return (self.postgres.password, self.supabase.service_role_key, self.auth_secret)

    def safe_summary(self) -> str:
        return (
            "backend=%s host=%s port=%s database=%s blob_backend=%s bucket=%s"
            % (self.persistence_backend, self.postgres.host, self.postgres.port,
               self.postgres.database, self.blob_backend, self.supabase.bucket)
        )


def secret_profile_fields(document: Mapping[str, Any]) -> tuple[str, ...]:
    findings: list[str] = []

    def visit(value: Any, prefix: str = "") -> None:
        if not isinstance(value, Mapping):
            return
        for raw_key, child in value.items():
            key = str(raw_key)
            path = "%s.%s" % (prefix, key) if prefix else key
            if _FORBIDDEN_PROFILE_KEY.search(key):
                findings.append(path)
            visit(child, path)

    visit(document)
    return tuple(sorted(findings))


def _required_string(section: Mapping[str, Any], name: str, section_name: str) -> str:
    value = section.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError("production profile field is invalid: %s.%s"
                                 % (section_name, name))
    return value.strip()


def load_production_profile(path: Path | str = DEFAULT_PRODUCTION_PROFILE) -> ProductionProfile:
    profile_path = Path(path)
    try:
        document = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeConfigError("production profile could not be read") from exc
    leaked_fields = secret_profile_fields(document)
    if leaked_fields:
        raise RuntimeConfigError("production profile contains forbidden secret fields: %s"
                                 % ", ".join(leaked_fields))
    if set(document) != {"profile", *_PROFILE_SECTIONS}:
        raise RuntimeConfigError("production profile has an unexpected top-level shape")
    for section_name, allowed in _PROFILE_SECTIONS.items():
        section = document.get(section_name)
        if not isinstance(section, Mapping) or set(section) != allowed:
            raise RuntimeConfigError("production profile section has an unexpected shape: %s"
                                     % section_name)

    persistence = document["persistence"]
    blob = document["blob"]
    postgres = document["postgres"]
    supabase = document["supabase"]
    profile_name = _required_string(document, "profile", "root")
    persistence_backend = _required_string(persistence, "backend", "persistence").lower()
    blob_backend = _required_string(blob, "backend", "blob").lower()
    if profile_name != "production" or persistence_backend != "postgres" or blob_backend != "supabase":
        raise RuntimeConfigError("production profile does not select the fixed production authority")
    port = postgres.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise RuntimeConfigError("production profile field is invalid: postgres.port")

    supabase_url = _required_string(supabase, "url", "supabase").rstrip("/")
    parsed_supabase = urlsplit(supabase_url)
    if (parsed_supabase.scheme != "https" or not parsed_supabase.hostname
            or not parsed_supabase.hostname.endswith(".supabase.co")
            or parsed_supabase.path not in ("", "/")):
        raise RuntimeConfigError("production profile Supabase URL is invalid")
    username = _required_string(postgres, "username", "postgres")
    project_ref = parsed_supabase.hostname.removesuffix(".supabase.co")
    expected_pooler_username = "postgres." + project_ref
    if username != expected_pooler_username:
        raise RuntimeConfigError("Postgres and Supabase profile entries identify different projects")

    return ProductionProfile(
        profile=profile_name,
        persistence_backend=persistence_backend,
        blob_backend=blob_backend,
        postgres_host=_required_string(postgres, "host", "postgres"),
        postgres_port=port,
        postgres_database=_required_string(postgres, "database", "postgres"),
        postgres_username=username,
        postgres_sslmode=_required_string(postgres, "sslmode", "postgres").lower(),
        supabase_url=supabase_url,
        supabase_bucket=_required_string(supabase, "bucket", "supabase"),
    )


def _resolve_secrets(provider: SecretProvider, environ: Mapping[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in PRODUCTION_SECRET_NAMES:
        environment_value = environ.get(name)
        value = environment_value if environment_value not in (None, "") else provider.get(name)
        if value is None:
            missing.append(name)
            continue
        resolved[name] = validate_secret_value(name, value)
    if missing:
        raise MissingProductionSecrets(missing)
    return resolved


def load_runtime_config(*, profile_path: Path | str = DEFAULT_PRODUCTION_PROFILE,
                        provider: SecretProvider | None = None,
                        environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Load production config without reading `.env` or persisting a constructed DSN.

    Local production operations use the selected provider only.  CI/ephemeral environment
    overrides remain supported, but only when the caller deliberately supplies ``environ``.
    """
    profile = load_production_profile(profile_path)
    selected_provider = provider if provider is not None else WindowsKeyringSecretProvider()
    secrets = _resolve_secrets(selected_provider, {} if environ is None else environ)
    return RuntimeConfig(
        profile=profile.profile,
        persistence_backend=profile.persistence_backend,
        blob_backend=profile.blob_backend,
        postgres=PostgresRuntimeConfig(
            host=profile.postgres_host, port=profile.postgres_port,
            database=profile.postgres_database, username=profile.postgres_username,
            sslmode=profile.postgres_sslmode, password=secrets["POSTGRES_PASSWORD"],
        ),
        supabase=SupabaseRuntimeConfig(
            url=profile.supabase_url, bucket=profile.supabase_bucket,
            service_role_key=secrets["SUPABASE_SERVICE_ROLE_KEY"],
        ),
        auth_secret=secrets["BASEERA_AUTH_SECRET"],
    )


def scan_secret_material(text: str, known_secrets: Sequence[str] = ()) -> tuple[str, ...]:
    findings: list[str] = []
    if _CREDENTIAL_DSN.search(text):
        findings.append("credential-bearing-postgres-dsn")
    if _JWT.search(text):
        findings.append("jwt-like-value")
    if _SUPABASE_SECRET.search(text):
        findings.append("supabase-secret-key")
    if _PRIVATE_KEY.search(text):
        findings.append("private-key-pem")
    if _SECRET_ASSIGNMENT.search(text):
        findings.append("environment-secret-assignment")
    if any(secret and secret in text for secret in known_secrets):
        findings.append("known-loaded-secret")
    return tuple(findings)


def redact(text: object, known_secrets: Sequence[str] = ()) -> str:
    safe = str(text)
    safe = _PRIVATE_KEY.sub("<private-key-redacted>", safe)
    safe = _CREDENTIAL_DSN.sub(r"\1<credentials-redacted>@<endpoint-redacted>", safe)
    safe = _JWT.sub("<jwt-redacted>", safe)
    safe = _SUPABASE_SECRET.sub("<supabase-secret-redacted>", safe)
    safe = _SECRET_ASSIGNMENT.sub(r"\1 <redacted>", safe)
    for secret in sorted((item for item in known_secrets if item), key=len, reverse=True):
        safe = safe.replace(secret, "<known-secret-redacted>")
    return safe
