# -*- coding: utf-8 -*-
"""1.1 — the competitor-relevance gate. search_terms is a broad KEYWORD match across ALL
advertisers, so a pull for one competitor returns strangers; this module decides "does this ad
actually belong to the competitor?" with MULTIPLE signals (Arabic brands live on English-named
pages and vice versa — a single strict compare would throw away real ads):

  (a) dual containment  — normalized page_name ⊆ competitor name or vice versa
  (b) token overlap     — any meaningful (≥4 chars) token shared between the two names
  (c) facebook handle   — the candidate's own fb handle (from its website) matches the page
  (d) name-in-ad-text   — the competitor's name appears verbatim in the ad's own copy

Deterministic, no AI. Unknown stays unknown: no signal -> not the competitor's ad."""
from __future__ import annotations

import re

_TOKEN_MIN = 4


def _norm(s: str) -> str:
    """Keep word chars (Latin + Arabic ranges), collapse the rest to single spaces, casefold."""
    return re.sub(r"[^\w؀-ۿ]+", " ", str(s or "")).casefold().strip()


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) >= _TOKEN_MIN}


def matches_competitor(page_name: str, ad_text: str, competitor_name: str,
                       *, handle: str = "") -> bool:
    pn, cn = _norm(page_name), _norm(competitor_name)
    if not cn:
        return False
    # (a) dual containment on the normalized names
    if pn and (pn in cn or cn in pn):
        return True
    # (b) meaningful token overlap
    pt, ct = _tokens(page_name), _tokens(competitor_name)
    if pt & ct:
        return True
    # (c) the candidate's own facebook handle vs the page name — the cross-script bridge.
    #     Handles glue words together ("nanocare.egypt"), so prefix matches count too.
    h = _norm(handle)
    if h:
        h_glued = h.replace(" ", "")
        pn_glued = pn.replace(" ", "")
        if pn_glued and (h_glued in pn_glued or pn_glued in h_glued):
            return True
        ht = {t for t in h.split() if len(t) >= _TOKEN_MIN}
        if ht & pt:
            return True
        if any(h_glued.startswith(t) or t.startswith(h_glued) for t in pt):
            return True
    # (d) the competitor's name spoken inside the ad copy itself
    at = _norm(ad_text)
    if at and cn in at:
        return True
    return False
