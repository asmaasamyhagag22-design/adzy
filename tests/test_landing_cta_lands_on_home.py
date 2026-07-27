# -*- coding: utf-8 -*-
"""Entering the app from the landing page lands on Home.

Owner, 2026-07-26: «عايزه اول ما اضغط ع كريت ناو اروح ع صفحه ال هوم».

MEASURED before the change: all seven landing CTAs — "Experience Now", "Create Now", "Start
Creating", "Start Now", "Create Yours" — pointed at `/app?studio=1`, and index.html ends with

    if (new URLSearchParams(location.search).get('studio') === '1') { goStudio(); }

and `goStudio()` calls `showPage("studio"); showStudioCreate();`. So every door into the product
skipped Home and dropped the visitor straight onto the Create canvas — before a brand is chosen,
which is also the one place the Create page cannot do anything useful.

`page-home` is already the SPA's default (`<div id="page-home" class="page active">`), so the fix
is to stop overriding it, not to add a redirect.

The `?studio=1` HANDLER stays: old links and bookmarks still work, and it remains the way to deep
link into the studio deliberately. What changes is that the landing's own buttons no longer use it.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LANDING = (_ROOT / "frontend" / "landing.html").read_text(encoding="utf-8", errors="replace")
_SPA = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")


def test_no_landing_cta_deep_links_past_home():
    """Every link from the landing into the app must open the app at its own front page."""
    deep = re.findall(r'href="(/app\?[^"]*)"', _LANDING)
    assert not deep, (
        f"{len(deep)} landing link(s) still skip Home: {sorted(set(deep))}")


def test_the_landing_still_has_a_way_in():
    """Guard against 'fixing' this by deleting the buttons."""
    links = re.findall(r'href="(/app[^"]*)"', _LANDING)
    assert len(links) >= 5, f"only {len(links)} links into the app remain"
    assert all(l == "/app" for l in links), f"unexpected targets: {sorted(set(links))}"


def test_home_is_the_page_that_opens():
    assert '<div id="page-home" class="page active">' in _SPA, (
        "Home is no longer the default active page, so landing on /app would show something else")


def test_the_deep_link_handler_survives_for_existing_links():
    """A bookmarked /app?studio=1 must still work — the param is not being retired, only unused
    by the landing's own buttons."""
    assert "get('studio') === '1'" in _SPA
    assert "goStudio()" in _SPA
