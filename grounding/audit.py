"""Shared brand-safety SCOPE for per-asset audit trails (Evidence-Ledger Step 3c).

ONE source of truth for the falsifiability POLICY + the product-level COVERAGE scoping, so
the poster trail and the content-calendar trail make the SAME honest statement about which
copy surfaces are gated and which are NOT. The reel is excluded until it is gated
(workstream #3) — claiming a brand-safety trail for an ungated surface is the overselling
that breaks the moat, so it stays named in `excluded_surfaces`.
"""
from __future__ import annotations

from typing import Optional


POLICY = (
    "Falsifiability: any number/year, certification/award/guarantee, or ranking/superlative "
    "claim in customer-facing copy must trace to the brand's REAL evidence, or it is softened, "
    "dropped, or blanked. Subjective puffery (a feeling, 'crafted with care') passes — it "
    "asserts nothing checkable. Only the brand's own scraped site is 'brand'-tier evidence; a "
    "web search snippet is weaker ('web'-tier) and must be reputable."
)

# The copy surfaces whose claims are gated by the Evidence Ledger today.
GATED_SURFACES = [
    "poster copy: headline, subheadline, CTA, proof chips",
    "content-calendar copy: item hooks and angles",
    "reel copy: voiceover, hook, CTA, captions — story AND creative/HITL paths, "
    "drop-to-grounded (gated 2026-07-12)",
]

# Surfaces NOT yet gated — explicitly named so the proof is honest about its limits.
# P-34: state precisely what the OCR check does. It is copy-FIDELITY only (that the intended
# headline/CTA rendered, tolerating up to 2 stray lines), one-shot poster path only — it does NOT
# gate incidental scene text, illegible glyphs, or the classic/reel render paths.
UNGATED_SURFACES = {
    "imagery": "generated scenes/backgrounds are design-domain (no per-pixel claim trail); text "
               "baked into images is only PARTIALLY checked — the one-shot poster path runs a "
               "copy-fidelity OCR check (intended copy present, ≤2 stray lines tolerated); it does "
               "NOT gate incidental scene text, illegible glyphs, or the classic/reel paths",
}


def coverage_block(extra_excluded: Optional[dict] = None) -> dict:
    """The scoping statement embedded in every audit artifact (covered vs excluded)."""
    return {
        "covered_surfaces": list(GATED_SURFACES),
        "excluded_surfaces": {**UNGATED_SURFACES, **(extra_excluded or {})},
    }
