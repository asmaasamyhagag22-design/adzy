# -*- coding: utf-8 -*-
"""The UNIFIED lavender package (2026-07-19, owner's directive): ten pages, lavender identity,
native dark ports of the classic sections (market/SWOT/calendar/honesty), and the interactive
Services page (serve mode) that degrades HONESTLY in the export. Hermetic."""
from __future__ import annotations

import json

from tests.test_war_room import _SLUG, _build, _result


def _rich_result() -> dict:
    doc = _result()
    doc["trends"] = [{"title": "Laser care demand rising", "url": "https://news.example/a",
                      "trend_score": 0.8, "matched_terms": ["ليزر", "جلدية"], "source": "serp"},
                     {"title": "Skin clinics boom", "url": "https://news.example/b"}]
    doc["competitors"] = [
        {"candidate": {"name": "Rival Clinic", "website": "https://rival.example/",
                       "rating": 4.4, "review_count": 210},
         "selection": {"why_selected": "top Places peer", "peer_fit_score": 0.8},
         "reviews": [], "has_scrapable_site": True, "is_local": True},
        {"candidate": {"name": "Web Peer Co", "website": "https://webpeer.example/"},
         "selection": {"why_selected": "web search peer (rank 8) for query: 'skin clinic'"},
         "reviews": [], "has_scrapable_site": True, "is_local": False},
    ]
    doc["tows"] = {"posture": "leverage",
                   "strategies": [{"type": "SO", "title": "وظّف اعتماد أطبائك",
                                   "description": "قُد الرسائل بالاعتماد الطبي الموثق."}],
                   "priority_actions": [{"rank": 1, "action": "Leverage: أطباء جلدية معتمدون",
                                         "horizon": "now", "rationale": ""}]}
    doc["swot"]["strengths"].append(
        {"text": "العملاء بيمدحوا سرعة المواعيد", "citation": ["«حجزت في نفس اليوم»"],
         "evidence": "own-brand customer voice"})
    return doc


def test_market_page_renders_competitors_and_trends(tmp_path):
    h = _build(tmp_path, result=_rich_result())
    assert "Rival Clinic" in h and "Web Peer Co" in h
    assert "#8 نتيجة بحث" in h                              # search-rank honesty, no fake fit %
    assert "4.4" in h and "210" in h                        # real Places signals
    assert "Laser care demand rising" in h
    assert "صلة 80%" in h and "مطابق لبراندك: ليزر · جلدية" in h    # the confidence surface
    row2 = h.split("Skin clinics boom")[1][:200]
    assert "صلة" not in row2                                  # no score -> no invented chip
    h2 = _build(tmp_path)                                   # bare result: no competitors/trends
    assert "لا منافسين مُسجّلين بعد" in h2 and "لا إشارات سوق حيّة" in h2


def test_swot_page_quads_tows_and_voice(tmp_path):
    h = _build(tmp_path, result=_rich_result())
    assert "أطباء جلدية معتمدون" in h                        # strength in the quad
    assert "وظّف اعتماد أطبائك" in h                         # TOWS strategy row
    assert "«حجزت في نفس اليوم»" in h                        # customer-voice verbatim quote
    assert "صوت عملائك" in h
    h2 = _build(tmp_path)                                   # no voice-tagged items
    assert "صوت عملائك" not in h2                            # the block disappears entirely


def test_command_page_posture_and_move(tmp_path):
    h = _build(tmp_path, result=_rich_result())
    assert "وضعك التنافسي قوي" in h                          # posture line from B._POSTURE
    assert "الخطوة رقم واحد" in h and "وظّف نقطة قوتك" in h  # the imperative ONE move


def test_calendar_page_chips_and_no_raw_enums(tmp_path):
    plan = {"business_name": "Baseera", "start_date": "2026-07-20", "days": 14, "items": [
        {"date": "2026-07-20", "platform": "instagram", "content_type": "post",
         "topic": "جلسة الليزر", "hook": "ليه تستني؟", "funnel_stage": "bofu", "cta": "احجزي"},
        {"date": "2026-07-22", "platform": "facebook", "content_type": "reel",
         "topic": "جولة العيادة", "hook": ""},                       # legacy item: no funnel/cta
    ]}
    pp = tmp_path / f"{_SLUG}_plan.json"
    pp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    h = _build(tmp_path, plan_path=str(pp))
    assert "جلسة الليزر" in h and "احجزي" in h
    assert "تحويل · BOFU" in h                               # funnel chip localized
    assert "OUTCOME_LEADS" not in h                          # raw enum never reaches the client
    assert "عملاء محتملون" in h
    row2 = h.split("جولة العيادة")[1][:400]
    assert "—" in row2                                       # legacy plan item degrades honestly
    h2 = _build(tmp_path)                                    # no plan file at all
    assert "لا تقويم محتوى بعد" in h2


