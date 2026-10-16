"""Secret-provider contracts and the Windows Credential Manager implementation."""
from __future__ import annotations

import os
import re
from typing import Any, Protocol, runtime_checkable

PRODUCTION_SECRET_SERVICE = "ADZY/Baseera production"
PRODUCTION_SECRET_NAMES = (
    "POSTGRES_PASSWORD",
    "SUPABASE_SERVICE_ROLE_KEY",
    "BASEERA_AUTH_SECRET",
)
_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class SecretStoreError(RuntimeError):
    """The selected secret store could not safely complete an operation."""


@runtime_checkable
class SecretProvider(Protocol):
    """Minimal provider boundary used by consumers and future managed stores."""

    def get(self, name: str) -> str | None:
        """Return a secret or ``None`` without disclosing it."""

    def set(self, name: str, value: str) -> None:
        """Persist a secret in the provider without disclosing it."""


def validate_secret_name(name: str) -> str:
    candidate = str(name or "")
    if not _SECRET_NAME.fullmatch(candidate):
        raise SecretStoreError("invalid production secret name")
    return candidate


def validate_secret_value(name: str, value: str | None) -> str:
    validate_secret_name(name)
    if not isinstance(value, str) or not value:
        raise SecretStoreError("production secret is empty: %s" % name)
    if "\r" in value or "\n" in value or "\x00" in value:
        raise SecretStoreError("production secret contains unsupported control bytes: %s" % name)
    return value


class WindowsKeyringSecretProvider:
    """Store secrets under one namespace in the native Windows keyring backend."""

    def __init__(self, *, service: str = PRODUCTION_SECRET_SERVICE,
                 keyring_module: Any | None = None) -> None:
        self.service = service
        self._module = keyring_module
        self._injected_module = keyring_module is not None

    def _keyring(self) -> Any:
        if self._module is None:
            if os.name != "nt":
                raise SecretStoreError("Windows Credential Manager is required on this host")
            try:
                import keyring
            except ImportError as exc:
                raise SecretStoreError("Python keyring support is not installed") from exc
            self._module = keyring

        if not self._injected_module:
            try:
                backend = self._module.get_keyring()
                module_name = type(backend).__module__.casefold()
                priority = float(getattr(backend, "priority", 0))
            except Exception as exc:  # pragma: no cover - backend-specific defensive boundary
                raise SecretStoreError("Windows Credential Manager backend is unavailable") from exc
            if not module_name.startswith("keyring.backends.windows") or priority <= 0:
                raise SecretStoreError("active keyring backend is not Windows Credential Manager")
        return self._module

    def get(self, name: str) -> str | None:
        name = validate_secret_name(name)
        try:
            value = self._keyring().get_password(self.service, name)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Windows Credential Manager read failed: %s" % name) from exc
        if value is None:
            return None
        return validate_secret_value(name, value)

    def set(self, name: str, value: str) -> None:
        name = validate_secret_name(name)
        value = validate_secret_value(name, value)
        try:
            self._keyring().set_password(self.service, name, value)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Windows Credential Manager write failed: %s" % name) from exc

