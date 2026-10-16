# -*- coding: utf-8 -*-
"""Brand constraints a verifier is allowed to enforce — each with its lineage.

WHY THIS EXISTS (measured, 2026-08-31, live run `poster_ebeae26f` for marasimltd.com).
The vision QA blocked a poster three times in a row with:

    "The CTA button is orange, which violates the brand's specific dusty blue color guideline"
    "...instead of the required dusty blue brand color."
    "...requiring a dusty, muted blue."

No Marasim authority contains a dusty-blue rule. The brand's own BrandCreativeDNA says the
opposite in two places: `color_usage` prescribes "a vibrant, fiery orange for accents", and
`dont_list` contains "Do not use a cool color palette (no blues, greens, or purples)." The
site's measured accent is #F04B1E. The verifier had been handed `color_usage` as FREE PROSE and
asked for a boolean; nothing required the rule it cited to exist, so it fused "dusty" (which is
in the prose, describing a sepia photographic palette) with "blue" (which appears only inside a
PROHIBITION that was never sent to it) and invented a mandatory constraint that inverted the
brand. Three paid background renders were spent chasing it.

THE RULE THIS ENCODES
    A verifier may detect violations of constraints it was SUPPLIED.
    A verifier may NOT invent brand constraints.

So a blocking brand-fidelity judgment must name a constraint that resolves to an artifact:
value, source artifact, source type, source field, source id/hash. A cited constraint with no
lineage is not a brand failure — it is a defect in the verifier, reported as
BRAND_CONSTRAINT_NOT_GROUNDED, and it must not block.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

#: Typed defect emitted when a verifier cites a brand constraint that no authority supplied.
BRAND_CONSTRAINT_NOT_GROUNDED = "BRAND_CONSTRAINT_NOT_GROUNDED"

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")

# Framework defaults are not brand authority. Bootstrap ships #0d6efd on every page that loads
# it; the marasimltd.com scrape captured it fifth in `brand_colors`, ahead of the brand's real
# accent. A palette scraped off computed styles is evidence of what a stylesheet contains, not
# of what the brand decided.
_FRAMEWORK_HEXES = {
    "#0d6efd", "#6610f2", "#6f42c1", "#d63384", "#dc3545", "#fd7e14",
    "#ffc107", "#198754", "#20c997", "#0dcaf0", "#212529", "#f8f9fa",
}

#: Colour words a verdict may name. Kept explicit so a cited word can be checked against the
#: constraint set rather than pattern-matched loosely.
_COLOR_WORDS = (
    "blue", "navy", "teal", "cyan", "turquoise", "green", "mint", "olive",
    "purple", "violet", "lilac", "lavender", "magenta", "pink", "red", "crimson",
    "orange", "amber", "gold", "golden", "yellow", "brown", "sepia", "beige",
    "cream", "white", "black", "charcoal", "grey", "gray", "silver",
)


@dataclass(frozen=True)
class BrandConstraint:
    """One enforceable brand rule, and where it came from.

    `source_id` is a content hash of the artifact, so a constraint cited in a verdict can be
    tied to the exact bytes that authorised it rather than to a file name that may have changed.
    """

    value: str
    source_artifact: str          # e.g. "outputs/brandbooks/marasim_..._dna.json"
    source_type: str              # e.g. "BrandCreativeDNA" | "BusinessProfile" | "CreativeContract"
    source_field: str             # e.g. "color_usage" | "visual.palette_hex"
    source_id: str                # sha256 of the source artifact's content
    kind: str = "color"           # what the constraint governs
    polarity: str = "allow"       # "allow" (prescribed) or "forbid" (prohibited)

    def as_dict(self) -> dict:
        return {
            "value": self.value, "kind": self.kind, "polarity": self.polarity,
            "source_artifact": self.source_artifact, "source_type": self.source_type,
            "source_field": self.source_field, "source_id": self.source_id,
        }


@dataclass
class ConstraintSet:
    """Everything a verifier is permitted to enforce for one render."""

    constraints: list[BrandConstraint] = field(default_factory=list)

    def add(self, c: BrandConstraint) -> None:
        self.constraints.append(c)

    def allowed_terms(self) -> set[str]:
        return {c.value.lower() for c in self.constraints if c.polarity == "allow"}

    def forbidden_terms(self) -> set[str]:
        return {c.value.lower() for c in self.constraints if c.polarity == "forbid"}

    def approved_colors(self) -> list[str]:
        """Hex values this brand actually authorises, in supplied order."""
        return [c.value for c in self.constraints
                if c.kind == "color" and c.polarity == "allow" and c.value.startswith("#")]

    def lineage(self) -> list[dict]:
        return [c.as_dict() for c in self.constraints]

    def find(self, term: str) -> Optional[BrandConstraint]:
        t = (term or "").strip().lower()
        for c in self.constraints:
            if c.value.lower() == t:
                return c
        return None


def _sha(obj: Any) -> str:
    try:
        blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _words_in(text: str) -> list[str]:
    low = (text or "").lower()
    return [w for w in _COLOR_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]


def constraint_set_from_direction(direction: Any) -> ConstraintSet:
    """The verifier's constraints, taken from the SAME ResolvedVisualDirection generation used.

    Parity is the point (§8). Before this, generation read a preset and the verifier read a prose
    paragraph, so the two could — and did — hold different beliefs about the brand's palette. Now
    both sides carry the same `direction_hash`, and a blocking finding can name the exact
    constraint the generator was given.
    """
    cs = ConstraintSet()
    src = getattr(direction, "dna_source_artifact", "") or "ResolvedVisualDirection"
    rid = getattr(direction, "resolution_hash", "") or ""
    for term in (getattr(direction, "palette_positive", None) or []):
        cs.add(BrandConstraint(str(term).lower(), src, "ResolvedVisualDirection",
                               "palette_positive", rid))
    for hexv in (getattr(direction, "primary_colors", None) or []):
        cs.add(BrandConstraint(str(hexv).lower(), src, "ResolvedVisualDirection",
                               "primary_colors", rid))
    for hexv in (getattr(direction, "accent_colors", None) or []):
        cs.add(BrandConstraint(str(hexv).lower(), src, "ResolvedVisualDirection",
                               "accent_colors", rid))
    for term in (getattr(direction, "palette_prohibited", None) or []):
        cs.add(BrandConstraint(str(term).lower(), src, "ResolvedVisualDirection",
                               "palette_prohibited", rid, polarity="forbid"))
    return cs


def build_constraint_set(*, brand_dna: Any = None, dna_path: str = "",
                         profile: Optional[Mapping[str, Any]] = None,
                         profile_path: str = "") -> ConstraintSet:
    """Collect every brand constraint a verifier may enforce, each carrying its lineage.

    Prescriptions and PROHIBITIONS both travel. The prohibition half is the part that was
    missing: `dont_list` is consumed by `poster/art_director.py` when writing the prompt and by
    nothing else, so the verifier never learned that this brand forbids cool colours — the exact
    fact that would have made "required dusty blue" impossible to state.
    """
    cs = ConstraintSet()

    if brand_dna is not None:
        dna_id = _sha(getattr(brand_dna, "__dict__", None) or str(brand_dna))
        artifact = dna_path or "BrandCreativeDNA"

        prose = str(getattr(brand_dna, "color_usage", "") or "")
        for hexv in _HEX_RE.findall(prose):
            if hexv.lower() not in _FRAMEWORK_HEXES:
                cs.add(BrandConstraint(hexv.lower(), artifact, "BrandCreativeDNA",
                                       "color_usage", dna_id))
        for w in _words_in(prose):
            cs.add(BrandConstraint(w, artifact, "BrandCreativeDNA", "color_usage", dna_id))

        # Prohibitions. "Do not use a cool color palette (no blues, greens, or purples)."
        for rule in (getattr(brand_dna, "dont_list", None) or []):
            for w in _words_in(str(rule)):
                cs.add(BrandConstraint(w, artifact, "BrandCreativeDNA", "dont_list", dna_id,
                                       polarity="forbid"))

    if profile:
        visual = (profile.get("visual") or {}) if isinstance(profile, Mapping) else {}
        pal = list(visual.get("palette_hex") or [])
        prof_id = _sha(pal)
        artifact = profile_path or "BusinessProfile"
        for hexv in pal:
            h = str(hexv).lower()
            if not h.startswith("#"):
                continue
            if h in _FRAMEWORK_HEXES:
                # Recorded as NOT authority rather than dropped, so "why isn't Bootstrap blue a
                # brand colour" has an answer in the lineage instead of a silent absence.
                cs.add(BrandConstraint(h, artifact, "BusinessProfile", "visual.palette_hex",
                                       prof_id, polarity="forbid"))
                continue
            cs.add(BrandConstraint(h, artifact, "BusinessProfile", "visual.palette_hex", prof_id))

    return cs


def check_cited_constraint(reason: str, cs: ConstraintSet) -> dict:
    """Is the brand rule this verdict CITES actually one the brand supplied?

    Returns a lineage record. `grounded=False` means the verifier named a constraint no
    authority carries — a defect in the verifier, never a brand-fidelity failure.
    """
    cited = _words_in(reason or "")
    # Only terms asserted as a REQUIREMENT matter — a verdict may freely describe the colour it
    # SAW ("the CTA is orange"), and naming what is on the poster is not citing a rule.
    #
    # Scan FORWARD from each requirement cue and take the first colour word after it. An earlier
    # version matched any colour within N characters of a cue, which read "cool and grey; the
    # required orange brand color" as requiring GREY — the checker's own false positive, and the
    # one failure mode this whole module exists to prevent.
    low = (reason or "").lower()
    required: list[str] = []
    for cue in re.finditer(r"\b(required|requires|requiring|must be|should be|specific|"
                           r"guidelines?)\b", low):
        window = low[cue.end():cue.end() + 60]
        best: Optional[tuple[int, str]] = None
        for w in _COLOR_WORDS:
            m = re.search(rf"\b{re.escape(w)}\b", window)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), w)
        if best and best[1] not in required:
            required.append(best[1])
    out = {"cited_terms": cited, "required_terms": required, "grounded": True,
           "defect": "", "lineage": [], "ungrounded_terms": []}
    if not required:
        return out

    ungrounded = []
    for term in required:
        c = cs.find(term)
        if c is None or c.polarity == "forbid":
            ungrounded.append(term)
        else:
            out["lineage"].append(c.as_dict())
    if ungrounded:
        out["grounded"] = False
        out["ungrounded_terms"] = ungrounded
        out["defect"] = BRAND_CONSTRAINT_NOT_GROUNDED
    return out


# --- deterministic enforcement of a SUPPLIED prohibition ---------------------
#
# MEASURED 2026-08-31, verification render `poster_e3a5e608`: the negative constraints reached
# the provider prompt (confirmed in the request), the model ignored them, and the model-based
# `on_brand_color` check PASSED the result — 61.7% of the frame and 76% of the background margin
# were the blues/teals/greens this brand explicitly forbids. Asking a language model "does the
# dominant colour match this prose?" cannot enforce a prohibition; counting pixels can, for free,
# and the answer is reproducible.
_HUE_BANDS = {
    "cool": (150.0, 270.0),      # blue / teal / cyan / green / purple-ish
    "warm": (330.0, 60.0),       # wraps 0: orange / amber / red
}


def prohibited_color_presence(image_path: "str | Any", constraints: "ConstraintSet",
                              *, min_saturation: float = 0.10) -> dict:
    """Fraction of the rendered frame occupied by colours this brand PROHIBITS.

    Deterministic, offline, no provider call. Returns the measurement plus a `violates` flag so a
    caller can turn a supplied prohibition into a real blocking finding with lineage.
    """
    import colorsys
    from PIL import Image

    forbidden = constraints.forbidden_terms()
    cool_forbidden = any(t in forbidden for t in
                         ("blue", "teal", "cyan", "green", "navy", "turquoise", "mint",
                          "olive", "purple", "violet", "lilac", "lavender"))
    if not cool_forbidden:
        return {"checked": False, "reason": "no colour prohibition supplied", "violates": False}

    im = Image.open(image_path).convert("RGB")
    im = im.resize((216, 270))
    px = list(im.getdata())
    lo, hi = _HUE_BANDS["cool"]
    cool = 0
    counted = 0
    for r, g, b in px:
        h, _l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s < min_saturation:
            continue
        counted += 1
        if lo <= h * 360 <= hi:
            cool += 1
    frac = (cool / counted) if counted else 0.0
    # A prohibited hue may legitimately appear as a tiny accent (a screen, a reflection). A
    # quarter of the saturated frame is not an accent; it is the palette.
    return {"checked": True, "prohibited_fraction": round(frac, 4),
            "threshold": 0.25, "violates": frac > 0.25,
            "constraint_terms": sorted(forbidden & {"blue", "teal", "cyan", "green", "purple"}),
            "lineage": [c.as_dict() for c in constraints.constraints
                        if c.polarity == "forbid" and c.value in ("blue", "teal", "green")][:3]}
