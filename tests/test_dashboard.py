# -*- coding: utf-8 -*-
"""Baseera dashboard generator — hermetic (no network, no browser).

Verifies the self-contained HTML "prints everything" from the real artifact shapes: the brand,
KPIs, the CITED SWOT (with citations + claim-strength), the discovered competitors with their
peer-fit + why-selected, the TOWS strategies (title, not the empty 'text' field), and the
content calendar — and that it stays self-contained (no external CDN/font links).
"""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.build import build_dashboard


def _write(tmp: Path) -> tuple[str, str, str]:
    competitor = {
        "subject_url": "https://brand.example/", "subject_category": "ecommerce",
        "competitor_count": 1,
        "swot": {
            "mode": "competitive",
            "strengths": [{"text": "23 social links vs peer avg 5.5", "citation": ["your profile"],
                           "claim_strength": "validated"}],
            "weaknesses": [{"text": "No WhatsApp; 1/2 peers have it", "citation": ["peers: Dima"],
                            "claim_strength": "validated"}],
            "opportunities": [], "threats": [],
        },
        "competitors": [{"candidate": {"name": "Dima Jewellery", "website": "https://dima.example/"},
                         "selection": {"name": "Dima Jewellery", "website": "https://dima.example/",
                                       "peer_fit_score": 0.33, "why_selected": "web peer rank 3"},
                         "has_scrapable_site": True, "is_local": False}],
        "tows": {"strategies": [{"type": "SO", "title": "Leverage social reach to open bookings",
                                 "description": "Use the strength to pursue the opportunity."}],
                 "posture": "contingency"},
    }
    profile = {"profile": {"name": {"value": "Azza Fahmy"}, "category": {"value": "ecommerce"},
                           "tagline": {"value": "An Enduring Symbol"}, "tone_of_voice": {"value": "luxury"},
                           "offerings": [{"name": "Rings"}, {"name": "Earrings"}],
                           "value_propositions": [{"value": "Handcrafted"}]}}
    plan = {"business_name": "Azza Fahmy", "days": 14,
            "items": [{"date": "2026-07-05", "platform": "instagram", "content_type": "reel",
                       "topic": "The Art of Craftsmanship", "hook": "Witness the artistry."}]}
    cp = tmp / "c.json"; cp.write_text(json.dumps(competitor), encoding="utf-8")
    pp = tmp / "p.json"; pp.write_text(json.dumps(profile), encoding="utf-8")
    lp = tmp / "l.json"; lp.write_text(json.dumps(plan), encoding="utf-8")
    return str(cp), str(pp), str(lp)


def test_dashboard_prints_everything(tmp_path):
    cp, pp, lp = _write(tmp_path)
    out = build_dashboard(cp, profile_path=pp, plan_path=lp, out_path=str(tmp_path / "d.html"))
    h = Path(out).read_text(encoding="utf-8")

    assert "Azza Fahmy" in h                                  # brand
    assert "ecommerce" in h and "luxury" in h                 # category + tone
    assert "23 social links vs peer avg 5.5" in h             # a SWOT strength verbatim
    assert "No WhatsApp" in h                                 # a SWOT weakness
    assert "your profile" in h and "peers: Dima" in h         # citations rendered (the moat)
    assert "VALIDATED" in h.upper()                           # claim-strength badge
    assert "Dima Jewellery" in h and "web peer rank 3" in h   # competitor + why-selected
    assert "33%" in h                                         # peer-fit score
    assert "Leverage social reach to open bookings" in h      # TOWS title (not the empty 'text')
    assert "الوضع الاستراتيجي · Your strategic stance" in h    # posture, now explained in plain words
    assert "The Art of Craftsmanship" in h                    # calendar topic
    assert "Witness the artistry" in h                        # calendar hook


def test_dashboard_is_self_contained(tmp_path):
    cp, pp, lp = _write(tmp_path)
    out = build_dashboard(cp, profile_path=pp, plan_path=lp, out_path=str(tmp_path / "d.html"))
    h = Path(out).read_text(encoding="utf-8")
    # No external CDN / web-font / script fetches — everything inline (opens & publishes anywhere).
    assert "http://" not in h.replace("http://www.w3.org", "")   # allow the SVG xmlns only
    assert "fonts.googleapis" not in h and "cdn." not in h
    assert "<script" not in h.lower()


def test_dashboard_degrades_without_optional_inputs(tmp_path):
    # Only the competitor result — no profile / plan / poster. Must still build a valid page.
    cp, _pp, _lp = _write(tmp_path)
    out = build_dashboard(cp, out_path=str(tmp_path / "d.html"))
    h = Path(out).read_text(encoding="utf-8")
    assert "Baseera" in h and "SWOT" in h


def test_dashboard_embeds_the_reel_video(tmp_path):
    # Owner: "everything you finish should show in the dashboard" — the reel plays IN the page,
    # base64-inlined so it stays self-contained (no external URL / separate media server).
    cp, pp, lp = _write(tmp_path)
    reel = tmp_path / "brand_reel.mp4"
    reel.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-reel-bytes")
    out = build_dashboard(cp, profile_path=pp, plan_path=lp, reel_path=str(reel),
                          out_path=str(tmp_path / "d.html"))
    h = Path(out).read_text(encoding="utf-8")
    assert "<video" in h and "data:video/mp4;base64," in h        # the reel plays in-page
    # CS-08 (2026-07-18): this reel has NO audit sidecar, so the creative section shows the honest
    # NEUTRAL caption — the strong "every hard claim traces to real evidence" guarantee (and the
    # logo/end-card promise) is gated on a passing audit, not on the file merely existing.
    assert "Creatives tied to your strategy" in h                 # neutral caption present
    assert "Every hard claim traces to real evidence" not in h    # strong guarantee NOT shown without an audit
    assert "http://" not in h.replace("http://www.w3.org", "")    # still self-contained


def test_creative_section_shows_with_only_a_reel(tmp_path):
    # A reel alone (poster failed/skipped) must still render the Creative section.
    cp, pp, lp = _write(tmp_path)
    reel = tmp_path / "brand_reel.mp4"
    reel.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    out = build_dashboard(cp, profile_path=pp, reel_path=str(reel), out_path=str(tmp_path / "d.html"))
    h = Path(out).read_text(encoding="utf-8")
    assert "Creative — tied to your strategy" in h and "<video" in h
    assert 'alt="poster"' not in h                                # no poster embedded, reel only
