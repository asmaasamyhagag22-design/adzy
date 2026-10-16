"""Lazy operational adapters to the legacy application's authoritative semantics.

The persistence layer must remain importable below application orchestration.  Migration commands,
however, must evaluate the exact legacy identity and locale functions instead of copying them and
allowing the rules to drift.  These adapters load those functions only after an operator explicitly
invokes inventory/plan work; importing :mod:`db` never loads the application.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


def brand_store_safe_key(domain_key: str) -> str:
    return str(import_module("api.brand_store")._safe(domain_key))


def domain_key_from_url(url: str) -> str:
    return str(import_module("api.brand_translate").domain_key_from_url(url))


def country_decision(profile: Any) -> Any:
    return import_module("poster.locale").country_decision(profile)


def content_language_decision(profile: Any) -> Any:
    return import_module("poster.locale").content_language_decision(profile)


__all__ = [
    "brand_store_safe_key", "content_language_decision", "country_decision",
    "domain_key_from_url",
]
