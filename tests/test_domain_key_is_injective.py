# -*- coding: utf-8 -*-
"""Two different sites must never share one brand record.

`domain_key_from_url` mapped BOTH "." and "-" to "_", so `foo-bar.com` and `foo.bar.com` produced
the identical key `foo_bar_com`. The key is the filename in the brand store, so the second site
scraped overwrote the first, and every later step — SWOT, strategy, plan, posters — then ran on a
blend of two unrelated companies while the UI showed one name. Nothing anywhere would report it.

THE FIX KEEPS EVERY EXISTING KEY. "." still becomes "_"; only "-" changes, to "__". A valid web
hostname is `[a-z0-9]` labels joined by "." with internal "-" only, so it can contain neither "_"
nor two adjacent separators. That makes runs of underscores in the encoded form exactly one (from
".") or two (from "-"), and the mapping injective. The three brands already on disk have no hyphen,
so their keys are unchanged and their files stay reachable.

The frontend computes the same key independently (`domainKeyFromUrl`), so it is asserted here too:
if the two drift, the browser asks for a record the server stores under a different name."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.brand_translate import domain_key_from_url as key


def test_a_hyphen_and_a_dot_no_longer_collide():
    assert key("https://foo-bar.com/") != key("https://foo.bar.com/"), \
        "two unrelated sites still share one brand record"


def test_the_keys_already_on_disk_are_unchanged():
    """A new scheme that renamed existing keys would orphan the stored brands."""
    assert key("https://nanocareegypt.com/ar-eg/") == "nanocareegypt_com"
    assert key("https://www.pepsi.com/") == "pepsi_com"
    assert key("https://volumeqatar.com/food%20menu/") == "volumeqatar_com"


@pytest.mark.parametrize("hosts", [
    ("a-b.com", "a.b.com"),
    ("my-shop.co.uk", "my.shop.co.uk"),
    ("x-y-z.io", "x.y.z.io", "x-y.z.io", "x.y-z.io"),
])
def test_distinct_hosts_get_distinct_keys(hosts):
    keys = [key("https://" + h + "/") for h in hosts]
    assert len(set(keys)) == len(keys), f"collision among {hosts}: {keys}"


def test_the_key_stays_filesystem_safe():
    for url in ("https://foo-bar.com/", "https://a.b.c.d.example/", "https://x--y.com/"):
        assert re.fullmatch(r"[a-z0-9_]+", key(url)), key(url)


def test_the_path_never_affects_the_key():
    assert key("https://x.com/a-b/c") == key("https://x.com/") == "x_com"


def test_an_unusual_host_still_gets_a_unique_key():
    """Underscores are not legal in a web hostname; if one appears anyway it must not alias."""
    assert key("https://foo_bar.com/") != key("https://foo.bar.com/")
    assert key("https://foo_bar.com/") != key("https://foo-bar.com/")


def test_the_frontend_computes_the_same_key():
    """The browser derives the key itself. If the two implementations drift, it requests a record
    the server filed under another name — the collision bug wearing a different hat."""
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    # THE DEFINITION, not the first mention. `html.find("domainKeyFromUrl")` landed on whichever
    # CALL SITE happened to appear earliest in the file, so adding a caller above the definition
    # made this test read someone else's function body and fail on code it never looked at.
    fn_at = html.find("function domainKeyFromUrl")
    assert fn_at > 0, "the frontend no longer derives a domain key — update this test"
    body = html[fn_at:fn_at + 500]
    assert '"__"' in body or "'__'" in body, (
        "the frontend still maps '-' to '_' — it will disagree with the server on hyphenated hosts")
