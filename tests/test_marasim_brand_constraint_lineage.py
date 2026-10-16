# -*- coding: utf-8 -*-
"""Marasim regression: a verifier enforces supplied constraints and invents none.

THE MEASURED INCIDENT (live run `poster_ebeae26f`, 2026-08-31). Vision QA blocked the poster
three times for violating a "dusty blue" brand colour guideline. marasimltd.com has no such
rule anywhere: its DNA prescribes "a vibrant, fiery orange for accents", its `dont_list`
forbids cool colours outright, and the site's measured accent is #F04B1E. Three paid renders
were spent chasing a constraint that inverted the brand.

These tests run against the REAL stored artifacts, not fixtures, so they fail if the brandbook
or the profile drifts away from what the audit measured.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from poster.brand_constraints import (BRAND_CONSTRAINT_NOT_GROUNDED, build_constraint_set,
                                      check_cited_constraint)

REPO = Path(__file__).resolve().parent.parent
DOMAIN_KEY = "marasimltd_com"
BRAND_PATH = REPO / "outputs" / "webapp" / "brands" / f"{DOMAIN_KEY}.json"
# CANONICAL, not the legacy name-slug file. Addressing the brandbook by display name is the
# 2026-09-01 identity defect: «مراسم للحلول الرقمية» slugged to "brand", so this brand read Al
# Nahdi's DNA out of the shared `brand_dna.json`. Resolving by domain_key here means these
# lineage tests fail if identity ever regresses, instead of quietly passing on a stale file.
DNA_PATH = REPO / "outputs" / "brandbooks" / f"{DOMAIN_KEY}_dna.json"

pytestmark = pytest.mark.skipif(
    not (DNA_PATH.is_file() and BRAND_PATH.is_file()),
    reason="Marasim brandbook/brand document not present in this checkout")


@pytest.fixture(scope="module")
def marasim():
    from brand.brandbook_identity import resolve_brandbook
    from brand.creative_dna import load_creative_dna
    profile = json.loads(BRAND_PATH.read_text(encoding="utf-8")).get("_profile") or {}
    # Resolve the way the pipeline does — by domain_key — and assert it landed on Marasim's own
    # artifact rather than anything a display name could have pointed at.
    lookup = resolve_brandbook(profile, DOMAIN_KEY,
                               cache_dir=str(REPO / "outputs" / "brandbooks"))
    assert lookup.exists and lookup.path == DNA_PATH, lookup.reason
    dna = load_creative_dna(DNA_PATH)
    assert dna.domain_key == DOMAIN_KEY, "the artifact does not claim to be Marasim"
    cs = build_constraint_set(brand_dna=dna, dna_path=str(DNA_PATH),
                              profile=profile, profile_path=str(BRAND_PATH))
    return dna, profile, cs


def test_marasims_own_dna_prescribes_orange_not_blue(marasim):
    """The authority the verifier was handed says the opposite of what it claimed."""
    dna, _profile, _cs = marasim
    color = (dna.color_usage or "").lower()
    assert "orange" in color
    assert "no blues" in " ".join(dna.dont_list).lower()


def test_f04b1e_is_recognised_as_an_approved_brand_colour(marasim):
    _dna, _profile, cs = marasim
    approved = [c.lower() for c in cs.approved_colors()]
    assert "#f04b1e" in approved, f"brand accent missing from approved set: {approved}"
    c = cs.find("#f04b1e")
    assert c.polarity == "allow"
    assert c.source_type in ("BusinessProfile", "BrandCreativeDNA")
    assert c.source_field and c.source_id, "an approved colour must carry its lineage"


def test_bootstrap_blue_is_never_brand_authority(marasim):
    """#0d6efd was captured off computed styles; a framework default is not a brand decision."""
    _dna, _profile, cs = marasim
    assert "#0d6efd" not in [c.lower() for c in cs.approved_colors()]
    c = cs.find("#0d6efd")
    if c is not None:                      # recorded, but as NOT authority
        assert c.polarity == "forbid"


def test_a_dusty_blue_requirement_cannot_be_grounded(marasim):
    """The exact verdict string from the incident must be rejected as ungrounded."""
    _dna, _profile, cs = marasim
    reason = ("The CTA button is orange, which violates the brand's specific dusty blue color "
              "guideline for this element.")
    out = check_cited_constraint(reason, cs)
    assert out["grounded"] is False
    assert out["defect"] == BRAND_CONSTRAINT_NOT_GROUNDED
    assert "blue" in out["ungrounded_terms"]


