# -*- coding: utf-8 -*-
"""ONE resolved visual authority per brand — consumed by generation AND by the verifier.

WHY THIS REPLACES THE PRESET CLASSIFIER (measured, run `poster_ebeae26f`, marasimltd.com,
2026-08-31). `brand.look.look_lock` collapsed a whole BrandCreativeDNA into one of five canned
strings by substring match on a concatenated blob, and tested `_CLINICAL` before `_WARM`. Marasim
hit CLINICAL on a single incidental "clean" — a word its DNA uses about TYPOGRAPHY ("clean white
for text") and 3-D renders — so two direct palette signals ("golden", "natural") lost, and the
image request received:

    "cool clean colour palette, bright soft daylight, crisp and airy"

for a brand whose DNA says "a vibrant, fiery orange for accents", "deep black/charcoal field",
"warm, dusty… sepia, golden-hour", and whose `dont_list` says literally "Do not use a cool color
palette (no blues, greens, or purples)." The preset INVERTED the brand it was meant to express.

THE ARCHITECTURAL DEFECT, stated plainly: a brand was forced to be exactly one bucket. Marasim is
warm palette AND clean typography AND documentary imagery AND premium composition; no single
preset can hold that, and picking one by keyword order silently discards the rest.

SO THE MODEL CHANGES SHAPE. A preset no longer CLASSIFIES a brand — it FILLS DIMENSIONS the DNA
left empty, per dimension, and it may never contradict an explicit DNA constraint. Ordering the
keyword tuples differently would have fixed Marasim and left the defect in place for the next
brand whose DNA happens to contain "clean".

Generation and verification then read the SAME resolved object, so a verifier can only enforce
what generation was actually given.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

# --- authority status -------------------------------------------------------
DNA_DRIVEN = "DNA_DRIVEN"
DNA_PLUS_FALLBACK = "DNA_PLUS_FALLBACK"
PRESET_FALLBACK = "PRESET_FALLBACK"
INSUFFICIENT_VISUAL_AUTHORITY = "INSUFFICIENT_VISUAL_AUTHORITY"

#: Raised as a typed finding, never resolved silently by keyword order.
VISUAL_DNA_CONFLICT = "VISUAL_DNA_CONFLICT"
#: A visual context nothing in the brand's authority supports (e.g. Gulf/Ramadan styling
#: for a brand whose DNA names Egypt).
UNSUPPORTED_VISUAL_CONTEXT = "UNSUPPORTED_VISUAL_CONTEXT"

_COOL_TERMS = ("blue", "navy", "teal", "cyan", "turquoise", "green", "mint", "olive",
               "purple", "violet", "lilac", "lavender")
_WARM_TERMS = ("orange", "amber", "gold", "golden", "sepia", "warm", "rust", "terracotta",
               "ochre", "brown", "red", "crimson", "fiery")
_DARK_TERMS = ("black", "charcoal", "dark", "deep", "night", "low-key")

# Colour NOUNS only. "deep", "warm", "fiery", "dusty" describe a colour; they are not one, and
# an accent list made of modifiers cannot be checked against a hex.
_COLOR_NOUNS = ("orange", "amber", "gold", "golden", "sepia", "ochre", "terracotta", "rust",
                "brown", "red", "crimson", "black", "charcoal", "white", "cream", "beige",
                "grey", "gray", "blue", "navy", "teal", "cyan", "turquoise", "green", "mint",
                "olive", "purple", "violet", "lilac", "lavender", "pink", "magenta", "yellow")

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")

# Framework defaults are not brand decisions. Bootstrap ships these on any page that loads it;
# the marasimltd.com scrape captured #0d6efd ahead of the brand's real accent.
_FRAMEWORK_HEXES = {
    "#0d6efd", "#6610f2", "#6f42c1", "#d63384", "#dc3545", "#fd7e14",
    "#ffc107", "#198754", "#20c997", "#0dcaf0", "#212529", "#f8f9fa",
}

# Place names worth pinning as geographic context when the DNA names them.
_PLACES = ("egypt", "cairo", "giza", "alexandria", "qatar", "doha", "saudi", "riyadh", "jeddah",
           "dubai", "abu dhabi", "uae", "kuwait", "beirut", "lebanon", "jordan", "amman",
           "morocco", "tunisia", "iraq", "oman", "bahrain")

# Gap-fill values ONLY. Each is a per-dimension default, not a brand identity.
_PRESET_DIMENSIONS = {
    "lighting": "even, controlled commercial lighting",
    "lens": "35mm lens",
    "grade": "clean digital grade",
    "composition": "single clear focal point with generous negative space",
}
_GLOBAL_DEFAULT_LOOK = ("cinematic lighting, consistent studio commercial look, 35mm lens, "
                        "clean digital grade")


@dataclass
class Lineage:
    """Where one resolved value came from."""

    field_name: str
    value: Any
    source_type: str          # BrandCreativeDNA | BusinessProfile | CreativeContract | Preset
    source_field: str
    source_id: str
    source_artifact: str = ""
    quote: str = ""

    def as_dict(self) -> dict:
        return {"field": self.field_name, "value": self.value, "source_type": self.source_type,
                "source_field": self.source_field, "source_id": self.source_id,
                "source_artifact": self.source_artifact, "quote": self.quote[:220]}


@dataclass
class ResolvedVisualDirection:
    """The single visual authority for one brand's render — generation and verifier share it."""

    domain_key: str = ""
    dna_source_id: str = ""
    dna_source_artifact: str = ""

    palette_positive: list[str] = field(default_factory=list)
    palette_prohibited: list[str] = field(default_factory=list)
    primary_colors: list[str] = field(default_factory=list)
    accent_colors: list[str] = field(default_factory=list)
    background_preferences: str = ""
    lighting: str = ""
    imagery_style: str = ""
    geographic_context: list[str] = field(default_factory=list)
    material_style_cues: str = ""
    typography_role: str = ""
    photography_render_preference: str = ""
    positive_style_constraints: list[str] = field(default_factory=list)
    negative_style_constraints: list[str] = field(default_factory=list)
    explicit_dont_list: list[str] = field(default_factory=list)

    fallback_preset: str = ""
    fallback_reason: str = ""
    discarded_preset_properties: list[dict] = field(default_factory=list)

    authority_status: str = INSUFFICIENT_VISUAL_AUTHORITY
    conflicts: list[dict] = field(default_factory=list)
    source_lineage: list[dict] = field(default_factory=list)
    resolution_hash: str = ""

    # ---- consumers -------------------------------------------------------
    def look_sentence(self) -> str:
        """The per-brand LOOK string, ASSEMBLED from resolved fields rather than picked.

        Replaces `look_lock`'s bucket pick at the poster/reel prompt seam. Deterministic for
        the same resolution, which is what makes one brand's assets share a camera.
        """
        bits: list[str] = ["cinematic lighting"]
        if self.palette_positive:
            bits.append(f"{', '.join(self.palette_positive[:4])} colour palette")
        if self.lighting:
            bits.append(self.lighting)
        if self.background_preferences:
            bits.append(self.background_preferences)
        bits.append(_PRESET_DIMENSIONS["lens"])
        bits.append(_PRESET_DIMENSIONS["grade"])
        return ", ".join(b for b in bits if b)

    def negative_directives(self) -> list[str]:
        """Provider-facing prohibitions compiled from the brand's OWN explicit constraints.

        `dont_list` previously reached `poster/art_director.py` only, so the image request never
        learned that this brand forbids cool colours — the exact fact that would have made the
        clinical preset impossible to apply and "required dusty blue" impossible to assert.
        """
        out: list[str] = []
        if self.palette_prohibited:
            out.append("NO " + ", ".join(sorted(set(self.palette_prohibited))) +
                       " anywhere in the image, including shadows and background")
        out.extend(self.negative_style_constraints)
        if self.geographic_context:
            out.append("NO visual context from outside " +
                       "/".join(c.title() for c in self.geographic_context) +
                       " — no unrelated cultural costume, architecture or festive styling")
        return [o for o in out if o]

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        return d


