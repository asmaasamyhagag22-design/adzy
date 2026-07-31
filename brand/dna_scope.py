# -*- coding: utf-8 -*-
"""The brand DNA has two audiences. It was only ever written for one.

FOUND BY AUDITING A REAL ASSEMBLED PROMPT (2026-07-28). One 6,498-character poster prompt carried
39 rule clauses — one every 26 words — and three nouns were simultaneously demanded and forbidden:

    "Place the 'NanoCare' logo top-center"        vs   "Do NOT draw, letter or imply the mark"
    "Assign one bold accent color to each product" vs   "DO NOT DRAW THE PRODUCT ITSELF"
    "the 'nano' dot pattern ... on the packaging"  vs   "ABSOLUTELY NO ... logos ... anywhere"
    "a large circular graphic icon for the pack"   vs   "leave its place EMPTY"

The block carrying the left-hand column is headed `BRAND DESIGN LANGUAGE (follow it faithfully)`.
It is a PACKAGING DESIGN BRIEF — learned, correctly, from the brand's own creatives, which include
its packs — and `poster/art_director._brand_dna_lines` pasted it verbatim into a prompt whose job
is a text-free background with the product deliberately absent.

So the model was handed two jobs and told both were faithful. It resolved the contradiction the
way models do: it drew a pack. That is the same defect chain seen live all day — a blue carton
with an invented atom icon ("create a large, stylized, circular graphic icon for the center of the
pack"), garbled signage ("place the logo top-center"), and accent-colour drift ("assign one single,
bold accent color to each product").

THE SPLIT IS BY SENTENCE, NOT BY FIELD, because one field mixes both audiences:

    "Composition: Packaging follows a strict, symmetrical, grid-based composition. The focal point
     is the large, central circular icon. Lifestyle product shots use a simple tabletop setup with
     the product in sharp focus and the background softly blurred."

The first two sentences are for a packaging designer. The third is exactly what a scene generator
needs. Dropping the field loses the third; keeping it ships the first two.

Universal by construction (D-8): the vocabulary is about DESIGN SURFACES and LAYOUT, not about any
market, language or category. Every brand whose DNA was learned from its packaging has this
problem — Nano Care is where it was caught, not where it lives.
"""
from __future__ import annotations

import re

# A clause aimed at whoever DESIGNS the pack or the layout. A scene generator cannot act on any of
# it, and every one of these that reaches it becomes an invented pack, a drawn logo or stray type.
_DESIGN_SURFACE = re.compile(
    r"\b(packaging|packs?|cartons?|labell?(?:ing|s)?|box art|sleeves?|wrappers?|"
    r"logos?|wordmarks?|lockups?|monograms?|brand ?marks?|"
    r"typograph\w*|typefaces?|fonts?|lettering|headline treatment|"
    r"grids?|layouts?|templates?|hierarchy|margins?|gutters?|"
    r"icons?|iconography|badges?|bands?|bar behind|stripes?|swatch(?:es)?)\b", re.I)

# ...unless the sentence is plainly describing a PHOTOGRAPH of a world. These win, because a
# sentence can name a pack while describing how it is SHOT ("the product in sharp focus").
#
# DELIBERATELY NARROW. The first version of this list included colour, tone, mood and the bare
# word "photography", and MEASURED on the real Nano Care DNA that rescued seven pack-layout
# clauses — "this accent color is deployed on a horizontal bar behind the product name, within
# the central graphic icon", "do not use serif, script or decorative fonts". Design copy is
# saturated with colour words; only a CAMERA or a photographed SUBJECT means a photograph.
_PHOTOGRAPHIC = re.compile(
    r"\b(shot|shots|shoot|lighting|lit\b|daylight|golden hour|backlit|rim light|"
    r"depth of field|bokeh|blurred|out of focus|sharp focus|lens|camera|framing|"
    r"model|models|person|people|hands?|woman|man|child|setting|location|environment|"
    r"interior|exterior|room|kitchen|street|tabletop|lifestyle|candid|scene)\b", re.I)

# When a sentence OPENS on a design surface, it is an instruction about that surface whatever
# vocabulary follows. "Packaging and branding elements are purely graphic ... and no photography"
# survived the word-anywhere test purely because it contains "photography" — while being, from
# its first two words, a rule about packs.
_SUBJECT_WINDOW = 6      # words

# Layout verbs that make a sentence an INSTRUCTION to place something, whatever else it says.
_PLACEMENT_VERB = re.compile(
    r"\b(place|position|align|centre|center|anchor|list|arrange|set)\b\s+(?:the\s+)?"
    r"\w*\s*\b(logo|icon|name|text|type|benefits?|badge|band)\b", re.I)


def _sentences(text: str) -> list[str]:
    """Split on sentence-ish boundaries AND on the semicolons the DO/DO NOT lists are joined with,
    since those lists are the densest source of packaging instructions in the whole block."""
    parts = re.split(r"(?<=[.!?])\s+|;\s*", str(text or ""))
    return [p.strip() for p in parts if p and p.strip()]


def is_scene_actionable(sentence: str) -> bool:
    """Can a model rendering a PHOTOGRAPH of a world do anything with this sentence?

    Order matters: an explicit placement instruction is never scene-actionable no matter how much
    photographic vocabulary surrounds it, because its verb is 'put this here on the artwork'."""
    s = str(sentence or "").strip()
    if not s:
        return False
    if _PLACEMENT_VERB.search(s):
        return False
    # A sentence whose SUBJECT is a design surface is a rule about that surface, whatever it goes
    # on to say. Checked before the rescue, or "Packaging ... uses no photography" is rescued by
    # the word it uses to exclude itself.
    subject = " ".join(re.sub(r"^(?:do(?:\s+not)?|always|never)\b[:,]?\s*", "", s, flags=re.I)
                       .split()[:_SUBJECT_WINDOW])
    if _DESIGN_SURFACE.search(subject):
        return False
    if _DESIGN_SURFACE.search(s):
        # A design-surface word later in the sentence survives only when the sentence is
        # describing how something is PHOTOGRAPHED rather than how it is laid out.
        return bool(_PHOTOGRAPHIC.search(s))
    return True


def scene_safe(block: str) -> str:
    """The DNA block reduced to what a scene generator can act on.

    Returns "" when nothing survives — an empty brand block is honest, and far better than a
    faithful-sounding brief for the wrong job."""
    kept: list[str] = []
    for raw_line in str(block or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # Keep the "- Label: " prefix so the surviving text stays attributed to its field.
        m = re.match(r"^(\s*(?:-\s*)?[A-Za-z ]{3,24}:\s*)(.*)$", line)
        prefix, body = (m.group(1), m.group(2)) if m else ("", line)
        good = [s for s in _sentences(body) if is_scene_actionable(s)]
        if good:
            kept.append(prefix + " ".join(g if g.endswith((".", "!", "?")) else g + "."
                                          for g in good))
    return "\n".join(kept)


def dropped_clauses(block: str) -> list[str]:
    """What `scene_safe` removed. Exists so a silent filter is never a silent filter — a brand
    whose whole DNA is packaging design should be visible in the log, not just quieter."""
    out: list[str] = []
    for raw_line in str(block or "").splitlines():
        m = re.match(r"^(\s*(?:-\s*)?[A-Za-z ]{3,24}:\s*)(.*)$", raw_line.rstrip())
        body = m.group(2) if m else raw_line
        out.extend(s for s in _sentences(body) if not is_scene_actionable(s))
    return out
