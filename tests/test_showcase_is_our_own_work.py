# -*- coding: utf-8 -*-
"""The front page shows work this system actually made for real brands.

Owner, 2026-07-26: «استخدم بدل صور الشخص القالع اللي انا معملتوش — دا عايزه يبان شغلنا احنا اول ما
يخش» ("use these instead of the shirtless person I didn't make — I want OUR work visible the moment
someone enters").

MEASURED before the change: `frontend/assets/showcase/` held exactly two files, and both were the
same bare-shouldered blond stock-styled male — `volt_portrait.png` (a headshot) and
`volt_poster.png` (a Spanish-language energy-drink ad). `/api/showcase` lists that folder verbatim,
and the landing page drives BOTH its hero slideshow and its gallery from that list, so those two
images were the entire visual argument the product made for itself. Neither was made for a real
client of hers.

Replaced with four assets from real runs: a Nahdi (Saudi pharmacy) app campaign, a Volume Qatar
café poster, an NTI (Egypt) bilingual poster, and an AI-influencer portrait. Three countries, two
scripts — which is also the D-8 claim the product makes, shown rather than stated.

This test does not judge taste. It pins two things that were measurably wrong: that the showcase
is not empty, and that the retired stock assets do not come back.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SHOWCASE = _ROOT / "frontend" / "assets" / "showcase"
_LANDING = (_ROOT / "frontend" / "landing.html").read_text(encoding="utf-8", errors="replace")

# Named individually rather than by pattern: a future asset that happens to contain "volt" is not
# the problem — these two specific files are.
RETIRED = {"volt_portrait.png", "volt_poster.png"}


def test_the_showcase_is_not_empty():
    """`/api/showcase` returns this folder verbatim; empty means a hero with no slides."""
    imgs = [p.name for p in _SHOWCASE.iterdir()
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    assert len(imgs) >= 3, f"only {len(imgs)} showcase image(s): {imgs}"


def test_the_retired_stock_portraits_are_gone():
    present = {p.name for p in _SHOWCASE.iterdir() if p.is_file()} & RETIRED
    assert not present, f"the stock portraits are back in the showcase: {sorted(present)}"


def test_the_landing_does_not_reference_a_retired_asset():
    """A hardcoded <img> keeps rendering after the file is deleted — as a broken image, on the
    first screen of the demo."""
    for name in RETIRED:
        assert name not in _LANDING, f"landing.html still points at {name}"


def test_every_image_the_landing_hardcodes_actually_exists():
    """The gallery is data-driven, but the step cards are not. A missing file here is a broken
    image in the middle of the pitch."""
    import re
    missing = []
    for src in re.findall(r'src="(/assets/[^"]+)"', _LANDING):
        rel = src.split("?")[0].lstrip("/")
        if not (_ROOT / "frontend" / rel).is_file():
            missing.append(src)
    assert not missing, f"landing.html references files that do not exist: {missing}"
