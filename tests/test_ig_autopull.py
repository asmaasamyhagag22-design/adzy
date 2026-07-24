# -*- coding: utf-8 -*-
"""IG autopull (owner-generalized 2026-07-19): username extraction from the evidenced profile,
the env gate (never under pytest/no-spend), and the full bridge over an injected fetcher —
PII stripped, moat enforced, honest None on every failure. Hermetic."""
from __future__ import annotations

import json

from business_profile.llm.caller import MockCaller

from marketing_intel.extractor import _RawItem, _RawResponse
from social_intel.ig_autopull import ig_autopull_enabled, ig_username_from_profile, pull_ig_intel

_PROFILE = {"social_presence": [
    {"platform": "facebook", "url": "https://www.facebook.com/6156821"},
    {"platform": "instagram", "url": "https://www.instagram.com/nano_care_pharma"},
]}


def test_username_extraction():
    assert ig_username_from_profile(_PROFILE) == "nano_care_pharma"
    assert ig_username_from_profile({"social_presence": [
        {"platform": "instagram", "url": "https://instagram.com/p/XYZ/"}]}) is None  # a post, not a handle
    assert ig_username_from_profile({}) is None


def test_gate_blocked_under_pytest_and_no_spend(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    assert ig_autopull_enabled() is False                     # PYTEST_CURRENT_TEST blocks
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    assert ig_autopull_enabled() is False
    monkeypatch.delenv("BASEERA_NO_SPEND", raising=False)
    monkeypatch.setenv("IG_AUTOPULL_DISABLED", "1")
    assert ig_autopull_enabled() is False
    monkeypatch.delenv("IG_AUTOPULL_DISABLED", raising=False)
    assert ig_autopull_enabled() is True


def test_bridge_with_fake_fetcher_moat_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setenv("APIFY_LEDGER", str(tmp_path / "led.jsonl"))
    items = [{"url": "https://instagram.com/p/AAA/",
              "caption": "خصم 20% على الكولاجين لفترة محدودة",
              "latestComments": [{"text": "المنتج غيّر بشرتي فعلاً", "ownerUsername": "PII"}]}]
    caller = MockCaller({"ig_marketing_intel": _RawResponse(items=[
        _RawItem(kind="offer", text="خصم 20% كولاجين",
                 quote="خصم 20% على الكولاجين لفترة محدودة", permalink=""),
        _RawItem(kind="pain_point", text="مخترع تماماً",
                 quote="جملة مش موجودة في أي إشارة", permalink=""),
    ])})

    def fake_fetch(username, posts, token):
        assert username == "nano_care_pharma" and posts <= 30
        return items

    mi = pull_ig_intel(_PROFILE, "brand_x", caller, fetcher=fake_fetch,
                       snapshots_dir=str(tmp_path))
    assert mi is not None and len(mi.offers) == 1 and mi.rejected == 1     # الموات شغال
    snap = json.loads((tmp_path / "brand_x_ig.json").read_text(encoding="utf-8"))
    assert "PII" not in json.dumps(snap)                       # author names never persist


def test_bridge_fetch_failure_degrades_to_none(tmp_path, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setenv("APIFY_LEDGER", str(tmp_path / "led.jsonl"))

    def boom(username, posts, token):
        raise RuntimeError("apify 500")

    assert pull_ig_intel(_PROFILE, "b", None, fetcher=boom,
                         snapshots_dir=str(tmp_path)) is None
