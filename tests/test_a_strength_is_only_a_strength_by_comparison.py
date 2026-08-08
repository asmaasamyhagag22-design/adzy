# -*- coding: utf-8 -*-
"""«بيطلع قشور» — the SWOT was 76% transcription, and this is the seam where that was decided.

MEASURED across the six stored brands, 86 items: 14 comparative + 2 site-audit = **19% analysis**;
45 of the brand's own slogans + 20 raw Google result titles = **76% transcription**.

The slogans all entered through `strengths_from_profile`, which copied `value_propositions`
verbatim while the loop DIRECTLY BELOW it filtered `trust_signals` — and the module's own docstring
gives the reason a filter exists: a site "also lists generic ones ('Secure Payments',
'International Shipping'). Keep only signals carrying REAL proof … so Strengths stay
differentiators, not checkout boilerplate." Applied to one list of two.

`swot_priority.py` states the rule on its front page: strengths and weaknesses "are only such in
comparison to the competition … they are relative and not absolute". The comparison was impossible
because nothing comparable was ever kept: MEASURED, `lite_peer_dims` downloads 215,968 bytes of a
peer's homepage and keeps 243 characters of its 3,501 characters of readable text — 6.9% — while
reporting `offerings_count: None` and `bilingual: None` as "LLM-stage only".

Inside the discarded 94%, on AlFouad's own registered peer Bloom Pharmacy: "FAST & FREE SHIPPING",
"SECURE SHOPPING", "CUSTOMER SUPPORT". AlFouad's SWOT lists "Fast Delivery" and "Secure and
Multiple Payment Methods" as its strengths.
"""
from __future__ import annotations

import pytest

from competitor.peer_words import (
    PeerWords, claim_is_commoditised, claim_text_for_comparison, differentiation_note,
    peer_words_from_html, promise_concepts,
)

_PAGE = """
<html><head>
  <link rel="alternate" hreflang="ar" href="/ar"><link rel="alternate" hreflang="en" href="/en">
</head><body>
  <nav><a href="/">Home</a><a href="/honey">Honey</a><a href="/dates">Dates</a>
       <a href="/cart">Cart</a><a href="/login">Log in</a></nav>
  <h1>Try it today</h1><h2>Sukkari Dates</h2>
  <div><span>EGP 24,200.00</span></div>
  <footer><div>FAST &amp; FREE SHIPPING</div><div>SECURE SHOPPING</div>
          <div>CUSTOMER SUPPORT</div></footer>
</body></html>
"""


# =====================================================================
# reading the peer's own page
# =====================================================================

def test_the_peers_categories_and_promises_are_both_read():
    pw = peer_words_from_html(_PAGE)
    assert pw.checked
    assert "Honey" in pw.categories and "Dates" in pw.categories
    assert "free_shipping" in pw.promises
    assert "payment_security" in pw.promises
    assert "support" in pw.promises


def test_chrome_is_not_mistaken_for_a_category():
    """Every storefront theme emits the same furniture. It is not vocabulary about the business."""
    pw = peer_words_from_html(_PAGE)
    lowered = [c.lower() for c in pw.categories]
    for chrome in ("home", "cart", "log in"):
        assert chrome not in lowered


def test_a_price_is_not_a_promise():
    """A price is a short leaf node too. MEASURED: "EGP 24,200.00" was being kept as a badge."""
    pw = peer_words_from_html(_PAGE)
    assert not any("24,200" in b for b in pw.badges)


def test_entity_encoded_arabic_is_decoded():
    """MEASURED on elabdfoods.com, which serves its Arabic as `&#x62D;&#x627;...`. Undecoded, every
    category name reaches a client's SWOT as mojibake."""
    html = "<nav><a>&#x645;&#x643;&#x633;&#x631;&#x627;&#x62A;</a></nav><h1>&#x62A;&#x645;&#x648;&#x631;</h1>"
    pw = peer_words_from_html(html)
    assert pw.checked
    assert any("مكسرات" in c for c in pw.categories)
    assert not any("&#x" in c for c in pw.categories + pw.headings)


def test_a_js_only_shell_is_unknown_not_empty():
    """"This competitor offers nothing" and "we could not read their page" are different facts.
    `lite_scrape` already keeps this rule for its own dimensions."""
    pw = peer_words_from_html("<html><body><div id='root'></div></body></html>")
    assert pw.checked is False
    assert pw.reason


def test_an_unreadable_page_never_raises():
    for bad in ("", None, "<<<not html", "\x00\x01"):
        assert peer_words_from_html(bad or "").checked in (True, False)


# =====================================================================
# the promise is compared, not the sentence
# =====================================================================

