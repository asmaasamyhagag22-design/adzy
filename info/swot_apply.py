# -*- coding: utf-8 -*-
"""Write the recomputed SWOT onto every stored brand — as a MERGE, not a replacement.

`info.swot_diff` shows what changes; this writes it. It is the same recomputation, with the same
inputs and the same zero spend (no model, no paid API — only the peers' own homepages, rate-capped).

WHY THIS IS A MERGE. The diff labels two very different kinds of "removed" line, and only one of
them is this change's doing:

  * A FILTER removed it — a product spec that belongs in the catalog, a claim every peer also
    makes, a customer's emoji reaction. Those go, and going is the point.
  * AN INPUT is missing — the Places key is suspended, or the brand record never kept the peers and
    trends its stored SWOT was built from. Those lines cannot be reproduced, and deleting them
    would destroy real work that no code change touched. nanocare alone would lose six
    opportunities and its peer comparisons that way.

So a stored line survives only when it is orphaned by a MISSING INPUT, never when a filter judged
it. And an orphan is not waved through either: trend-derived opportunities are re-tested with the
current commerce filter, because several of the stored ones ("Liposomal Vitamin C 2000mg - House Of
Supplements") are shop listings — precisely the "قشور" this whole pass exists to remove.

  python -m info.swot_apply              # DRY RUN — prints the plan, writes nothing
  python -m info.swot_apply --write      # writes
  python -m info.swot_apply alfouad --write

Back up `outputs/webapp/brands` before `--write`. This tool does not: a backup that lives in the
same script as the mutation is one bug away from being useless.
"""
from __future__ import annotations

import sys

from api.brand_store import BrandStore, _brands_dir
from api.webapp_analysis import _swot_from_raw, _swot_raw_dict, _swot_to_frontend
from info.swot_diff import _inputs_from, _peer_scraper, _peers_from, _texts

from competitor.matrix import build_matrix
from competitor.swot import synthesize_swot

_QUADRANTS = ("strengths", "weaknesses", "opportunities", "threats")

# What a stored line DEPENDS ON. Matching is on the rendered text because that is the only thing
# the store kept — the generator that produced it is long gone.
_PLACES_WORDS = ("review volume", "rating", "عدد التقييمات", "التقييم")
_PEER_WORDS = ("peer average", "compared peers", "competitors lead", "compared peer",
               "no measured competitor", "some competitors")
_TREND_WORDS = ("coverage on", "category headwind", "إشارة سوق")


def _depends_on(text: str) -> str:
    low = str(text or "").lower()
    if any(w in low for w in _PLACES_WORDS):
        return "places"
    if any(w in low for w in _PEER_WORDS):
        return "peers"
    if any(w in low for w in _TREND_WORDS):
        return "trends"
    return ""


def _trend_line_survives(text: str) -> bool:
    """Re-test a stored trend-derived line with the CURRENT commerce filter.

    An orphan gets kept because we cannot reproduce it — not because it is good. Five of nanocare's
    six stored opportunities are product listings on other people's shops, which is the exact defect
    `trends.sources.is_commerce_result` was written for. Keeping an input-orphan must not become a
    back door for the very lines the filter rejects.
    """
    try:
        from trends.sources import is_commerce_result
    except Exception:  # noqa: BLE001 — no filter available -> keep, never silently drop
        return True
    # The stored text is "Coverage on <terms>: <title>"; the title is what was searched.
    title = str(text or "").split(":", 1)[-1].strip()
    return not is_commerce_result(title, "")


# ONE definition, not two. `_swot_from_raw` now carries id/priority/peers_compared itself — it had
# to, because the CLI→dashboard bridge and the strategy builder both go through it and both were
# losing the ranking on every adoption. Keeping a private copy here would let the two drift.
def _rehydrate(row: dict):
    from api.webapp_analysis import _swot_from_raw
    return _swot_from_raw({"strengths": [row]}).strengths[0]


def _orphans(brand: dict, available: dict) -> dict:
    """Stored lines whose INPUT is gone, per quadrant, as SWOTItem objects."""
    raw = brand.get("_swot_raw") or {}
    out = {q: [] for q in _QUADRANTS}
    if not raw:
        return out
    for q in _QUADRANTS:
        for row in (raw.get(q) or []):
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "")
            need = _depends_on(text)
            if not need or available.get(need):
                continue                       # reproducible — let the recomputation decide
            if need == "trends" and not _trend_line_survives(text):
                continue                       # orphan, but the filter rejects it anyway
            out[q].append(_rehydrate(row))
    return out


def _merge(new, orphans: dict):
    """Recomputed lines first, rescued orphans appended, deduped on text."""
    added = {q: 0 for q in _QUADRANTS}
    for q in _QUADRANTS:
        have = {i.text.strip() for i in (getattr(new, q, None) or [])}
        for item in orphans[q]:
            if item.text.strip() in have:
                continue
            getattr(new, q).append(item)
            have.add(item.text.strip())
            added[q] += 1
    return added