def _sha(obj: Any) -> str:
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _terms_in(text: str, vocab) -> list[str]:
    """Colour terms present in `text`, matching simple plurals.

    "no blues, greens, or purples" and "cool clinical blues" both name colours; an exact
    word-boundary match saw neither, so a prohibition could be stated and never collected.
    """
    low = (text or "").lower()
    return [t for t in vocab if re.search(rf"\b{re.escape(t)}e?s?\b", low)]


def _sentence_with(text: str, term: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+", text or ""):
        if re.search(rf"\b{re.escape(term)}\b", s, re.I):
            return s.strip()
    return (text or "")[:200]


def resolve_visual_direction(*, brand_dna: Any = None,
                             profile: Optional[Mapping[str, Any]] = None,
                             dna_path: str = "", profile_path: str = "",
                             domain_key: str = "") -> ResolvedVisualDirection:
    """Build the brand's visual authority. DNA first; a preset only fills what DNA left empty."""
    rvd = ResolvedVisualDirection(domain_key=domain_key)
    lineage: list[Lineage] = []

    dna_id = ""
    if brand_dna is not None:
        dna_id = _sha(getattr(brand_dna, "__dict__", None) or str(brand_dna))
    rvd.dna_source_id = dna_id
    rvd.dna_source_artifact = dna_path or ("BrandCreativeDNA" if brand_dna is not None else "")

    def g(attr: str) -> str:
        return str(getattr(brand_dna, attr, "") or "") if brand_dna is not None else ""

    color_usage, imagery, mood = g("color_usage"), g("imagery_style"), g("mood")
    motifs, signature = g("motifs"), g("signature_moves")
    typo = g("typographic_character")
    do_list = [s for s in (getattr(brand_dna, "do_list", None) or []) if s]
    dont_list = [s for s in (getattr(brand_dna, "dont_list", None) or []) if s]

    # --- 1. explicit DNA constraints -------------------------------------
    # PROHIBITIONS FIRST: they decide what a later gap-fill is allowed to add.
    prohibited: list[str] = []
    for rule in dont_list:
        low = rule.lower()
        hits = _terms_in(rule, _COOL_TERMS + _WARM_TERMS + _DARK_TERMS)
        if "cool" in low and "palette" in low:
            hits = list(dict.fromkeys(hits + list(_COOL_TERMS)))
        for t in hits:
            if t not in prohibited:
                prohibited.append(t)
                lineage.append(Lineage("palette_prohibited", t, "BrandCreativeDNA",
                                       "dont_list", dna_id, rvd.dna_source_artifact, rule))
    rvd.palette_prohibited = prohibited
    rvd.explicit_dont_list = list(dont_list)
    rvd.negative_style_constraints = list(dont_list)
    for rule in dont_list:
        lineage.append(Lineage("negative_style_constraints", rule, "BrandCreativeDNA",
                               "dont_list", dna_id, rvd.dna_source_artifact, rule))

    # EVERY colour term the DNA prescribes, not just the warm ones. Collecting only warm/dark
    # would have made a genuinely cool brand (a pharmacy, a lab) unable to state its own palette,
    # and would have hidden a prescribed-AND-prohibited colour from the conflict check.
    positive: list[str] = []
    contradicted: list[str] = []
    for t in _terms_in(color_usage, _WARM_TERMS + _DARK_TERMS + _COOL_TERMS):
        if t in prohibited:
            # PRESCRIBED AND PROHIBITED BY THE SAME DNA. Dropping it here would be exactly the
            # silent keyword-order resolution this module exists to end (§13), so it is reported.
            if t not in contradicted:
                contradicted.append(t)
            continue
        if t in positive:
            continue
        positive.append(t)
        lineage.append(Lineage("palette_positive", t, "BrandCreativeDNA", "color_usage",
                               dna_id, rvd.dna_source_artifact, _sentence_with(color_usage, t)))
    rvd.palette_positive = positive

    # Accents vs field, read from the CLAUSE that names them, not the sentence. Marasim states
    # both roles in one sentence — "a deep black/charcoal field, a vibrant, fiery orange for
    # accents and gradients, and clean white for text" — so sentence-level matching tagged the
    # field colour as an accent too. Commas are the role boundary here.
    for t in positive:
        sent = _sentence_with(color_usage, t).lower()
        clause = next((c for c in re.split(r"[,;]", sent)
                       if re.search(rf"\b{re.escape(t)}\b", c)), sent)
        # Only colour NOUNS are accents; "deep"/"warm"/"fiery" are modifiers of one.
        if ("accent" in clause and t in _COLOR_NOUNS and t not in rvd.accent_colors):
            rvd.accent_colors.append(t)
        if any(w in sent for w in ("field", "background", "backdrop")) and t in _DARK_TERMS:
            rvd.background_preferences = rvd.background_preferences or \
                f"deep {t} field background"
            lineage.append(Lineage("background_preferences", rvd.background_preferences,
                                   "BrandCreativeDNA", "color_usage", dna_id,
                                   rvd.dna_source_artifact, _sentence_with(color_usage, t)))

    # Lighting, from what the DNA says about light — not from a bucket.
    for cue in ("golden hour", "golden-hour", "natural, dramatic light", "dramatic light",
                "overcast", "low-key", "soft daylight", "hazy"):
        if cue in (imagery + " " + color_usage + " " + mood).lower():
            rvd.lighting = cue.replace("-", " ")
            lineage.append(Lineage("lighting", rvd.lighting, "BrandCreativeDNA",
                                   "imagery_style", dna_id, rvd.dna_source_artifact,
                                   _sentence_with(imagery, cue.split()[0])))
            break

    if imagery:
        rvd.imagery_style = imagery
        lineage.append(Lineage("imagery_style", imagery, "BrandCreativeDNA", "imagery_style",
                               dna_id, rvd.dna_source_artifact, imagery))
        low = imagery.lower()
        modes = []
        if "photograph" in low or "documentary" in low:
            modes.append("documentary photography")
        if "3d" in low or "render" in low:
            modes.append("conceptual 3D render")
        rvd.photography_render_preference = " or ".join(modes)

    places = _terms_in(imagery + " " + color_usage + " " + mood + " " + signature, _PLACES)
    if places:
        rvd.geographic_context = places
        lineage.append(Lineage("geographic_context", places, "BrandCreativeDNA",
                               "imagery_style", dna_id, rvd.dna_source_artifact,
                               _sentence_with(imagery, places[0])))

    if typo:
        rvd.typography_role = typo
        lineage.append(Lineage("typography_role", typo, "BrandCreativeDNA",
                               "typographic_character", dna_id, rvd.dna_source_artifact, typo))
    cues = " ".join(x for x in (motifs, signature) if x)
    if cues:
        rvd.material_style_cues = cues
        lineage.append(Lineage("material_style_cues", cues, "BrandCreativeDNA",
                               "motifs+signature_moves", dna_id, rvd.dna_source_artifact, cues))
    if do_list:
        rvd.positive_style_constraints = list(do_list)
        for rule in do_list:
            lineage.append(Lineage("positive_style_constraints", rule, "BrandCreativeDNA",
                                   "do_list", dna_id, rvd.dna_source_artifact, rule))

    # --- 2. explicit brand/profile visual facts ---------------------------
    visual = dict((profile or {}).get("visual") or {}) if profile else {}
    pal = [str(h).lower() for h in (visual.get("palette_hex") or []) if str(h).startswith("#")]
    prof_id = _sha(pal)
    for hexv in pal:
        if hexv in _FRAMEWORK_HEXES:
            lineage.append(Lineage("rejected_framework_hex", hexv, "BusinessProfile",
                                   "visual.palette_hex", prof_id, profile_path,
                                   "CSS framework default — not brand authority"))
            continue
        rvd.primary_colors.append(hexv)
        lineage.append(Lineage("primary_colors", hexv, "BusinessProfile", "visual.palette_hex",
                               prof_id, profile_path, ""))
    prim = str(visual.get("primary_color") or "").lower()
    if prim.startswith("#") and prim not in _FRAMEWORK_HEXES and prim not in rvd.accent_colors:
        rvd.accent_colors.insert(0, prim)
        lineage.append(Lineage("accent_colors", prim, "BusinessProfile", "visual.primary_color",
                               prof_id, profile_path, ""))

    # --- 3/4. preset fills GAPS ONLY, and never contradicts the DNA -------
    filled: list[str] = []
    for dim, value in _PRESET_DIMENSIONS.items():
        if dim == "lighting" and rvd.lighting:
            continue
        if dim not in ("lighting",):
            continue
        contradicts = [t for t in _terms_in(value, _COOL_TERMS) if t in rvd.palette_prohibited]
        if contradicts:
            rvd.discarded_preset_properties.append(
                {"dimension": dim, "value": value, "discarded_because": contradicts})
            continue
        rvd.lighting = value
        filled.append(dim)
        lineage.append(Lineage("lighting", value, "Preset", dim, "preset_v1", "", ""))

    # A preset property that names a prohibited colour is DISCARDED, loudly. This is the exact
    # inversion that shipped: the clinical preset's "cool clean colour palette" against a DNA
    # that forbids cool colours.
    for name, text in (("clinical", "cool clean colour palette bright soft daylight"),
                       ("warm", "warm colour palette golden hour glow")):
        clash = [t for t in _terms_in(text, _COOL_TERMS + _WARM_TERMS)
                 if t in rvd.palette_prohibited]
        # "cool" is a temperature word, not a colour name, so a term-overlap test alone misses
        # the exact preset that shipped: "cool clean colour palette" contains no colour NAME.
        if "cool" in text and any(t in rvd.palette_prohibited for t in _COOL_TERMS):
            clash = clash or ["cool palette"]
        if clash:
            rvd.discarded_preset_properties.append(
                {"dimension": "look_preset", "value": name, "discarded_because": clash})

    # --- conflicts inside the DNA itself ----------------------------------
    both = (sorted(set(rvd.palette_positive) & set(rvd.palette_prohibited))
            or sorted(contradicted))
    if both:
        rvd.conflicts.append({"type": VISUAL_DNA_CONFLICT, "fields": ["color_usage", "dont_list"],
                              "terms": both,
                              "detail": "the same colour is prescribed and prohibited"})

    # --- status ------------------------------------------------------------
    has_dna = bool(rvd.palette_positive or rvd.imagery_style or rvd.palette_prohibited)
    if has_dna and not filled:
        rvd.authority_status = DNA_DRIVEN
    elif has_dna:
        rvd.authority_status = DNA_PLUS_FALLBACK
        rvd.fallback_preset = "preset_v1"
        rvd.fallback_reason = f"DNA left {', '.join(filled)} unspecified"
    elif rvd.primary_colors:
        rvd.authority_status = PRESET_FALLBACK
        rvd.fallback_preset = "preset_v1"
        rvd.fallback_reason = "no BrandCreativeDNA; profile palette only"
        rvd.lighting = rvd.lighting or _PRESET_DIMENSIONS["lighting"]
    else:
        rvd.authority_status = INSUFFICIENT_VISUAL_AUTHORITY
        rvd.fallback_preset = "global_default"
        rvd.fallback_reason = "no DNA and no profile palette"

    rvd.source_lineage = [ln.as_dict() for ln in lineage]
    rvd.resolution_hash = _sha({
        "pos": rvd.palette_positive, "neg": rvd.palette_prohibited,
        "prim": rvd.primary_colors, "acc": rvd.accent_colors,
        "bg": rvd.background_preferences, "light": rvd.lighting,
        "img": rvd.imagery_style, "geo": rvd.geographic_context,
        "status": rvd.authority_status,
    })
    return rvd


def legacy_look(*, brand_dna: Any = None, profile: Optional[dict] = None) -> str:
    """The old preset resolver, kept for brands with insufficient visual DNA (§14)."""
    from brand.look import look_lock
    return look_lock(brand_dna=brand_dna, profile=profile)
