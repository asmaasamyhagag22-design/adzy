# -*- coding: utf-8 -*-
"""Reviews printed on the brand's own homepage must reach the analysis.

The owner sent a screenshot of alfouadpharmacies.com's testimonials section — star ratings, named
customers (عبد الرحمن بدر · شمس محمد · ريهام عزت · فاطمة إسماعيل), real Arabic review text — after
I had concluded from the Instagram snapshot alone that the brand had no customer voice. She was
right; I had looked in one place.

MEASURED IN THE SCRAPE WE ALREADY HAD:
    "testimonial" in raw/en.html                 116 occurrences
    the review text «التوصيل سريع ومتابعه العملاء ممتازة»   present
    the reviewer's name                           present, as <img alt="Fatma Ismail">
    the page record in manifest.json              contains the review text
    brand.testimonials                            0

So the page was crawled, the text was extracted, it reached the stage that reads it — and nothing
came out. The reason is precise and is not a bug in the rule that dropped it: the LLM extractor is
instructed to take testimonials "only when a real quote with attribution is present", and the
attribution lives in an IMAGE ALT ATTRIBUTE. Text extraction drops alt text, so a properly
attributed review arrives looking anonymous, and a rule that exists to prevent invented quotes
correctly refuses it. A good rule, starved of an input.

The fix is therefore at the DOM, where the quote and its attribution are still adjacent — not a
loosening of the rule.
"""
from __future__ import annotations

# The PORTRAIT COMES FIRST, then the sentence — verified against the real page rather than assumed:
#   <img alt="Abdelrahman Badr">  at 712573
#   «التوصيل سريع ومتابعه العملاء…»  at 714946
#   the next portrait (Fatma Ismail) at 715597, i.e. AFTER that quote.
# My first fixture had it the other way round and the extractor "passed" while attributing all nine
# of the client's reviews to the wrong customer.
_SHOPIFY = """
<section class="testimonial-section">
  <div class="testimonial-card">
    <div class="inner"><div class="row">
      <img src="//cdn.shop/files/x.jpg?width=360" alt="Fatma Ismail"
           srcset="//cdn.shop/files/x.jpg?width=50 50w, //cdn.shop/files/x.jpg?width=100 100w" />
      <svg class="star"></svg><svg class="star"></svg>
      <p>التوصيل سريع ومتابعه العملاء ممتازة اتمنى لكم دوام النجاح</p>
    </div></div>
  </div>
  <div class="testimonial-card">
    <div class="inner">
      <img src="//cdn.shop/files/y.jpg" alt="شمس محمد" />
      <p>المنتج جودته ممتازة والتعامل كويس جدا</p>
    </div>
  </div>
</section>
"""


def test_a_quote_keeps_the_name_that_sits_in_the_image_alt():
    from scraper.extractors.testimonials import extract_testimonials
    out = extract_testimonials(_SHOPIFY)
    assert len(out) == 2, out
    first = out[0]
    assert "التوصيل سريع" in first["quote"]
    assert "cdn.shop" not in first["quote"], (
        "the image tag leaked into the quote — srcset became the review text")
    assert first["author"] == "Fatma Ismail", (
        "the attribution is in the img alt; without it the quote reads as anonymous and the "
        "downstream rule correctly discards it")


def test_arabic_names_survive():
    from scraper.extractors.testimonials import extract_testimonials
    out = extract_testimonials(_SHOPIFY)
    assert out[1]["author"] == "شمس محمد"


def test_an_unattributed_quote_is_not_invented_into_a_testimonial():
    """The rule this restores input to must keep working: no name, no testimonial."""
    from scraper.extractors.testimonials import extract_testimonials
    html = '<div class="testimonial"><p>خدمة ممتازة جدا وسريعة</p></div>'
    assert extract_testimonials(html) == []


def test_decorative_and_empty_alts_are_not_authors():
    from scraper.extractors.testimonials import extract_testimonials
    html = ('<div class="testimonial-card"><p>خدمة ممتازة جدا وسريعة</p>'
            '<img src="a.png" alt="" /><img src="b.png" alt="icon" /></div>')
    assert extract_testimonials(html) == []


def test_a_page_with_no_testimonials_yields_nothing():
    from scraper.extractors.testimonials import extract_testimonials
    assert extract_testimonials("<html><body><p>Buy now</p></body></html>") == []
    assert extract_testimonials("") == []


def test_it_finds_the_real_thing_in_the_real_scrape():
    """The end-to-end claim, against the actual saved page rather than a fixture."""
    import pathlib

    from scraper.extractors.testimonials import extract_testimonials
    root = pathlib.Path(__file__).resolve().parents[1]
    scrapes = sorted(root.glob("scrapes/alfouadpharmacies_com_*"))
    if not scrapes:
        import pytest
        pytest.skip("no alfouad scrape on this machine")
    page = scrapes[-1] / "raw" / "en.html"
    if not page.is_file():
        import pytest
        pytest.skip("en.html not in this scrape")
    out = extract_testimonials(page.read_text(encoding="utf-8", errors="replace"))
    assert out, "the saved page contains 116 'testimonial' hits and the extractor found none"
    assert any(t["author"] for t in out), "every testimonial came out unattributed"

    # ATTRIBUTION, pinned against the real page. Getting this backwards is worse than finding
    # nothing: it puts one customer's words under another customer's name on a client deliverable.
    by_author = {t["author"]: t["quote"] for t in out}
    assert "Abdelrahman Badr" in by_author, sorted(by_author)
    assert "التوصيل سريع ومتابعه العملاء" in by_author["Abdelrahman Badr"], (
        f"wrong attribution: {by_author['Abdelrahman Badr'][:80]!r}")
    for quote in by_author.values():
        assert "cdn" not in quote and "srcset" not in quote, f"markup leaked: {quote[:80]!r}"
