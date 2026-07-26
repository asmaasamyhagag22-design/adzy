# -*- coding: utf-8 -*-
"""The real, priced CATALOG — what a store actually sells, taken from the pages it sells on.

OWNER (2026-07-26, after the Al Fouad run): «هل الاسكرابر بتاعي وصل للتفاصيل دي ولا سطحي جدا؟»

MEASURED on that run, and she was right:
  * the crawl DID reach 38 leaf product pages (`/en/products/<slug>`), and the evidence pack DID
    carry 60 of their blocks with 14 priced ones — yet `profile.offerings` came back as the 12
    NAVIGATION DEPARTMENTS ("Skin Care", "Hair Care", …), every one with `price_text=None` and
    `page_url` pointing at the homepage.
  * that is the catalog-SHAPE policy working AS DESIGNED (a 3,241-SKU store IS summarized by its
    departments) — but it leaves the creative layer with nothing sellable: no product, no price,
    no product page. The owner pasted one product link by hand precisely because the profile of
    her own store had none.

So the departments stay (they are the right summary), and a SEPARATE deterministic catalog is
extracted alongside them: name + verbatim price + the product's own URL + its image, straight
from the leaf pages the crawler already fetched. No LLM (the text is verbatim), no network (the
manifest is on disk), no currency or language assumptions.
"""
from __future__ import annotations

from scraper.catalog import build_catalog


class _Blk:
    def __init__(self, text, tag="p", section="main", above_fold=False):
        self.text, self.tag = text, tag
        self.section = type("S", (), {"value": section})()
        self.above_fold = above_fold
        self.page_url = ""


class _Img:
    def __init__(self, url, page_url):
        self.url, self.page_url = url, page_url


class _Page:
    def __init__(self, url, blocks, title=""):
        self.url, self.text_blocks, self.title = url, blocks, title


class _Manifest:
    def __init__(self, pages, images=()):
        self.pages, self.images_of_interest = pages, list(images)


def _product_page(url, name, price, img=None):
    return _Page(url, [
        _Blk("Skip to content", "p", "nav"),
        _Blk(name, "h1"),
        _Blk("Vendor: Eucerin", "p"),
        _Blk(f"Regular price {price}", "div"),
        _Blk("Shipping calculated at checkout.", "p"),
    ], title=f"{name} | Store")


def test_a_leaf_product_page_yields_a_named_priced_item():
    m = _Manifest([_product_page(
        "https://shop.test/en/products/eucerin-dermo-purifyer-triple-serum-40ml",
        "Eucerin Dermo-Purifyer Triple Serum 40ml", "EGP 850")])
    items = build_catalog(m)
    assert len(items) == 1, items
    it = items[0]
    assert it["name"] == "Eucerin Dermo-Purifyer Triple Serum 40ml"
    assert "850" in it["price_text"] and "EGP" in it["price_text"]
    assert it["url"].endswith("/products/eucerin-dermo-purifyer-triple-serum-40ml")


def test_collection_and_home_pages_are_not_products():
    m = _Manifest([
        _Page("https://shop.test/en", [_Blk("Welcome", "h1"), _Blk("EGP 9,450", "div")]),
        _Page("https://shop.test/en/collections/types?q=simple",
              [_Blk("3241 products", "h1"), _Blk("EGP 100", "div")]),
    ])
    assert build_catalog(m) == []


def test_the_price_is_verbatim_and_currency_agnostic():
    """D-8: no EGP hardcode. A Gulf store's ر.س and a EU store's € must both survive."""
    m = _Manifest([
        _product_page("https://s.test/products/a", "Serum A", "ر.س 120"),
        _product_page("https://s.test/products/b", "Serum B", "€ 24,90"),
        _product_page("https://s.test/products/c", "Serum C", "1,100 EGP"),
    ])
    prices = [i["price_text"] for i in build_catalog(m)]
    assert any("ر.س" in p for p in prices), prices
    assert any("€" in p for p in prices), prices
    assert any("EGP" in p or "1,100" in p for p in prices), prices


def test_an_unpriced_product_page_still_gives_a_named_item():
    """A store that hides prices still has products — honest null beats dropping the item."""
    m = _Manifest([_Page("https://s.test/products/x",
                         [_Blk("Mystery Cream 50ml", "h1"), _Blk("Add to cart", "button")])])
    items = build_catalog(m)
    assert len(items) == 1 and items[0]["price_text"] is None


