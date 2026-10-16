"""Safe bootstrap/status CLI for the production OS secret store."""
from __future__ import annotations

import argparse
import getpass
import sys
from typing import Sequence

from .runtime_config import redact
from .secrets import (PRODUCTION_SECRET_NAMES, SecretStoreError,
                      WindowsKeyringSecretProvider, validate_secret_value)


def _hidden_value(name: str) -> str:
    if not sys.stdin.isatty():
        raise SecretStoreError("hidden secret input requires an interactive terminal")
    value = getpass.getpass("%s (hidden): " % name)
    return validate_secret_value(name, value)


def _status(provider: WindowsKeyringSecretProvider) -> int:
    ready = True
    for name in PRODUCTION_SECRET_NAMES:
        present = provider.get(name) is not None
        ready = ready and present
        print("%s: %s" % (name, "PRESENT" if present else "MISSING"))
    print("PRODUCTION_SECRET_STORE: %s" % ("READY" if ready else "MISSING"))
    return 0 if ready else 1


def _bootstrap(provider: WindowsKeyringSecretProvider) -> int:
    for name in PRODUCTION_SECRET_NAMES:
        if provider.get(name) is None:
            provider.set(name, _hidden_value(name))
        print("%s: READY" % name)
    print("PRODUCTION_SECRET_STORE: READY")
    return 0


def _rotate(provider: WindowsKeyringSecretProvider, name: str) -> int:
    provider.set(name, _hidden_value(name))
    print("%s: READY" % name)
    print("PRODUCTION_SECRET_STORE: READY")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage ADZY/Baseera production secrets safely.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="report PRESENT/MISSING only")
    commands.add_parser("bootstrap", help="prompt only for missing production secrets")
    rotate = commands.add_parser("rotate", help="replace one production secret using hidden input")
    rotate.add_argument("name", choices=PRODUCTION_SECRET_NAMES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provider = WindowsKeyringSecretProvider()
    try:
        if args.command == "status":
            return _status(provider)
        if args.command == "bootstrap":
            return _bootstrap(provider)
        return _rotate(provider, args.name)
    except Exception as exc:  # noqa: BLE001 - never emit a traceback containing provider detail
        print("PRODUCTION_SECRET_STORE: NOT READY")
        print("BLOCKER: %s" % redact(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

