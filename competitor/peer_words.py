# -*- coding: utf-8 -*-
"""What the competitor says about itself — from HTML we have already downloaded.

THE MEASUREMENT THAT FORCED THIS. `lite_peer_dims` fetches a peer's homepage and keeps six numbers
from the anchor tags. Measured on a real Abu Auf peer (zadnaeg.com): **215,968 bytes downloaded,
3,501 characters of readable text inside it, 243 characters kept — 6.9%.** In the 94% it discards
were that competitor's entire product map (honey, dates, oils, spices, health drinks, peanut
butter, Turkish coffee, Arabic coffee, baked goods, nuts), their monthly offers, and the fact that
they publish in both Arabic and English. In the same breath the dims dict reports
`offerings_count: None` and `bilingual: None` with the comment "LLM-stage only" — announcing that
we do not know things we are holding in memory.

WHY THAT IS THE ROOT OF "THE SWOT IS ALL SURFACE". The owner's words: «بيطلع قشور». Measured across
six brands, 76% of SWOT items are transcription (the brand's own slogans, or Google result titles)
and 19% are analysis. Every analytical item comes from ONE source: a comparison against a peer. And
`competitor/swot_priority.py` carries the reason on its own front page — "strengths and weaknesses
are only such in comparison to the competition… they are relative and not absolute". The system
could not compare because it never kept anything comparable. "Fast Delivery" is a strength only if
the competitor is slow, and nothing here knew whether the competitor was slow.

WHAT THIS IS NOT. No model is called, no extra request is made, nothing is inferred. It reads text
that is already in the string `lite_peer_dims` already has, and returns None when the page does not
actually say anything — a JS-only shell yields nothing rather than a confident empty list, which is
the same rule `lite_peer_dims` keeps for its own dimensions.

TWO THINGS MEASURED ON REAL PEERS AND HANDLED HERE, because a naive version ships garbage into a
client's deliverable:
  * elabdfoods.com serves its Arabic as HTML entities (`&#x62D;&#x627;...`) — unescaped here, or
    every category name reaches the SWOT as mojibake;
  * every storefront theme litters the nav and headings with UI chrome — "Popup Title", "تعديل",
    "تمت إضافة المنتج إلى سلة التسوق", "Skip to content" — which is not vocabulary about the
    business. Dropped by shape and by a bilingual chrome list.
"""
from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Chrome a storefront theme emits regardless of what it sells. Bilingual because the pages are, and
# matched on the WHOLE label so a category that merely contains one of these words survives.
_CHROME = {
    "home", "search", "cart", "log in", "login", "sign in", "sign up", "account", "my account",
    "menu", "close", "skip to content", "language", "currency", "checkout", "wishlist",
    "popup title", "edit", "share", "view all", "shop all", "all products", "back", "next",
    "previous", "submit", "subscribe", "newsletter", "contact", "contact us", "about",
    "about us", "blog", "faq", "terms", "privacy", "shipping", "returns",
    "الرئيسية", "الرئيسيه", "بحث", "ابحث", "السلة", "عربة التسوق", "تسجيل الدخول", "حسابي",
    "القائمة", "إغلاق", "اغلاق", "لغة", "اللغة", "العملة", "الدفع", "المفضلة", "تعديل",
    "مشاركة", "عرض الكل", "جميع المنتجات", "رجوع", "التالي", "السابق", "اشتراك",
    "تواصل معنا", "اتصل بنا", "من نحن", "معلومات عنا", "المدونة", "الشروط", "الخصوصية",
}

# A label that is really a sentence about the cart/checkout, not a category.
_SENTENCE_NOISE = re.compile(
    r"(added to (your )?cart|added to your|تمت إضافة|أضيف إلى|تمت الاضافة|عربة التسوق"
    r"|out of stock|نفدت الكمية|sold out)", re.I)

_MIN_LABEL, _MAX_LABEL = 2, 34
_MAX_HEADING = 90


@dataclass
class PeerWords:
    """A peer's own vocabulary. `checked=False` means the page said nothing readable — which is
    NOT the same as a peer that offers nothing, and must never be rendered as one."""
    checked: bool = False
    reason: str = ""
    categories: list = field(default_factory=list)   # their product/section map, their words
    headings: list = field(default_factory=list)     # their hero lines
    badges: list = field(default_factory=list)       # a SAMPLE of the promise strip, for evidence
    promises: list = field(default_factory=list)     # the concepts they promise — the ANSWER
    languages: list = field(default_factory=list)    # declared editions (hreflang)

    @property
    def vocabulary(self) -> str:
        """Everything they say, lowercased — the haystack a differentiation test searches."""
        return " ".join(self.categories + self.headings + self.badges).lower()


