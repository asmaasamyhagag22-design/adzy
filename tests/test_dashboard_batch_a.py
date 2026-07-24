# -*- coding: utf-8 -*-
"""Batch A — confirmed client-facing dashboard defects (2026-07-18).

Each test asserts on the RENDERED HTML (the client surface), RED-first: it fails on the pre-fix
code and passes once the defect is closed. Hermetic.
"""
from __future__ import annotations

import json


def _render(tmp_path, competitor):
    cp = tmp_path / "acme_result.json"
    cp.write_text(json.dumps(competitor, ensure_ascii=False), encoding="utf-8")
    from dashboard.build import build_dashboard_html
    return build_dashboard_html(str(cp))


def test_xss_scheme_in_competitor_href_is_neutralized(tmp_path):
    """CS-16/23: a competitor/SERP URL with a javascript: (or data:) scheme must NOT survive into
    an href — html.escape does not stop scheme-based XSS."""
    competitor = {
        "subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
        "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                 "opportunities": [], "threats": []}, "tows": {},
        "competitors": [{"selection": {"name": "Evil", "website": "javascript:alert(1)",
                                       "peer_fit_score": 0.4, "why_selected": "x"},
                         "candidate": {"name": "Evil"}}],
        "trends": [{"title": "Click me", "url": "javascript:steal()"}],
    }
    h = _render(tmp_path, competitor)
    assert "javascript:" not in h, "a javascript: scheme reached the client HTML"
    assert "data:text/html" not in h


def test_audience_repr_does_not_leak_into_client_html(tmp_path):
    """CS-17: an enum / EvidencedField object must render its plain value, never its Python repr."""
    import enum

    class AudienceType(enum.Enum):
        MIXED = "mixed"
    competitor = {
        "subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
        "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                 "opportunities": [], "threats": []}, "tows": {},
    }
    plan = {"objectives": [{"objective": "OUTCOME_LEADS"}],
            "base_geo": {"mode": "national"},
            "base_persona": {"gender": AudienceType.MIXED,
                             "age_range": {"value": "25-34"}}}
    cp = tmp_path / "acme_result.json"
    cp.write_text(json.dumps(competitor, ensure_ascii=False), encoding="utf-8")
    mp = tmp_path / "acme_media_plan.json"
    # enums aren't JSON-serializable; write via a builder call instead
    from dashboard.build import build_dashboard_html
    # feed the enum through a python dict the loader gets as-is (simulate the live object path)
    import dashboard.build as B
    orig = B._load
    B._load = lambda p: (competitor if str(p).endswith("result.json") else plan)  # type: ignore
    try:
        h = build_dashboard_html(str(cp), media_plan_path=str(mp))
    finally:
        B._load = orig
    assert "AudienceType." not in h and "EvidencedField(" not in h


def test_peer_fit_percent_not_shown_for_a_serp_rank_only_peer(tmp_path):
    """CS-09/100: a web peer whose score is just 1/SERP-rank must NOT render as 'N% · peer fit'
    (that's not a measured fit). The `basis` flag exists to stop exactly this."""
    competitor = {
        "subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
        "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                 "opportunities": [], "threats": []}, "tows": {},
        "competitors": [{"selection": {"name": "WebPeer", "website": "https://wp/",
                                       "peer_fit_score": 1.0, "basis": "search_rank",
                                       "breakdown": {}, "why_selected": "web peer rank 1"},
                         "candidate": {"name": "WebPeer"}}],
    }
    h = _render(tmp_path, competitor)
    # for a search_rank-only peer the value must render as a rank ("#1"), never as "100% peer fit"
    assert "#1" in h                                   # the honest rank
    assert "comp-fit-v\">100%" not in h                # NOT a fabricated fit percentage


def test_moat_guarantee_not_printed_without_a_passing_audit_sidecar(tmp_path):
    """CS-08: the 'every hard claim traces to real evidence' guarantee must be gated on the poster's
    audit verdict, not on the poster file merely existing."""
    competitor = {
        "subject_url": "https://x/", "profile": {"name": {"value": "Acme"}},
        "swot": {"mode": "competitive", "strengths": [], "weaknesses": [],
                 "opportunities": [], "threats": []}, "tows": {},
    }
    cp = tmp_path / "acme_result.json"
    cp.write_text(json.dumps(competitor, ensure_ascii=False), encoding="utf-8")
    # a poster file with NO audit sidecar (the 0/10 case)
    poster = tmp_path / "acme_poster.png"
    poster.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)   # minimal bytes; _data_uri just base64s
    from dashboard.build import build_dashboard_html
    h = build_dashboard_html(str(cp), poster_path=str(poster))
    # the strong guarantee sentence must NOT appear when no passing audit backs it
    assert "Every hard claim traces to real evidence" not in h


def test_archive_answer_support_does_not_fail_open_cross_script():
    """CS-10/18: a cross-script answer that shares NO number and NO latin token with its cited
    excerpts must NOT be auto-'supported' (the old code returned True for any cross-script pair,
    the dominant path since the ask UI is Arabic and much of the archive is Latin)."""
    from dashboard.archive_qa import _answer_supported
    # fabricated Arabic answer, English excerpt, zero shared signal -> UNSUPPORTED
    assert _answer_supported("النظام يضمن أرباحاً مؤكدة للجميع",
                             ["Our training covers Python and data pipelines."]) is False
    # a genuine cross-lingual answer that carries a shared NUMBER stays supported (CS-18: numbers
    # are a real support signal, previously invisible to the token check)
    assert _answer_supported("التأسيس كان سنة 1998 في القاهرة",
                             ["The institute was founded in 1998."]) is True
    # a shared latin proper noun also supports across scripts
    assert _answer_supported("نقدّم دورة Python للمبتدئين",
                             ["The Python course runs monthly."]) is True
