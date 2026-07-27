# -*- coding: utf-8 -*-
"""Customer comments -> `ReviewTheme`, so the SWOT hears the voice it was missing.

THE OWNER'S POINT: "كان كومنت اكتر كان ادق واحسن" — more comments make the analysis sharper. The
measurement agrees: Places reviews reach 29% of runs, social reaches 82%, and a review theme is the
only generator in the SWOT that produces an item from a real customer SENTENCE instead of from a
site mechanic.

The comments were already collected, analysed and stored — and dropped. From a saved Instagram
snapshot, `outputs/buffaloburger_com_result.json` holds:

    "The staff at The Isle branch provide very bad service"
    quoting «الفرع اللي في the isle تعاملهم وحش اووي وتعاملهم صعب»
    from https://www.instagram.com/p/DaDdrEUqobZ/

A textbook weakness, quoted verbatim, with its source — and `grep marketing_intel swot.py` returns
zero.

WHY A CONVERTER, NOT A GENERATOR. `synthesize_swot` already routes `ReviewTheme` exactly the way
the owner described this feature:

    own complaint  -> Weakness          peer complaint -> Opportunity  ("a gap you can fill")
    own praise     -> Strength          peer praise    -> Threat

`SocialSignal` even carries the same `is_own_brand` flag. So a comment becomes a theme and the
routing that already works, works. No second sentiment system to keep in step with the first.

WHAT IS DELIBERATELY REFUSED HERE: the brand's own captions and posts. A sentence the brand wrote
about itself is an opinion it holds, not evidence from a customer — the exact defect the SWOT
rebuild removed when it stopped serving `value_propositions` as measured strengths.

Deterministic and hermetic: no model call, no network. Sentiment is decided by explicit lexicons in
BOTH scripts, so an Arabic complaint is not silently invisible to an English word list — the defect
already recorded for `_TREND_THREAT_KEYS`. Universal (D-8): no brand, country or vertical.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .swot import ReviewTheme

# Complaint and praise markers in both scripts. Kept explicit rather than delegated to a model so a
# theme is reproducible and auditable — and bilingual on purpose: `_TREND_THREAT_KEYS` was an
# English-only list, so an Arabic headwind was silently filed as an opportunity.
_COMPLAINT = (
    "وحش", "سيء", "سيئة", "زفت", "مش حلو", "بطيء", "بطيئة", "تأخر", "اتأخر", "متأخر",
    "غالي", "مبالغ", "زحمة", "مشكلة", "مشاكل", "شكوى", "خدمة سيئة", "ماينفعش", "مش عاجبني",
    "خسارة", "ندمت", "بارد", "مقرف", "اهمال", "إهمال", "رد وحش",
    "bad", "worst", "terrible", "awful", "slow", "late", "delay", "expensive",
    "overpriced", "rude", "poor", "disappointing", "disappointed", "never again",
    "cold", "dirty", "problem", "complaint", "refund", "waiting",
)
_PRAISE = (
    "أحسن", "احسن", "ممتاز", "ممتازة", "رائع", "رائعة", "تحفة", "جامد", "حلو أوي", "حلو اوي",
    "تسلم", "شكرا", "شكراً", "أفضل", "افضل", "بحبكم", "برافو", "نضيف", "سريع", "محترم",
    "best", "excellent", "amazing", "great", "perfect", "love", "delicious", "fast",
    "friendly", "clean", "recommend", "awesome", "wonderful",
)

# A comment that is only emoji/punctuation carries no theme. Letters in ANY script count.
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
_MIN_LETTERS = 4

# Only genuine audience voice. A caption or a post is the BRAND talking about itself.
_CUSTOMER_KINDS = {"comment", "reply"}


# A praise WORD aimed at somebody else is a complaint about you. MEASURED on Coca-Cola's real
# comments: «محدش بيبدل حاجة عروض وهميه بيبسى افضل منكم» ("…Pepsi is BETTER THAN YOU") was filed
# as praise because it contains «افضل». The word is genuinely a praise word; the sentence is not
# about the brand. Comparative-away phrasing is the tell, in both scripts.
_PRAISE_FOR_SOMEONE_ELSE = (
    "افضل منكم", "أفضل منكم", "احسن منكم", "أحسن منكم", "افضل منك", "احسن منك",
    "افضل من", "أفضل من", "احسن من", "أحسن من",
    "better than you", "better than yours", "better than u",
)


def _polarity(text: str) -> Optional[str]:
    low = str(text or "").lower()
    if any(p in low for p in _PRAISE_FOR_SOMEONE_ELSE):
        return "complaint"
    has_complaint = any(w in low for w in _COMPLAINT)
    has_praise = any(w in low for w in _PRAISE)
    if has_complaint and not has_praise:
        return "complaint"
    if has_praise and not has_complaint:
        return "praise"
    # Both or neither: a mixed or neutral comment is not evidence for either box. Guessing here
    # would put an unearned verdict into the client's SWOT.
    return None


def _key(text: str) -> str:
    """A merge key so the same complaint said by several people becomes ONE weighted theme.

    "كان كومنت اكتر كان ادق" — three people saying the same thing is stronger evidence than one,
    and that strength belongs in the theme's support, not in three near-identical items."""
    return " ".join(sorted(set(re.findall(r"[^\W\d_]{3,}", str(text or "").lower(), re.UNICODE))))