# THE PROMISES ARE NOT IN THE NAV — measured, and it is why the first version of this module found
# nothing. A storefront's nav is its CATEGORY MAP ("Shop K-Beauty", "Dermocosmetics"); the promises
# live in a badge strip built from short leaf text nodes.
#
# MEASURED on Bloom Pharmacy, one of AlFouad's own registered peers: 302 short text nodes, and
# among them "FAST & FREE SHIPPING", "SECURE SHOPPING", "CUSTOMER SUPPORT". AlFouad's SWOT lists
# "Fast Delivery" and "Secure and Multiple Payment Methods" as its STRENGTHS. The rival says the
# same thing on its own homepage, and the system had that page in memory when it decided.
_BADGE_MIN, _BADGE_MAX = 6, 60

# A price is a short leaf node too, and it is not a promise. Matched with the currency word
# attached, because "EGP 24,200.00" is what a storefront actually emits.
_PRICEY = re.compile(
    r"^\s*(?:[A-Z]{2,4}\s*)?[\d][\d\s.,]*\s*(?:[A-Z]{2,4}|[%$£€]|ج\.?م|ر\.?س|د\.?إ)?\s*$"
    r"|^\s*(?:from|starting at|ابتداءً من|يبدأ من)\b", re.I)


def _clean(raw: str) -> str:
    """Tag-stripped, entity-decoded, whitespace-collapsed."""
    s = re.sub(r"<[^>]+>", " ", raw or "")
    s = _html.unescape(s)
    # a second pass: some themes double-encode (`&amp;#x62D;`)
    if "&#" in s or "&amp;" in s:
        s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _usable(label: str, *, limit: int) -> bool:
    if not (_MIN_LABEL <= len(label) <= limit):
        return False
    if label.lower() in _CHROME or _SENTENCE_NOISE.search(label):
        return False
    # a label that is only punctuation, digits or a currency amount says nothing about the business
    return bool(re.search(r"[^\W\d_]", label, re.UNICODE))


def peer_words_from_html(html_text: str) -> PeerWords:
    """The peer's own vocabulary, or `checked=False`. Never raises, never calls a model."""
    if not html_text or not html_text.strip():
        return PeerWords(reason="no HTML")
    try:
        body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text,
                      flags=re.S | re.I)

        heads: list = []
        for h in re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", body, re.S | re.I):
            t = _clean(h)
            if _usable(t, limit=_MAX_HEADING) and t not in heads:
                heads.append(t)

        # The nav is the category map. Fall back to the whole anchor inventory when a theme
        # ships no <nav> — a site with no nav still names its sections in its links.
        navs = re.findall(r"<nav[^>]*>(.*?)</nav>", body, re.S | re.I)
        scope = " ".join(navs) if navs else body
        cats: list = []
        seen: set = set()
        for a in re.findall(r"<a[^>]*>(.*?)</a>", scope, re.S | re.I):
            t = _clean(a)
            if _usable(t, limit=_MAX_LABEL) and t.lower() not in seen:
                seen.add(t.lower())
                cats.append(t)

        # The promise strip: short LEAF text nodes. This is where "FAST & FREE SHIPPING" lives.
        badges: list = []
        bseen: set = set()
        for m in re.finditer(r">([^<>]{%d,%d})<" % (_BADGE_MIN, _BADGE_MAX), body):
            t = _clean(m.group(1))
            if not t or t.lower() in bseen:
                continue
            if _PRICEY.search(t) or not _usable(t, limit=_BADGE_MAX):
                continue
            bseen.add(t.lower())
            badges.append(t)

        langs = sorted({l.split("-")[0].lower()
                        for l in re.findall(r'hreflang="([a-zA-Z-]+)"', body)
                        if l.lower() != "x-default"})

        if not cats and not heads and not badges:
            # A client-rendered shell. Reporting "this competitor offers nothing" would be
            # evidence-of-absence dressed as knowledge — the rule `lite_scrape` already keeps.
            return PeerWords(reason="no readable text (JS-only shell?)")
        # CONCEPTS OVER **ALL** BADGES, THEN A SAMPLE — not the first N and hope.
        #
        # MEASURED and this is why it matters: Bloom Pharmacy's "FAST & FREE SHIPPING" sits at node
        # 652 of 673, in the footer trust strip, and a `badges[:200]` slice cut it off. The promise
        # was on the page, in memory, and truncated away before anything asked the question — the
        # same shape as every other defect this session found. The concept set is computed over the
        # whole strip and is what the comparison actually reads; `badges` keeps only a bounded
        # SAMPLE for evidence, and prefers the lines that carry a promise.
        promises = sorted(promise_concepts(" ".join(badges + heads + cats)))
        bearing = [b for b in badges if promise_concepts(b)]
        sample = bearing[:40] + [b for b in badges if b not in bearing][:60]
        return PeerWords(checked=True, reason="read from the page",
                         categories=cats[:30], headings=heads[:20],
                         badges=sample, promises=promises, languages=langs)
    except Exception as exc:  # noqa: BLE001 — a peer's page must never break a SWOT
        logger.info("[peer-words] unreadable (%s)", type(exc).__name__)
        return PeerWords(reason=f"unreadable ({type(exc).__name__})")


