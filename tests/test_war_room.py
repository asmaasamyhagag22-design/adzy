# -*- coding: utf-8 -*-
"""War Room dashboard (dashboard/war_room.py) — the paged dark client deliverable. Hermetic:
dict fixtures -> tmp_path JSON -> build -> structural grep (the house style). fetch_logo=False
everywhere (no network); media_plan_v2 computed on the fly is deterministic (no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.war_room import build_war_room_html
from media_plan.config import capacity_default, kill_switch_cpl

_SLUG = "brand_example"


def _result(*, priced: bool = False, ads: bool = False, budget: bool = False) -> dict:
    offerings = [{"name": {"value": "Laser session"},
                  "price_text": {"value": "from EGP 800"} if priced else None}]
    doc = {
        "subject_url": "https://brand.example/",
        "generated_at": "2026-07-19T09:00:00+00:00",
        "profile": {
            "name": {"value": "Baseera Clinic"},
            "category": {"value": "clinic"},
            "tagline": {"value": None},
            "source_url": {"value": "https://brand.example.eg/"},
            "offerings": offerings,
            "visual": {"palette_hex": ["#246cb4"], "primary_color": "#246cb4"},
        },
        "swot": {"strengths": [{"text": "أطباء جلدية معتمدون", "citation": ["your profile"]}],
                 "weaknesses": [], "opportunities": [{"text": "لا منافس يقدم استشارة مجانية"}],
                 "threats": [], "mode": "standalone"},
        "tows": {},
        "competitors": [],
        "media_plan": {"objectives": [{
            "objective": "OUTCOME_LEADS", "destination": "lead_form", "funnel_stage": "bofu",
            "test_budget": 5000.0 if budget else None, "budget_allocation_pct": 100.0,
        }]},
    }
    if ads:
        doc["ads_available"] = True
        doc["ad_presence"] = [{"brand_ref": "rival_a", "page_name": "Rival A", "active_count": 3,
                               "longest_days": 190, "proven_winners": 1, "formats": {},
                               "platforms": {}}]
    return doc


def _build(tmp_path, *, result=None, audit=None, poster=False, reel=False, **kw) -> str:
    rp = tmp_path / f"{_SLUG}_result.json"
    rp.write_text(json.dumps(result or _result(), ensure_ascii=False), encoding="utf-8")
    ap = None
    if audit is not None:
        ap = tmp_path / f"{_SLUG}_plan.audit.json"
        ap.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    pp = rp2 = None
    if poster:
        pp = tmp_path / "posters" / f"{_SLUG}_poster.png"
        pp.parent.mkdir(exist_ok=True)
        pp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 24)
    if reel:
        rp2 = tmp_path / "reels" / f"{_SLUG}_reel.mp4"
        rp2.parent.mkdir(exist_ok=True)
        rp2.write_bytes(b"\x00" * 32)
    return build_war_room_html(
        str(rp), audit_path=str(ap) if ap else None,
        poster_path=str(pp) if pp else None, reel_path=str(rp2) if rp2 else None,
        fetch_logo=False, slug=_SLUG, out_dir=str(tmp_path), **kw)


def test_war_room_ten_pages_rtl(tmp_path):
    h = _build(tmp_path)
    assert 'lang="ar" dir="rtl"' in h
    for i in range(1, 11):
        assert f'id="p{i}"' in h, f"page p{i} missing"
    assert h.count('<section class="page') == 10
    assert 'class="rail"' in h and 'class="dot' in h            # the side-rail navigation
    assert "'Cairo','Tajawal'" in h and "tabular-nums" in h


def test_war_room_self_containment_contract(tmp_path):
    # the War Room's OWN contract: ONE inline <script> allowed; ZERO external resource loads.
    h = _build(tmp_path)
    low = h.lower()
    assert "<script>" in low                                    # inline nav script IS present
    assert "<script src" not in low
    assert "<link" not in low
    assert "fonts.googleapis" not in low and "cdn." not in low
    assert 'src="http' not in low and "@import" not in low


def test_war_room_radar_awaiting_meta_pull(tmp_path):
    # the marker is the CARD class (the bare "is-awaiting" string always exists as a CSS selector)
    h = _build(tmp_path)                                        # no ads_available (all old runs)
    assert "Awaiting Meta Pull" in h and 'class="glass intel is-awaiting"' in h
    h2 = _build(tmp_path, result=_result(ads=True))
    assert "Awaiting Meta Pull" not in h2 and 'class="glass intel is-awaiting"' not in h2
    assert "Rival A" in h2 and "190" in h2                      # real ad-presence chips render


def test_war_room_three_cells_with_lane_colors(tmp_path):
    h = _build(tmp_path)
    for hexcode in ("#5CB3FF", "#3FD8C2", "#FF9E7A"):           # sky / mint / coral lanes
        assert hexcode in h, hexcode
    for pct in (">40%<", ">35%<", ">25%<"):                     # the 40/35/25 budget split, mono
        assert pct in h, pct
    assert h.count('class="lane"') == 3


def test_war_room_creative_cell_mapping(tmp_path):
    h = _build(tmp_path, poster=True, reel=True)
    # poster -> the OFFER cell (purple, index 1); reel -> the AUDIENCE cell (blue, index 0)
    assert "Belongs to: اختبار العرض" in h
    assert "Belongs to: اختبار الجمهور" in h
    assert "Pending Creative" in h                              # the competitor lane stays pending
    assert 'data-src-from="wr-poster"' in h                     # thumb wired from the ONE embed
    h2 = _build(tmp_path)                                       # no assets at all
    assert "Belongs to:" not in h2
    assert h2.count("Pending Creative") >= 3                    # lanes + both studio panes


def test_war_room_vault_numbers_deterministic(tmp_path):
    h = _build(tmp_path, result=_result(priced=True))
    cpl, basis = kill_switch_cpl(800.0, "clinic")
    assert basis == "price_margin"
    assert f">{int(cpl):,}<" in h                               # 144 rendered mono
    ceiling = capacity_default("clinic") * cpl
    assert f">{int(ceiling):,}<" in h                           # 2,160 daily spend ceiling
    assert "سعر حقيقي مسحوب" in h                                # grounded-price basis line
    # the auto-stop line carries the engine's REAL learning multiple (3×), never a hardcoded 2
    assert 'بعد صرف <span class="num">3×</span>' in h
    # no price -> the exact disclosure, never a silent number
    h2 = _build(tmp_path)
    assert "افتراضي لعدم توفر الأسعار" in h2


def test_war_room_bundle_button_honest_states(tmp_path):
    h = _build(tmp_path)
    assert f"python -m launch_bundle outputs/{_SLUG}_result.json" in h   # the copyable command
    assert "جاهز ✓" not in h
    bdir = tmp_path / "launch_bundle" / _SLUG
    bdir.mkdir(parents=True)
    (bdir / "launch_bundle.json").write_text("{}", encoding="utf-8")
    (bdir / "ads_manager.csv").write_text("", encoding="utf-8")
    h2 = _build(tmp_path)
    assert "جاهز ✓" in h2 and "ads_manager.csv" in h2
    assert f"python -m launch_bundle outputs/{_SLUG}_result.json" not in h2


def test_war_room_audit_table_from_fixture(tmp_path):
    audit = {"business_name": "Baseera", "evidence_count": 9, "items": [{
        "date": "2026-07-01", "platform": "instagram", "content_type": "post",
        "remediated": False, "shipped_copy": {"passes": True, "fields": [{
            "field": "headline", "text": "جملة الإعلان المعتمدة", "grounded": True,
            "claims": [
                {"kind": "certification", "token": "معتمدون", "sourced": True,
                 "source_url": "https://brand.example/about", "matched_quote": "نحن معتمدون رسمياً"},
                {"kind": "superlative", "token": "الأفضل", "sourced": False,
                 "source_url": "javascript:alert(1)", "matched_quote": ""},
            ]}]}}]}
    h = _build(tmp_path, audit=audit)
    assert "جملة الإعلان المعتمدة" in h
    assert "brand.example" in h and "«نحن معتمدون رسمياً»" in h
    assert "javascript:" not in h                               # _safe_href neutralized to '#'
    assert ">9<" in h                                           # evidence_count rendered mono
    assert "براءة الذمة التسويقية" in h and "Expand Audit Trail" in h
    h2 = _build(tmp_path)                                       # no audit file
    assert "لا يوجد سجل تدقيق" in h2


def test_war_room_budget_open_input_placeholder(tmp_path):
    h = _build(tmp_path)                                        # no test_budget in the media plan
    assert "الميزانية: مطلوب رقمك" in h                          # page-1 tag asks, never invents
    assert "عدّليها بالرقم الحقيقي" in h                          # page-4 capacity default disclosed
    h2 = _build(tmp_path, result=_result(budget=True))
    assert "الميزانية: مطلوب رقمك" not in h2
    assert ">5,000<" in h2                                      # the real number, mono