# Notes the RECOMPUTATION wrote about itself. After a rescue they describe a document that no
# longer exists, so they are dropped and the disclosure is re-run against the merged content.
_SELF_DESCRIBING = ("standalone strategic analysis", "no competitors found",
                    "فاضية —", "لا توجد نقاط قوة أو ضعف", "need market comparison",
                    "نُقل ", "peers' site dimensions are unknown")


def _reconcile(new, brand: dict, rescued_any: bool) -> None:
    """Make the NARRATIVE describe the document that was actually written.

    THIS IS THE DEFECT AN ADVERSARIAL AUDIT FOUND IN THE FIRST WRITE, and it was the worst of them
    because it is what a client reads. The rescue puts peer-derived lines back; the notes, the
    `mode` flag and `benchmark_quality` all came from a recomputation that ran with ZERO peers. So
    nanocare's file said "Standalone Strategic Analysis (No Competitors Found)" and "نقاط الضعف:
    فاضية" three lines above a populated weakness reading "below the peer average (9.0, n=1)".
    A document that contradicts itself on one page is worse than either version alone.

    Two corrections, both derived from the merged content rather than asserted:
      * a quadrant's "why it is empty" note is regenerated against what the quadrant now holds;
      * `mode` returns to "competitive" when a measured peer comparison survived, because the peer
        WAS measured — the record simply stopped storing it, which is a storage defect and not a
        statement about this brand's market.
    """
    from competitor.swot import disclose_empty_quadrants

    new.notes = [n for n in (new.notes or [])
                 if not any(m in str(n).lower() or m in str(n) for m in _SELF_DESCRIBING)]

    # IDS FOR THE RESCUED LINES. `_prioritise` sorts and numbers each quadrant DURING synthesis;
    # rescued items arrive afterwards, so they keep whatever id the old record held — and Volume
    # Qatar's record predates ids entirely, so three of its lines shipped with `id: ""`. TOWS
    # anchors every plan step on the id, so a blank one silently re-points a strategy.
    #
    # EVERY item is renumbered, not just the blank ones. A rescued line arrives carrying the id it
    # held in the PREVIOUS run — `S1`, `W1` — which collides head-on with the id the recomputation
    # just assigned to a different line. Numbering only the blanks left abuauf with two `S1`s and
    # nahdi with two `W1`s, and a duplicate id is worse than a missing one: `swot_item_ids` resolves
    # to whichever it finds first, so a TOWS step cites one factor and points at another.
    #
    # Order is preserved rather than re-sorted: rescued lines were scored in a previous run against
    # peers this record no longer holds, so they have no comparable rank, and inventing one would be
    # exactly the kind of number that looks like analysis and is not. "Measured this run, then
    # carried from the last" is an honest order; a fabricated ranking is not.
    for prefix, q in (("S", "strengths"), ("W", "weaknesses"), ("O", "opportunities"), ("T", "threats")):
        for n, item in enumerate(getattr(new, q, None) or [], 1):
            item.id = f"{prefix}{n}"

    peer_lines = sum(1 for q in _QUADRANTS for i in (getattr(new, q, None) or [])
                     if _depends_on(i.text) == "peers")
    if rescued_any and peer_lines:
        new.mode = "competitive"
        n_peers = _peers_measured(brand)
        new.notes.append(
            f"المقارنة دي مبنية على {n_peers} قرين اتقاسوا في تشغيلة سابقة؛ السجل مابيحتفظش "
            f"بأعمدتهم دلوقتي، فالسطور المقارنة اتحافظ عليها زي ما هي بدل ما تتحذف.")
    else:
        n_peers = len(_peers_from(brand))

    disclose_empty_quadrants(new, n_peers=n_peers,
                             scrapable_peers=sum(1 for p in _peers_from(brand)
                                                 if getattr(p, "has_scrapable_site", False)))


def _peers_measured(brand: dict) -> int:
    """How many peers the STORED SWOT was actually built against, read off its own text.

    The count is in the lines themselves ("peer average (9.0, n=1)", "2/2 compared peers"), which
    is the only place it survives on a record whose peer columns were discarded.
    """
    import re
    best = 0
    for q in _QUADRANTS:
        for row in ((brand.get("_swot_raw") or {}).get(q) or []):
            t = str((row or {}).get("text") or "")
            for m in re.finditer(r"n=(\d+)|(\d+)\s*/\s*(\d+)\s+compared peers", t):
                best = max(best, int(m.group(1) or m.group(3) or 0))
    return best


# Keys that are CARRIED, not recomputed: the quadrants are this tool's job to change, everything
# else on the stored SWOT is data it must hand back intact.
_CARRIED = ("trends", "site_readiness", "notes")