# ---------------------------------------------------------------------------
# The differentiation test
# ---------------------------------------------------------------------------

# Words that carry no distinguishing power in a promise. Kept short and generic on purpose: a
# longer list starts encoding a market, and the whole point is that this works for any brand in
# any country (D-8).
_STOP = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "your", "our", "we",
    "you", "is", "are", "all", "best", "top", "new", "more", "most", "very", "free",
    "في", "من", "على", "مع", "الى", "إلى", "عن", "هو", "هي", "كل", "أفضل", "افضل", "جديد",
    "أكثر", "اكثر", "جدا", "جداً",
}
_MIN_TOKENS = 1
_PEERS_TO_COMMODITISE = 2


def _tokens(text: str) -> set:
    words = re.findall(r"[^\W\d_]{3,}", str(text or "").lower(), re.UNICODE)
    return {w for w in words if w not in _STOP}


# THE PROMISE CONCEPTS — matched instead of words, because the words never match.
#
# MEASURED, and it is the whole reason this exists. AlFouad's own badge says "Same-Day Delivery in
# Cairo & Giza"; its rival Bloom Pharmacy's badge says "FAST & FREE SHIPPING". Same promise, ZERO
# shared content words — a lexical test scores that 0% and calls a commodity a differentiator.
# Conversely the words that DO overlap across rival storefronts are the meaningless ones: measured
# on the real pages, the shared tokens were "offers", "products", "health", "beauty".
#
# So the comparison is at the level of the PROMISE, not the sentence. This list is a closed set on
# purpose: e-commerce promises really are a small family, and a longer list would start encoding a
# market. Every family carries both scripts because both sides of the comparison are bilingual —
# these are CONCEPTS, not a country or a language (D-8).
_PROMISE_CONCEPTS = {
    "delivery_speed": ("same-day", "same day", "next-day", "next day", "fast delivery",
                       "fast shipping", "express", "quick delivery", "توصيل سريع", "نفس اليوم",
                       "خلال ساعات", "توصيل فوري", "شحن سريع"),
    "free_shipping": ("free shipping", "free delivery", "شحن مجاني", "توصيل مجاني", "التوصيل مجانا"),
    "delivery_reach": ("nationwide", "across egypt", "all governorates", "everywhere",
                       "جميع المحافظات", "كل المحافظات", "جميع أنحاء", "لكل مصر"),
    "authenticity": ("100% original", "original products", "authentic", "genuine", "not counterfeit",
                     "أصلي", "اصلي", "منتجات أصلية", "أصلية 100", "ضمان الأصلية"),
    "payment_security": ("secure payment", "secure shopping", "safe payment", "secure checkout",
                         "دفع آمن", "دفع امن", "الدفع الآمن", "تسوق آمن"),
    "payment_choice": ("multiple payment", "payment methods", "cash on delivery", "installment",
                       "طرق دفع", "الدفع عند الاستلام", "تقسيط"),
    "returns": ("satisfied or refunded", "money back", "easy returns", "return policy", "refund",
                "استرجاع", "استبدال", "ضمان استرداد", "سياسة الإرجاع"),
    "support": ("customer support", "24/7", "support team", "help center", "دعم", "خدمة العملاء",
                "دعم فني", "على مدار"),
    "breadth": ("wide range", "wide selection", "everything you need", "one-stop",
                "تشكيلة واسعة", "كل ما تحتاج", "مجموعة واسعة"),
    "price": ("best price", "lowest price", "affordable", "discount", "أفضل سعر", "أرخص",
              "خصومات", "أسعار تنافسية"),
}


