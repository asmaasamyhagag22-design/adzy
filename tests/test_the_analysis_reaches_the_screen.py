"""The intelligence a run produces has to be readable, not just stored.

MEASURED 2026-08-02 on Al Fouad's own stored record: a CLI analysis produces 20 blocks and 9 of
them reach the dashboard. Two of the missing eleven were not diagnostics — they were the analysis
the client is paying for:

    marketing_intel    the brand's offers, promises, complaints and angles, each with the post it
                       was quoted from — 13 lines for this one brand
    competitor_intel   the same, per competitor

Both lived only under `_full_result`, which the brand projection does not send. Computed,
serialized, unreadable: this repo's most-repeated defect.

The tests below are about the three states, because that is where this kind of panel goes wrong:
"never analysed", "analysed and found nothing" and "here it is" are different answers, and a
competitor whose pages WERE read and yielded nothing is a finding about that competitor.
"""
from __future__ import annotations

from pathlib import Path

from api.routes.analysis import _intel_block


def _item(value, quote="q", url="https://x.test/p/1"):
    return {"value": value, "evidence": [{"quote": quote, "page_url": url}]}


# --- the reshaping keeps the evidence and the counts -------------------------------------

def test_every_line_keeps_the_source_it_was_quoted_from():
    """A claim without its source is the thing the grounding gate exists to reject; showing one on
    screen would undo that at the last step."""
    b = _intel_block({"offers": [_item("20% off Eucerin", "خصم 20% على منتجات Eucerin",
                                       "https://www.instagram.com/p/DasGWOMtH18/")]})
    (row,) = b["items"]["offers"]
    assert row["value"] == "20% off Eucerin"
    assert row["quote"].startswith("خصم")
    assert row["source_url"] == "https://www.instagram.com/p/DasGWOMtH18/"
    assert row["sources"] == 1


def test_the_total_counts_every_list():
    b = _intel_block({"offers": [_item("a"), _item("b")], "usps": [_item("c")],
                      "pain_points": [], "message_angles": [_item("d")]})
    assert b["total"] == 4 and b["empty"] is False


def test_a_line_with_no_value_is_dropped_not_shown_blank():
    b = _intel_block({"offers": [{"value": "  ", "evidence": []}, _item("real")]})
    assert [r["value"] for r in b["items"]["offers"]] == ["real"]


def test_a_line_with_no_evidence_still_shows_but_says_so():
    """Dropping it would hide a claim the run really made; showing it as sourced would be a lie.
    It ships with an empty source and the panel renders "no source" rather than a link."""
    b = _intel_block({"usps": [{"value": "We are the best"}]})
    (row,) = b["items"]["usps"]
    assert row["value"] == "We are the best"
    assert row["source_url"] == "" and row["sources"] == 0


# --- the three states ---------------------------------------------------------------------

def test_nothing_found_and_claims_rejected_are_different_answers():
    """`rejected: 4` means the grounding gate threw four ungrounded claims away; `rejected: 0`
    means nothing was ever found. A client asks about the first and not the second, and a panel
    that reports both as "no data" cannot be asked."""
    nothing = _intel_block({"offers": [], "rejected": 0})
    filtered = _intel_block({"offers": [], "rejected": 4})
    assert nothing["empty"] and filtered["empty"]
    assert nothing["rejected"] == 0 and filtered["rejected"] == 4


def test_a_missing_block_does_not_crash_the_panel():
    b = _intel_block(None)
    assert b["total"] == 0 and b["empty"] is True and b["rejected"] == 0


# --- the screen -----------------------------------------------------------------------------

def _html() -> str:
    return Path("frontend/index.html").read_text(encoding="utf-8")


def test_the_panel_exists_and_is_loaded():
    html = _html()
    assert 'id="intel-panel"' in html
    assert "loadBrandIntel()" in html
    assert "/api/intel/" in html


def test_the_panel_distinguishes_the_three_states():
    html = _html()
    assert "No full analysis stored" in html, "never analysed"
    assert "nothing quotable was found" in html, "analysed, found nothing"
    assert "dropped for having no source" in html, "analysed, claims rejected"


def test_a_peer_that_was_read_and_yielded_nothing_says_so():
    """"We read their pages and there was nothing" is a finding about that competitor. Rendering it
    the same as "we never opened them" throws the finding away."""
    assert "read, and nothing quotable was on their pages" in _html()
    assert "No competitor was analysed in this run" in _html()


def test_a_truncated_section_says_how_many_are_hidden():
    """MEASURED in the browser: the header read "13 line(s)" and nine were on screen, because each
    section shows the first few. A silent cap reads as "that is all there was"."""
    html = _html()
    assert "more not shown" in html


def test_every_source_link_is_safe_to_click():
    """These URLs come from scraped pages — third-party content opening in the client's browser."""
    html = _html()
    i = html.index("function intelRows")
    body = html[i:i + 2000]
    assert 'target="_blank"' in body and "noopener" in body
    assert "encodeURI(r.source_url)" in body
