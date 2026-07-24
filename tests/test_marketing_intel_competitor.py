# -*- coding: utf-8 -*-
"""ش3 — competitor mode in marketing_intel (2026-07-19): the SAME verbatim-quote moat now reads a
COMPETITOR's public ad texts and additionally extracts the MESSAGE ANGLE (زاوية الرسالة). The
own-brand prompt stays byte-stable (pinned); evidence rebinds to the matched ad's snapshot_url,
never the model's claim. Hermetic — MockCaller, no network, no spend."""
from __future__ import annotations

from datetime import datetime, timezone

from business_profile.llm.caller import MockCaller
from ads_intel.schemas import CompetitorAd
from marketing_intel.extractor import _RawItem, _RawResponse, _system_prompt

_NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


def _ad(ad_id, text):
    return CompetitorAd(source="fixture", ad_id=ad_id, page_name="Rival A", brand_ref="rival_a",
                        platforms=["facebook"], started_at=None, is_active=True,
                        creative_format="image", ad_text=text,
                        snapshot_url=f"https://fb.example/ads/{ad_id}", scraped_at=_NOW)


_ADS = [_ad("1", "خصم 25% على جلسات الليزر لفترة محدودة — احجزي دلوقتي قبل ما الأماكن تخلص!"),
        _ad("2", "تعبتي من نتائج مؤقتة؟ عيادتنا بأحدث أجهزة 2026."),
        _ad("3", "")]                                          # empty text: must be skipped


def _caller(items):
    return MockCaller({"ads_competitor_intel": _RawResponse(items=items)})


def test_signals_from_ads_skips_empty_texts():
    from marketing_intel.from_ads import signals_from_ads
    sigs = signals_from_ads(_ADS)
    assert len(sigs) == 2
    assert sigs[0].source_url == "https://fb.example/ads/1" and sigs[0].signal_kind == "post"


def test_competitor_moat_and_message_angle():
    from marketing_intel.from_ads import extract_competitor_intel
    caller = _caller([
        _RawItem(kind="offer", text="25% off laser sessions",
                 quote="خصم 25% على جلسات الليزر", permalink="https://fb.example/ads/1"),
        _RawItem(kind="message_angle", text="urgency / scarcity",
                 quote="قبل ما الأماكن تخلص", permalink="https://WRONG.example/"),
        _RawItem(kind="message_angle", text="fear of pain",
                 quote="الليزر عندنا من غير ألم نهائياً",       # NOT in any ad -> fabricated
                 permalink="https://fb.example/ads/2"),
    ])
    mi = extract_competitor_intel(_ADS, caller, brand_ref="rival_a")
    assert len(mi.offers) == 1 and len(mi.message_angles) == 1
    assert mi.rejected == 1                                    # the fabricated angle died
    # evidence rebinds to the MATCHED ad's snapshot_url, not the model's wrong permalink
    assert mi.message_angles[0].evidence[0].page_url == "https://fb.example/ads/1"
    assert mi.brand_ref == "rival_a"


def test_own_brand_prompt_unchanged_and_has_no_message_angle():
    own = _system_prompt()                                     # default mode = own_brand
    assert "brand's own Instagram/social SNAPSHOT" in own      # the pinned sentinel
    assert "message_angle" not in own
    comp = _system_prompt(mode="competitor")
    assert "COMPETITOR" in comp and "message_angle" in comp
    assert "WORD-FOR-WORD" in comp                              # the moat instruction survives


def test_schema_additive_old_dumps_validate():
    from marketing_intel.schemas import MarketingIntelligence
    MarketingIntelligence(brand_ref="x", offers=[], usps=[], pain_points=[], rejected=0)


def test_extract_cli_refuses_under_no_spend(monkeypatch, tmp_path):
    import sys
    import pytest
    import scripts.extract_competitor_intel as cli
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    monkeypatch.setattr(sys, "argv", ["x", "rival_a"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "SPENDS REAL MONEY" in str(e.value)
