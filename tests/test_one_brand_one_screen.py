# -*- coding: utf-8 -*-
"""One brand per screen, one action per tab, and a zero that explains itself.

Three defects the owner hit in one sitting, all visible in her screenshots.

1. "Generate poster" IN THE CALENDAR DOES NOTHING. «بضغط مش بيعمل حاجه»
   MEASURED: the SPA keeps TWO brand slots — `state.currentBrand` and `state.studioBrand` — and
   there are 44 reads of `studioBrand`, of which only three fall back to `currentBrand`.
   `openBrand()` (clicking a card on Home) sets ONLY `currentBrand`, and `renderBrand()` then draws
   the whole strategy dashboard from it — header, counts, calendar, buttons. So the panel shows
   AlFouad while `generateCalendarDayPoster` reads `studioBrand`, finds null, and returns after a
   toast the user reads as "nothing happened". The screen and the button disagree about which
   brand is open.

2. THE TREND TAB OFFERS "REGENERATE SWOT". The action row sits ABOVE the views and
   `switchStratView` only toggles which view is visible — it never touches the buttons. So every
   tab shows the SWOT actions, including the one tab that is not about the SWOT. With Trend now a
   stage of its own (owner's ruling), it needs its own action.

3. "0 WEAKNESSES" IS PRESENTED AS A FINDING. MEASURED on AlFouad: weaknesses=0, and the SWOT's own
   `notes` say why —
       «نقاط الضعف: فاضية — نقاط الضعف محتاجة أبعاد مقارنة من موقعك أو من الأقران.
        المتاح في التشغيلة دي: 4 قرين، منهم 4 بموقع قابل للفحص.»
   The system explains itself and nothing renders the explanation, so a big "0" reads as "this
   brand has no weaknesses" when it means "we could not compare". Same failure class as the rest of
   this session, pointed at the client.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPA = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------
# 1 — one brand slot
# ---------------------------------------------------------------------

def test_there_is_a_single_resolver_for_the_open_brand():
    assert re.search(r"function activeBrand\s*\(", _SPA), (
        "no single accessor for 'the brand this screen is showing'")


def test_no_handler_reads_a_raw_brand_slot():
    """A bare `state.studioBrand` in a handler is the bug: it is null on every path that opened the
    brand from Home, and the button dies while the screen shows a brand."""
    body = _SPA
    # the resolver and the setters are allowed to touch the raw slots; nothing else is.
    allowed = re.compile(r"(function activeBrand[\s\S]{0,400}?\n\})"
                         r"|(state\.studioBrand\s*=)"
                         r"|(state\.currentBrand\s*=)")
    stripped = allowed.sub("", body)
    leaks = re.findall(r"const b = state\.studioBrand[^;]*;", stripped)
    assert not leaks, (
        f"{len(leaks)} handler(s) still read a raw brand slot instead of activeBrand(): "
        f"{leaks[:3]}")


def test_the_calendar_poster_button_uses_the_resolver():
    m = re.search(r"async function generateCalendarDayPoster\([^)]*\)\s*\{(.*?)\n\}", _SPA, re.S)
    assert m, "generateCalendarDayPoster moved"
    assert "activeBrand()" in m.group(1), (
        "the calendar poster button still reads state.studioBrand — it is null whenever the brand "
        "was opened from a Home card, which is the path in the owner's screenshot")


# ---------------------------------------------------------------------
# 2 — each tab offers its own action
# ---------------------------------------------------------------------

def test_the_trend_tab_has_its_own_action():
    assert 'id="strat-gen-trends-btn"' in _SPA, "the Trend tab has no Trend action"
    assert "refreshTrends" in _SPA, "nothing fetches trends from the UI"


def test_switching_tabs_switches_the_actions():
    m = re.search(r"function switchStratView\(name\)\s*\{(.*?)\n\}", _SPA, re.S)
    assert m, "switchStratView moved"
    body = m.group(1)
    assert "STRAT_TAB_ACTIONS" in body, (
        "switchStratView only toggles views, so every tab keeps showing the SWOT buttons — "
        "including Trend, where 'Regenerate SWOT' is the wrong action entirely")
    assert re.search(r"style\.display\s*=\s*'none'", body), (
        "switching away from a tab must HIDE that tab's actions, not just reveal the new ones")

    # and the map itself must give the Trend tab its own action and no SWOT action
    m2 = re.search(r"const STRAT_TAB_ACTIONS\s*=\s*\{(.*?)\n\};", _SPA, re.S)
    assert m2, "STRAT_TAB_ACTIONS missing"
    trend_line = re.search(r"trend:\s*\[([^\]]*)\]", m2.group(1))
    assert trend_line, "no trend entry"
    assert "trends" in trend_line.group(1)
    assert "swot" not in trend_line.group(1), (
        "the Trend tab still owns a SWOT button")


# ---------------------------------------------------------------------
# 3 — an empty quadrant says why
# ---------------------------------------------------------------------

def test_an_empty_quadrant_shows_the_reason_the_swot_recorded():
    """`swot.notes` already carries the explanation; the UI has to print it. Otherwise a 0 that
    means 'not comparable' is read as 'nothing wrong here'."""
    assert "renderSwotCaveat" in _SPA or 'id="strat-swot-caveat"' in _SPA, (
        "nothing renders swot.notes, so a zero quadrant is shown with no explanation")


def test_the_reason_is_taken_from_the_data_not_hardcoded():
    """D-8: the note is written by the analysis in the brand's own language and must not be
    replaced by an invented English sentence."""
    m = re.search(r"function renderSwotCaveat\([^)]*\)\s*\{(.*?)\n\}", _SPA, re.S)
    assert m, "renderSwotCaveat missing"
    body = m.group(1)
    assert re.search(r"\.notes\b", body), "the caveat is not read from swot.notes"
    assert "weaknesses need" not in body.lower(), (
        "an English explanation is hardcoded instead of showing the note the analysis wrote")
