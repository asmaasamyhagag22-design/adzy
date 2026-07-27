# -*- coding: utf-8 -*-
"""Customer reviews printed on the brand's OWN site, read at the DOM where the name is still there.

MEASURED on alfouadpharmacies.com (2026-07-27), in a scrape we already had on disk:

    "testimonial" in raw/en.html                          116 occurrences
    «التوصيل سريع ومتابعه العملاء ممتازة»                  present
    the reviewer's name                                    present, as <img alt="Fatma Ismail">
    the page record in manifest.json                       contains the review text
    brand.testimonials                                     0

The page was crawled, the text was extracted, and it reached the stage that reads it. Nothing came
out — and the reason is not a broken rule. The LLM extractor is told to take a testimonial "only
when a real quote with attribution is present", and on this site the attribution lives in an IMAGE
ALT ATTRIBUTE, which text extraction drops. A properly attributed review therefore arrives looking
anonymous, and a rule that exists to stop invented quotes correctly refuses it.

So the fix belongs HERE, at the markup, where the quote and its author are still adjacent — not in
loosening the rule that protects the client from fabricated praise.

Deliberately conservative: a quote with no recoverable name yields NOTHING. An unattributed
testimonial is exactly what that rule is for.
"""
from __future__ import annotations

import html as _html
import re
from typing import Dict, List

# Blocks whose class/id marks them as a testimonial/review card. Kept broad on the WORD, narrow on
# nothing else: themes name these things differently and a review section is not worth missing over
# a naming convention.
_CONTEXT = re.compile(r"(?:class|id)\s*=\s*[\"'][^\"']*(?:testimonial|review|feedback)", re.I)

_IMG_ALT = re.compile(r"<img\b[^>]*\balt\s*=\s*[\"']([^\"']*)[\"']", re.I)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# alt values that are decoration, not a person.
_NOT_A_NAME = {"", "icon", "star", "stars", "rating", "avatar", "photo", "image", "img",
               "quote", "logo", "profile", "user", "customer"}

_MIN_QUOTE_LETTERS = 12          # a real sentence, not a rating label
_MAX_NAME_WORDS = 5
_LOOKAHEAD = 4000                # markup between a portrait and its sentence is verbose
_CONTEXT_SPAN = 4000             # how far back to look for the review-section marker
_MAX_QUOTE_CHARS = 400           # a testimonial, not a paragraph of page copy


def _text(fragment: str) -> str:
    return _WS.sub(" ", _html.unescape(_TAG.sub(" ", fragment))).strip()


def _looks_like_a_name(value: str) -> bool:
    v = _WS.sub(" ", _html.unescape(value or "")).strip()
    if not v or v.lower() in _NOT_A_NAME:
        return False
    if len(v.split()) > _MAX_NAME_WORDS:
        return False              # a sentence in the alt is a description, not an author
    return bool(re.search(r"[^\W\d_]", v, re.UNICODE))


def extract_testimonials(html_text: str) -> List[Dict[str, str]]:
    """Every attributed review on the page: [{"quote", "author"}], in document order.

    A block contributes at most one testimonial, and only when BOTH a quote of real length and a
    plausible author survive. No author -> no testimonial; inventing attribution is the failure this
    guards against.
    """
    if not html_text:
        return []

    # ANCHORED ON THE PORTRAIT, not on a balanced block. Matching `<div class="testimonial">...
    # </div>` with a regex fails the moment the card contains a nested <div> — the non-greedy
    # close binds to the INNER tag, so the capture held the image and not the sentence beside it.
    # Real cards are deeply nested. The author image is the one landmark that is always adjacent
    # to the quote, so the quote is read as the text immediately preceding it.
    out: List[Dict[str, str]] = []
    seen = set()
    for m in _IMG_TAG.finditer(html_text):
        tag = m.group(0)
        alt_m = _IMG_ALT.match(tag)
        if not alt_m:
            continue
        author = _WS.sub(" ", _html.unescape(alt_m.group(1) or "")).strip()
        if not _looks_like_a_name(author):
            continue

        # The portrait comes BEFORE its quote. MEASURED on the real page: reading backwards paired
        # every review with the NEXT customer's name and prefixed the previous one's — nine
        # testimonials, all nine attributed to the wrong person. Wrong attribution on a client's
        # own review is worse than no review at all, so the direction is pinned by test.
        if not _CONTEXT.search(html_text[max(0, m.start() - _CONTEXT_SPAN):m.start()]):
            continue          # a portrait outside any review context is a team photo, not a review
        window = html_text[m.end():m.end() + _LOOKAHEAD]
        # Stop at the next portrait: without this a quote absorbs the following card too.
        nxt = _IMG_TAG.search(window)
        if nxt is not None:
            window = window[:nxt.start()]
        quote = _WS.sub(" ", _text(window)).strip()
        quote = _WS.sub(" ", quote.replace(author, " ")).strip()
        # keep the head: the sentence follows the portrait, the next card's chrome trails it
        if len(quote) > _MAX_QUOTE_CHARS:
            quote = quote[:_MAX_QUOTE_CHARS].rstrip()
        if len(re.findall(r"[^\W\d_]", quote, re.UNICODE)) < _MIN_QUOTE_LETTERS:
            continue
        key = (quote[:80], author)
        if key in seen:
            continue
        seen.add(key)
        out.append({"quote": quote, "author": author})
    return out
