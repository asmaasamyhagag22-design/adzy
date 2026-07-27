# -*- coding: utf-8 -*-
"""Analyze must reach the server. It stopped, silently, and this pins the reason.

MEASURED IN THE LIVE APP, 2026-07-27, after the owner reported «بعمل انلايزس مش بيشتغل»:

    server log for the whole session   ZERO requests to /api/scrape
    startScrape() in the browser       TypeError: Cannot set properties of null
                                       (setting 'className')
    SCAN_STEP_ORDER                    ['crawl','reviews','classify','extract','score',
                                        'swot','strategy']
    elements actually in the DOM       dot-crawl, dot-reviews, dot-classify, dot-extract,
                                        dot-score      (dot-swot and dot-strategy MISSING)

I caused this. Removing the two scan stages that never ran ("Generating Trend & SWOT analysis",
"Building marketing strategy" — /api/scrape runs the crawl pipeline and neither of those) deleted
their DOM nodes and updated `stepForProgress`, and left `SCAN_STEP_ORDER` naming all seven. The
reset loop at the top of `startScrape` then dereferenced null on the sixth entry.

Two things made it invisible rather than merely broken:
  * the loop runs BEFORE the try/catch that would have shown "Failed to start scrape", so nothing
    was reported;
  * `startScrape` is `async`, so the throw became a rejected promise from an inline `onclick`,
    which the browser discards without a console error.

Click Analyze, nothing happens, no message, no request. The most important button in the product.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPA = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")


def _scan_step_order() -> list[str]:
    m = re.search(r"const SCAN_STEP_ORDER\s*=\s*\[([^\]]*)\]", _SPA)
    assert m, "SCAN_STEP_ORDER is gone — this test needs updating deliberately"
    return re.findall(r"'([^']+)'", m.group(1))


def test_every_scan_step_has_an_element_to_drive():
    """The list and the markup are two halves of one thing; when they drift, the first click into
    the product dies before it sends anything."""
    steps = _scan_step_order()
    missing = [s for s in steps if f'id="dot-{s}"' not in _SPA]
    assert not missing, (
        f"SCAN_STEP_ORDER names {missing} with no matching #dot-* element — the reset loop in "
        "startScrape() will throw on null before it ever calls /api/scrape")


def test_no_orphan_step_dots():
    """The other direction: a dot nothing drives stays grey forever and reads as a stalled stage."""
    in_dom = set(re.findall(r'id="dot-([a-z]+)"', _SPA))
    orphans = sorted(in_dom - set(_scan_step_order()))
    assert not orphans, f"#dot-{orphans} exist but no step drives them"


def test_the_reset_loop_cannot_kill_the_click_again():
    """Defence in depth: even if the two drift again, Analyze must still reach the server. A
    missing progress dot is a cosmetic problem; it must never be a fatal one."""
    m = re.search(r"SCAN_STEP_ORDER\.forEach\(\s*s\s*=>\s*\{(.*?)\n  \}\)", _SPA, re.S)
    assert m, "the reset loop moved — re-check that it still guards a missing element"
    body = m.group(1)
    assert re.search(r"if\s*\(\s*!\s*dot\s*\)\s*return", body), (
        "the reset loop still dereferences the element without checking it exists")


def test_the_click_handler_reports_its_own_failure():
    """`startScrape` is async and called from an inline onclick, so a rejection is swallowed with
    no console error. It must not be able to fail in silence."""
    m = re.search(r"async function startScrape\(\)\s*\{(.*?)\n\}", _SPA, re.S)
    assert m, "startScrape moved"
    body = m.group(1)
    assert "catch" in body, "startScrape has no catch at all"
    # the reset loop must sit INSIDE the guarded region, not above it
    loop_at = body.find("SCAN_STEP_ORDER.forEach")
    try_at = body.find("try {")
    assert try_at != -1 and try_at < loop_at, (
        "the step reset runs before the try block, so a failure there is invisible to the user")