def promise_concepts(text: str) -> set:
    """Which universal commerce promises this line makes. Empty = it is not a promise of that kind."""
    blob = " ".join(str(text or "").lower().split())
    return {c for c, phrases in _PROMISE_CONCEPTS.items() if any(p in blob for p in phrases)}


def _as_concepts(values) -> set:
    """Accept `PeerWords.promises` as EITHER concept ids or the raw promise text behind them.

    The field holds concept ids (`free_shipping`), because `peer_words_from_html` runs
    `promise_concepts` over the whole promise strip before storing. A caller who fills it by hand
    naturally puts the promise TEXT there instead — the field is called `promises` — and the old
    line compared "fast free shipping" against `{free_shipping}`, found no overlap, and returned
    the single most dangerous answer this module can produce: "no compared peer promises this",
    i.e. a claimed differentiator, from nothing but a shape mismatch. Silent, and wrong in the
    direction that flatters the client.

    So each entry is taken as a concept id when it is one, and otherwise run through the same
    extractor the stored path uses. Both shapes now mean the same thing.
    """
    out: set = set()
    for v in (values or []):
        s = str(v or "").strip()
        if not s:
            continue
        if s in _PROMISE_CONCEPTS:
            out.add(s)
        else:
            out |= promise_concepts(s)
    return out


def claim_is_commoditised(claim: str, peers: list, *,
                          min_peers: int = _PEERS_TO_COMMODITISE) -> tuple:
    """(is_commoditised, peer_names_saying_it) for one of the subject's own promises.

    A promise is a STRENGTH only if the competition does not make it. "Fast Delivery" and "100%
    Original Products" appear on every pharmacy site in the market; presenting them as this brand's
    advantages is the transcription the owner objected to. This is the test that was impossible
    before, because no peer vocabulary was ever kept.

    Deliberately conservative: it needs a real content-word overlap and `min_peers` independent
    peers before it will demote anything. Silence from the peers is never evidence — a peer whose
    page could not be read does not count either way.
    """
    mine = promise_concepts(claim)
    if not mine:
        # Not a recognisable commerce promise. That is NOT the same as "it is a differentiator" —
        # it usually means the line is a product description rather than a claim about the
        # business (measured: every one of Abu Auf's "value propositions" is a blurb about one SKU,
        # e.g. "macadamia roasted and coated in a creamy cheese layer"). The caller must decide
        # what to do with that; this function refuses to guess.
        return False, []
    said_by = []
    for name, pw in peers or []:
        if not getattr(pw, "checked", False):
            continue                       # unreadable peer: no vote, in either direction
        # `promises` is computed over the peer's WHOLE promise strip at extraction time; reading
        # `vocabulary` here would re-derive it from the truncated evidence sample instead.
        theirs = _as_concepts(getattr(pw, "promises", None)) or promise_concepts(pw.vocabulary)
        if mine & theirs:
            said_by.append(name)
    return (len(said_by) >= max(1, int(min_peers))), said_by


def differentiation_note(claim: str, peers: list) -> Optional[str]:
    """The sentence that turns a slogan into a finding, or None when nothing can be said.

    This is the half people forget: demoting a commodity promise is only half the value. The other
    half is being able to say, of the promise that SURVIVES, exactly how rare it is — which is what
    makes it a strength rather than another sentence off their homepage.
    """
    readable = [(n, p) for n, p in (peers or []) if getattr(p, "checked", False)]
    if not readable:
        return None                        # no peer could be read — there is genuinely nothing to say
    n = len(readable)
    if not promise_concepts(claim):
        # THE THIRD STATE. This used to return None — "nothing comparable was claimed; say
        # nothing" — and silence is the one answer that cannot be read correctly. MEASURED on Al
        # Fouad: three strengths from `value_propositions` carried a peer verdict and a fourth from
        # the SAME field carried none, so nothing in the document told a reader whether "Broad
        # Delivery Coverage" had been checked against the peers and passed, or never checked at
        # all. Two different facts, one blank space. Say which.
        return f"not comparable to the {n} peer(s) read — it states no promise they could match"
    _, said_by = claim_is_commoditised(claim, readable, min_peers=1)
    if said_by:
        return f"{len(said_by)}/{n} compared peers promise the same thing"
    return f"no compared peer promises this ({n} checked)"


