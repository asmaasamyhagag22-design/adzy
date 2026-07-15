"""Ad Compliance Sheet — the client-facing deliverable derived from the audit trail.

Hermetic (MockCaller; no network/LLM). Proves the pure transform:
  * a grounded poster -> verdict PASS; every verified row carries the REAL source URL +
    the verbatim matched quote; claim-free copy is labeled 'no_checkable_claims';
  * a softened/dropped fabrication -> shown in the sheet's remediation rows (transparent,
    marked handled) and NEVER as a verified row;
  * an unsourced claim surviving to shipped copy -> verdict NEEDS_REVIEW (honest, not hidden);
  * malformed input -> None (ship without a sheet rather than fabricate one).
"""
from __future__ import annotations

import json

from business_profile.llm.caller import MockCaller
from grounding.compliance import build_compliance_sheet
from poster.audit import build_poster_audit
from poster.concept import _ConceptResponse, build_creative_concept

_ABOUT = "https://digilians.eg/about"


def _ef(value, quote, url):
    return {"value": value, "evidence": [{"block_id": "b", "page_url": url, "quote": quote,
            "extractor": "llm"}], "confidence": "high", "source_type": "extracted"}


_AR = {
    "name": _ef("ديجيليانس", "ديجيليانس", "https://digilians.eg/"),
    "languages": ["ar"],
    "tagline": _ef("الرواد الرقميون", "الرواد الرقميون", _ABOUT),
    "description": _ef("ندرّب 5000 شاب سنويًا في البرمجة", "ندرّب 5000 شاب سنويًا في البرمجة", _ABOUT),
    "offerings": [],
}


def _resp(**kw) -> _ConceptResponse:
    base = dict(
        audience="شباب", single_message="رسالة", core_benefit="مهارة", emotional_tone="طموح",
        visual_idea="youth coding under warm light",
        proof_points=["تدريب عملي"], headline="روّاد الرقمنة",
        subheadline="ندرّب 5000 شاب", cta="سجّل الآن",
    )
    base.update(kw)
    return _ConceptResponse(**base)


def _sheet_for(resp: _ConceptResponse, max_retries: int = 1) -> dict:
    concept = build_creative_concept(
        _AR, caller=MockCaller({"poster_concept_brief": resp}),
        enforce_grounding=True, max_retries=max_retries)
    audit = build_poster_audit(_AR, concept)
    sheet = build_compliance_sheet(audit)
    assert sheet is not None
    return sheet


def test_grounded_poster_yields_pass_sheet_with_verified_sourced_rows():
    sheet = _sheet_for(_resp())

    assert sheet["verdict"] == "PASS"
    assert sheet["asset_type"] == "poster"
    verified = [r for r in sheet["shipped_copy"] if r["status"] == "verified"]
    assert verified, "expected at least one verified claim row (روّاد / 5000)"
    # Every verified row carries a REAL source URL + the verbatim evidence quote.
    assert all((r["source_url"] or "").startswith("http") for r in verified)
    assert all(r["matched_quote"] for r in verified)
    assert any(r["source_url"] == _ABOUT for r in verified)
    # Claim-free copy (e.g. the CTA) is labeled — not silently omitted.
    clean = [r for r in sheet["shipped_copy"] if r["status"] == "no_checkable_claims"]
    assert clean
    # Summary counts are consistent with the rows.
    assert sheet["summary"]["claims_verified"] == len(verified)
    assert sheet["summary"]["claims_unverified"] == 0
    assert sheet["summary"]["claims_remediated"] == 0
    # The sheet states its own scoping honestly (reel excluded until gated).
    assert "imagery" in (sheet["coverage"] or {}).get("excluded_surfaces", {})  # reel gated 2026-07-12


