# -*- coding: utf-8 -*-
"""Three permanently-empty sections on the Dashboard tab — two of them had the data all along.

MEASURED on the shipped page. `#panel-dashboard` renders three labelled sections whose content
comes from fields NOTHING in this repository produces (`grep` over every .py: zero writers):

    Brand voice guide       -> always "No brand voice guide generated."
    Content suggestions     -> always "No suggestions generated."
    Readiness - missing data -> always empty

A labelled section that is empty for every brand, forever, is a promise the product does not keep.
The owner's ruling covers it: «لو شايف فيتشر او حاجه ملهاش لازم شيها».

BUT TWO OF THEM SHOULD BE FILLED, NOT DELETED — the data exists under different names:

  * BRAND VOICE. `_profile.tone_of_voice` is measured and carries its evidence quote. Abu Auf:
    "inspirational", quoted from its own product page. The section was reading `brand_voice_guide`,
    a name from a cloned repo's schema that this pipeline never adopted.

  * READINESS. `_profile.readiness` holds `missing_critical`, `missing_recommended`, and the
    blockers that decide `ready_for_swot` / `ready_for_strategy`. Abu Auf:
    `swot_blockers: ["low_evidence_coverage"]`, and four quality signals explicitly false
    (tagline, trust_signals_2plus, locations_with_geo, hours_known). "What is missing" is exactly
    what that section is named after, and it was reading `readiness_missing`, which no code writes.

CONTENT SUGGESTIONS goes. The content calendar is the real feature and has its own tab; a second,
weaker, permanently-empty version of it is noise.

Static assertions on the shipped file — this repo has no JS test runner, and the same approach
already caught the disclosure and priority regressions.
"""
from __future__ import annotations

import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
    encoding="utf-8")
# Comments are the RECORD of why a field was abandoned; the test is about what the page READS.
# Stripping both comment styles is what lets the explanation be written down next to the fix —
# the same slip this suite made twice today, once per comment syntax.
CODE = re.sub(r"<!--.*?-->", " ", HTML, flags=re.S)
CODE = re.sub(r"^\s*//[^\n]*$", " ", CODE, flags=re.M)


# --- the two that get filled ---------------------------------------------------------------------

def test_the_voice_section_reads_the_measured_tone_not_a_phantom_field():
    assert "brand_voice_guide" not in CODE, (
        "still reading a field no code in this repo writes")
    assert "tone_of_voice" in CODE, "the measured tone never reaches the voice section"


def test_the_voice_section_can_show_the_evidence_behind_the_tone():
    """`tone_of_voice` is an evidenced field — value plus the quote it was read from. Showing the
    verdict without its source is the habit this project keeps having to unlearn."""
    assert "renderBrandVoice" in CODE, "no renderer for the measured voice"


def test_the_readiness_section_reads_the_real_readiness():
    assert "readiness_missing" not in CODE, (
        "still reading a field no code in this repo writes")
    for real in ("missing_critical", "missing_recommended"):
        assert real in CODE, f"{real} is computed and stored, and still not shown"


def test_the_readiness_section_names_what_is_blocked():
    """"Missing: hours" means nothing on its own. "Missing: hours — which is why the strategy is
    marked unsupported" is actionable."""
    assert "swot_blockers" in CODE or "strategy_blockers" in CODE


# --- the one that goes ---------------------------------------------------------------------------

def test_the_permanently_empty_suggestions_section_is_gone():
    assert "content-suggestions" not in CODE, (
        "a section that says 'No suggestions generated.' for every brand, forever")
    assert "content_suggestions" not in CODE


def test_the_real_calendar_is_still_there():
    """It was removed because the content calendar already does this properly."""
    assert "content_calendar" in CODE


# --- and no section is left promising nothing ----------------------------------------------------

def test_no_section_hardcodes_an_apology_for_a_feature_that_never_runs():
    for phrase in ("No brand voice guide generated", "No suggestions generated"):
        assert phrase not in CODE, phrase