def is_product_level_claim(vp) -> bool:
    """True when this "value proposition" describes ONE PRODUCT rather than the business.

    A SWOT strength is a claim about the company. "Carefully roasted macadamia nuts with a creamy
    cheese coating" is a claim about a bag of nuts — true, well-evidenced, and not a strength of Abu
    Auf the business. Four of its "value propositions" are exactly that, and they were shipping as
    top-level Strengths.

    THE EVIDENCE ITSELF IS THE DISCRIMINATOR, so nothing has to be guessed or listed (D-8 — no
    ingredient vocabulary, no language check). MEASURED on the two real profiles:

        Abu Auf   -> /products/macadamia-with-cheese-flavor-100, /products/coconut-200-gram
        AlFouad   -> /en, /en/collections/types

    A promise the business makes is stated where the business speaks: the homepage, a collection, a
    policy page. A product's own page speaks for that product. The test reuses the repo's single
    definition of a detail page (`scraper.classify.page_type.is_leaf_detail`) rather than inventing
    a second one.

    Conservative: it needs evidence, and EVERY cited page must be a product page. A claim quoted on
    both the homepage and a product page is a business claim that also appears on a product page —
    which is exactly what AlFouad's "100% Original Products" is.

    WHERE ALONE IS NOT ENOUGH, and a false positive here is the most expensive kind. MEASURED on
    nanocare: «مسجل بالهيئة القومية لسلامة الغذاء» — a NATIONAL FOOD SAFETY AUTHORITY registration,
    the single most defensible company-level line in that entire SWOT — is evidenced only on a
    product page, and page-provenance alone deleted it. A regulatory badge, a policy, a guarantee
    are printed on product pages all the time; that is where a shop puts its trust marks.

    So the claim must also TALK ABOUT the product whose page it sits on. The page's own identity is
    in its URL slug, which the repo already uses this way in `scraper/catalog.py`:

        "Their dried apricots are processed without sulfur dioxide"
            on /products/dried-natural-apricot-100-gm   -> shares "dried", "apricot"  -> product
        «مسجل بالهيئة القومية لسلامة الغذاء»
            on /products/نانو-كير-…                      -> shares nothing            -> company

    Grounded in the site's own words, in the site's own script, with no vocabulary to maintain.
    """
    if not isinstance(vp, dict):
        return False
    pages = [str((e or {}).get("page_url") or "").strip()
             for e in (vp.get("evidence") or []) if isinstance(e, dict)]
    pages = [p for p in pages if p]
    if not pages:
        return False
    try:
        from scraper.classify.page_type import is_leaf_detail
    except Exception:  # noqa: BLE001 — no classifier, no claim either way
        return False
    if not all(is_leaf_detail(p) for p in pages):
        return False
    text = " ".join([str(vp.get("value") or "")]
                    + [str((e or {}).get("quote") or "") for e in (vp.get("evidence") or [])
                       if isinstance(e, dict)])
    return any(_names_the_page_product(text, p) for p in pages)


def _names_the_page_product(text: str, page_url: str) -> bool:
    """Does this claim mention the product the page is about, per the page's own URL slug?"""
    import re
    from urllib.parse import unquote

    tail = unquote(str(page_url or "")).split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    tokens = {t for t in re.split(r"[-_+.]+", tail.lower()) if len(t) > 2 and not t.isdigit()}
    if not tokens:
        return False
    words = {w for w in re.split(r"[^\w؀-ۿ]+", str(text or "").lower()) if len(w) > 2}
    # Singular/plural and Arabic clitics: a shared 4-character stem counts as the same word
    # ("apricot"/"apricots", «الكاجو»/«كاجو»). Below four it starts matching noise.
    for tok in tokens:
        for w in words:
            if tok == w or (len(tok) >= 4 and len(w) >= 4 and (tok[:4] == w[:4])):
                return True
    return False


def claim_text_for_comparison(vp) -> str:
    """The ON-PAGE words for a value proposition, not the model's rewrite of them.

    MEASURED: the profile stores both. The value is the LLM's paraphrase ("Ensures secure payments
    with multiple payment methods"); the evidence quote is what the page actually shows ("Secure
    payments Multiple payment methods"). Comparing a paraphrase against a rival's raw badge is
    comparing two different kinds of text, and it is why the first version of this test scored a
    real commodity promise at 0% overlap. Compare page-text to page-text.
    """
    if not isinstance(vp, dict):
        return str(vp or "")
    for ev in (vp.get("evidence") or []):
        q = str((ev or {}).get("quote") or "").strip() if isinstance(ev, dict) else ""
        if q:
            return q
    return str(vp.get("value") or "")