def test_the_same_promise_in_different_words_is_recognised():
    """THE measurement that decided this design: AlFouad's badge says "Same-Day Delivery in Cairo &
    Giza"; Bloom's says "FAST & FREE SHIPPING". Same promise family, ZERO shared content words — a
    lexical test scores that 0% and calls a commodity a differentiator."""
    assert "delivery_speed" in promise_concepts("Same-Day Delivery in Cairo & Giza")
    assert "free_shipping" in promise_concepts("FAST & FREE SHIPPING")
    assert "payment_security" in promise_concepts("Secure payments Multiple payment methods")
    assert "payment_security" in promise_concepts("SECURE SHOPPING")


def test_the_concepts_read_arabic_too():
    """D-8 — both sides of the comparison are bilingual because the pages are."""
    assert "delivery_speed" in promise_concepts("توصيل سريع خلال ساعات")
    assert "authenticity" in promise_concepts("منتجات أصلية 100%")
    assert "returns" in promise_concepts("سياسة الإرجاع والاستبدال")


def test_a_promise_two_peers_make_is_commoditised():
    peers = [("A", PeerWords(checked=True, promises=["payment_security"])),
             ("B", PeerWords(checked=True, promises=["payment_security", "returns"]))]
    com, who = claim_is_commoditised("Secure payments", peers)
    assert com is True
    assert set(who) == {"A", "B"}


def test_one_peer_is_not_enough_to_demote():
    """Conservative on purpose: dropping a client's stated advantage needs more than one echo."""
    peers = [("A", PeerWords(checked=True, promises=["payment_security"])),
             ("B", PeerWords(checked=True, promises=["support"]))]
    com, who = claim_is_commoditised("Secure payments", peers)
    assert com is False
    assert who == ["A"]
    assert "1/2" in differentiation_note("Secure payments", peers)


def test_an_unreadable_peer_votes_neither_way():
    """Silence is not evidence. A peer whose page is a JS shell cannot argue in either direction."""
    peers = [("A", PeerWords(checked=True, promises=["returns"])),
             ("B", PeerWords(checked=False))]
    assert claim_is_commoditised("Money back guarantee", peers)[0] is False
    assert "1/1" in differentiation_note("Money back guarantee", peers)


def test_a_product_blurb_is_not_judged_as_a_promise():
    """MEASURED: every one of Abu Auf's "value propositions" is a blurb about one SKU — "macadamia
    roasted and coated in a creamy cheese layer". Judging that against a rival's promise strip
    would be comparing two different kinds of statement, so the test refuses to judge it.

    IT NO LONGER REFUSES TO SPEAK. This used to assert `is None`, which encoded the MECHANISM
    rather than the intent, and the mechanism had a cost an adversarial audit found in a shipped
    record: a fourth strength from the same profile field sat with no annotation beside three
    annotated ones, and nothing told a reader whether it had been checked against the peers and
    passed, or never checked at all. The note that replaced the silence still passes no judgement
    on the claim — it reports that the comparison does not apply, which is the honest third state.
    """
    peers = [("A", PeerWords(checked=True, promises=["returns", "support"]))]
    claim = "Carefully roasted macadamia nuts with a creamy cheese coating"
    assert promise_concepts(claim) == set()
    assert claim_is_commoditised(claim, peers) == (False, [])
    note = differentiation_note(claim, peers)
    assert note, "silence is the one answer a reader cannot interpret"
    # It says the comparison does not apply — it does NOT rule on the claim either way.
    assert "not comparable" in note
    assert "promise the same thing" not in note
    assert "no compared peer promises this" not in note


def test_with_no_readable_peers_nothing_is_claimed():
    assert differentiation_note("Fast delivery", []) is None
    assert differentiation_note("Fast delivery", [("A", PeerWords(checked=False))]) is None


def test_the_page_text_is_compared_not_the_models_paraphrase():
    """The profile stores both. The value is the model's rewrite ("Ensures secure payments with
    multiple payment methods"); the quote is what the page shows ("Secure payments Multiple payment
    methods"), which is the same KIND of string as a rival's badge."""
    vp = {"value": "Ensures secure payments with multiple payment methods",
          "evidence": [{"quote": "Secure payments Multiple payment methods"}]}
    assert claim_text_for_comparison(vp) == "Secure payments Multiple payment methods"
    assert claim_text_for_comparison({"value": "x"}) == "x"
    assert claim_text_for_comparison("plain") == "plain"


# =====================================================================
# the strengths producer uses it
# =====================================================================

