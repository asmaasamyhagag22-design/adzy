# -*- coding: utf-8 -*-
"""The typed generation spec that sits between brand authority and the provider request.

The DNA used to reach the image model two ways, both lossy: a five-way preset pick
(`brand.look.look_lock`) that could invert the brand, and a prose block pasted by the art
director. Neither was inspectable before spending, and the prohibitions never travelled at all.

So authority is COMPILED first — `ResolvedVisualDirection` -> `PosterVisualSpec` -> provider
request — and the spec carries the DNA hash it came from. What the model is about to be told can
be read, diffed and asserted on before a single paid call.

The same spec's constraint set is what the verifier receives, so generation and verification
cannot disagree about what this brand's rules are.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# --- typed defects the repair loop routes on (§10) --------------------------
BRAND_PALETTE_DRIFT = "BRAND_PALETTE_DRIFT"
UNSUPPORTED_VISUAL_CONTEXT = "UNSUPPORTED_VISUAL_CONTEXT"
UNAUTHORIZED_TEXT = "UNAUTHORIZED_TEXT"
TYPOGRAPHY_DRIFT = "TYPOGRAPHY_DRIFT"


@dataclass
class PosterVisualSpec:
    palette: list[str] = field(default_factory=list)
    accent: list[str] = field(default_factory=list)
    background: str = ""
    lighting: str = ""
    environment: str = ""
    imagery_mode: str = ""
    geography: list[str] = field(default_factory=list)
    composition: str = ""
    typography_constraints: str = ""
    prohibited_colors: list[str] = field(default_factory=list)
    prohibited_styles: list[str] = field(default_factory=list)
    required_brand_elements: list[str] = field(default_factory=list)
    text_policy: str = ""
    dna_source_hash: str = ""
    direction_hash: str = ""
    authority_status: str = ""
    spec_hash: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    # ---- provider translation -------------------------------------------
    def to_provider_directives(self) -> tuple[str, str]:
        """(positive_block, negative_block) — what actually reaches the image request."""
        pos: list[str] = []
        if self.palette:
            pos.append("COLOUR PALETTE (mandatory): " + ", ".join(self.palette))
        if self.accent:
            pos.append("ACCENT COLOUR (must be visibly present): " + ", ".join(self.accent))
        if self.background:
            pos.append("BACKGROUND: " + self.background)
        if self.lighting:
            pos.append("LIGHT: " + self.lighting)
        if self.imagery_mode:
            pos.append("IMAGERY MODE: " + self.imagery_mode)
        if self.geography:
            pos.append("GEOGRAPHIC CONTEXT: " +
                       ", ".join(g.title() for g in self.geography) +
                       " — the location is specific, not a generic 'Middle East'")
        if self.composition:
            pos.append("COMPOSITION: " + self.composition)
        neg: list[str] = list(self.prohibited_styles)
        if self.prohibited_colors:
            neg.insert(0, "NO " + ", ".join(sorted(set(self.prohibited_colors))) +
                       " anywhere — not in the subject, the background, or the shadows")
        return ("\n".join(f"- {p}" for p in pos), "\n".join(f"- {n}" for n in neg))


def compile_visual_spec(direction: Any, *, text_policy: str = "",
                        required_brand_elements: Optional[list[str]] = None) -> PosterVisualSpec:
    """ResolvedVisualDirection -> PosterVisualSpec. Pure; no provider contact."""
    d = direction
    spec = PosterVisualSpec(
        palette=list(getattr(d, "palette_positive", []) or []),
        accent=list(getattr(d, "accent_colors", []) or []),
        background=getattr(d, "background_preferences", "") or "",
        lighting=getattr(d, "lighting", "") or "",
        environment=getattr(d, "material_style_cues", "") or "",
        imagery_mode=getattr(d, "photography_render_preference", "") or "",
        geography=list(getattr(d, "geographic_context", []) or []),
        composition="single clear focal point with generous negative space",
        typography_constraints=getattr(d, "typography_role", "") or "",
        prohibited_colors=list(getattr(d, "palette_prohibited", []) or []),
        prohibited_styles=list(getattr(d, "negative_style_constraints", []) or []),
        required_brand_elements=list(required_brand_elements or []),
        text_policy=text_policy or "no text rendered by the image model",
        dna_source_hash=getattr(d, "dna_source_id", "") or "",
        direction_hash=getattr(d, "resolution_hash", "") or "",
        authority_status=getattr(d, "authority_status", "") or "",
    )
    spec.spec_hash = hashlib.sha256(
        json.dumps(spec.as_dict(), ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()
    return spec


# --- §9/§10: a verifier finding may not become a prompt ---------------------

def route_finding_to_repair(finding: dict, spec: PosterVisualSpec) -> dict:
    """Turn a GROUNDED verifier finding into a TARGETED repair instruction.

    The rule this enforces: raw critic prose never reaches the generator. The "dusty blue"
    incident happened because a failed reason string was fed straight back as a regeneration
    instruction, so an invented rule got three paid renders to chase itself. Now a finding must
    first survive constraint-lineage validation, then map to a typed defect, and only the typed
    defect selects a narrow instruction built from the SPEC — never from the critic's words.
    """
    if not finding.get("grounded", False):
        return {"repair": False, "defect": finding.get("defect", ""),
                "reason": "ungrounded verifier constraint — no repair instruction issued"}

    defect = finding.get("defect") or BRAND_PALETTE_DRIFT
    if defect == BRAND_PALETTE_DRIFT:
        instruction = ("Regenerate with the mandatory palette only: "
                       + ", ".join(spec.palette + spec.accent)
                       + ". Prohibited: " + ", ".join(sorted(set(spec.prohibited_colors))))
    elif defect == UNSUPPORTED_VISUAL_CONTEXT:
        instruction = ("Regenerate with the geographic context "
                       + ", ".join(g.title() for g in spec.geography)
                       + " only; remove any unrelated cultural, festive or architectural cues.")
    elif defect == UNAUTHORIZED_TEXT:
        instruction = ("Text repair only: the compositor owns every glyph. "
                       + spec.text_policy + " Do not change palette, lighting or composition.")
    elif defect == TYPOGRAPHY_DRIFT:
        instruction = ("Typography repair only: " + (spec.typography_constraints or "")
                       + " Do not change palette, lighting or composition.")
    else:
        return {"repair": False, "defect": defect, "reason": "unrouted defect type"}

    return {"repair": True, "defect": defect, "instruction": instruction,
            "spec_hash": spec.spec_hash, "direction_hash": spec.direction_hash}
