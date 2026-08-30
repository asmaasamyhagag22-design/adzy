"""Inline-svg logo rasterization — the non-browser logic (index parse, ink gate,
enrich no-op paths). The actual Playwright capture is exercised live, not in CI."""
from __future__ import annotations

import io
import json
from pathlib import Path

from scraper.inline_svg_logo import (
    _inline_svg_index, _gate, enrich_profile_logo, resolve_raw_homepage,
)


def test_resolve_raw_homepage_handles_both_scrape_layouts(tmp_path: Path):
    # Older scrapes save 00_homepage.html, newer ones home.html — checking only the
    # former silently disabled enrichment for every new scrape.
    old = tmp_path / "old";  old.mkdir()
    (old / "00_homepage.html").write_text("<html/>", encoding="utf-8")
    new = tmp_path / "new";  new.mkdir()
    (new / "home.html").write_text("<html/>", encoding="utf-8")
    empty = tmp_path / "empty";  empty.mkdir()
    assert resolve_raw_homepage(old).name == "00_homepage.html"
    assert resolve_raw_homepage(new).name == "home.html"
    assert resolve_raw_homepage(empty) is None


def test_inline_svg_index_parses_only_pseudo_src():
    assert _inline_svg_index("inline-svg:1") == 1
    assert _inline_svg_index("inline-svg:15") == 15
    assert _inline_svg_index("https://x.com/logo.png") is None
    assert _inline_svg_index("data:image/png;base64,AAAA") is None
    assert _inline_svg_index("text-wordmark:Acme") is None
    assert _inline_svg_index("inline-svg:notanint") is None


def _png_mark(color, *, canvas=(100, 100), mark=(20, 20)):
    """A small opaque `mark` of `color` centered on a larger TRANSPARENT canvas — a
    logo-like moderate opaque fraction (here 4%)."""
    from PIL import Image
    im = Image.new("RGBA", canvas, (0, 0, 0, 0))
    block = Image.new("RGBA", mark, color)
    im.paste(block, ((canvas[0] - mark[0]) // 2, (canvas[1] - mark[1]) // 2))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _png_solid(color, size=(40, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_gate_rejects_blank_and_solid_block():
    # Fully transparent -> no ink -> reject.
    assert _gate(_png_solid((0, 0, 0, 0))) is None
    # A 100%-opaque white block (Assih's failure mode) -> not a logo -> reject.
    assert _gate(_png_solid((255, 255, 255, 255))) is None


def test_gate_picks_chip_by_luminance():
    # A dark/colour logo (red mark on transparent) -> default LIGHT chip.
    assert _gate(_png_mark((230, 0, 0, 255))) is False
    # A WHITE logo (Andalusia's case) -> needs a DARK chip (recovered, not rejected).
    assert _gate(_png_mark((255, 255, 255, 255))) is True


def test_enrich_is_noop_for_non_inline_svg_logo(tmp_path: Path):
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({
        "visual": {"primary_logo": {"src": "https://x.com/logo.png"}}
    }), encoding="utf-8")
    before = prof.read_text(encoding="utf-8")
    # Even with a real HTML file, a non-inline-svg logo is left untouched.
    html = tmp_path / "home.html"
    html.write_text("<html><body><svg></svg></body></html>", encoding="utf-8")
    assert enrich_profile_logo(prof, html) is False
    assert prof.read_text(encoding="utf-8") == before          # file unchanged


def test_enrich_is_noop_when_html_missing(tmp_path: Path):
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({
        "visual": {"primary_logo": {"src": "inline-svg:1"}}
    }), encoding="utf-8")
    assert enrich_profile_logo(prof, tmp_path / "does_not_exist.html") is False