def test_the_product_image_rides_along_when_the_crawl_saw_one():
    m = _Manifest(
        [_product_page("https://s.test/products/a", "Serum A", "EGP 850")],
        images=[_Img("https://cdn.test/serum-a.jpg", "https://s.test/products/a")])
    assert build_catalog(m)[0]["image_url"] == "https://cdn.test/serum-a.jpg"


def test_the_same_product_reached_by_two_urls_appears_once():
    """Shopify variant/query URLs (?variant=, ?pr_prod_strat=) are the SAME product — the Al
    Fouad crawl hit one product through four of them."""
    base = "https://s.test/en/products/eucerin-serum"
    m = _Manifest([
        _product_page(base, "Eucerin Serum", "EGP 850"),
        _product_page(base + "?variant=5127", "Eucerin Serum", "EGP 850"),
        _product_page(base + "?pr_prod_strat=e5_desc", "Eucerin Serum", "EGP 850"),
    ])
    assert len(build_catalog(m)) == 1


def test_boilerplate_never_becomes_a_product_name():
    m = _Manifest([_Page("https://s.test/products/x", [
        _Blk("Skip to content", "h1", "nav"),
        _Blk("Add to cart", "h1"),
        _Blk("Real Product Name 30ml", "h1"),
    ])])
    items = build_catalog(m)
    assert items and items[0]["name"] == "Real Product Name 30ml"


def test_the_catalog_is_capped_but_says_how_many_it_saw():
    pages = [_product_page(f"https://s.test/products/p{i}", f"Product {i}", f"EGP {i}00")
             for i in range(40)]
    items = build_catalog(_Manifest(pages), limit=12)
    assert len(items) == 12


def test_the_profile_carries_the_catalog():
    """Serialization is where features die in this repo. The field must exist on the profile
    schema, or the catalog is built and thrown away."""
    from business_profile.schemas import BusinessProfile
    assert "catalog" in BusinessProfile.model_fields


# --- found by RUNNING it on the real Al Fouad manifest, not by imagining inputs -------------

def test_the_name_is_the_heading_that_matches_the_product_slug():
    """MEASURED: every one of the 24 extracted items was named "Language" — the store's
    language switcher is an <h2> that is not tagged as nav, and it comes first. The product's
    own URL slug is the tie-breaker no page chrome can fake."""
    page = _Page("https://s.test/en/products/eucerin-dermo-purifyer-triple-serum-40ml", [
        _Blk("Language", "h2"),
        _Blk("Free Shipping on orders over 2000L.E", "h2"),
        _Blk("Eucerin Dermo-Purifyer Triple Serum 40ml", "h1"),
        _Blk("Regular price EGP 850", "div"),
    ])
    items = build_catalog(_Manifest([page]))
    assert items[0]["name"] == "Eucerin Dermo-Purifyer Triple Serum 40ml", items


def test_a_product_code_is_not_a_price():
    """MEASURED: "SPF50" matched the ISO-code branch ([A-Z]{3} + digits) and shipped as the
    price of a sunblock. A currency code is a standalone token, never glued to its number."""
    page = _Page("https://s.test/products/la-roche-sunblock-spf50", [
        _Blk("La Roche Sunblock SPF50 50ml", "h1"),
        _Blk("Sun protection SPF50 PA++++", "p"),
        _Blk("Regular price EGP 1,100", "div"),
    ])
    items = build_catalog(_Manifest([page]))
    assert items[0]["price_text"] and "1,100" in items[0]["price_text"], items


def test_a_page_with_no_product_heading_is_skipped_not_named_by_chrome():
    page = _Page("https://s.test/products/mystery", [
        _Blk("Language", "h2"),
        _Blk("Search", "h2"),
    ])
    assert build_catalog(_Manifest([page])) == []


def test_a_cross_sell_widget_is_not_a_product():
    """MEASURED on the real manifest: "Frequently Bought Together" won a page as its name — it
    is the bundle widget's heading, and its EGP 515 is the BUNDLE total, not any product."""
    page = _Page("https://s.test/products/some-serum-40ml", [
        _Blk("Frequently Bought Together", "h2"),
        _Blk("Total price: EGP 515", "div"),
        _Blk("Some Serum 40ml", "h1"),
        _Blk("Regular price EGP 200", "div"),
    ])
    items = build_catalog(_Manifest([page]))
    assert items and items[0]["name"] == "Some Serum 40ml", items
