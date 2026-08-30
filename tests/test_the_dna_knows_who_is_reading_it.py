# -*- coding: utf-8 -*-
"""The brand DNA has two audiences and was written for one.

FOUND BY AUDITING A REAL ASSEMBLED PROMPT (2026-07-28, `outputs/_nano_handoff/calls.json`). One
6,498-character poster prompt carried 39 rule clauses — one every 26 words — and three nouns were
BOTH demanded and forbidden inside it:

    "Place the 'NanoCare' logo top-center"          vs  "Do NOT draw, letter or imply the mark"
    "Assign one bold accent color to each product"  vs  "DO NOT DRAW THE PRODUCT ITSELF"
    "a large circular graphic icon for the pack"    vs  "leave its place EMPTY"
    "the 'nano' dot pattern ... on the packaging"   vs  "ABSOLUTELY NO ... logos ... anywhere"

The block carrying the left column is headed `BRAND DESIGN LANGUAGE (follow it faithfully)`. It is
a PACKAGING DESIGN BRIEF — learned correctly from the brand's own creatives, which include its
packs — and it was pasted verbatim into a prompt whose job is a text-free background with the
product deliberately absent.

Handed two faithful jobs, the model resolved the contradiction the only way it can: it drew a
pack. That is the defect chain seen live all day — a blue carton with an invented atom icon
("create a large, stylized, circular graphic icon for the center of the pack"), garbled signage
("place the logo top-center"), and accent-colour drift ("assign one single, bold accent color").

Universal, not brand-specific (D-8): every brand whose DNA was learned from its packaging carries
this. Nano Care is where it was caught, not where it lives.
"""
from __future__ import annotations

# The real block, verbatim from the captured prompt — the fixture IS the evidence.
_REAL_DNA = """- Imagery style: There are two distinct styles. 1) Packaging and branding elements are purely graphic, using stylized icons, geometric patterns, and no photography. 2) Marketing and product shots use clean, bright, high-key photography. This includes aspirational beauty shots of models with flawless skin, and pristine tabletop photos of the product in a lifestyle context.
- Colour usage: The core brand palette is a metallic silver-grey and white. This accent color is deployed specifically on a horizontal bar behind the product name, within the central graphic icon, and sometimes on a solid band at the bottom of the pack.
- Composition: Packaging follows a strict, symmetrical, grid-based composition. The focal point is the large, central circular icon. Lifestyle product shots use a simple tabletop setup with the product in sharp focus and the background softly blurred.
- Signature moves: Using the 'nano' dot pattern from the logo as a subtle, textural background on the packaging.
- Mood: Clinical efficacy meets premium wellness. The packaging feels scientific, trustworthy, and technical.
DO: Always start with a metallic silver or clean white base for packaging.; Place the 'NanoCare' logo top-center.; Create a large, stylized, circular graphic icon for the center of the pack.; List secondary benefits in a neat horizontal row of smaller, uniform icons.
DO NOT: Do not use photography or realistic illustrations on the product packaging itself.; Do not use serif, script, or decorative fonts.; Do not place the logo anywhere other than the top-center on packaging.; Do not use dark, moody, or low-key lighting for standard product shots."""


# =====================================================================
# the clauses that made the model draw a pack
# =====================================================================

def test_every_measured_offender_is_withheld():
    """Each of these was in the shipped prompt and each has a matching defect in a real render."""
    from brand.dna_scope import scene_safe
    kept = scene_safe(_REAL_DNA).lower()
    for offender in (
        "place the 'nanocare' logo top-center",
        "create a large, stylized, circular graphic icon",
        "list secondary benefits in a neat horizontal row",
        "always start with a metallic silver or clean white base for packaging",
        "dot pattern from the logo",
        "horizontal bar behind the product name",
        "packaging follows a strict, symmetrical, grid-based composition",
        "do not place the logo anywhere other than the top-center",
    ):
        assert offender not in kept, offender


def test_the_world_survives():
    """The filter must not cost the brand its actual look — that would trade one failure for
    another. These are the sentences a photograph can obey."""
    from brand.dna_scope import scene_safe
    kept = scene_safe(_REAL_DNA).lower()
    for keeper in (
        "high-key photography",
        "aspirational beauty shots of models",
        "tabletop setup with the product in sharp focus",
        "metallic silver-grey and white",
        "clinical efficacy meets premium wellness",
        "low-key lighting",
    ):
        assert keeper in kept, keeper