def _profile():
    return {"value_propositions": [
        {"value": "Ensures secure payments", "evidence": [{"quote": "Secure payments"}]},
        {"value": "Offers same-day delivery", "evidence": [{"quote": "Same-Day Delivery"}]},
    ]}


def test_a_commoditised_promise_does_not_become_a_strength():
    from competitor.brand_signals import strengths_from_profile
    peers = [("A", PeerWords(checked=True, promises=["payment_security"])),
             ("B", PeerWords(checked=True, promises=["payment_security"]))]
    texts = [s.text for s in strengths_from_profile(_profile(), None, peers=peers)]
    assert not any("secure payments" in t.lower() for t in texts), texts
    assert any("same-day delivery" in t.lower() for t in texts)


def test_a_surviving_promise_carries_the_count():
    """Demoting the commodity is half the value; the other half is being able to say how rare the
    survivor is — that is what turns a slogan into a finding."""
    from competitor.brand_signals import strengths_from_profile
    peers = [("A", PeerWords(checked=True, promises=["support"])),
             ("B", PeerWords(checked=True, promises=["support"]))]
    got = strengths_from_profile(_profile(), None, peers=peers)
    ev = " ".join(s.evidence for s in got)
    assert "no compared peer promises this" in ev


def test_no_peers_reproduces_the_old_behaviour_exactly():
    """Every existing caller passes no peers; their output must not move."""
    from competitor.brand_signals import strengths_from_profile
    a = [(s.text, s.evidence) for s in strengths_from_profile(_profile(), None)]
    b = [(s.text, s.evidence) for s in strengths_from_profile(_profile(), None, peers=[])]
    assert a == b
    assert len(a) == 2
    assert all("—" not in e for _t, e in a)


def test_a_dropped_promise_is_logged_not_silently_lost(caplog):
    """A strength that vanishes without explanation is the same defect as one that appears without
    evidence."""
    import logging

    from competitor.brand_signals import strengths_from_profile
    peers = [("A", PeerWords(checked=True, promises=["payment_security"])),
             ("B", PeerWords(checked=True, promises=["payment_security"]))]
    with caplog.at_level(logging.INFO, logger="competitor.brand_signals"):
        strengths_from_profile(_profile(), None, peers=peers)
    assert "commoditised" in caplog.text


# =====================================================================
# the peer's words survive the whole pipe
# =====================================================================

def test_lite_peer_dims_carries_the_peers_words():
    from competitor.lite_scrape import lite_peer_dims
    dims = lite_peer_dims("https://example.com/", fetch=lambda _u: _PAGE)
    assert dims is not None
    pwords = dims.get("peer_words")
    assert isinstance(pwords, dict)
    assert "free_shipping" in (pwords.get("promises") or [])


def test_the_projection_no_longer_deletes_them():
    """`extract_scraped_dimensions` projects every peer through `_DIM_KEYS`. Anything missing from
    that list is destroyed — which is exactly how `social_platforms` was lost for months."""
    from competitor.matrix import _DIM_KEYS, extract_scraped_dimensions
    assert "peer_words" in _DIM_KEYS
    kept = extract_scraped_dimensions({k: None for k in _DIM_KEYS} | {"peer_words": {"promises": ["x"]}})
    assert kept.get("peer_words") == {"promises": ["x"]}


def test_the_swot_hands_the_peers_to_the_strengths_producer():
    import inspect

    import competitor.swot as s
    src = inspect.getsource(s.synthesize_swot)
    assert "peers=_peers_words" in src
    assert 'get("peer_words")' in src


def test_nothing_here_calls_a_model_or_fetches_again():
    """The whole point is that the evidence was already downloaded and already in memory."""
    import ast
    import inspect

    import competitor.peer_words as pw
    tree = ast.parse(inspect.getsource(pw))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree).lower()
    for forbidden in ("urlopen", "requests", "caller", "generate_content", "gemini", "fetch("):
        assert forbidden not in code, forbidden


# =====================================================================
# a strength is about the COMPANY, not about one product
# =====================================================================

def _vp(value, *pages):
    return {"value": value,
            "evidence": [{"page_url": u, "quote": value} for u in pages]}


def test_a_claim_evidenced_only_on_product_pages_is_about_the_product():
    """MEASURED on the two real profiles, and the separation is clean with no tuning because the
    PROVENANCE is the discriminator:

        Abu Auf  -> /products/macadamia-with-cheese-flavor-100, /products/coconut-200-gram   4/4
        AlFouad  -> /en, /en/collections/types                                               0/5
        Nano Care-> mixed                                                                    3/5

    A promise the business makes is stated where the business speaks. A product's own page speaks
    for that product.
    """
    from competitor.peer_words import is_product_level_claim
    assert is_product_level_claim(_vp(
        "Carefully roasted macadamia nuts with a creamy cheese coating",
        "https://www.abuauf.com/products/macadamia-with-cheese-flavor-100")) is True


