# -*- coding: utf-8 -*-
"""Recompute every stored brand's SWOT with the CURRENT code and print the difference.

WHAT THIS DOES NOT DO: it does not store anything, and it does not spend anything. The owner
asked to see the difference before deciding, so this reads `_profile` and the stored peer
columns off each brand record and re-runs the SYNTHESIS only. No Places call (the key is
suspended anyway), no web discovery, no LLM — so every line of the diff is attributable to a
code change and nothing else. Re-running discovery would mix "the code changed" with "the web
changed", and the SERP is measured at 66% noise between identical queries minutes apart.

  python -m info.swot_diff              # all brands
  python -m info.swot_diff alfouad      # one, by substring of the domain key
  python -m info.swot_diff --offline    # no peer re-read at all (understates the change)
"""
from __future__ import annotations

import sys

from api.brand_store import BrandStore
from competitor.matrix import build_matrix
from competitor.swot import synthesize_swot

_QUADRANTS = ("strengths", "weaknesses", "opportunities", "threats")


def _texts(quadrant) -> list[str]:
    """The line as the DASHBOARD shows it, from either shape.

    The store serializes a SWOT point under `title`; the in-memory dataclass calls it `text`. The
    first version of this read `text`/`value` only, so every stored quadrant came back EMPTY and
    the tool printed "0 -> 7" — a diff that shows the whole SWOT as newly invented. A diff harness
    that misreads the BEFORE side is worse than no diff: it is confidently wrong in the exact
    place the reader cannot check.
    """
    out = []
    for item in (quadrant or []):
        if isinstance(item, dict):
            out.append(str(item.get("title") or item.get("text") or item.get("value") or "").strip())
        else:
            out.append(str(getattr(item, "text", None) or getattr(item, "title", "") or "").strip())
    return [t for t in out if t]


def _peers_from(brand: dict) -> list:
    """Rehydrate the stored competitor columns into the dataclasses the matrix expects.

    The store serializes CompetitorProfile to plain dicts, so passing them straight back gives
    `'dict' object has no attribute 'candidate'`. Only the fields the matrix reads are restored;
    anything the store did not keep stays at its dataclass default rather than being invented.
    """
    from competitor.models import Candidate, CompetitorProfile, Review, SelectionRecord

    def _only(cls, d: dict):
        fields = getattr(cls, "__dataclass_fields__", {})
        return cls(**{k: v for k, v in (d or {}).items() if k in fields})

    peers = []
    for c in (brand.get("competitors") or []):
        if not isinstance(c, dict) or not isinstance(c.get("candidate"), dict):
            continue
        try:
            peers.append(CompetitorProfile(
                candidate=_only(Candidate, c["candidate"]),
                selection=_only(SelectionRecord, c.get("selection") or {}),
                reviews=[_only(Review, r) for r in (c.get("reviews") or []) if isinstance(r, dict)],
                has_scrapable_site=bool(c.get("has_scrapable_site")),
                is_local=bool(c.get("is_local", True)),
            ))
        except Exception:  # noqa: BLE001 — a peer that will not rehydrate is dropped, not faked
            continue
    return peers


def _peer_scraper(rate_limit_s: float = 2.0):
    """A scrape_fn that re-reads each peer's HOMEPAGE so the new `peer_words` dimension exists.

    Free — plain HTTP to the peer's own site, no API, no key, nothing billable. Rate-capped on
    purpose: an earlier uncapped diagnostic sweep from this machine poisoned a shared IP's
    reputation, and a peer's homepage is someone else's server.

    Without this the diff would UNDERSTATE the change, because the commoditisation filter needs
    peer words and the stored peer columns predate that dimension entirely.
    """
    import time

    from competitor.lite_scrape import lite_peer_dims

    last = {"t": 0.0}

    def scrape(url):
        wait = rate_limit_s - (time.monotonic() - last["t"])
        if wait > 0:
            time.sleep(wait)
        last["t"] = time.monotonic()
        return lite_peer_dims(url)

    return scrape