def test_services_export_degrades_honestly(tmp_path):
    services = [{"name": "جلسة ليزر كاملة", "url": "https://brand.example/laser",
                 "price_text": "800 EGP"}]
    h = _build(tmp_path, services=services, serve_mode=False)
    assert "جلسة ليزر كاملة" in h and "800 EGP" in h
    assert "النسخة الحية" in h and "python -m dashboard.server" in h
    assert "/generate/" not in h and "/hitl/" not in h       # zero dead action buttons
    assert 'id="svc-poster"' not in h
    assert h.lower().count("<script") == 1                   # export contract intact
    h2 = _build(tmp_path, services=[], serve_mode=False)
    assert "لا خدمات مستخرَجة من الموقع بعد" in h2


def test_services_served_is_interactive_with_hitl_gate(tmp_path):
    h = _build(tmp_path, services=[{"name": "Laser", "url": "https://x/l"}], serve_mode=True)
    assert 'id="purl"' in h and 'id="svc-poster"' in h       # picker + paste-URL + generate
    assert "/products?slug=" in h and "/product_by_url" in h
    assert "/hitl/preview" in h and "EXECUTE" in h           # the approval gate
    # the paid call happens ONLY inside the EXECUTE handler, after the previewed brief
    assert h.index("/hitl/preview") < h.index("/generate/poster")
    assert "&hitl=1" in h


def test_radar_winner_fatigue_chips_and_counter_provenance(tmp_path):
    # ش4 surfacing: an EXTENDED ad_presence (ش2 facts) renders the winner/fatigue/format/CTA chips
    # AND the counter cell carries its "أب القرار" provenance; an OLD-shape result renders neither.
    doc = _result(ads=True)
    doc["ad_presence"][0].update({"winners_30d": 2, "fatigue_groups": 1,
                                  "top_format": "image", "top_cta": "whatsapp"})
    h = _build(tmp_path, result=doc)
    assert "فائز 30+ يوم" in h and "بيكرر إعلانه بصيغ متعددة" in h
    assert "أب القرار" in h and "Meta Ad Library" in h        # the counter cell's provenance chip
    assert ("Reel" in h) or ("ريل" in h)                       # image rival -> we bet Reel
    h2 = _build(tmp_path, result=_result(ads=True))            # old shape: no fact fields
    assert "فائز 30+ يوم" not in h2 and "أب القرار" not in h2


def test_batch1_brand_summary_card_and_bidi(tmp_path):
    # 1.7: the business owner lands on a compact evidence-grounded brand card — first screen.
    doc = _result()
    doc["profile"]["description"] = {"value": "معهد تدريب تكنولوجي بيقدم برامج Software معتمدة"}
    doc["profile"]["audience_type"] = {"value": "خريجون جدد"}
    doc["profile"]["value_propositions"] = [{"value": "شهادات معتمدة دولياً"}]
    h = _build(tmp_path, result=doc)
    assert "بطاقة البراند" in h and "كما فهمناها من موقعك" in h
    assert "معهد تدريب تكنولوجي" in h and "الجمهور: خريجون جدد" in h
    assert "✦ شهادات معتمدة دولياً" in h
    # 1.4: mixed-script text resolves per-paragraph — the bidi rule + dir=auto are in place
    assert "unicode-bidi:plaintext" in h and 'class="ns-text" dir="auto"' in h
    bare = dict(_result())
    bare["profile"] = {"name": {"value": "X"}, "offerings": []}
    h2 = _build(tmp_path, result=bare)
    assert "بطاقة البراند" not in h2                          # honest degrade: no empty shell


