# -*- coding: utf-8 -*-
"""Visual authority: DNA decides, a preset only fills gaps, and both sides read one object.

THE DEFECT (run `poster_ebeae26f`, marasimltd.com, 2026-08-31). `brand.look.look_lock` collapsed a
BrandCreativeDNA into one of five presets by substring match, testing `_CLINICAL` before `_WARM`.
Marasim hit CLINICAL on one incidental "clean" — a word its DNA uses about TYPOGRAPHY — and the
image request received "cool clean colour palette, bright soft daylight, crisp and airy" for a
brand whose `dont_list` reads "Do not use a cool color palette (no blues, greens, or purples)."

The cross-brand cases below are the reason the fix is not "move _WARM first": that would repair
Marasim and leave the next brand containing "clean" broken in exactly the same way.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brand.visual_direction import (DNA_DRIVEN, INSUFFICIENT_VISUAL_AUTHORITY,
                                    PRESET_FALLBACK, VISUAL_DNA_CONFLICT,
                                    resolve_visual_direction)
from poster.visual_spec import (BRAND_PALETTE_DRIFT, UNAUTHORIZED_TEXT,
                                UNSUPPORTED_VISUAL_CONTEXT, compile_visual_spec,
                                route_finding_to_repair)

REPO = Path(__file__).resolve().parent.parent
DNA_PATH = REPO / "outputs" / "brandbooks" / "marasim_digital_solution_agency_dna.json"
BRAND_PATH = REPO / "outputs" / "webapp" / "brands" / "marasimltd_com.json"
_HAVE_MARASIM = DNA_PATH.is_file() and BRAND_PATH.is_file()

_COOL_WORDS = ("cool", "blue", "teal", "cyan", "green", "purple", "crisp and airy",
               "bright soft daylight")


class FakeDNA:
    """A BrandCreativeDNA shaped by hand, for the cross-brand matrix."""

    def __init__(self, **kw):
        self.color_usage = kw.get("color_usage", "")
        self.imagery_style = kw.get("imagery_style", "")
        self.mood = kw.get("mood", "")
        self.motifs = kw.get("motifs", "")
        self.signature_moves = kw.get("signature_moves", "")
        self.typographic_character = kw.get("typographic_character", "")
        self.do_list = kw.get("do_list", [])
        self.dont_list = kw.get("dont_list", [])


@pytest.fixture(scope="module")
def marasim():
    if not _HAVE_MARASIM:
        pytest.skip("Marasim artifacts not in this checkout")
    from brand.creative_dna import load_creative_dna
    dna = load_creative_dna(DNA_PATH)
    profile = json.loads(BRAND_PATH.read_text(encoding="utf-8")).get("_profile") or {}
    rvd = resolve_visual_direction(brand_dna=dna, profile=profile, dna_path=str(DNA_PATH),
                                   domain_key="marasimltd_com")
    return dna, profile, rvd


# --- §11 A: the OLD resolver still reproduces the known bad classification ----
def test_A_old_resolver_reproduces_the_clinical_inversion(marasim):
    from brand.look import look_lock
    dna, profile, _rvd = marasim
    old = look_lock(brand_dna=dna, profile=profile).lower()
    assert "cool" in old, "the old preset resolver no longer reproduces the defect"
    assert "no blues" in " ".join(dna.dont_list).lower()


# --- §11 B: the NEW resolver does not produce a cool palette -----------------
def test_B_new_resolver_produces_no_cool_palette(marasim):
    _dna, _profile, rvd = marasim
    look = rvd.look_sentence().lower()
    for w in _COOL_WORDS:
        assert w not in look, f"resolved look still contains {w!r}: {look}"


# --- §11 C: the real warm/orange/charcoal direction is preserved -------------
def test_C_new_direction_preserves_the_real_dna(marasim):
    _dna, _profile, rvd = marasim
    assert rvd.authority_status == DNA_DRIVEN
    assert {"orange", "golden", "sepia", "warm"} <= set(rvd.palette_positive)
    assert {"black", "charcoal"} & set(rvd.palette_positive)
    assert "#f04b1e" in [c.lower() for c in rvd.accent_colors]
    assert "black" in rvd.background_preferences
    assert "golden hour" in rvd.lighting
    assert {"egypt", "cairo", "giza"} <= set(rvd.geographic_context)
    assert "documentary" in rvd.photography_render_preference


# --- §11 D: negative constraints survive into the generation spec ------------
def test_D_negative_colour_constraints_reach_the_generation_spec(marasim):
    _dna, _profile, rvd = marasim
    spec = compile_visual_spec(rvd)
    pos, neg = spec.to_provider_directives()

    assert {"blue", "green", "purple"} <= set(spec.prohibited_colors)
    assert "NO " in neg and "blue" in neg
    assert "Do not use a cool color palette" in neg, "the DNA's own words must reach the provider"
    # §5: the positive direction must be present and not paraphrased into its opposite.
    low = pos.lower()
    assert "orange" in low and "golden hour" in low
    assert "#f04b1e" in low
    assert "cairo" in low or "giza" in low
    for w in ("cool", "crisp and airy"):
        assert w not in low


def test_D2_unsupported_geographic_context_is_prohibited(marasim):
    """The Gulf/Ramadan drift is excluded because the DNA names Egypt, not because I said so."""
    _dna, _profile, rvd = marasim
    negs = " ".join(rvd.negative_directives()).lower()
    assert "outside egypt" in negs
    assert "festive" in negs


# --- §11 E: verifier receives matching constraint identity -------------------
def test_E_verifier_constraints_come_from_the_same_direction(marasim):
    from poster.brand_constraints import constraint_set_from_direction
    _dna, _profile, rvd = marasim
    cs = constraint_set_from_direction(rvd)

    assert "#f04b1e" in [c.lower() for c in cs.approved_colors()]
    assert "blue" in cs.forbidden_terms()
    ids = {c.source_id for c in cs.constraints}
    assert ids == {rvd.resolution_hash}, "verifier constraints must carry the direction hash"

    spec = compile_visual_spec(rvd)
    assert spec.direction_hash == rvd.resolution_hash


def test_resolution_is_deterministic(marasim):
    """Both sides resolve independently; identical inputs must give an identical hash."""
    dna, profile, rvd = marasim
    again = resolve_visual_direction(brand_dna=dna, profile=profile, dna_path=str(DNA_PATH),
                                     domain_key="marasimltd_com")
    assert again.resolution_hash == rvd.resolution_hash


# --- §12 cross-brand: an incidental word must not capture the whole brand ----
@pytest.mark.parametrize("name,dna,expect_present,expect_absent", [
    ("warm + clean typography",
     FakeDNA(color_usage="warm golden and amber tones throughout",
             typographic_character="clean modern sans, clean white text",
             imagery_style="clean, warm documentary photography",
             dont_list=["Do not use a cool color palette (no blues or greens)."]),
     ("golden", "warm"), ("blue", "cool")),
    ("cool + clinical",
     FakeDNA(color_usage="cool clinical blues and clean whites",
             typographic_character="clean medical sans",
             imagery_style="sterile lab photography",
             dont_list=["Do not use warm oranges."]),
     ("blue",), ("orange",)),
    ("luxury + warm",
     FakeDNA(color_usage="deep black field with warm gold accents",
             typographic_character="refined premium serif",
             imagery_style="low-key luxury product photography",
             dont_list=["Do not use bright pastel colours."]),
     ("gold", "black"), ("blue",)),
    ("minimal + saturated accent",
     FakeDNA(color_usage="minimal white space with one saturated red accent",
             typographic_character="clean minimal grotesque",
             imagery_style="clean studio photography",
             dont_list=["Do not use green or purple."]),
     ("red",), ("green", "purple")),
    ("documentary + clean typography",
     FakeDNA(color_usage="warm sepia and ochre documentary grade",
             typographic_character="clean editorial sans",
             imagery_style="documentary street photography",
             dont_list=["Do not use cool teal grades."]),
     ("sepia", "warm"), ("teal",)),
])
def test_incidental_words_do_not_capture_the_brand(name, dna, expect_present, expect_absent):
    rvd = resolve_visual_direction(brand_dna=dna, profile={"visual": {"palette_hex": []}})
    look = rvd.look_sentence().lower()
    for w in expect_present:
        assert w in look, f"{name}: resolved look lost {w!r} -> {look}"
    for w in expect_absent:
        assert w not in look, f"{name}: resolved look wrongly contains {w!r} -> {look}"
    # Whatever the DNA actually prohibits must survive to the provider block. Asserted against
    # the brand's OWN dont_list rather than against `expect_absent`: a colour can be absent from
    # a look simply because the brand never mentions it, which is not a prohibition.
    neg = " ".join(rvd.negative_directives()).lower()
    for rule in dna.dont_list:
        assert rule.lower() in neg, f"{name}: dont_list rule did not reach provider: {rule}"


def test_a_brand_is_not_forced_into_one_bucket():
    """warm palette + clean typography + documentary imagery + premium composition, together."""
    dna = FakeDNA(color_usage="warm golden hour palette over a deep charcoal field",
                  typographic_character="clean premium editorial sans",
                  imagery_style="documentary photography, premium composition",
                  dont_list=["Do not use a cool color palette (no blues)."])
    rvd = resolve_visual_direction(brand_dna=dna, profile={})
    assert {"golden", "warm", "charcoal"} <= set(rvd.palette_positive)
    assert "clean premium editorial sans" in rvd.typography_role
    assert "documentary" in rvd.photography_render_preference
    assert "blue" in rvd.palette_prohibited
    assert "cool" not in rvd.look_sentence().lower()


# --- §13 contradiction, §14 fallback ----------------------------------------
def test_contradictory_dna_is_reported_not_silently_resolved():
    dna = FakeDNA(color_usage="a deep blue field with blue accents",
                  dont_list=["Do not use blue anywhere."])
    rvd = resolve_visual_direction(brand_dna=dna, profile={})
    assert rvd.conflicts, "a prescribed-and-prohibited colour must be reported"
    c = rvd.conflicts[0]
    assert c["type"] == VISUAL_DNA_CONFLICT
    assert "blue" in c["terms"]
    assert set(c["fields"]) == {"color_usage", "dont_list"}


def test_preset_property_that_contradicts_the_dna_is_discarded_loudly():
    dna = FakeDNA(color_usage="warm amber tones",
                  dont_list=["Do not use a cool color palette (no blues, greens, or purples)."])
    rvd = resolve_visual_direction(brand_dna=dna, profile={})
    discarded = [d["value"] for d in rvd.discarded_preset_properties]
    assert "clinical" in discarded, "the cool preset must be recorded as discarded, not applied"
    assert "cool" not in rvd.look_sentence().lower()


def test_authority_status_is_explicit_for_thin_and_absent_dna():
    thin = resolve_visual_direction(brand_dna=None,
                                    profile={"visual": {"palette_hex": ["#123456"]}})
    assert thin.authority_status == PRESET_FALLBACK
    assert thin.fallback_reason

    none_at_all = resolve_visual_direction(brand_dna=None, profile={})
    assert none_at_all.authority_status == INSUFFICIENT_VISUAL_AUTHORITY


def test_framework_hex_never_becomes_brand_authority():
    rvd = resolve_visual_direction(
        brand_dna=None, profile={"visual": {"palette_hex": ["#0d6efd", "#f04b1e"]}})
    assert "#0d6efd" not in rvd.primary_colors
    assert "#f04b1e" in rvd.primary_colors
    rejected = [ln for ln in rvd.source_lineage if ln["field"] == "rejected_framework_hex"]
    assert rejected and rejected[0]["value"] == "#0d6efd"


# --- §9/§10 repair routing ---------------------------------------------------
def test_an_ungrounded_finding_never_produces_a_repair_instruction(marasim):
    _dna, _profile, rvd = marasim
    spec = compile_visual_spec(rvd)
    out = route_finding_to_repair(
        {"grounded": False, "defect": "BRAND_CONSTRAINT_NOT_GROUNDED",
         "reason": "requires a dusty muted blue"}, spec)
    assert out["repair"] is False
    assert "ungrounded" in out["reason"]


def test_grounded_findings_route_to_targeted_instructions(marasim):
    _dna, _profile, rvd = marasim
    spec = compile_visual_spec(rvd)

    palette = route_finding_to_repair({"grounded": True, "defect": BRAND_PALETTE_DRIFT}, spec)
    assert palette["repair"] is True
    assert "#f04b1e" in palette["instruction"].lower()
    assert "blue" in palette["instruction"].lower()      # named as prohibited

    context = route_finding_to_repair(
        {"grounded": True, "defect": UNSUPPORTED_VISUAL_CONTEXT}, spec)
    assert "Cairo" in context["instruction"] or "Egypt" in context["instruction"]

    text = route_finding_to_repair({"grounded": True, "defect": UNAUTHORIZED_TEXT}, spec)
    assert "Text repair only" in text["instruction"]
    assert "palette" in text["instruction"]              # explicitly scoped OUT of palette

    for out in (palette, context, text):
        assert out["direction_hash"] == rvd.resolution_hash


# --- the residual defect, locked as a fact ----------------------------------
@pytest.mark.skipif(not _HAVE_MARASIM, reason="Marasim artifacts not present")
def test_a_supplied_colour_prohibition_is_enforced_on_the_pixels(marasim):
    """The model-based `on_brand_color` check cannot enforce a prohibition; counting can.

    MEASURED on verification render `poster_e3a5e608` (2026-08-31): the negative constraints
    reached the provider request, the model ignored them, and the vision verdict still returned
    on_brand_color=True — while 76.5% of the saturated frame was the blues/teals/greens this
    brand's `dont_list` forbids. Asking a language model whether a palette "matches" prose is a
    different question from whether a prohibited hue is present.
    """
    from poster.brand_constraints import (constraint_set_from_direction,
                                          prohibited_color_presence)
    _dna, _profile, rvd = marasim
    cs = constraint_set_from_direction(rvd)

    bad = REPO / "outputs" / "posters" / "poster_e3a5e608.png"
    good = (REPO / ".claude" / "worktrees" / "marasim-poster-design-eb8305" /
            "artifacts" / "marasim" / "marasim_poster.png")
    if not bad.is_file():
        pytest.skip("verification render not retained in this checkout")

    r = prohibited_color_presence(bad, cs)
    assert r["checked"] is True
    assert r["violates"] is True, "the cool-palette render must be caught"
    assert r["prohibited_fraction"] > 0.25
    assert r["lineage"], "a blocking colour finding must name its supplied constraint"
    assert r["lineage"][0]["source_field"] == "palette_prohibited"

    if good.is_file():
        ok = prohibited_color_presence(good, cs)
        assert ok["violates"] is False, "an on-brand warm poster must not be flagged"


def test_prohibition_check_is_inert_when_no_prohibition_was_supplied():
    """No supplied constraint => no finding. The checker may not invent one either."""
    from poster.brand_constraints import ConstraintSet, prohibited_color_presence
    out = prohibited_color_presence("anything.png", ConstraintSet())
    assert out["checked"] is False and out["violates"] is False


# --- the DNA cache key must identify the BRAND, not its display name ---------
def test_a_non_latin_brand_name_must_not_collide_on_one_shared_brandbook(tmp_path):
    """REPAIRED 2026-09-01 (was a strict xfail). Three stored brands all resolved to
    `outputs/brandbooks/brand_dna.json`, because `poster.pipeline._slug` was
    `re.sub(r"[^a-z0-9]+", "_", name.lower()) or "brand"` — which strips every Arabic character
    and leaves the empty-string fallback. So «مراسم للحلول الرقمية», «حلواني العبد» and
    «صيدلية النهدي» shared ONE brandbook, reading and overwriting each other.

    That is how the Marasim verification render `poster_bac9ec3a` was art-directed by a retail
    brand: its request carried "COLOUR PALETTE (mandatory): orange, blue, turquoise" — blue and
    turquoise being the exact hues Marasim's own DNA forbids — and a `dont_list` about a
    "Shop Now" button and a "discount number". Marasim's real brandbook was never opened.

    Identity is now the `domain_key`. The old slug survives only as
    `brandbook_identity.legacy_name_slug`, for finding the files it wrote; it addresses nothing.
    """
    from brand.brandbook_identity import canonical_path, domain_key_for, legacy_name_slug

    arabic = {"مراسم للحلول الرقمية": "https://marasimltd.com/",
              "حلواني العبد": "https://elabdfoods.com/",
              "صيدلية النهدي": "https://www.nahdionline.com/ar-sa"}

    # The defect, still true of the old function — which is why it may never address a file.
    assert {legacy_name_slug(n) for n in arabic} == {"brand"}

    keys = {domain_key_for({"final_url": u}) for u in arabic.values()}
    assert keys == {"marasimltd_com", "elabdfoods_com", "nahdionline_com"}
    paths = {canonical_path(k, cache_dir=tmp_path) for k in keys}
    assert len(paths) == 3, f"brand names must not share a cache key: {paths}"
    assert not any(p.name == "brand_dna.json" for p in paths)


def test_the_shared_brand_dna_bucket_can_never_be_addressed_again(tmp_path):
    """The bucket itself is unreachable: no domain_key can name it, and the sentinel is refused."""
    from brand.brandbook_identity import canonical_path, domain_key_for
    from brand.brandbook_identity import BrandbookIdentityError
    import pytest as _pytest

    assert canonical_path("marasimltd_com", cache_dir=tmp_path).name == "marasimltd_com_dna.json"
    # An empty/absent URL yields no identity at all rather than the "brand" bucket.
    assert domain_key_for({"final_url": "", "source_url": ""}) == ""
    with _pytest.raises(BrandbookIdentityError):
        canonical_path("", cache_dir=tmp_path)

