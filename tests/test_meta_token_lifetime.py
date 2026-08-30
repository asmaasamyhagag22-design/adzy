# -*- coding: utf-8 -*-
"""A short-lived token is the classic Ad-Library trap: it works, then stops in an hour and the
system looks broken. The verifier must (a) report how long the token lives, and (b) be able to
exchange it for a long-lived one IN PLACE, without the credential ever reaching the console.

Hermetic — the Graph calls are injected."""
from __future__ import annotations

from pathlib import Path

from scripts.verify_meta_token import _lifetime_note, _replace_env_token


def test_short_lived_token_is_called_out_with_its_remaining_time():
    note, short = _lifetime_note({"data": {"expires_at": 1_000_000, "issued_at": 999_000}},
                                 now=999_100)
    assert short is True
    assert "دقيقة" in note or "ساعة" in note              # says HOW long is left, not just "soon"


def test_long_lived_token_is_reported_as_safe():
    day = 86400
    note, short = _lifetime_note({"data": {"expires_at": 1_000_000 + 55 * day}}, now=1_000_000)
    assert short is False and ("يوم" in note)


def test_never_expiring_token_is_handled():
    note, short = _lifetime_note({"data": {"expires_at": 0}}, now=1_000_000)
    assert short is False and ("مالوش" in note or "دائم" in note)


def test_exchange_rewrites_only_the_token_line(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-keep\nMETA_ADS_TOKEN=old\nMETA_APP_ID=123\n",
                   encoding="utf-8")
    assert _replace_env_token(env, "NEWLONGLIVED") is True
    out = env.read_text(encoding="utf-8")
    assert "META_ADS_TOKEN=NEWLONGLIVED" in out
    assert "OPENAI_API_KEY=sk-keep" in out and "META_APP_ID=123" in out   # nothing else touched
    assert "old" not in out


def test_exchange_appends_when_the_key_is_absent(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("META_APP_ID=123\n", encoding="utf-8")
    assert _replace_env_token(env, "TOK") is True
    body = env.read_text(encoding="utf-8")
    assert body.count("META_ADS_TOKEN=") == 1 and body.endswith("\n")


def test_exchange_refuses_to_write_an_empty_token(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("META_ADS_TOKEN=keepme\n", encoding="utf-8")
    assert _replace_env_token(env, "") is False
    assert "keepme" in env.read_text(encoding="utf-8")    # the working token survives