def test_batch1_sourced_claim_without_url_links_to_brand_site(tmp_path):
    # 1.5: "حط لينك حتى لو الكلام متعمله إعادة صياغة" — the SOURCED claim falls back to the
    # brand's own site; the REJECTED claim NEVER gains a link.
    audit = {"business_name": "B", "evidence_count": 2, "items": [{
        "date": "2026-07-01", "platform": "instagram", "content_type": "post",
        "remediated": False, "shipped_copy": {"passes": True, "fields": [{
            "field": "headline", "text": "نص معاد صياغته", "grounded": True,
            "claims": [
                {"kind": "contact", "token": "01000000000", "sourced": True,
                 "source_url": "", "matched_quote": "01000000000"},
                {"kind": "superlative", "token": "الأفضل", "sourced": False,
                 "source_url": "", "matched_quote": ""},
            ]}]}}]}
    h = _build(tmp_path, audit=audit)
    row = h.split("01000000000")[1][:400]
    assert 'href="https://brand.example.eg/"' in row and "من موقعك" in row
    bad = h.split("الأفضل")[1][:300]
    assert "من موقعك" not in bad                              # a rejected claim stays linkless


def test_p8_studio_generation_controls(tmp_path):
    # owner ask 2026-07-20 ("عايزة أطلع ريل أضغط فين؟"): p8 carries generate buttons in serve
    # mode — reel is PAID so the HITL EXECUTE gate is mandatory; the export stays button-free.
    h = _build(tmp_path, serve_mode=True)
    assert 'id="st-reel"' in h and 'id="st-poster"' in h
    assert 'id="st-lang"' in h and 'id="st-dialect"' in h      # her language/register choice
    assert "'/generate/'+stKind" in h and "EXECUTE" in h
    assert h.index("/hitl/preview?kind='+kind") < h.index("'/generate/'+stKind")  # approval before spend
    assert "{status_html}" not in h                            # the literal-placeholder bug
    h2 = _build(tmp_path, serve_mode=False)
    assert 'id="st-reel"' not in h2 and "{status_html}" not in h2


def test_ledger_page_carries_the_honesty_surface(tmp_path):
    h = _build(tmp_path)                                     # bare: no competitors/voice/ads
    assert "ما لا نعرفه بعد" in h
    assert "لم نؤكّد منافسين مباشرين بعد" in h
    assert "تأكيد هوية Meta" in h


def test_hitl_panels_have_lang_toggle_and_refine(tmp_path):
    # owner ask 2026-07-20: the previewed brief gets AR/EN toggles + a suggestion box that
    # REVISES the prompt (/hitl/refine) — on BOTH panels (services + studio), serve mode only.
    h = _build(tmp_path, services=[{"name": "Laser", "url": "https://x/l"}], serve_mode=True)
    for el in ('id="svc-br-ar"', 'id="svc-br-en"', 'id="svc-note"', 'id="svc-refine"',
               'id="st-br-ar"', 'id="st-br-en"', 'id="st-note"', 'id="st-refine"'):
        assert el in h, el
    assert "/hitl/refine" in h
    assert h.index("/hitl/refine") > h.index("/hitl/preview")   # refine follows a preview
    h2 = _build(tmp_path, serve_mode=False)
    assert 'id="svc-refine"' not in h2 and 'id="st-refine"' not in h2


def test_service_scrape_audit_file(tmp_path):
    # owner ask: every service scrape lands in outputs/<slug>_service_scrapes.json for auditing.
    import dataclasses
    import json

    from dashboard.server import _persist_service_scrape

    @dataclasses.dataclass
    class _Info:
        name: str = "جلسة ليزر"
        url: str = "https://b.example/laser"
        image: str = "https://b.example/l.jpg"
        price: str = "800"
        currency: str = "EGP"
        usable: bool = True

    _persist_service_scrape(str(tmp_path), "brand_x", _Info())
    _persist_service_scrape(str(tmp_path), "brand_x", _Info(name="نسخة أحدث"))  # same url -> replace
    doc = json.loads((tmp_path / "brand_x_service_scrapes.json").read_text(encoding="utf-8"))
    assert doc["profile_file"] == "brand_x_profile.json"
    assert len(doc["scrapes"]) == 1 and doc["scrapes"][0]["name"] == "نسخة أحدث"
    assert doc["scrapes"][0]["scraped_at"]
    _persist_service_scrape(str(tmp_path), "brand_x", _Info(url="https://b.example/other"))
    doc2 = json.loads((tmp_path / "brand_x_service_scrapes.json").read_text(encoding="utf-8"))
    assert len(doc2["scrapes"]) == 2                            # different url -> appended
