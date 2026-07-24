# -*- coding: utf-8 -*-
"""A protection vendor's script on a working page is not a block.

MEASURED (pepsi.com, 2026-07-21) — and this reverses an earlier conclusion in this same session.
With a consistent browser identity the site responds:

    status=200 · html=35,039 bytes · title='Pepsi Official Site | Refreshing the World w…'

That is a fully served page. Our crawler still classified it BOT_PROTECTION because the HTML
contains the string `_incapsula_` — the vendor's own script, which ships on every page Imperva
protects, including the ones served happily to real users.

The marker says "this site uses a protection vendor". It does NOT say "we were refused". Treating
presence-of-vendor as refusal cost us the entire site: 0 pages, a hollow profile, and a fallback
that was never needed. (My earlier 403s came from a bare headless browser with no user agent — a
different request, not this one.)

So a marker only counts as a block when the page ALSO looks like an interstitial: no real title,
almost no content, or an explicit challenge phrase. Hermetic."""
from __future__ import annotations

from scraper.fetcher import looks_bot_protected


def _page(html: str, *, title: str = "", status: int = 200):
    return {"html": html, "title": title, "status": status}


def test_a_served_page_carrying_a_vendor_script_is_not_a_block():
    html = "<html><head><title>Pepsi Official Site</title></head><body>" + ("real content " * 900) \
           + "<script src='/_incapsula_/x.js'></script></body></html>"
    assert not looks_bot_protected(**_page(html, title="Pepsi Official Site"))


def test_an_actual_interstitial_is_still_caught():
    html = "<html><head></head><body><script>_incapsula_</script></body></html>"
    assert looks_bot_protected(**_page(html, title=""))


def test_an_explicit_challenge_is_caught_however_long_the_page():
    html = ("<html><head><title>Just a moment...</title></head><body>"
            + ("padding " * 900) + "Checking your browser before accessing</body></html>")
    assert looks_bot_protected(**_page(html, title="Just a moment..."))


def test_a_403_is_a_block_whatever_the_body_says():
    assert looks_bot_protected(**_page("<html><body>ok</body></html>", title="ok", status=403))


def test_an_ordinary_page_is_never_flagged():
    html = "<html><head><title>Volume Cafe</title></head><body>" + ("menu " * 500) + "</body></html>"
    assert not looks_bot_protected(**_page(html, title="Volume Cafe"))


def test_an_empty_200_is_empty_not_blocked():
    """CORRECTED while writing this file: an empty page served with 200 is an EMPTY PAGE, not a
    block. Labelling it BOT_PROTECTION would re-introduce the same class of false positive this
    file exists to remove — telling the owner "this site blocks crawlers" when the real story is
    "the page rendered nothing". The 0-page case has its own guard
    (`competitor/full_run.scrape_yielded_nothing`) and its own honest message."""
    assert not looks_bot_protected(**_page("", title=""))
