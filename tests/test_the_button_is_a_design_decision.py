"""Not every poster gets the same button.

OWNER, 2026-08-02, after four posters of the same brand side by side:
«نفس طريقه كتابه البوستر ثانيه في كل البوسترز؟ زرار وكلام بيحاول يتحط كويس».

She was right, and the CSS proved it. The layout archetype varied by a hash of the brand, the
headline treatment varied, the font pairing varied — and the call to action was ONE hardcoded rule
on every poster of every brand:

    border-radius:999px; background:<solid>; padding:22px 52px; font-size:28px;

An art-critic note had made it LOUDER ("the pill was a tiny footnote") and nobody had made it
DIFFERENT. `text_arrow` is the archetype that matters: a premium poster often has no button at
all, and a renderer that can only draw buttons cannot make one.
"""
from __future__ import annotations

import re

from poster.schemas import PosterBrief, PosterDesignSpec
from poster.template import _CTA_STYLES, default_design_spec, render_poster_html


def _spec(name: str, seed=None):
    return default_design_spec(
        PosterBrief(business_name=name, headline="Headline here", cta_text="Order"),
        variation_seed=seed)


def test_different_brands_get_different_buttons():
    got = {_spec(n).cta_style for n in
           ("AlFouad Pharmacies", "Abu Auf", "Nano Care", "Volume Cafe", "Nahdi Online", "Pepsi")}
    assert len(got) >= 2, f"every brand still gets the same button: {got}"


def test_one_brand_varies_between_runs():
    got = {_spec("AlFouad Pharmacies", seed=s).cta_style for s in range(1, 9)}
    assert len(got) >= 3, f"the same brand always draws the same button: {got}"


def test_the_button_is_not_a_passenger_of_the_layout():
    """Decorrelated on purpose: two brands sharing a composition must not also share a button."""
    pairs = {(s.layout, s.cta_style) for s in (_spec("B%d" % i, seed=i) for i in range(40))}
    by_layout: dict = {}
    for layout, cta in pairs:
        by_layout.setdefault(layout, set()).add(cta)
    assert any(len(v) > 1 for v in by_layout.values()), (
        "cta_style is fully determined by layout — it is the same decision twice"
    )


def test_every_archetype_renders_and_they_are_actually_different():
    brief = PosterBrief(business_name="Brand", headline="Headline here", cta_text="Order")
    css = {}
    for style in _CTA_STYLES:
        spec = PosterDesignSpec(cta_style=style)
        html = render_poster_html(brief, spec=spec)
        m = re.search(r"\.cta\{[^}]*\}", html)
        assert m, f"{style} produced no .cta rule"
        css[style] = m.group(0)
    assert len(set(css.values())) == len(_CTA_STYLES), (
        f"archetypes render identical CSS: {css}"
    )


def test_the_quiet_archetype_draws_no_plate():
    """The whole point of `text_arrow`: no fill, no pill, no plate — the words and a rule."""
    brief = PosterBrief(business_name="Brand", headline="Headline here", cta_text="Order")
    html = render_poster_html(brief, spec=PosterDesignSpec(cta_style="text_arrow"))
    rule = re.search(r"\.cta\{[^}]*\}", html).group(0)
    assert "background:none" in rule
    assert "border-radius:999px" not in rule
    assert "border-bottom" in rule


def test_the_loud_archetype_is_still_available():
    """A retail offer still deserves a button. Variety is the fix, not the removal of the pill."""
    brief = PosterBrief(business_name="Brand", headline="Headline here", cta_text="Order")
    html = render_poster_html(brief, spec=PosterDesignSpec(cta_style="pill"))
    rule = re.search(r"\.cta\{[^}]*\}", html).group(0)
    assert "border-radius:999px" in rule and "background:" in rule