def _inputs_from(brand: dict) -> dict:
    """The non-peer inputs the stored SWOT was built with, handed BACK to the recomputation.

    Without this the diff lies by omission. Opportunities are largely trend coverage, so a
    recomputation with `trends=None` drops every one of them and the report reads "-6
    opportunities" as though the new code deleted them. It did not — the harness withheld the
    input. Same class of error as the `title`/`text` mismatch above: the tool, not the code.
    """
    full = brand.get("_full_result") or {}
    # PRODUCTION'S OWN READER, not a private copy. It knows both storage locations (two brands hold
    # their trends only under `swot.trends`), it returns the object shape `trend_signals` needs —
    # passing the raw dicts made every opportunity vanish, because that function reads title/url
    # with getattr and a dict yields "" — and it applies the commerce filter, so a shop listing
    # stored before that filter existed cannot come back as an Opportunity here.
    from api.webapp_analysis import market_signals_for
    trends = market_signals_for(brand) or None
    # `other_unique_insights` becomes Strengths through this parameter, NOT through
    # `strengths_from_profile` (which mines value_propositions + trust_signals). Omitting it made
    # Nanocare's regulatory registration — "مسجل بالهيئة القومية لسلامة الغذاء", a third-party
    # credential and the least superficial line in that whole SWOT — read as though the new
    # filters had deleted it. Both production callers pass it, so the diff must too.
    from competitor.swot import unique_insight_texts
    try:
        insights = unique_insight_texts(brand.get("_profile") or {})
    except Exception:  # noqa: BLE001
        insights = None
    # Customer-voice themes, read off the SAME saved snapshots production reads (no network, no
    # spend). Omitting them made every social-derived Strength look deleted — including the ones
    # the new reaction filter genuinely drops, which is the change this tool exists to show.
    #
    # NOT `_customer_voice_themes`, which is production's collector: it also runs the ABSA review
    # path, and that calls Gemini. On this account the call 403s and degrades, so it looked free —
    # on a working account this tool would have quietly started billing on every run, breaking the
    # zero-spend guarantee in its own docstring. The two deterministic sources are called directly.
    themes = []
    try:
        from api.webapp_analysis import _social_themes_for
        from competitor.social_themes import themes_from_testimonials
        prof = brand.get("_profile") or {}
        themes = list(_social_themes_for(prof) or [])
        themes += list(themes_from_testimonials(prof.get("testimonials")) or [])
    except Exception:  # noqa: BLE001
        themes = []
    return {"trends": trends, "ad_presence": full.get("ad_presence") or None,
            "unique_insights": insights or None, "themes": themes or None}


def _recompute(brand: dict, *, scrape: bool):
    profile = brand.get("_profile") or {}
    if not profile:
        return None, "no stored profile — nothing to recompute from"
    try:
        matrix = build_matrix(profile, _peers_from(brand),
                              scrape_fn=_peer_scraper() if scrape else None)
        return synthesize_swot(matrix, profile=profile, **_inputs_from(brand)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


# Lines the RECOMPUTATION cannot produce today for a reason that has nothing to do with the code
# change under test: the Google Places key is suspended (measured 2026-08-02, 403 CONSUMER_
# SUSPENDED), so no subject Places record exists and every rating/review-volume comparison is
# absent. Reporting those as "removed" would credit a billing outage to a code fix.
_PLACES_MARKERS = ("review volume", "rating", "عدد التقييمات", "التقييم")


def _diff(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    b, a = set(before), set(after)
    return sorted(a - b), sorted(b - a)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    offline = "--offline" in argv[1:]
    want = args[0].lower() if args else ""
    store = BrandStore()
    brands = [b for b in store.list() if not want or want in str(b.get("domain_key", "")).lower()]
    if not brands:
        print("no matching brands")
        return 1
    print("re-reading peer homepages (free, rate-capped)" if not offline
          else "--offline: peer words unavailable, the diff UNDERSTATES the change")

    seen: set[str] = set()
    for brand in brands:
        key = str(brand.get("domain_key") or "?")
        label = key if key not in seen else f"{key}  (duplicate record)"
        seen.add(key)
        print("=" * 78)
        print(label)
        print("=" * 78)

        # WHAT THE RECOMPUTATION HAD TO WORK WITH. Printed because a missing INPUT and a deleted
        # LINE look identical in a diff. Several stored SWOTs were built with peers that the
        # record no longer holds — `webapp_analysis` discarded them at `swot, _peers = ...` — so
        # their peer-comparison lines cannot come back, and that is not this change's doing.
        peers, ins = _peers_from(brand), _inputs_from(brand)
        stored_peer_lines = sum(
            1 for q in _QUADRANTS for t in _texts((brand.get("swot") or {}).get(q))
            if "peer average" in t or "compared peers" in t or "competitors" in t.lower())
        print(f"  inputs: peers={len(peers)}  trends={len(ins['trends'] or [])}  "
              f"insights={len(ins['unique_insights'] or [])}")
        if stored_peer_lines and not peers:
            print(f"  NOTE: the stored SWOT has {stored_peer_lines} peer-comparison line(s) but the "
                  f"record kept 0 peers — those cannot be reproduced and their loss is not a code change")

        new, err = _recompute(brand, scrape=not offline)
        if err:
            print(f"  SKIPPED: {err}\n")
            continue

        stored = brand.get("swot") or {}
        total_add = total_del = total_blocked = 0
        for q in _QUADRANTS:
            before = _texts(stored.get(q))
            after = _texts(getattr(new, q, None))
            added, removed = _diff(before, after)
            blocked = [t for t in removed
                       if any(m in t.lower() for m in _PLACES_MARKERS)]
            removed = [t for t in removed if t not in blocked]
            total_add += len(added)
            total_del += len(removed)
            total_blocked += len(blocked)
            flag = "" if (added or removed or blocked) else "   (unchanged)"
            print(f"\n  {q.upper():14} {len(before)} -> {len(after)}{flag}")
            for t in removed:
                print(f"      - {t[:110]}")
            for t in added:
                print(f"      + {t[:110]}")
            for t in blocked:
                print(f"      ~ {t[:104]}   [Places key suspended, not a code change]")
        print(f"\n  net: +{total_add} / -{total_del}"
              + (f"   ({total_blocked} unavailable: Places)" if total_blocked else "") + "\n")

    print("Nothing was written. Storing is a separate, explicit step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