def test_softened_fabrication_appears_as_remediation_row_never_verified():
    sheet = _sheet_for(_resp(headline="الأكبر منذ 1850"))

    rem = sheet["remediation"]
    assert rem, "the caught fabrication must be SHOWN, marked handled"
    softened = [r for r in rem if r["status"] == "softened"]
    assert softened and any("1850" in r["original_text"] for r in softened)
    assert all("reworded" in r["basis"] or "removed" in r["basis"] for r in rem)
    # The fabricated text never appears among the shipped (verified/clean) rows.
    shipped_blob = json.dumps(sheet["shipped_copy"], ensure_ascii=False)
    assert "1850" not in shipped_blob
    # Shipped copy is clean -> the sheet still PASSES (the gate worked).
    assert sheet["verdict"] == "PASS"
    assert sheet["summary"]["claims_remediated"] == len(rem)


def test_dropped_fabricated_chip_is_a_dropped_row():
    sheet = _sheet_for(_resp(proof_points=["تدريب عملي", "شهادة معتمدة دوليًا"]),
                       max_retries=0)

    dropped = [r for r in sheet["remediation"] if r["status"] == "dropped"]
    assert dropped and any("معتمدة" in r["original_text"] for r in dropped)
    assert all("removed before publish" in r["basis"] for r in dropped)


def test_unverified_surviving_claim_flags_needs_review():
    # Hand-crafted audit: an unsourced hard claim SURVIVED to shipped copy (post-gate this
    # shouldn't happen; the sheet must say so honestly rather than show a clean PASS).
    audit = {
        "asset_type": "poster", "gate": "evidence_ledger", "policy": "p", "coverage": {},
        "evidence_count": 3, "remediation": [],
        "final_copy": {"passes": False, "evidence_count": 3, "fields": [
            {"field": "headline", "text": "الأفضل في مصر", "grounded": False,
             "claims": [{"kind": "superlative", "token": "الأفضل", "sourced": False,
                         "source_url": None, "source_tier": None, "matched_quote": None,
                         "confidence": None, "copy_lang": "ar", "matched_lang": None,
                         "lang_mismatch": False}]},
        ]},
    }
    sheet = build_compliance_sheet(audit)
    assert sheet is not None
    assert sheet["verdict"] == "NEEDS_REVIEW"
    assert sheet["summary"]["claims_unverified"] == 1
    assert any(r["status"] == "unverified" for r in sheet["shipped_copy"])


def test_malformed_audit_returns_none_never_a_fabricated_sheet():
    assert build_compliance_sheet(None) is None
    assert build_compliance_sheet("nope") is None
    assert build_compliance_sheet({}) is None                       # no final_copy
    assert build_compliance_sheet({"final_copy": "bad"}) is None    # unusable trail


def test_an_EMPTY_audit_never_ships_a_green_PASS():
    """P6 #2 — the all([]) vacuity landing in the client-facing deliverable.

    `{"final_copy": {"fields": []}}` produced zero rows, hence zero unverified, hence
    `verdict: "PASS"` — a sheet that checked NOTHING certifying itself clean. Any upstream that
    fails to populate `fields` (a changed copy path, a serialisation miss) yielded a green PASS.
    Nothing checked = nothing certified.
    """
    sheet = build_compliance_sheet({"final_copy": {"fields": []}})
    assert sheet is not None                       # the shape is usable -> we still build a sheet
    assert sheet["shipped_copy"] == []             # ...that reviewed nothing...
    assert sheet["verdict"] == "NEEDS_REVIEW"      # ...so it must not say PASS


def test_a_claim_row_we_could_not_read_forces_review_instead_of_vanishing():
    """A claim we cannot parse is a claim we did NOT check. It used to `continue` — silently
    dropping out of the counts — and the sheet still said PASS."""
    sheet = build_compliance_sheet({"final_copy": {"fields": [
        {"field": "headline", "text": "Egypt's #1 clinic", "claims": ["not-a-dict"]},
    ]}})
    assert sheet["verdict"] == "NEEDS_REVIEW"
    assert sheet["summary"]["claims_unreadable"] == 1