def _would_empty(stored: dict, patch: dict) -> list[str]:
    """Which carried keys would go from having content to having none.

    THE GUARD EXISTS BECAUSE THE FIRST WRITE NEEDED IT. `_swot_to_frontend` emits every key it
    knows, so a value this tool forgot to carry ships as `[]` — and an empty list is a WRITE, not
    an absence: BrandStore's one-level merge happily replaced four brands' trends with nothing.
    Nobody would have noticed until a Trend tab rendered blank in front of a client.

    A patch that empties something the record had is a bug in this tool by definition. Refuse, say
    which key, and change nothing.
    """
    out = []
    for k in _CARRIED:
        if (stored.get(k) or []) and not (patch.get(k) or []):
            out.append(k)
    return out


def _trend_mirror(brand: dict) -> list:
    """The trend rows this brand already had, in the shape both serializers write.

    Read back rather than regenerated: fetching them is a paid Serper call, and they have not
    changed. `trends_of` is the production reader and knows both storage locations.
    """
    from api.webapp_analysis import trends_of
    rows = trends_of(brand) or (brand.get("_swot_raw") or {}).get("market_trends") or []
    return [r for r in rows if isinstance(r, dict)]


def _records():
    """The real brand records. `*.bak.json` is an ownership-migration copy that `BrandStore.list`
    also returns; writing through it would just overwrite the live file twice."""
    store = BrandStore()
    seen = set()
    for brand in store.list():
        key = str(brand.get("domain_key") or "")
        path = _brands_dir() / f"{key}.json"
        if not key or key in seen or not path.is_file():
            continue
        seen.add(key)
        yield store, brand


def main(argv: list[str]) -> int:
    write = "--write" in argv[1:]
    args = [a for a in argv[1:] if not a.startswith("-")]
    want = args[0].lower() if args else ""

    print("WRITING" if write else "DRY RUN — nothing will be written (add --write)")
    print()
    touched = 0
    for store, brand in _records():
        key = str(brand.get("domain_key"))
        if want and want not in key.lower():
            continue
        profile = brand.get("_profile") or {}
        if not profile:
            print(f"{key:26} SKIPPED — no stored profile")
            continue

        peers = _peers_from(brand)
        inputs = _inputs_from(brand)
        available = {"peers": bool(peers), "trends": bool(inputs.get("trends")), "places": False}

        try:
            matrix = build_matrix(profile, peers, scrape_fn=_peer_scraper())
            new = synthesize_swot(matrix, profile=profile, **inputs)
        except Exception as exc:  # noqa: BLE001
            print(f"{key:26} SKIPPED — {type(exc).__name__}: {exc}")
            continue

        # THE TREND MIRROR. `market_trends` is not a field on the SWOT dataclass — `_compute_swot`
        # hangs it on the object after synthesis, and both serializers read it with getattr. Without
        # this line the recomputed object carries none, `_swot_to_frontend` emits `"trends": []`,
        # and BrandStore's one-level merge lets that empty list overwrite the stored one. MEASURED
        # on the first write: four brands went from 6/5/5/6 stored trends to 0. `_swot_raw` survived
        # only by luck — it is a nested dict, so the merge went one level deeper and kept the key.
        #
        # Two brands hold their trends ONLY here (`webapp_analysis.trends_of` exists for exactly
        # that), so this was not a cosmetic loss — it emptied a working tab.
        setattr(new, "market_trends", list(_trend_mirror(brand)))

        before = {q: _texts((brand.get("swot") or {}).get(q)) for q in _QUADRANTS}
        rescued = _merge(new, _orphans(brand, available))
        _reconcile(new, brand, rescued_any=bool(sum(rescued.values())))
        after = {q: [i.text for i in (getattr(new, q, None) or [])] for q in _QUADRANTS}

        dropped = sum(len(set(before[q]) - set(after[q])) for q in _QUADRANTS)
        gained = sum(len(set(after[q]) - set(before[q])) for q in _QUADRANTS)
        print(f"{key:26} "
              f"{sum(len(v) for v in before.values()):3} -> {sum(len(v) for v in after.values()):3} lines"
              f"   filtered out {dropped:2}   new {gained:2}   rescued {sum(rescued.values()):2}"
              f"   [peers={len(peers)} trends={len(inputs.get('trends') or [])}]")
        for q in _QUADRANTS:
            for t in sorted(set(before[q]) - set(after[q])):
                print(f"      - {q[:4]}  {t[:96]}")
            for t in sorted(set(after[q]) - set(before[q])):
                print(f"      + {q[:4]}  {t[:96]}")

        patch = {"swot": _swot_to_frontend(new),
                 "_swot_raw": _swot_raw_dict(new),
                 "has_swot": True}
        lost = _would_empty(brand.get("swot") or {}, patch["swot"])
        if lost:
            print(f"      REFUSED — writing would empty {', '.join(lost)} on a record that has "
                  f"content there. Fix the tool, not the record.")
            print()
            continue

        if write:
            store.update(key, patch)
            touched += 1
        print()

    print(f"{touched} brand record(s) written." if write
          else "Nothing written. Re-run with --write once the plan above looks right.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