def test_a_promise_on_a_site_level_page_is_about_the_business():
    from competitor.peer_words import is_product_level_claim
    assert is_product_level_claim(_vp("Offers same-day delivery in Cairo & Giza",
                                      "https://alfouadpharmacies.com/en")) is False


def test_a_claim_seen_on_both_is_still_a_business_claim():
    """AlFouad's "100% Original Products" is quoted on a product page AND on the homepage. It is a
    business promise that also appears on a product page — the test needs EVERY cited page to be a
    product page before it will demote."""
    from competitor.peer_words import is_product_level_claim
    assert is_product_level_claim(_vp(
        "Guarantees 100% original products",
        "https://alfouadpharmacies.com/en/products/alejon-anti-dandruff-shampoo",
        "https://alfouadpharmacies.com/en")) is False


def test_no_evidence_means_no_claim_either_way():
    """Absence of provenance is not evidence that something is a product blurb."""
    from competitor.peer_words import is_product_level_claim
    assert is_product_level_claim({"value": "x"}) is False
    assert is_product_level_claim("plain string") is False
    assert is_product_level_claim({"value": "x", "evidence": [{"page_url": ""}]}) is False


def test_product_claims_do_not_become_strengths():
    from competitor.brand_signals import strengths_from_profile
    prof = {"value_propositions": [
        _vp("Roasted macadamia with a cheese coating",
            "https://b.com/products/macadamia-100"),
        _vp("Same-day delivery across the country", "https://b.com/en"),
    ]}
    texts = [s.text for s in strengths_from_profile(prof, None)]
    assert texts == ["Same-day delivery across the country"]


def test_a_product_claim_is_logged_not_silently_lost(caplog):
    import logging

    from competitor.brand_signals import strengths_from_profile
    # The slug SPELLS the product, which is what a real store's slug does
    # (`/products/dried-natural-apricot-100-gm`). The abbreviated `mac-100` this fixture used
    # before is not a shape the check can read, and deliberately so — see the test below.
    prof = {"value_propositions": [_vp("Roasted macadamia",
                                       "https://b.com/products/macadamia-100")]}
    with caplog.at_level(logging.INFO, logger="competitor.brand_signals"):
        strengths_from_profile(prof, None)
    assert "about a PRODUCT" in caplog.text


def test_an_unreadable_slug_keeps_the_claim_rather_than_guessing():
    """The asymmetry is deliberate and it was paid for once already.

    Page provenance alone deleted «مسجل بالهيئة القومية لسلامة الغذاء» — a national food-safety
    registration, the most defensible company-level line in nanocare's whole SWOT — because a shop
    prints its trust marks on product pages. So a claim is product-level only when it also NAMES
    the product whose page it sits on, read off that page's own slug.

    When the slug is abbreviated past recognition (`/products/mac-100`) the check cannot tell, and
    it keeps the claim. Keeping a product spec costs one weak line; deleting a regulatory
    credential costs the client's trust in the whole document."""
    from competitor.peer_words import is_product_level_claim
    assert is_product_level_claim(_vp("Roasted macadamia", "https://b.com/products/mac-100")) is False
    assert is_product_level_claim(_vp("Roasted macadamia", "https://b.com/products/macadamia-100")) is True


def test_the_repos_own_definition_of_a_product_page_is_reused():
    """Not a second definition that can drift from `scraper.classify.page_type`."""
    import ast
    import inspect

    import competitor.peer_words as pw
    tree = ast.parse(inspect.getsource(pw.is_product_level_claim))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    assert "is_leaf_detail" in ast.unparse(tree)


# =====================================================================
# the gate stops asking the claim to vouch for itself
# =====================================================================