def test_the_other_two_wordings_from_the_incident_are_also_rejected(marasim):
    """The model restated the invented rule three different ways; all three must fail."""
    _dna, _profile, cs = marasim
    for reason in (
        "The primary call-to-action button uses an orange accent color instead of the required "
        "dusty blue brand color.",
        "The CTA button is orange, which violates the brand's specific color guidelines "
        "requiring a dusty, muted blue.",
    ):
        out = check_cited_constraint(reason, cs)
        assert out["grounded"] is False, reason
        assert out["defect"] == BRAND_CONSTRAINT_NOT_GROUNDED


def test_a_real_supplied_constraint_still_grounds_and_carries_lineage(marasim):
    """The rule is not "never block" — a SUPPLIED constraint must still be enforceable."""
    _dna, _profile, cs = marasim
    out = check_cited_constraint(
        "The background is cool and grey; the required orange brand color is absent.", cs)
    assert out["grounded"] is True
    assert out["defect"] == ""
    assert out["lineage"], "a grounded verdict must name the artifact that authorised it"
    first = out["lineage"][0]
    for key in ("value", "source_artifact", "source_type", "source_field", "source_id"):
        assert first.get(key), f"lineage missing {key}"


def test_every_constraint_carries_full_lineage(marasim):
    _dna, _profile, cs = marasim
    assert cs.constraints
    for c in cs.constraints:
        assert c.value and c.source_artifact and c.source_type and c.source_field and c.source_id


def test_the_dna_forbids_the_cool_palette_the_look_bucket_injected(marasim):
    """GENERATION_DNA_DRIFT, locked as a fact rather than a memory.

    `brand.look.look_lock` collapses the DNA to one of five canned strings by substring match.
    Marasim hits CLINICAL on the single word "clean" — which its DNA uses about TYPOGRAPHY and
    3D renders — and CLINICAL is checked before WARM, so two direct warm signals ("golden",
    "natural") lose. The resulting look string injects "cool clean colour palette" into the
    image request for a brand whose DNA forbids exactly that.
    """
    from brand.look import _CLINICAL, _WARM, _dna_blob, look_lock
    dna, profile, _cs = marasim
    blob = _dna_blob(dna, profile).lower()

    assert [k for k in _CLINICAL if k in blob] == ["clean"]
    assert set(k for k in _WARM if k in blob) >= {"golden"}

    lock = look_lock(brand_dna=dna, profile=profile).lower()
    forbidden = " ".join(dna.dont_list).lower()
    assert "no blues" in forbidden
    # This is the defect, asserted as present so the fix flips it deliberately.
    assert "cool" in lock, "look_lock no longer injects a cool palette — update this test"
    assert "warm" not in lock and "golden" not in lock


def test_vision_qa_does_not_block_on_an_ungrounded_brand_rule(marasim, monkeypatch):
    """End-to-end through `poster_vision_qa`: the incident verdict must stop blocking.

    The model still returns on_brand_color=False with its invented reason; the gate checks the
    citation against what the brand supplied, finds no such rule, and refuses to turn it into a
    brand-fidelity failure. The reason text is preserved — the judgment is recorded, not erased.
    """
    import poster.vision_qa as vq
    dna, profile, _cs = marasim

    class _Resp:
        has_latin_text = False; cta_clipped = False; on_brand_color = False
        candid_violation = False; logo_ok = True; single_focal = True
        strong_typography = True; cta_prominent = True; script_wellformed = True
        invented_product = False; third_party_mark = False; placement_logical = True
        score = 8; overall_pass = False
        reason = ("The CTA button is orange, which violates the brand's specific dusty blue "
                  "color guideline for this element.")
        text_lines = ["مراسم"]; garbled_text = False; product_count = 0

    # The seam is the caller itself: `resp, _usage = caller(system, user, _QAResponse, ...)`.
    def _caller(_system, _user, _schema, **_kw):
        return _Resp(), None

    v = vq.poster_vision_qa(b"\x89PNG\r\n\x1a\n", caller=_caller, brand_dna=dna,
                            profile=profile, arabic=True)
    assert v.checked, "the stub caller should have produced a real verdict"

    assert v.on_brand_color is True, "an ungrounded citation must not be a brand failure"
    lin = v.brand_constraint_lineage or {}
    assert lin.get("defect") == BRAND_CONSTRAINT_NOT_GROUNDED
    assert "blue" in lin.get("ungrounded_terms", [])
    assert "dusty blue" in (v.reason or "").lower(), "the judgment itself must still be recorded"