# ---- vector-preference upgrade (owner: "اللوجو اعمله isolation صح") -----------------
# A JPEG primary CANNOT carry transparency, so when the scraper ALSO saw an inline-<svg>
# logo candidate (nanocare: Zid CDN .jpg thumbnail primary + the real mark at
# inline-svg:1), the vector is tried first. Conservative: fires ONLY for certainly-
# opaque formats (.jpg/.jpeg) — a PNG primary may already be transparent and is left
# alone — and a gate-rejected capture leaves the profile byte-identical.

def _profile_with_candidates(tmp_path: Path, primary_src: str, candidates) -> Path:
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({
        "visual": {"primary_logo": {"src": primary_src, "confidence": 0.9},
                   "logo_candidates": candidates}
    }), encoding="utf-8")
    return prof


def _fake_capture(monkeypatch, result, calls):
    def fake(html, idx, **kw):
        calls.append(idx)
        return result
    monkeypatch.setattr("scraper.inline_svg_logo.capture_inline_svg_logo", fake)


def test_enrich_upgrades_opaque_jpeg_primary_from_svg_candidate(tmp_path, monkeypatch):
    prof = _profile_with_candidates(
        tmp_path, "https://cdn.x/img/h=175,q=100/logo.jpg",
        [{"src": "https://cdn.x/banner.png", "confidence": 0.2},
         {"src": "inline-svg:1", "source_type": "inline_svg", "confidence": 0.38}])
    html = tmp_path / "home.html"
    html.write_text("<html><svg></svg></html>", encoding="utf-8")
    calls: list[int] = []
    _fake_capture(monkeypatch, ("data:image/png;base64,QUJD", False), calls)
    assert enrich_profile_logo(prof, html) is True
    assert calls == [1]                                         # the candidate's index
    primary = json.loads(prof.read_text(encoding="utf-8"))["visual"]["primary_logo"]
    assert primary["src"] == "data:image/png;base64,QUJD"
    assert primary["source_type"] == "inline_svg_upgraded_from_jpeg"
    assert primary["opaque_raster_src"].endswith("logo.jpg")    # provenance kept
    assert primary["logo_chip"] == "light"


def test_enrich_candidate_upgrade_is_conservative(tmp_path, monkeypatch):
    calls: list[int] = []
    _fake_capture(monkeypatch, ("data:image/png;base64,QUJD", False), calls)
    html = tmp_path / "home.html"
    html.write_text("<html><svg></svg></html>", encoding="utf-8")
    # (a) a PNG primary may already carry alpha -> untouched (flood-fill covers it).
    p1 = _profile_with_candidates(tmp_path, "https://cdn.x/logo.png",
                                  [{"src": "inline-svg:1", "confidence": 0.38}])
    assert enrich_profile_logo(p1, html) is False
    # (b) a JPEG primary but NO svg candidate -> nothing to try.
    p2 = _profile_with_candidates(tmp_path, "https://cdn.x/logo.jpg",
                                  [{"src": "https://cdn.x/banner.png"}])
    assert enrich_profile_logo(p2, html) is False
    # (c) a JPEG primary + an svg candidate the scraper itself DOUBTS (< 0.3) -> skip.
    p3 = _profile_with_candidates(tmp_path, "https://cdn.x/logo.jpg",
                                  [{"src": "inline-svg:2", "confidence": 0.1}])
    assert enrich_profile_logo(p3, html) is False
    assert calls == []                                          # capture never attempted


def test_enrich_candidate_upgrade_keeps_raster_when_gate_rejects(tmp_path, monkeypatch):
    prof = _profile_with_candidates(tmp_path, "https://cdn.x/logo.jpg",
                                    [{"src": "inline-svg:1", "confidence": 0.38}])
    html = tmp_path / "home.html"
    html.write_text("<html><svg></svg></html>", encoding="utf-8")
    before = prof.read_text(encoding="utf-8")
    calls: list[int] = []
    _fake_capture(monkeypatch, None, calls)                     # capture fails the gate
    assert enrich_profile_logo(prof, html) is False
    assert calls == [1]                                         # it WAS tried
    assert prof.read_text(encoding="utf-8") == before           # JPEG + flood-fill stand
