# -*- coding: utf-8 -*-
"""Three things that would break the owner's demo in front of a client, each measured first.

Found by a pre-demo audit (23 agents, every load-bearing claim attacked by an independent skeptic)
and then re-measured by hand before anything was changed here.

  1. "START OVER" DOES NOT START OVER. `api/routes/influencer.py` resets by patching
     `{"ai_influencer": {"candidates": {}, ...}}` through `BrandStore.update`, and
     `_merge_one_level` RECURSES into dicts — merging an empty dict removes nothing. Its own
     docstring says so out loud: "none of them means 'remove this nested key'". MEASURED by calling
     the function directly: base `candidates={'c1': …}` + the reset patch -> `candidates` is still
     `{'c1': …}`. Consequence: the second portrait click leaves two candidates, "Approve" then
     answers with the literal string "choose one with /api/influencer/select {candidate_id}", and
     the fourth click hits MAX_CANDIDATES and surfaces as "Generation step failed."

  2. THE DOWNLOAD BUTTON STAYS BOUND TO THE REEL. `#download-btn` is declared
     `onclick="downloadImage()"` (frontend/index.html:1971) and the reel path overwrites it as a
     DOM property, `dl.onclick = () => downloadReel()`. A DOM property beats the attribute and
     nothing ever rebinds it, so: render a reel, then a poster, click Download — and the user
     silently receives the old .mp4 with a poster on screen.

  3. "1 WEEK" PRODUCES 30 DAYS. The <select> sends `value="week"`, and the server's `_PERIOD_DAYS`
     knows `1_week`/`weekly` but not `week`, so it falls through to the 30-day default. "1 Month"
     is wrong in the same way and is only correct by accident, because the default happens to be 30.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPA = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------
# 1 — start over must actually clear the previous looks
# ---------------------------------------------------------------------

def test_replacing_a_block_removes_the_keys_the_merge_would_have_kept():
    """`update` is an enrichment primitive and cannot express deletion — by design. Starting over
    needs a primitive that REPLACES, so the fix is a new one rather than weakening the merge."""
    from api.brand_store import BrandStore
    assert hasattr(BrandStore, "replace_block"), (
        "no primitive can express 'remove this nested key'; reset silently no-ops")


def test_start_over_really_empties_the_candidates(tmp_path, monkeypatch):
    import api.brand_store as bs
    monkeypatch.setattr(bs, "webapp_dir", lambda: tmp_path)

    store = bs.BrandStore()
    store.save({"domain_key": "x_test", "ai_influencer": {
        "candidates": {"c1": {"url": "a"}, "c2": {"url": "b"}},
        "validated": True, "selected_id": "c1"}}, owner_id="u1")

    store.replace_block("x_test", "ai_influencer",
                        {"candidates": {}, "selected_id": "", "has_portrait": False,
                         "validated": False, "portrait_url": None}, for_user="u1")

    ai = (store.get("x_test", for_user="u1") or {}).get("ai_influencer") or {}
    assert ai.get("candidates") == {}, f"start over left {ai.get('candidates')} behind"
    assert ai.get("validated") is False


def test_replace_block_refuses_another_account(tmp_path, monkeypatch):
    """A destructive primitive must obey the same ownership rule as every other write."""
    import api.brand_store as bs
    monkeypatch.setattr(bs, "webapp_dir", lambda: tmp_path)
    store = bs.BrandStore()
    store.save({"domain_key": "x_test", "ai_influencer": {"candidates": {"c1": {}}}},
               owner_id="u1")
    assert store.replace_block("x_test", "ai_influencer", {}, for_user="someone_else") is None
    ai = (store.get("x_test", for_user="u1") or {}).get("ai_influencer") or {}
    assert ai.get("candidates") == {"c1": {}}, "another account wiped a block"


def test_the_reset_route_uses_the_replacing_primitive():
    """The route must not go back to `update` — that is the bug, and it is invisible at the seam.

    Read from the file rather than via `inspect`: importing `api.routes.influencer` on its own
    raises, because `api/webapp_images.py:27` and `api/webapp_influencer.py` form an import cycle
    that only resolves in the order `api.main` happens to use. Pre-existing and out of scope here,
    but it means an isolated import is not a safe way to inspect this module.
    """
    src = (_ROOT / "api" / "routes" / "influencer.py").read_text(encoding="utf-8")
    start = src.find("def _candidate_job")
    body = "\n".join(l.split("#")[0] for l in src[start:src.find("\n@router", start)].splitlines())
    # DB2G-B renamed the primitive as it moved behind the persistence provider: `replace_section`
    # is the same operation with the same guarantee on both backends. The claim under test is
    # unchanged — the reset REPLACES; it must never go back to the merging call.
    assert "replace_section" in body
    assert not re.search(r"record_enrichment\(\s*$|record_enrichment\([^)]*patch=", body), (
        "the reset went back to a merging write; an empty dict deletes nothing")
    assert not re.search(r"update\(\s*req\.domain_key", body), (
        "the reset still merges; an empty dict deletes nothing")


# ---------------------------------------------------------------------
# 2 — the download button must follow what is on screen
# ---------------------------------------------------------------------

def test_the_image_path_rebinds_the_download_button():
    """The reel path claims the button as a DOM property; the image path must claim it back, or
    Download hands out the previous reel while a poster is displayed."""
    reel_bind = _SPA.count("downloadReel()")
    image_bind = len(re.findall(r"onclick\s*=\s*\(\)\s*=>\s*downloadImage\(\)", _SPA))
    assert reel_bind >= 1
    assert image_bind >= 1, (
        "only the reel ever assigns download-btn.onclick — after a reel, Download always "
        "returns the mp4 no matter what is on screen")


# ---------------------------------------------------------------------
# 3 — the calendar range the user picks is the range they get
# ---------------------------------------------------------------------

def test_every_calendar_range_option_is_a_value_the_server_knows():
    from api.webapp_analysis import _PERIOD_DAYS

    block = _SPA[_SPA.find("1 Week"):_SPA.find("1 Week") + 4000]
    sent = set(re.findall(r'<option value="([^"]+)"[^>]*>\s*(?:1 Week|1 Month|3 Months)', block))
    assert sent, "could not find the range options"
    unknown = sorted(v for v in sent if v not in _PERIOD_DAYS)
    assert not unknown, (
        f"{unknown} reach the server, which maps {sorted(_PERIOD_DAYS)} — the rest silently "
        "fall through to the 30-day default, so '1 Week' produces a month")


def test_a_week_is_seven_days():
    from api.webapp_analysis import _PERIOD_DAYS
    values = set(re.findall(r'<option value="([^"]+)"[^>]*>\s*1 Week', _SPA))
    assert values, "no '1 Week' option found"
    for v in values:
        assert _PERIOD_DAYS.get(v) == 7, f"'1 Week' sends {v!r} -> {_PERIOD_DAYS.get(v)} days"