def test_the_ledger_can_be_built_without_the_fields_it_will_judge():
    """The circle, and the exact size of the cut.

    MEASURED: `_gated` asked a ledger whether a value proposition was sourced, and
    `EvidenceLedger.from_profile` indexed value_propositions AS the evidence it would answer with.
    Every candidate grounded itself: reject rate 0 of 192 across the 25 stored profiles. The repo
    had already found and deleted the identical construction on the trends path — "it built the
    ledger FROM the candidate trend lines themselves ... the gate's reject rate was STRUCTURALLY
    ZERO".

    CORRECTED AFTER A SECOND MEASUREMENT. The first cut excluded the whole field and a test caught
    it: it also deleted the `evidence[].quote`, which is not the claim — it is verbatim page text.
    Across those same profiles, 165 of 192 quotes (86%) DIFFER from their value, most of them in
    another language entirely (an Arabic page line behind an English paraphrase). Deleting them
    dropped true strengths whose only proof was the page itself. So the exclusion is of the
    model's RESTATEMENT, not of the page: `value` goes, quotes stay. Reject rate 0/192 -> 4/192,
    unchanged by the correction, with the collateral damage gone."""
    from grounding import EvidenceLedger
    # The realistic shape (86% of the corpus): the page speaks Arabic, the paraphrase is English.
    prof = {"name": {"value": "B"},
            "value_propositions": [{"value": "Cashews from the best farms in Vietnam",
                                    "evidence": [{"quote": "كاجو من أفضل المزارع في فيتنام",
                                                  "page_url": "https://b.com/"}]}]}
    circular = EvidenceLedger.from_profile(prof)
    honest = EvidenceLedger.from_profile(prof, exclude_fields=("value_propositions",))
    texts = [str(getattr(e, "text", e)).lower() for e in honest.entries]
    # the model's own restatement is gone from the evidence pool ...
    assert not any("cashews from the best farms" in t for t in texts)
    # ... and the page it came from is still there. Cutting a circle must not cut the wire.
    assert any("فيتنام" in t for t in texts)
    assert len(honest.entries) < len(circular.entries)


def test_a_hard_claim_with_no_independent_evidence_is_now_rejected():
    """A pure paraphrase — a value with NO page quote behind it — is what the circle used to
    launder. With the value excluded it has nothing left to ground it, and the gate says so."""
    from competitor.brand_signals import _gated
    from grounding import EvidenceLedger
    prof = {"name": {"value": "B"},
            "value_propositions": [{"value": "Trusted by 500000 clients worldwide"}]}
    circular = EvidenceLedger.from_profile(prof)
    honest = EvidenceLedger.from_profile(prof, exclude_fields=("value_propositions",))
    assert _gated(circular, "Trusted by 500000 clients worldwide") is True   # self-grounding
    assert _gated(honest, "Trusted by 500000 clients worldwide") is False    # nothing backs it


def test_an_exact_echo_of_the_page_is_evidence_not_a_circle():
    """The 14% case, decided by measurement rather than by symmetry.

    When a page line is short, the scraped quote and the paraphrase come out IDENTICAL. That is
    not the model restating itself — the page really does say it, with a URL. The Ledger's job is
    grounding (did we invent this?), not truth (is the brand honest?), so a verbatim page quote
    grounds its claim whether or not it happens to equal the paraphrase. Judging the CLAIM is the
    differentiation test's job, and it runs separately."""
    from competitor.brand_signals import _gated
    from grounding import EvidenceLedger
    prof = {"name": {"value": "B"},
            "value_propositions": [{"value": "100% original products",
                                    "evidence": [{"quote": "100% original products",
                                                  "page_url": "https://b.com/"}]}]}
    honest = EvidenceLedger.from_profile(prof, exclude_fields=("value_propositions",))
    assert _gated(honest, "100% original products") is True


def test_a_claim_free_line_still_passes_and_the_code_says_why():
    """Deliberate, not overlooked: `all([])` is True because a sentence with no number, no
    superlative and no named entity has nothing this engine can check. Failing it would be refusing
    prose, not being strict — and those lines are labelled `internally_supported`, which is the
    honest description. The differentiation test is what judges them."""
    from competitor.brand_signals import _gated
    from grounding import EvidenceLedger
    honest = EvidenceLedger.from_profile({"name": {"value": "B"}},
                                         exclude_fields=("value_propositions",))
    assert _gated(honest, "We care about our customers") is True
    import inspect
    assert "IT STILL PASSES A CLAIM-FREE LINE" in inspect.getdoc(_gated)


def test_the_default_is_the_old_behaviour_exactly():
    """`exclude_fields=None` must index everything — every existing caller is unchanged."""
    from grounding import EvidenceLedger
    prof = {"name": {"value": "B"}, "value_propositions": [{"value": "fast delivery"}]}
    a = EvidenceLedger.from_profile(prof)
    b = EvidenceLedger.from_profile(prof, exclude_fields=None)
    assert len(a.entries) == len(b.entries)


def test_the_swot_judges_strengths_with_the_non_circular_ledger():
    import inspect

    import competitor.swot as s
    src = inspect.getsource(s.synthesize_swot)
    assert 'exclude_fields=("value_propositions", "trust_signals")' in src
    assert "strengths_from_profile(profile, _self_judge_ledger" in src
