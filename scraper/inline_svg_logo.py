"""Rasterize an inline ``<svg>`` brand logo to a self-contained PNG ``data:`` URI.

Many sites ship the brand logo as an inline ``<svg>`` — often a ``<use href="#sprite">``
whose real paths and colours live elsewhere (a ``<symbol>`` plus CSS-class fills). The
scraper can only record such a logo as the pseudo-src ``inline-svg:N`` (no fetchable
URL), so the poster/reel had to drop it and fall back to a text wordmark.

This RASTERIZES the logo exactly as the browser renders it — the ``<use>`` sprite
resolved and CSS-class fills applied — ISOLATED from the rest of the page (every other
element is hidden, so no nav chrome or header background bleeds in) on a TRANSPARENT
background. It then GATES the result: the capture is accepted ONLY if it shows visible
ink when composited on the poster's light logo chip. A logo that would render
blank/invisible there (a white-on-dark mark, or a mis-selected nav icon) is REJECTED,
so the caller keeps the clean wordmark. The worst case is the existing wordmark — never
a broken or blank logo.

MEASURED across the 5 saved inline-svg sites: Vodafone (red ``<use>`` sprite), JewelPin
and Artemer (self-contained, colour from inline fills / contextual CSS) are captured as
their REAL logos; Andalusia (white-on-dark) and Assih (a mis-indexed icon) are rejected
to wordmark. 3 recovered, 0 broken.

Side-effect free (no env mutation). Playwright is imported lazily so importing this
module never requires a browser; capture returns ``None`` on any failure.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Optional

# The SAME selector the scraper's visual extractor uses to enumerate inline-svg logo
# candidates, so a candidate's ``inline-svg:N`` index maps to ``select(...)[N-1]`` here.
_SELECTOR = "header svg, nav svg, a svg, [class*=logo] svg, [id*=logo] svg"

# Gate thresholds, MEASURED across the 5 saved inline-svg sites (fraction of OPAQUE
# pixels in the transparent capture):
#   < _MIN_OPAQUE  -> blank / a mis-indexed empty icon          -> reject
#   > _MAX_OPAQUE  -> a featureless SOLID block, not a logo      -> reject (Assih: a
#                     100%-opaque white rectangle; real logos never fill their whole box
#                     — Vodafone's disc is 79%, the rest 4-5%)
# A logo whose opaque ink is lighter than _LIGHT_LUM (mean luminance) is a white/light
# mark that would vanish on the poster's default light chip -> flag it for a DARK chip
# (Andalusia, lum 1.0) instead of rejecting it.
_MIN_OPAQUE = 0.01
_MAX_OPAQUE = 0.95
_LIGHT_LUM = 0.62

# In-page: isolate the Nth logo svg (hide everything else so no chrome/background bleeds
# in), reveal any <symbol>/sprite it references via <use>, enlarge it, and tag it.
_ISOLATE_JS = r"""(i) => {
  const svgs = Array.from(document.querySelectorAll(%(sel)r));
  const s = svgs[i - 1];
  if (!s) return false;
  const st = document.createElement('style');
  st.textContent = '*{visibility:hidden !important;}';
  document.documentElement.appendChild(st);
  function show(el) {
    if (!el) return;
    try { el.style.setProperty('visibility', 'visible', 'important'); } catch (e) {}
    if (el.querySelectorAll) el.querySelectorAll('*').forEach(n => {
      try { n.style.setProperty('visibility', 'visible', 'important'); } catch (e) {}
    });
  }
  show(s);
  // Reveal symbols/sprites referenced by <use> so sprite-based logos render.
  s.querySelectorAll('use').forEach(u => {
    const h = u.getAttribute('href') || u.getAttribute('xlink:href') || '';
    if (h[0] === '#') {
      const ref = document.getElementById(h.slice(1));
      show(ref);
      let p = ref;
      while (p) {
        if (p.tagName && p.tagName.toLowerCase() === 'svg') {
          p.style.setProperty('visibility', 'visible', 'important');
          break;
        }
        p = p.parentElement;
      }
    }
  });
  s.style.position = 'fixed';
  s.style.left = '10px';
  s.style.top = '10px';
  s.style.width = '360px';
  s.style.height = '360px';
  s.style.zIndex = '2147483647';
  s.setAttribute('data-logoprobe', '1');
  return true;
}""" % {"sel": _SELECTOR}

_BLOCKED_RESOURCES = ("image", "media", "font", "script", "xhr", "fetch")


def _destination_is_allowed(url: str) -> bool:
    """The F-01 browser destination policy, shared with the primary Playwright fetch path.

    Imported lazily from :mod:`scraper.fetcher` (which needs Playwright at import time) so this
    module keeps its promise that importing it never requires a browser. Reusing that predicate
    rather than restating it is the point: two copies of a destination policy drift apart the
    next time either one is tuned, and this path exists precisely because it had none.
    """
    from .fetcher import _browser_request_is_allowed

    return _browser_request_is_allowed(url)


def _route_request(route) -> None:
    """Decide one browser request provoked by the UNTRUSTED captured HTML.

    Two independent conditions, in this order: the destination must be one we are entitled to
    reach, and the resource must be one this capture wants. A resource type can never make a
    destination safe — the old handler let `document` (iframe), `stylesheet`, `websocket`,
    `manifest` and `other` through on type alone, which is a browser fetch sink outside F-01.
    """
    try:
        request = route.request
        url = str(request.url or "")
        resource_type = str(request.resource_type or "")
    except Exception:  # noqa: BLE001 — an unreadable request is not safe to continue
        _abort(route)
        return
    try:
        destination_allowed = _destination_is_allowed(url)
    except Exception:  # noqa: BLE001 — no policy available means no permission
        destination_allowed = False
    if not destination_allowed or resource_type in _BLOCKED_RESOURCES:
        _abort(route)
        return
    try:
        route.continue_()
    except Exception:  # noqa: BLE001 — a route Playwright already handled
        pass


def _abort(route) -> None:
    try:
        route.abort()
    except Exception:  # noqa: BLE001 — a route Playwright already handled
        pass


def _gate(png_bytes: bytes) -> Optional[bool]:
    """Decide whether a transparent logo capture is usable, and on which chip.

    Returns ``None`` to REJECT (blank, or a featureless solid block — not a logo);
    otherwise returns ``prefers_dark_chip``: ``True`` for a light/white logo that needs
    a DARK chip to be visible, ``False`` for a dark/colour logo (the default light chip).
    """
    try:
        from PIL import Image
    except Exception:
        return False  # can't measure -> accept on the default (light) chip
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    px = list(im.getdata())
    if not px:
        return None
    opaque = [(r, g, b) for r, g, b, a in px if a > 40]
    frac = len(opaque) / len(px)
    if frac < _MIN_OPAQUE or frac > _MAX_OPAQUE:
        return None
    avg_lum = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in opaque) / (len(opaque) * 255)
    return avg_lum > _LIGHT_LUM


def capture_inline_svg_logo(
    html: str, svg_index: int, *, timeout_ms: int = 10000
) -> Optional[tuple[str, bool]]:
    """Rasterize the inline-svg logo at 1-based ``svg_index`` from a page's HTML.

    Returns ``(data_uri, prefers_dark_chip)`` — a ``data:image/png;base64,...`` PNG and
    whether it needs a DARK chip (a light/white logo) — or ``None`` if the logo can't be
    captured or fails the visibility gate. Never raises (best-effort enrichment).
    """
    if svg_index < 1 or not html:
        return None
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import TimeoutError as PWTimeout
    except Exception:
        return None

    shot: Optional[bytes] = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                # A Service Worker intercepts fetches BENEATH `route`, so it could issue the
                # requests the guard below exists to refuse. Blocked for the same reason
                # `fetcher.make_browser_context` blocks them on the primary fetch path.
                context = browser.new_context(
                    viewport={"width": 800, "height": 800},
                    service_workers="block",
                )
                # Registered on the CONTEXT, before any untrusted HTML is loaded, so a popup
                # the captured page opens is guarded by the same decision as the page itself.
                context.route("**/*", _route_request)
                page = context.new_page()
                try:
                    page.set_content(html, wait_until="domcontentloaded", timeout=timeout_ms)
                except PWTimeout:
                    pass  # the DOM is parsed enough for a static logo
                if page.evaluate(_ISOLATE_JS, svg_index):
                    el = page.query_selector('[data-logoprobe="1"]')
                    if el is not None:
                        shot = el.screenshot(omit_background=True)
            finally:
                browser.close()
    except Exception:
        return None

    if not shot:
        return None
    prefers_dark = _gate(shot)
    if prefers_dark is None:
        return None
    data_uri = "data:image/png;base64," + base64.b64encode(shot).decode("ascii")
    return data_uri, prefers_dark


def resolve_raw_homepage(raw_dir: str | Path) -> Optional[Path]:
    """The scrape's saved homepage HTML. Two layouts exist on disk: older scrapes save
    ``00_homepage.html``, newer ones ``home.html`` — checking only the former silently
    disabled enrichment for every new scrape."""
    raw_dir = Path(raw_dir)
    for name in ("00_homepage.html", "home.html"):
        p = raw_dir / name
        if p.is_file():
            return p
    return None


def _inline_svg_index(src: str) -> Optional[int]:
    if isinstance(src, str) and src.startswith("inline-svg:"):
        try:
            return int(src.split(":", 1)[1])
        except (ValueError, IndexError):
            return None
    return None


# Formats that CANNOT carry transparency. A PNG/WebP primary may already be transparent,
# so it is never touched — the upgrade below fires only when the primary is provably
# opaque, and can therefore never replace a brand logo that was already fine.
_OPAQUE_RASTER_EXTS = (".jpg", ".jpeg")

# An inline-svg candidate the scraper itself scored below this is too doubtful to swap
# in for the primary (a nav icon that happens to live in a logo-ish container).
_MIN_CANDIDATE_CONFIDENCE = 0.3


def _is_certainly_opaque_raster(src: str) -> bool:
    if not isinstance(src, str) or not src.startswith(("http://", "https://")):
        return False
    path = src.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(_OPAQUE_RASTER_EXTS)


def _svg_candidate_index(visual: dict) -> Optional[int]:
    """The first inline-svg entry in ``logo_candidates`` worth trying, or ``None``."""
    for cand in visual.get("logo_candidates") or []:
        if not isinstance(cand, dict):
            continue
        try:
            conf = float(cand.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < _MIN_CANDIDATE_CONFIDENCE:
            continue
        idx = _inline_svg_index(str(cand.get("src") or ""))
        if idx is not None:
            return idx
    return None


def enrich_logo_in_profile_dict(profile: dict, raw_html_path: str | Path) -> bool:
    """The in-memory half of :func:`enrich_profile_logo` — mutates ``profile`` in place.

    Exists because the two profile-producing paths differ: the ``business_profile`` CLI writes a
    file, while the analysis run (``competitor/full_run.py``, which is what the DASHBOARD uses)
    keeps the profile in the result document. Only the file path was wired, so every dashboard
    brand silently skipped logo enrichment.
    """
    raw_html_path = Path(raw_html_path)
    if not isinstance(profile, dict) or not raw_html_path.is_file():
        return False
    visual = profile.get("visual") or {}
    primary = visual.get("primary_logo")
    if not isinstance(primary, dict):
        return False
    primary_src = str(primary.get("src") or "")
    idx = _inline_svg_index(primary_src)
    upgraded_from: Optional[str] = None
    if idx is None and _is_certainly_opaque_raster(primary_src):
        idx = _svg_candidate_index(visual)
        if idx is not None:
            upgraded_from = primary_src
    if idx is None:
        return False
    captured = capture_inline_svg_logo(
        raw_html_path.read_text(encoding="utf-8", errors="replace"), idx)
    if not captured:
        return False
    data_uri, prefers_dark = captured
    primary["src"] = data_uri
    primary["source_type"] = ("inline_svg_upgraded_from_jpeg" if upgraded_from
                              else "inline_svg_rasterized")
    if upgraded_from:
        primary["opaque_raster_src"] = upgraded_from
    primary["logo_chip"] = "dark" if prefers_dark else "light"
    visual["primary_logo"] = primary
    profile["visual"] = visual
    return True


def enrich_profile_logo(profile_path: str | Path, raw_html_path: str | Path) -> bool:
    """If a profile's primary logo is an unfetchable ``inline-svg:N``, rasterize it from
    the scrape's saved homepage HTML and write the PNG ``data:`` URI back into the
    profile so the poster/reel render the REAL logo. No-op (returns ``False``) when the
    logo isn't inline-svg, the HTML is missing, or the capture fails the gate (the
    wordmark fallback then stands). Profile JSON is poster-facing, so a large data URI
    is fine here; the manifest schema is untouched.
    """
    profile_path = Path(profile_path)
    raw_html_path = Path(raw_html_path)
    if not profile_path.is_file() or not raw_html_path.is_file():
        return False
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if not enrich_logo_in_profile_dict(profile, raw_html_path):
        return False
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return True
