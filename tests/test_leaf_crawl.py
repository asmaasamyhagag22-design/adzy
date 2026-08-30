"""FIX-A — universal leaf discovery (2026-07-11 owner campaign, front 3).

The crawl used to be structurally depth-1 (frontier built once from homepage+sitemap) and the
leaf notion was Shopify-only, so education/services/clinic sites never reached their individual
courses/services and the studio picker showed nothing. These tests pin the three pieces:
`is_leaf_detail` (universal leaf shapes), `_bfs_new_candidates` (frontier re-seeding, pure),
and the picker's new priority sources (profile offerings -> leaf pages -> Shopify fallback).
Hermetic — no network, no Playwright.
"""
from __future__ import annotations

import json

from scraper.classify.page_type import is_leaf_detail, is_product_detail
from scraper.crawler import _bfs_new_candidates
from scraper.schemas import LinkCategory, PageTier


class _L:
    def __init__(self, href, anchor="", category=LinkCategory.INTERNAL):
        self.href, self.anchor_text, self.category = href, anchor, category


# ---------------------------------------------------------------- is_leaf_detail

def test_leaf_detail_keeps_shopify_behavior():
    assert is_leaf_detail("https://s.com/products/hair-oil")            # existing rule intact
    assert is_leaf_detail("https://s.com/collections/eid/products/x")
    assert not is_leaf_detail("https://s.com/products")                 # a list, not a leaf
    assert is_product_detail("https://s.com/products/hair-oil")         # old API untouched


def test_leaf_detail_covers_education_services_menu():
    assert is_leaf_detail("https://e.com/courses/python-101")           # course leaf
    assert is_leaf_detail("https://c.com/services/teeth-whitening")     # service leaf
    assert is_leaf_detail("https://r.com/menu/koshari")                 # dish leaf
    assert is_leaf_detail("https://e.com/tracks/data-engineering")
    assert not is_leaf_detail("https://e.com/courses")                  # category list
    assert not is_leaf_detail("https://e.com/about/team")               # not an offering segment


def test_leaf_detail_matches_cms_id_query_shape():
    # nti's real shape: an offering-named CMS file + an id-bearing query param
    assert is_leaf_detail("https://nti.sci.eg/eta/courses.php?catID=601")
    assert is_leaf_detail("https://x.com/track.php?trackID=4")
    assert not is_leaf_detail("https://x.com/courses.php?page=2")       # faceted browse, not an id
    assert not is_leaf_detail("https://x.com/news.php?id=7")            # not an offering segment


# ---------------------------------------------------------------- _bfs_new_candidates

def test_bfs_reseeds_internal_offering_links_leaf_first():
    frontier = {"https://e.com/about"}
    links = [
        _L("https://e.com/courses/python-101", "Python 101"),
        _L("https://e.com/blog/some-post", "read more"),                # SKIP (blog detail)
        _L("https://other.com/courses/x", "external"),                  # different host
        _L("https://e.com/about", "about"),                             # already planned
        _L("https://e.com/testimonials", "reviews"),                    # MEDIUM
        _L("https://e.com/faq#top", "faq", category=LinkCategory.EXTERNAL),  # not internal
    ]
    out = _bfs_new_candidates(links, "https://e.com/", frontier, max_new=10)
    urls = [c[0] for c in out]
    assert "https://e.com/courses/python-101" in urls[0]                # leaf ordered FIRST
    assert all("other.com" not in u and "blog" not in u for u in urls)
    assert "https://e.com/about" not in urls                            # deduped
    # every accepted candidate was added to the frontier set (no double-plan later)
    assert set(urls) <= frontier | set(urls)


def test_bfs_respects_max_new():
    links = [_L(f"https://e.com/courses/c{i}") for i in range(20)]
    out = _bfs_new_candidates(links, "https://e.com/", set(), max_new=5)
    assert len(out) == 5


# ---------------------------------------------------------------- picker sources

def _nti_like_manifest():
    return {
        "pages": [
            {"final_url": "https://e.com/", "page_type": "homepage", "text_blocks": []},
            {"final_url": "https://e.com/courses/advanced-web-design", "page_type": "courses",
             "text_blocks": [{"tag": "h1", "text": "Advanced Web Design (72 Hours)"}]},
            {"final_url": "https://e.com/courses", "page_type": "courses", "text_blocks": []},
        ],
        "images_of_interest": [
            {"role": "content", "src": "https://e.com/img/web-design.jpg", "alt": "web design lab"},
        ],
    }










def test_compound_cms_filenames_are_leaves_owner_popup_bug():
    # OWNER POPUP BUG (nti dey/coursesev.php?catID=205 — the course-details modal never
    # scraped): 'coursesev' is a compound CMS filename; exact matching missed it so the
    # page never earned leaf priority and sat unfetched. Prefix match fixes the family.
    assert is_leaf_detail("https://www.nti.sci.eg/dey/coursesev.php?catID=205")
    assert is_leaf_detail("https://x.com/serviceslist.php?serviceID=7")
    assert not is_leaf_detail("https://x.com/coursesev.php?page=2")     # still needs an id param
    assert not is_leaf_detail("https://x.com/contactus.php?id=3")       # not an offering prefix


def test_regression_fixture_coursesev_modal_resolves():
    # The REAL page (saved fixture): the ajax resolver must reconstruct the modal URLs
    # (dey/pages/modules/<data-target>.html) exactly as it does for the eta family.
    from pathlib import Path
    from scraper.ajax_details import discover_ajax_details
    html = Path("tests/fixtures/nti_coursesev_catid205.html").read_text(encoding="utf-8")
    # hermetic: serve the popup JS template from a stub instead of the network
    js = 'popup.load("pages/modules/" + link.getAttribute("data-target") + ".html");'
    out = list(discover_ajax_details(html, "https://www.nti.sci.eg/dey/coursesev.php?catID=205",
                                     fetch=lambda u: js))
    assert any(u.endswith("/dey/pages/modules/2631.html") for u in out)
    assert len(out) >= 5