def themes_from_testimonials(testimonials: Any) -> list:
    """Reviews printed on the brand's OWN site -> own-brand review themes.

    MEASURED on alfouadpharmacies.com: nine attributed Arabic reviews sit on the homepage, and
    before this existed nothing could turn them into SWOT evidence — `grep is_own_brand=True`
    found only the Instagram path. The reviews were extracted, carried, and then had nowhere to go.

    Own-brand by construction: these are reviews of THIS business on ITS site, so praise is a
    Strength and a complaint a Weakness — never a rival's advantage.

    Two refusals, both deliberate:
      * NO NAME, NO THEME. The reviewer's name is the citation; an unattributed quote is exactly
        what the grounding gate exists to reject.
      * NO POLARITY, NO THEME. «العرض ده متاح على الموقع بس؟» is a question, not praise, and a
        Strength built on it would be a claim the customer never made.
    """
    out = []
    seen = set()
    for row in (testimonials or []):
        if isinstance(row, dict):
            quote = str(row.get("quote") or row.get("text") or "").strip()
            author = str(row.get("author") or row.get("name") or "").strip()
        else:
            quote = str(getattr(row, "quote", "") or getattr(row, "text", "") or "").strip()
            author = str(getattr(row, "author", "") or getattr(row, "name", "") or "").strip()
        if not quote or not author:
            continue
        if len(_LETTERS.findall(quote)) < _MIN_LETTERS:
            continue
        polarity = _polarity(quote)
        if polarity is None:
            continue
        key = (polarity, _key(quote))
        if key in seen:
            continue
        seen.add(key)
        out.append(ReviewTheme(polarity=polarity, text=quote,
                               support=[f"{author} — review on the brand's own site"],
                               is_unmet_need=False, is_own_brand=True))
    return out


def themes_from_signals(signals: Any, *, is_own_brand: Optional[bool] = None) -> list:
    """Convert collected social signals into review themes. Never raises; [] when nothing qualifies.

    `is_own_brand` OVERRIDES whatever the signals carry, because whose account a snapshot came from
    is known by the CALLER and not always by the row. MEASURED: `scripts.pull_instagram_apify`
    emits an `IgSignal` with no such field at all, so comments pulled from the brand's OWN
    Instagram defaulted to `False` and would have been routed to Opportunities/Threats — a
    customer praising this brand read as a rival's strength."""
    merged: dict = {}
    for sig in (signals or []):
        kind = str(getattr(sig, "signal_kind", "") or "")
        if kind not in _CUSTOMER_KINDS:
            continue                       # the brand's own caption is not customer voice
        text = str(getattr(sig, "text", "") or "").strip()
        if len(_LETTERS.findall(text)) < _MIN_LETTERS:
            continue                       # emoji-only / blank
        polarity = _polarity(text)
        if polarity is None:
            continue                       # neutral or mixed — not evidence either way
        own = (bool(getattr(sig, "is_own_brand", False)) if is_own_brand is None
               else bool(is_own_brand))
        key = (own, polarity, _key(text))
        url = str(getattr(sig, "source_url", "") or "").strip()
        if key in merged:
            if url:
                merged[key].support.append(url)
            continue
        merged[key] = ReviewTheme(
            polarity=polarity,
            text=text,
            support=[url] if url else [],
            # A PEER's complaint is a gap this brand can fill — the owner's own framing, and the
            # flag `synthesize_swot` reads to route it into Opportunities rather than Threats.
            is_unmet_need=(not own and polarity == "complaint"),
            is_own_brand=own,
        )
    return list(merged.values())
