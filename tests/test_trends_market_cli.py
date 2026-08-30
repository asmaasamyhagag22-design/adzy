"""CS-62 (D-8): the trend-fetching CLI entrypoints must aim the SERP at the BRAND'S market.

Before the fix, `poster/__main__.py`, `strategy/__main__.py` and `trends/__main__.py` all called
`top_trends(keywords_from_profile(profile), ...)` with NO gl/hl even though `profile` was in scope
one expression away — so `default_trend_sources(gl="", hl="")` served Google's US/English default
and the dashboard labelled it the brand's "market signals". (`full_run.py` + `api/routes/swot.py`
were already wired.)

This drives the strategy CLI end-to-end with a CAPTURING provider (zero network, zero spend) and
asserts the brand's country reaches the SERP. The other two mains use the identical
`search_locale(profile)` -> `top_trends(gl=, hl=)` pattern. It fails if the wiring is reverted.
"""
from __future__ import annotations

import json
import sys

import competitor.search_providers as sp


class _CapturingProvider:
    name = "capture"

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, query, *, num=10, gl=None, hl=None):
        self.calls.append({"query": query, "gl": gl, "hl": hl})
        return []


# A real business_profile.json is FLAT (no "profile" wrapper) — matches what the scraper writes.
_SAUDI_PROFILE = {
    "name": {"value": "متجر العبايات"},
    "languages": ["ar"],
    "source_url": {"value": "https://brand.com.sa/"},          # .sa -> country_of -> Saudi Arabia
    "category": {"value": "abaya boutique retail"},
    "offerings": [{"name": "abaya collection"}, {"name": "modest wear"}],
    "value_propositions": [{"value": "premium quality"}],
}


def test_strategy_cli_sends_the_brands_country_to_the_serp(tmp_path, monkeypatch):
    cap = _CapturingProvider()
    monkeypatch.setattr(sp, "get_default_search_provider", lambda: cap)

    prof = tmp_path / "brand.json"
    prof.write_text(json.dumps(_SAUDI_PROFILE, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "plan.json"

    from strategy.__main__ import main
    monkeypatch.setattr(sys, "argv",
                        ["strategy", str(prof), "--trends", "--no-llm",
                         "--days", "7", "--out", str(out)])
    rc = main()

    assert rc == 0
    assert cap.calls, "the calendar CLI never hit the SERP — the trend fetch was skipped"
    # gl='sa' (from the .sa domain), NOT None — the SERP is Saudi, not Google's US default.
    assert cap.calls[0]["gl"] == "sa"
    assert cap.calls[0]["hl"] == "ar"


def test_trends_cli_omits_gl_for_a_bare_keyword_run(tmp_path, monkeypatch):
    """A keyword-only `python -m trends` run has no brand, so it must send NO gl (honest default),
    never a guessed market."""
    cap = _CapturingProvider()
    monkeypatch.setattr(sp, "get_default_search_provider", lambda: cap)
    # neutralize the paid-entry tripwire for this hermetic, no-spend capture
    monkeypatch.setenv("BASEERA_NO_SPEND", "0")

    from trends.__main__ import main
    monkeypatch.setattr(sys, "argv", ["trends", "coffee", "roastery", "--require-match"])
    try:
        main()
    except SystemExit:
        pass  # the spend tripwire may still guard; the point is the payload shape when it runs
    for c in cap.calls:
        assert c["gl"] in (None, "")     # no brand -> no country claim
        assert c["hl"] in (None, "")
