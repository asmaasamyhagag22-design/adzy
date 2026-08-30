# -*- coding: utf-8 -*-
"""1.1 (owner report 2026-07-19): the supplements pull returned many ads with only ONE actually
related — search_terms is a broad keyword match and NOTHING gated relevance afterward. The gate is
multi-signal (Arabic brands live on English-named pages and vice versa): dual containment, token
overlap, the facebook handle from the candidate's website, or the brand name inside the ad's own
text. Filtering is enforced at the ONE parse chokepoint (ads_from_snapshot) so the orchestrator,
the manual pull script, and the offline consumer all inherit it. Hermetic."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from business_profile.llm.caller import MockCaller

from ads_intel.relevance import matches_competitor
from ads_intel.providers.meta_ad_library import ads_from_snapshot
from marketing_intel.extractor import _RawResponse

_NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


def _row(i, page="NanoCare Egypt", text="نانو كير ليبوسومال — خصم على كل المكملات", page_id="777"):
    return {"id": str(i), "page_name": page, "page_id": page_id,
            "publisher_platforms": ["facebook"], "ad_delivery_start_time": "2026-06-01",
            "media_type": "image", "ad_creative_bodies": [f"{text} #{i}"]}


def _snap(rows, *, query="نانو كير إيجيبت", website="https://www.facebook.com/nanocare.egypt"):
    return {"brand_ref": "nano_kir_ijibt", "query": query, "website": website,
            "pulled_at": "2026-07-19T00:00:00Z", "ads": rows}


# ---- the multi-signal matcher ---------------------------------------------------------

def test_matcher_same_script_containment():
    assert matches_competitor("Rival Clinic Cairo", "", "Rival Clinic")
    assert matches_competitor("نانو كير", "", "نانو كير إيجيبت")            # dual containment


def test_matcher_cross_script_via_handle():
    # Arabic competitor name, English page name — the candidate's fb handle bridges the scripts
    assert matches_competitor("NanoCare Egypt", "", "نانو كير إيجيبت",
                              handle="nanocare.egypt")
    assert not matches_competitor("Golden Pharma", "", "نانو كير إيجيبت",
                                  handle="nanocare.egypt")


def test_matcher_brand_name_inside_ad_text():
    # English-named page, but the ad copy itself says the Arabic brand name
    assert matches_competitor("NC Official", "عروض نانو كير إيجيبت للمكملات", "نانو كير إيجيبت")


def test_matcher_rejects_keyword_false_positives():
    assert not matches_competitor("سوبر ماركت الخير", "أفضل مكملات غذائية بأرخص سعر",
                                  "نانو كير إيجيبت")
    assert not matches_competitor("", "", "نانو كير إيجيبت")


# ---- the parse chokepoint filters for every consumer ----------------------------------

def test_ads_from_snapshot_filters_unrelated_advertisers():
    rows = ([_row(i) for i in range(3)]
            + [_row(10 + i, page=f"Random Advertiser {i}", page_id="999",
                    text="منتجات تانية خالص مالهاش دعوة") for i in range(5)])
    kept = ads_from_snapshot(_snap(rows))
    assert len(kept) == 3 and all(a.page_name == "NanoCare Egypt" for a in kept)
    raw = ads_from_snapshot(_snap(rows), only_competitor=False)
    assert len(raw) == 8                                       # raw access still available
    assert raw[0].page_id == "777"                             # page_id captured (additive)


def test_old_snapshot_without_query_is_not_filtered():
    # pre-1.1 snapshots carry no query/website — parsing them must not silently drop ads
    rows = [_row(1, page="Whatever Page")]
    snap = {"brand_ref": "r", "pulled_at": "2026-07-01T00:00:00Z", "ads": rows}
    assert len(ads_from_snapshot(snap)) == 1


# ---- orchestrator surfaces the honesty counter ----------------------------------------

def test_orchestrator_counts_filtered_out(tmp_path):
    from ads_intel.orchestrator import adaptive_pull_competitor

    class OneShotTransport:
        def fetch(self, url):
            assert "KEYWORD_EXACT_PHRASE" in url               # query-level precision
            return {"data": ([_row(i) for i in range(3)]
                             + [_row(10 + i, page=f"Stranger {i}", text="حاجة تانية")
                                for i in range(5)])}

    caller = MockCaller({"ads_competitor_intel": _RawResponse(items=[])})
    b = adaptive_pull_competitor("نانو كير إيجيبت", country="EG", token="tkn", caller=caller,
                                 transport=OneShotTransport(), snapshots_dir=str(tmp_path),
                                 ledger=str(tmp_path / "l.jsonl"), today=date(2026, 7, 19),
                                 website="https://www.facebook.com/nanocare.egypt")
    assert b.ads_filtered_out == 5 and b.ads_pulled == 8
    assert b.presence is not None and b.presence.active_count == 3      # facts from KEPT only
    snap = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert len(snap["ads"]) == 8 and snap["query"]             # raw truth + query for re-filtering
    assert snap.get("website")


def test_consumer_presence_filters_and_reports(tmp_path):
    from ads_intel.consumer import ad_presence_for_refs
    rows = [_row(1), _row(2, page="Stranger", text="مش مرتبط")]
    (tmp_path / "nano_kir_ijibt.json").write_text(
        json.dumps(_snap(rows), ensure_ascii=False), encoding="utf-8")
    aps = ad_presence_for_refs(["nano_kir_ijibt"], snapshots_dir=str(tmp_path),
                               today=date(2026, 7, 19))
    assert len(aps) == 1 and aps[0].active_count == 1
    assert aps[0].filtered_out == 1                            # surfaced for the radar chip
