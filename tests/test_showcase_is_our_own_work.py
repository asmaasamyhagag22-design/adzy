# -*- coding: utf-8 -*-
"""The public gallery uses three project-created synthetic demo examples.

Optional branded campaign media remain in the owner's original workspace and are excluded from
this public candidate. The existing endpoint and gallery are unchanged. These checks retain the
minimum populated gallery and prevent retired stock imagery / broken hardcoded landing assets.
Synthetic fixtures establish application behavior, not paid-provider design quality.
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