def test_the_split_is_by_sentence_not_by_field():
    """One field mixes both audiences: 'Packaging follows a strict grid... Lifestyle product shots
    use a simple tabletop setup...'. Dropping the field loses the second; keeping it ships the
    first."""
    from brand.dna_scope import scene_safe
    kept = scene_safe(_REAL_DNA).lower()
    assert "tabletop setup" in kept
    assert "grid-based composition" not in kept


def test_a_meaningful_share_is_actually_removed():
    from brand.dna_scope import dropped_clauses, scene_safe
    dropped = dropped_clauses(_REAL_DNA)
    assert len(dropped) >= 10, f"only {len(dropped)} clauses withheld: {dropped}"
    assert len(scene_safe(_REAL_DNA)) < len(_REAL_DNA)


# =====================================================================
# the discriminators, and why each is shaped the way it is
# =====================================================================

def test_a_placement_instruction_is_never_scene_actionable():
    from brand.dna_scope import is_scene_actionable
    for s in ("Place the logo top-center",
              "Position the icon in the centre of the pack",
              "List secondary benefits in a row",
              "Align the wordmark to the left margin"):
        assert is_scene_actionable(s) is False, s


def test_a_sentence_whose_subject_is_a_pack_is_dropped_whatever_follows():
    """"Packaging and branding elements are purely graphic ... and no photography" was rescued by
    the very word it uses to exclude itself, until the subject was checked first."""
    from brand.dna_scope import is_scene_actionable
    assert is_scene_actionable(
        "Packaging and branding elements are purely graphic, using stylized icons, "
        "geometric patterns, and no photography.") is False


def test_a_pack_named_inside_a_photographic_sentence_survives():
    from brand.dna_scope import is_scene_actionable
    assert is_scene_actionable(
        "Lifestyle product shots use a simple tabletop setup with the product in sharp "
        "focus and the background softly blurred.") is True


def test_colour_words_alone_do_not_rescue_a_layout_rule():
    """The first version of the rescue list included colour, tone and mood — and MEASURED on this
    same DNA it rescued seven pack-layout clauses. Design copy is saturated with colour words;
    only a CAMERA or a photographed SUBJECT means a photograph."""
    from brand.dna_scope import is_scene_actionable
    assert is_scene_actionable(
        "This accent color is deployed on a horizontal bar behind the product name, "
        "within the central graphic icon.") is False


def test_plurals_do_not_slip_through():
    """"fonts" survived a pattern written for "font"."""
    from brand.dna_scope import is_scene_actionable
    for s in ("Do not use serif, script, or decorative fonts",
              "Keep the icons uniform", "Use consistent typefaces", "Avoid heavy bands"):
        assert is_scene_actionable(s) is False, s


def test_ordinary_scene_language_is_untouched():
    from brand.dna_scope import is_scene_actionable
    for s in ("Warm afternoon daylight through a window",
              "A pharmacist smiles at a customer across the counter",
              "Shallow depth of field, muted natural palette",
              "إضاءة طبيعية دافئة في مطبخ حديث"):
        assert is_scene_actionable(s) is True, s


def test_an_all_packaging_dna_yields_nothing_rather_than_something_wrong():
    """An empty brand block is honest. A faithful-sounding brief for the wrong job is not."""
    from brand.dna_scope import scene_safe
    assert scene_safe("- Signature moves: Place the logo top-left on every pack.\n"
                      "DO: Use a 12-column grid.; Keep the wordmark centred.") == ""


def test_the_filter_never_costs_the_dna_when_it_cannot_parse():
    from brand.dna_scope import scene_safe
    assert scene_safe("") == ""
    assert scene_safe(None) == ""


# =====================================================================
# wired, and not silently
# =====================================================================

def test_the_poster_prompt_builder_applies_the_filter():
    import inspect

    import poster.art_director as ad
    src = inspect.getsource(ad._brand_dna_lines)
    assert "scene_safe" in src, "the raw DNA still goes straight into the scene prompt"


def test_the_withholding_is_logged():
    """A brand whose DNA is ALL packaging must be visible as such, not merely quieter."""
    import inspect

    import poster.art_director as ad
    src = inspect.getsource(ad._brand_dna_lines)
    assert "dropped_clauses" in src and "logger" in src


def test_a_filter_failure_returns_the_dna_rather_than_nothing():
    """Losing the brand's whole visual language to a regex bug would be a worse defect than the
    one being fixed."""
    import inspect

    import poster.art_director as ad
    src = inspect.getsource(ad._brand_dna_lines)
    tail = src[src.find("except Exception"):]
    assert "return block" in tail
