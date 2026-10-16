"""Visual identity extraction.

v0.2 keeps the old v0.1 outputs working while adding structured,
candidate-based visual evidence:
- multiple logo candidates, scored + classified
- low-confidence / co-branding notes instead of silent wrong picks
- raw screenshot palette separated from brand palette
- palette warnings for background-dominated screenshots
"""
from __future__ import annotations

import io
import logging
import math
import re
from collections import Counter, defaultdict
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from PIL import Image
try:
    from colorthief import ColorThief
except ModuleNotFoundError:  # pragma: no cover - dependency fallback for light test envs
    ColorThief = None  # type: ignore[assignment]
from playwright.sync_api import Page
from playwright.sync_api import Error as PWError

from ..config import MAX_PALETTE_COLORS
from ..schemas import ColorEntry, LogoCandidate, LogoRecord, VisualIdentity

logger = logging.getLogger(__name__)

PRIMARY_LOGO_THRESHOLD = 55.0

SOCIAL_KEYWORDS = (
    "facebook", "instagram", "twitter", "x-twitter", "linkedin", "youtube",
    "tiktok", "whatsapp", "telegram", "pinterest", "snapchat",
)
LOGO_KEYWORDS = ("logo", "brand", "identity")
# A PURE brand-mark filename: `logo`, `brand-logo`, `ColoredLogo`, `WhiteLogo`, `logo-dark`… — a
# variant qualifier optionally around 'logo'/'wordmark'/'brandmark'. NOT `ministry-logo` /
# `techcrunch-logo` / `feature-badge` (a third-party or generic name has a non-variant token).
_LOGO_VARIANT = (r"colou?red|white|black|dark|light|main|primary|secondary|site|header|footer|"
                 r"full|mono|colou?r|brand")
_PURE_LOGO_RE = re.compile(
    rf"^(?:{_LOGO_VARIANT})?[-_ ]?(?:logo|wordmark|brandmark)(?:[-_ ]?(?:{_LOGO_VARIANT}|mark))?$",
    re.IGNORECASE)


def _is_pure_logo_filename(src: str) -> bool:
    """True when the filename basename is a PURE brand-mark token (see _PURE_LOGO_RE)."""
    s = (src or "").split("?")[0]
    if s.lower().startswith("data:"):
        return False
    base = s.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return bool(_PURE_LOGO_RE.match(base.strip().lower()))


HERO_KEYWORDS = ("hero", "banner", "slider", "slide", "carousel", "gallery", "cover")
PARTNER_KEYWORDS = ("partner", "partners", "sponsor", "sponsored", "powered", "alliance")
AUTHORITY_KEYWORDS = (
    "ministry", "government", "governorate", "authority", "official", "gov",
    "mcit", "وزارة", "حكومة", "هيئة",
)
INITIATIVE_KEYWORDS = ("initiative", "program", "hub", "creativa", "academy", "institute")


# ---------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------

def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _hex_to_rgb(hex_color: str) -> Optional[tuple[int, int, int]]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _normalize_css_color(v: Optional[str]) -> Optional[str]:
    """Convert CSS rgb/rgba/#RGB/#RRGGBB to lowercase #RRGGBB when possible."""
    if not v:
        return None
    v = str(v).strip().lower()
    if v in ("transparent", "rgba(0, 0, 0, 0)", "none", "inherit", "initial"):
        return None
    if v.startswith("rgb"):
        try:
            inside = v[v.index("(") + 1: v.index(")")]
            parts = [p.strip() for p in inside.split(",")]
            if len(parts) >= 4 and float(parts[3]) == 0:
                return None
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
            return "#{:02x}{:02x}{:02x}".format(r, g, b)
        except (ValueError, IndexError):
            return None
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            return "#" + "".join(c * 2 for c in h)
        if len(h) == 6:
            return "#" + h
    return None


def _luma(hex_color: str) -> Optional[float]:
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return None
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _saturation(hex_color: str) -> float:
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return 0.0
    r, g, b = [x / 255.0 for x in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _is_near_white(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    return bool(rgb and min(rgb) >= 225)


def _is_near_black(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    return bool(rgb and max(rgb) <= 38)


def _is_low_saturation_gray(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    return bool(rgb and max(rgb) - min(rgb) < 18 and not _is_near_black(hex_color))


def _is_gold_or_brown(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return False
    r, g, b = rgb
    return (r >= 95 and g >= 55 and b <= 85 and r >= g >= b) or (r >= 150 and g >= 115 and b <= 75)

def _is_background_like_color(hex_color: str) -> bool:
    """True for beige/gray/washed page backgrounds that should not win brand primary.

    This is intentionally stricter than "neutral": black can be a brand color,
    and gold/brown can be a brand color even when their pixel dominance is low.
    """
    if _is_near_white(hex_color) or _is_low_saturation_gray(hex_color):
        return True
    if _is_near_black(hex_color) or _is_gold_or_brown(hex_color):
        return False
    return _saturation(hex_color) < 0.16


def _raw_palette_has_black_gold_system(raw_palette: list[ColorEntry]) -> bool:
    has_dark = any(
        _is_near_black(e.hex)
        or (_luma(e.hex) is not None and (_luma(e.hex) or 1) < 0.16)
        for e in raw_palette
    )
    has_gold_brown = any(_is_gold_or_brown(e.hex) for e in raw_palette)
    return has_dark and has_gold_brown
def _is_brand_neutral(hex_color: str, supported_roles: set[str]) -> bool:
    """Neutral does not always mean non-brand.

    Black can be a real restaurant/luxury brand color when supported by logo,
    header/nav, or CTA evidence. White/gray body backgrounds should stay neutral.
    """
    if _is_near_white(hex_color) or _is_low_saturation_gray(hex_color):
        return True
    if _is_near_black(hex_color):
        return not bool(supported_roles & {"logo", "logo_svg", "header", "nav", "button"})
    return False


# ---------------------------------------------------------------------
# Raw screenshot palette
# ---------------------------------------------------------------------

def extract_palette_from_screenshot(screenshot_bytes: bytes) -> list[ColorEntry]:
    """Extract a raw pixel-dominance palette from the full-page screenshot."""
    if not screenshot_bytes:
        return []
    try:
        img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
        img.thumbnail((200, 200))
        pixels = list(img.getdata())
    except Exception as e:
        logger.warning("PIL palette load failed: %s", e)
        return []

    try:
        if ColorThief is not None:
            ct = ColorThief(io.BytesIO(screenshot_bytes))
            palette = ct.get_palette(color_count=MAX_PALETTE_COLORS, quality=10)
        else:
            # Lightweight fallback: quantize via Pillow when ColorThief is not
            # installed. Keeps tests and degraded environments functional.
            q = img.quantize(colors=MAX_PALETTE_COLORS).convert("RGB")
            palette = [rgb for _count, rgb in q.getcolors(maxcolors=MAX_PALETTE_COLORS * 40000) or []]
            palette = palette[:MAX_PALETTE_COLORS]
    except Exception as e:
        logger.warning("palette extraction failed: %s", e)
        return []

    if not palette:
        return []

    counts = [0] * len(palette)
    for px in pixels:
        best_i = 0
        best_d = 10**12
        for i, pal in enumerate(palette):
            d = (px[0]-pal[0])**2 + (px[1]-pal[1])**2 + (px[2]-pal[2])**2
            if d < best_d:
                best_d = d
                best_i = i
        counts[best_i] += 1
    total = sum(counts) or 1

    entries = [
        ColorEntry(hex=_rgb_to_hex(palette[i]), dominance=counts[i] / total)
        for i in range(len(palette))
    ]
    entries.sort(key=lambda e: e.dominance, reverse=True)
    return entries


# ---------------------------------------------------------------------
# Browser-side extraction
# ---------------------------------------------------------------------

_COMPUTED_CSS_JS = r"""
() => {
    function cleanFont(f) {
        return f ? f.replace(/\"/g, '').split(',')[0].trim() : null;
    }
    function fontOf(sel) {
        const el = document.querySelector(sel);
        if (!el) return null;
        return cleanFont(window.getComputedStyle(el).fontFamily);
    }
    function colorOf(sel, prop) {
        const el = document.querySelector(sel);
        if (!el) return null;
        return window.getComputedStyle(el)[prop] || null;
    }
    function absUrl(raw) {
        if (!raw) return null;
        try { return new URL(raw, document.baseURI).href; } catch (_) { return raw; }
    }
    function firstSrcset(srcset) {
        if (!srcset) return null;
        const first = srcset.split(',')[0]?.trim()?.split(/\s+/)[0];
        return first || null;
    }
    function bgUrl(v) {
        if (!v || v === 'none') return null;
        const m = String(v).match(/url\(["']?([^"')]+)["']?\)/i);
        return m ? m[1] : null;
    }
    function nodeText(el) {
        if (!el) return '';
        return [el.getAttribute('alt'), el.getAttribute('title'), el.getAttribute('aria-label'), el.id, el.className]
            .map(v => (typeof v === 'string' ? v : '')).join(' ');
    }
    function contextText(el) {
        const a = el?.closest?.('a');
        const box = el?.closest?.('header, nav, footer, [class*=logo], [class*=brand], [id*=logo], [id*=brand], [class*=partner], [class*=sponsor]');
        return [nodeText(el), nodeText(a), nodeText(box), box?.textContent?.slice(0, 180) || ''].join(' ');
    }
    function homeLike(href) {
        if (!href) return false;
        try {
            const u = new URL(href, document.baseURI);
            const here = new URL(document.baseURI);
            return u.origin === here.origin && (u.pathname === '/' || u.pathname === '' || u.href.replace(/\/$/, '') === here.origin);
        } catch (_) { return false; }
    }
    function visibleBox(el) {
        const r = el?.getBoundingClientRect?.();
        if (!r) return { width: null, height: null, x: null, y: null };
        return { width: Math.round(r.width) || null, height: Math.round(r.height) || null, x: Math.round(r.x), y: Math.round(r.y) };
    }
    function pushCandidate(out, el, src, sourceType, extra = {}) {
        if (!src) return;
        const a = el?.closest?.('a');
        const box = visibleBox(el);
        const ctx = contextText(el);
        const lowerCtx = ctx.toLowerCase();
        const classId = [el?.id || '', el?.className || '', el?.parentElement?.className || ''].join(' ');
        out.push({
            src: absUrl(src),
            alt: el?.getAttribute?.('alt') || el?.getAttribute?.('title') || el?.getAttribute?.('aria-label') || extra.text_wordmark || null,
            text_wordmark: extra.text_wordmark || null,
            source_type: sourceType,
            context_text: ctx,
            class_id: classId,
            in_header: !!el?.closest?.('header'),
            near_nav: !!el?.closest?.('header, nav, [role="navigation"]'),
            in_footer: !!el?.closest?.('footer'),
            links_to_home: homeLike(a?.getAttribute?.('href')),
            href: a?.getAttribute?.('href') || null,
            width: box.width,
            height: box.height,
            x: box.x,
            y: box.y,
            is_social: /(facebook|instagram|twitter|linkedin|youtube|tiktok|whatsapp|telegram|pinterest|snapchat)/i.test(lowerCtx + ' ' + String(src)),
            is_hero_gallery: /(hero|banner|slider|slide|carousel|gallery|cover)/i.test(lowerCtx + ' ' + String(src)),
            ...extra
        });
    }

    let btn = document.querySelector('button');
    if (!btn) {
        btn = document.querySelector('a.btn, a.button, a.cta, a[class*="button"], a[class*="btn"], a[href*="book"], a[href*="contact"]');
    }
    let btnBg = null, btnFont = null;
    if (btn) {
        const s = window.getComputedStyle(btn);
        btnBg = s.backgroundColor || null;
        btnFont = cleanFont(s.fontFamily);
    }

    const candidates = [];
    for (const img of Array.from(document.querySelectorAll('img'))) {
        const src = img.getAttribute('src') || img.currentSrc || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original') || firstSrcset(img.getAttribute('srcset'));
        pushCandidate(candidates, img, src, src && String(src).toLowerCase().includes('.svg') ? 'svg_file' : 'img');
        const lazySrc = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original');
        if (lazySrc && lazySrc !== src) pushCandidate(candidates, img, lazySrc, 'lazy_img');
    }
    for (const source of Array.from(document.querySelectorAll('picture source'))) {
        const src = source.getAttribute('src') || firstSrcset(source.getAttribute('srcset'));
        const pic = source.closest('picture');
        pushCandidate(candidates, pic || source, src, 'picture_source');
    }
    let inlineIndex = 0;
    for (const svg of Array.from(document.querySelectorAll('header svg, nav svg, a svg, [class*=logo] svg, [id*=logo] svg'))) {
        inlineIndex += 1;
        const fills = Array.from(svg.querySelectorAll('[fill], [stroke]')).flatMap(n => [n.getAttribute('fill'), n.getAttribute('stroke')]).filter(Boolean);
        pushCandidate(candidates, svg, `inline-svg:${inlineIndex}`, 'inline_svg', { svg_colors: fills });
    }
    for (const el of Array.from(document.querySelectorAll('header, nav, a, div, span')).filter(e => /(logo|brand)/i.test(e.className + ' ' + e.id + ' ' + e.getAttribute('aria-label')))) {
        const bg = bgUrl(window.getComputedStyle(el).backgroundImage);
        if (bg) pushCandidate(candidates, el, bg, 'css_background');
    }

    function cleanWordmarkText(t) {
        return String(t || '').replace(/\s+/g, ' ').trim();
    }
    function looksLikeBrandText(el, text) {
        if (!text || text.length < 3 || text.length > 70) return false;
        const lower = text.toLowerCase();
        if (/^(home|menu|about|contact|booking|order|login|العربية|english)$/i.test(lower)) return false;
        if (!/[a-zA-Z\u0600-\u06FF]/.test(text)) return false;

        const a = el?.closest?.('a');
        const classId = String(el?.id || '') + ' ' + String(el?.className || '') + ' ' + String(el?.parentElement?.className || '');
        const structural = /logo|brand|site-title|navbar-brand|site-name/i.test(classId);
        const home = homeLike(a?.getAttribute?.('href')) || homeLike(el?.getAttribute?.('href'));
        const shortHeaderTitle = !!el?.closest?.('header, nav') && text.length <= 32 && /^[A-Z0-9& .'-]+$/.test(text);
        return structural || home || shortHeaderTitle;
    }
    const textWordmarkEls = Array.from(document.querySelectorAll(
        'header a, header h1, header .brand, header .logo, header [class*=brand], header [class*=logo], nav a, nav .brand, nav .logo, [class*=navbar-brand], [class*=site-title]'
    ));
    for (const el of textWordmarkEls) {
        const text = cleanWordmarkText(el.textContent);
        if (!looksLikeBrandText(el, text)) continue;
        pushCandidate(
            candidates,
            el,
            'text-wordmark:' + encodeURIComponent(text),
            'text_wordmark',
            { text_wordmark: text }
        );
    }

    for (const icon of Array.from(document.querySelectorAll('link[rel~="icon"], link[rel="shortcut icon"], link[rel="apple-touch-icon"]'))) {
        candidates.push({
            src: absUrl(icon.getAttribute('href')),
            alt: icon.getAttribute('rel'),
            source_type: icon.getAttribute('rel')?.includes('apple') ? 'apple_touch_icon' : 'favicon',
            context_text: icon.getAttribute('rel') || '',
            class_id: '',
            in_header: false,
            near_nav: false,
            in_footer: false,
            links_to_home: false,
            width: null,
            height: null,
            x: null,
            y: null,
            is_social: false,
            is_hero_gallery: false,
        });
    }
    const og = document.querySelector('meta[property="og:image"], meta[name="og:image"]')?.getAttribute('content');
    if (og) {
        candidates.push({
            src: absUrl(og),
            alt: 'og:image',
            source_type: 'og_image',
            context_text: 'og:image',
            class_id: '',
            in_header: false,
            near_nav: false,
            in_footer: false,
            links_to_home: false,
            width: null,
            height: null,
            x: null,
            y: null,
            is_social: false,
            is_hero_gallery: true,
        });
    }

    function pushColor(out, el, role, prop, weight) {
        if (!el) return;
        const v = window.getComputedStyle(el)[prop];
        if (!v) return;
        out.push({ color: v, role, prop, weight });
    }
    const colorSignals = [];
    pushColor(colorSignals, document.body, 'body_bg', 'backgroundColor', 1);
    pushColor(colorSignals, document.body, 'body_text', 'color', 1);
    pushColor(colorSignals, document.querySelector('header'), 'header', 'backgroundColor', 6);
    pushColor(colorSignals, document.querySelector('header'), 'header_text', 'color', 2);
    pushColor(colorSignals, document.querySelector('nav'), 'nav', 'backgroundColor', 5);
    pushColor(colorSignals, btn, 'button', 'backgroundColor', 8);
    pushColor(colorSignals, btn, 'button_text', 'color', 2);
    for (const a of Array.from(document.querySelectorAll('header a, nav a')).slice(0, 12)) {
        pushColor(colorSignals, a, 'nav_text', 'color', 1.5);
        pushColor(colorSignals, a, 'nav_bg', 'backgroundColor', 2);
    }
    // FOOTER: with the header + logo, one of the three places brand colors actually
    // live (owner's rule) — it was never captured, so a photo-heavy page could
    // out-vote the real brand chrome.
    const footer = document.querySelector('footer') || document.querySelector('[class*="footer" i]');
    pushColor(colorSignals, footer, 'footer', 'backgroundColor', 5);
    pushColor(colorSignals, footer, 'footer_text', 'color', 1.5);
    if (footer) {
        for (const a of Array.from(footer.querySelectorAll('a')).slice(0, 10)) {
            pushColor(colorSignals, a, 'footer_link', 'color', 1.5);
        }
    }
    for (const svg of Array.from(document.querySelectorAll('header svg, nav svg, [class*=logo] svg, [id*=logo] svg')).slice(0, 8)) {
        for (const n of Array.from(svg.querySelectorAll('[fill], [stroke]')).slice(0, 30)) {
            const fill = n.getAttribute('fill');
            const stroke = n.getAttribute('stroke');
            if (fill && fill !== 'none' && !fill.startsWith('url(')) colorSignals.push({ color: fill, role: 'logo_svg', prop: 'fill', weight: 10 });
            if (stroke && stroke !== 'none' && !stroke.startsWith('url(')) colorSignals.push({ color: stroke, role: 'logo_svg', prop: 'stroke', weight: 10 });
        }
    }

    return {
        body_bg: colorOf('body', 'backgroundColor'),
        header_bg: colorOf('header', 'backgroundColor'),
        body_font: fontOf('body'),
        heading_font: fontOf('h1') || fontOf('h2') || fontOf('h3'),
        button_bg: btnBg,
        button_font: btnFont,
        logo_candidates: candidates,
        color_signals: colorSignals,
        site_name: document.querySelector('meta[property="og:site_name"]')?.getAttribute('content') || document.title || null
    };
}
"""


def extract_from_page(page: Page, page_url: str) -> dict:
    """Run JS to read computed styles + visual candidates from the page."""
    try:
        data = page.evaluate(_COMPUTED_CSS_JS) or {}
        data["page_url"] = page_url
        return data
    except PWError as e:
        logger.warning("computed-CSS eval failed: %s", e)
        return {"page_url": page_url}


# ---------------------------------------------------------------------
# Logo scoring + classification
# ---------------------------------------------------------------------

def _domain_terms(page_url: str) -> list[str]:
    host = urlparse(page_url).netloc.lower().replace("www.", "")
    root = host.split(":")[0].split(".")[0]
    terms = [root]
    terms.extend([p for p in re.split(r"[-_]+", root) if len(p) >= 3])
    return list(dict.fromkeys(terms))


def _brand_terms(computed: dict, page_url: str) -> list[str]:
    raw = " ".join([computed.get("site_name") or "", page_url])
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9\u0600-\u06FF]{3,}", raw)]
    terms = _domain_terms(page_url) + words[:12]
    stop = {"https", "http", "www", "com", "org", "net", "title", "home", "page"}
    return list(dict.fromkeys([t for t in terms if t not in stop]))[:20]


def _text_blob(c: dict) -> str:
    return " ".join(str(c.get(k) or "") for k in ("src", "alt", "context_text", "class_id", "href")).lower()


def _classify_blob(c: dict) -> str:
    """Keyword blob for CLASSIFICATION — like _text_blob but with the host/domain
    stripped from src/href.

    The domain is constant across all of a site's assets, so including it lets a
    single token in the host (e.g. "gov" in a .gov.eg domain) mislabel EVERY logo
    — including third-party partner logos (Microsoft, Google, AWS, ...) — as a
    government_logo. The path, filename, alt, surrounding text and class id still
    carry the real authority/partner/sponsor signal. (Measured: on digilians.gov.eg
    this drops government_logo 18 -> 1; zero change on 27 non-.gov sites.)
    """
    def _no_host(u: object) -> str:
        return re.sub(r"^[a-z]+://[^/]+", "", str(u or ""), flags=re.IGNORECASE)

    return " ".join([
        _no_host(c.get("src")),
        str(c.get("alt") or ""),
        str(c.get("context_text") or ""),
        str(c.get("class_id") or ""),
        _no_host(c.get("href")),
    ]).lower()


def _candidate_aspect(width: Optional[int], height: Optional[int]) -> Optional[float]:
    if not width or not height or height <= 0:
        return None
    return round(width / height, 3)


def _suitable_logo_shape(width: Optional[int], height: Optional[int], source_type: str) -> bool:
    if source_type in {"inline_svg", "favicon", "apple_touch_icon", "text_wordmark"}:
        return True
    if not width or not height:
        return False
    if width < 18 or height < 18:
        return False
    if width > 900 or height > 450:
        return False
    ratio = width / max(height, 1)
    return 0.35 <= ratio <= 6.5


def _classify_candidate(c: dict, score: float) -> str:
    blob = _classify_blob(c)  # host-stripped: the domain must not drive classification
    source_type = str(c.get("source_type") or "")
    if source_type in {"favicon", "apple_touch_icon"}:
        return "favicon"
    if source_type == "og_image":
        return "og_image"
    if any(k in blob for k in AUTHORITY_KEYWORDS):
        return "government_logo"
    if "sponsor" in blob or "sponsored" in blob:
        return "sponsor_logo"
    if any(k in blob for k in PARTNER_KEYWORDS):
        return "partner_logo"
    if any(k in blob for k in INITIATIVE_KEYWORDS) and not any(k in blob for k in LOGO_KEYWORDS):
        return "initiative_logo"
    if c.get("in_footer") and not c.get("in_header") and score < PRIMARY_LOGO_THRESHOLD + 10:
        return "footer_logo"
    if score >= PRIMARY_LOGO_THRESHOLD:
        return "primary_brand_logo"
    return "unknown_candidate"


def _score_logo_candidates(raw_candidates: list[dict], computed: dict, page_url: str) -> list[LogoCandidate]:
    terms = _brand_terms(computed, page_url)
    seen_counts = Counter(str(c.get("src") or "") for c in raw_candidates if c.get("src"))
    scored: list[LogoCandidate] = []
    seen_key: set[tuple[str, str]] = set()

    for raw in raw_candidates:
        src = raw.get("src")
        if not src:
            continue
        source_type = str(raw.get("source_type") or "unknown")
        key = (src, source_type)
        if key in seen_key:
            continue
        seen_key.add(key)

        blob = _text_blob(raw)
        width = raw.get("width") if isinstance(raw.get("width"), int) else None
        height = raw.get("height") if isinstance(raw.get("height"), int) else None
        aspect = _candidate_aspect(width, height)
        reasons: list[str] = []
        score = 0.0

        if raw.get("in_header"):
            score += 30; reasons.append("appears_in_header")
        if raw.get("near_nav"):
            score += 18; reasons.append("near_nav")
        if raw.get("links_to_home"):
            score += 22; reasons.append("links_to_homepage")
        # Keyword/brand matching must use the HOST-STRIPPED blob (same measured fix
        # as classification): on nahdionline.com the CDN host `dam.nahdionline.com`
        # gave EVERY one of 432 product banners a brand_name_match (+18), and the
        # "Shop_by_brands" carousel path matched the 'brand' keyword (+22) — 432
        # promo tiles scored 60 = primary_brand_logo while the real logo lost.
        kw_blob = _classify_blob(raw)
        if any(re.search(rf"{re.escape(k)}(?!s)", kw_blob) for k in LOGO_KEYWORDS):
            score += 22; reasons.append("logo_keyword")
        if any(t and t in kw_blob for t in terms):
            score += 18; reasons.append("brand_name_match")
        # A multi-brand SHOP section ("shop by brands" / "our brands") is a brand
        # LISTING, not the site's own mark — hard-demote like hero/partner assets.
        if re.search(r"(shop[\s_-]*by[\s_-]*brand|our[\s_-]*brands|top[\s_-]*brands)", kw_blob):
            score -= 60; reasons.append("penalty_brand_listing_section")
        if source_type in {"img", "svg_file", "lazy_img", "picture_source", "inline_svg", "css_background", "text_wordmark"}:
            score += 8; reasons.append(source_type)
        if source_type == "text_wordmark":
            score += 16; reasons.append("text_wordmark_candidate")
        if _suitable_logo_shape(width, height, source_type):
            score += 12; reasons.append("suitable_logo_shape")
        if seen_counts.get(src, 0) > 1:
            score += min(12, 4 * seen_counts[src]); reasons.append("repeated_candidate")

        # Penalties: preserve candidates but avoid automatic primary selection.
        # A brand logo's anchor links HOME, never to a contact endpoint — an svg inside
        # a mailto:/tel:/SMS/WhatsApp link is a CONTACT icon, not the logo. MEASURED on
        # Assih (a Squarespace site): its header email icon scored 0.86 as
        # "primary_brand_logo" and OUTSCORED the real raster logo (0.78), because the
        # is_social regex only matches social-network names, not mailto:. Treat contact
        # links as social so they get the same -80 (the is_social regex still covers the
        # network icons; this closes the contact-link gap).
        href = str(raw.get("href") or "").lower()
        is_contact_icon = (
            href.startswith(("mailto:", "tel:", "sms:"))
            or "api.whatsapp.com" in href or "wa.me/" in href
        )
        if raw.get("is_social") or is_contact_icon or any(k in blob for k in SOCIAL_KEYWORDS):
            score -= 80; reasons.append("penalty_social_icon")
        if raw.get("is_hero_gallery") or any(k in blob for k in HERO_KEYWORDS):
            score -= 45; reasons.append("penalty_hero_or_gallery")
        if source_type in {"favicon", "apple_touch_icon"}:
            score -= 30; reasons.append("fallback_icon_only")
        if source_type == "og_image":
            score -= 40; reasons.append("og_image_not_primary_logo")
        if raw.get("in_footer") and not raw.get("in_header"):
            score -= 8; reasons.append("footer_only")

        classification = _classify_candidate(raw, score)
        if classification in {"government_logo", "partner_logo", "sponsor_logo", "initiative_logo"}:
            # These may still be valuable evidence but should not auto-win.
            score -= 12
            reasons.append(f"classified_{classification}")

        confidence = max(0.0, min(1.0, score / 100.0))
        scored.append(LogoCandidate(
            src=src,
            alt=raw.get("alt"),
            page_url=page_url,
            source_type=source_type,
            classification=classification,
            confidence=round(confidence, 3),
            score=round(score, 2),
            reasons=reasons,
            width=width,
            height=height,
            aspect_ratio=aspect,
            in_header=bool(raw.get("in_header")),
            near_nav=bool(raw.get("near_nav")),
            links_to_home=bool(raw.get("links_to_home")),
            repeated_count=max(1, seen_counts.get(src, 1)),
            is_fallback=source_type in {"favicon", "apple_touch_icon", "og_image"},
            text_wordmark=raw.get("text_wordmark"),
        ))

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


_RASTER_LOGO_TYPES = {
    "img", "svg_file", "lazy_img", "picture_source", "inline_svg", "css_background",
}


def _brand_in_filename(c: LogoCandidate, brand_terms: Optional[list[str]]) -> bool:
    """True if a brand term (len > 3) appears in the candidate's FILENAME — a
    strong signal it's the brand's OWN asset, not a co-branded partner/ministry
    logo. The filename (not the full URL) is used so the shared domain doesn't
    match everything."""
    base = (c.src or "").rsplit("/", 1)[-1].lower()
    return any(t in base for t in (brand_terms or []) if t and len(t) > 3)


# --- Structural-independent logo floor (measured: elkbabgi / Strikingly false-negative) ---
# A SaaS site-builder (Strikingly/Wix/...) can render the header in an opaque DOM that denies
# EVERY structural signal (in_header/near_nav/links_to_home), leaving a REAL raster logo one
# point short at 54 (logo_keyword 22 + raster 8 + suitable_logo_shape 12 + repeated 12). The
# missing point is not doubt — it's the extractor's blindness to a non-standard DOM. The floor
# recovers exactly that profile WITHOUT touching any threshold or weight, so it cannot regress
# a single existing passer or shift any score. Two guards keep it honest against the profile's
# twin — a hosted third-party logo-WALL (press/client/payment tiles named `*-logo.png`):
#   1. UNIQUENESS: promote only when the whole page has ONE floor-eligible image. A wall has
#      many distinct tiles -> we cannot tell the brand's own from a third party's without
#      structure -> return UNKNOWN honestly rather than guess a `techcrunch-logo.png`.
#   2. It runs LAST — after the footer-rescue — so a genuine footer logo is never suppressed.
_FLOOR_MIN_REPEAT = 4          # site-wide chrome, not a one-off tile
_FLOOR_VOID_REASONS = frozenset({  # any of these on the candidate voids the floor
    "penalty_social_icon", "penalty_hero_or_gallery", "og_image_not_primary_logo",
    "fallback_icon_only", "penalty_brand_listing_section",
    "classified_government_logo", "classified_partner_logo",
    "classified_sponsor_logo", "classified_initiative_logo",
})


def _floor_ok(c: LogoCandidate) -> bool:
    """The content-only logo signature: logo-named, logo-shaped, HOSTED raster, repeated
    site-wide, with no disqualifying penalty. A conjunction (all-or-nothing) — it adds no
    points, so a partial or penalized candidate can never satisfy it."""
    r = set(c.reasons or [])
    return (
        "logo_keyword" in r
        and "suitable_logo_shape" in r
        and c.source_type in _RASTER_LOGO_TYPES            # a real raster image, not a text wordmark
        and not str(c.src or "").startswith("data:")       # a hosted asset, not an inline placeholder blob
        and (c.repeated_count or 1) >= _FLOOR_MIN_REPEAT
        and not (r & _FLOOR_VOID_REASONS)
    )


def _choose_primary_logo(
    candidates: list[LogoCandidate],
    brand_terms: Optional[list[str]] = None,
) -> Optional[LogoCandidate]:
    # Eligible primaries: a classified brand logo, or a strong structurally-placed
    # unknown candidate (same bar as before).
    pool = [
        c for c in candidates
        if c.classification == "primary_brand_logo" and c.score >= PRIMARY_LOGO_THRESHOLD
    ]
    pool += [
        c for c in candidates
        if c.classification == "unknown_candidate"
        and c.score >= PRIMARY_LOGO_THRESHOLD + 5
        and not c.is_fallback
        and (c.in_header or c.links_to_home or c.near_nav)
    ]
    if not pool:
        # FOOTER-logo rescue (measured on nahdionline.com): some sites carry the brand
        # mark ONLY in the footer (the header logo is an uncaptured inline asset), so
        # after the promo-tile false-positives were fixed the primary came back None
        # while the REAL `nahdi-logo-footer.png` sat classified footer_logo at the top
        # of the list. Promote a footer logo ONLY on the strong signature — the file
        # is a named logo AND carries the brand — never a generic footer badge (VAT /
        # payment icons lack the pair + the score floor).
        footer = [
            c for c in candidates
            if c.classification == "footer_logo"
            and c.score >= PRIMARY_LOGO_THRESHOLD - 10
            and {"logo_keyword", "brand_name_match"} <= set(c.reasons or [])
        ]
        if footer:
            pool = footer
        else:
            # Last resort: the structural-independent floor. Promote a content-only logo
            # ONLY when it is the UNIQUE such image on the page (all floor-eligible entries
            # share one src — the site's own site-wide mark). Two or more distinct
            # floor-eligible images = an ambiguous third-party logo-wall we refuse to guess.
            floored = [c for c in candidates if _floor_ok(c)]
            if floored and len({c.src for c in floored}) == 1:
                pool = [max(floored, key=lambda c: c.score)]
            else:
                # NAMED-LOGO rescue (SPA headers — MEASURED on ITI): an SVG/img whose FILENAME
                # literally names the brand's OWN mark ("logo"/"brand"/"identity") is the logo even
                # when the header-position heuristic missed it — a custom Angular/React header
                # (<app-header>, class="header__image") drops the +30 in-header bonus, so the real
                # logo lands as a sub-threshold unknown_candidate (ITI: ColoredLogo.svg scored 42).
                # Exclude partner/authority/sponsor/favicon marks; when several are just COLOURWAYS
                # of the same mark (colored/white/dark…), take the coloured/non-inverse one.
                _NON_OWN = {"partner_logo", "government_logo", "sponsor_logo",
                            "initiative_logo", "favicon", "apple_touch_icon", "og_image"}
                # Restricted to SVG marks with a PURE-logo filename: a vector logo is almost always
                # the brand's OWN mark (partners/press/badges are raster PNGs), and the strict
                # filename gate (`ColoredLogo`/`logo`/`brand-logo`, NOT `ministry-logo`/
                # `techcrunch-logo`) keeps this from reopening the floor-rescue's deliberate refusals.
                named = [
                    c for c in candidates
                    if c.classification not in _NON_OWN
                    and c.source_type in {"svg_file", "inline_svg"}
                    and _is_pure_logo_filename(c.src)
                    and c.score >= 8
                ]
                if named:
                    def _own_rank(c: LogoCandidate) -> tuple:
                        f = (c.src or "").lower()
                        inverse = any(w in f for w in ("white", "mono", "dark", "black",
                                                       "inverse", "invert", "footer"))
                        return (0 if inverse else 1, c.score)
                    pool = [max(named, key=_own_rank)]
                else:
                    return None

    # Prefer a REAL raster logo image over a text wordmark (a high-scoring wordmark
    # is frequently a nav label like "Home" / "الرئيسية" / "About us").
    rasters = [c for c in pool if c.source_type in _RASTER_LOGO_TYPES]
    cands = rasters or pool

    # Among those, prefer the candidate whose FILENAME carries the brand name (the
    # brand's own asset), THEN by score. This beats a co-branded partner/ministry
    # logo that merely outscores it via a generic "logo" in its filename.
    chosen = max(cands, key=lambda c: (_brand_in_filename(c, brand_terms), c.score))
    if chosen.classification != "primary_brand_logo":
        chosen = chosen.model_copy(update={"classification": "primary_brand_logo"})
    return chosen


# ---------------------------------------------------------------------
# Brand palette scoring
# ---------------------------------------------------------------------

def _extract_svg_colors(raw_candidates: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in raw_candidates:
        for raw_color in c.get("svg_colors") or []:
            out.append({"color": raw_color, "role": "logo_svg", "weight": 10})
    return out


def _logo_svg_signals(logo_src: Optional[str], page_url: str) -> list[dict[str, Any]]:
    """Brand colors parsed from the SELECTED primary logo when it is an EXTERNAL .svg FILE.

    `_logo_pixel_signals` samples raster pixels but SKIPS svg, and only INLINE-svg fills were ever
    read — so an EXTERNAL svg logo (ITI's ColoredLogo.svg, brand red #9a3333) contributed NOTHING
    and the page background (navy #203947) won the palette. This fetches the svg (SSRF-guarded,
    bounded), parses fill/stroke/stop-color, drops white/black/neutral-gray (plates + the dark
    wordmark text), QUANTIZES near-duplicate shades so a logo's many reds cluster into ONE, and
    emits weighted 'logo_svg' signals so the brand's TRUE colour drives the palette. Never raises."""
    if not logo_src:
        return []
    src = str(logo_src)
    if not (src.lower().split("?")[0].endswith(".svg") and src.startswith(("http://", "https://"))):
        return []
    try:
        from urllib.parse import urljoin
        from urllib.request import Request

        from scraper.url_utils import is_safe_public_url, open_public_url
        url = urljoin(page_url or "", src)
        if not is_safe_public_url(url):
            return []
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with open_public_url(req, timeout=8) as resp:
            text = resp.read(1_500_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return []
    raw = re.findall(r'(?:fill|stroke|stop-color)\s*[:=]\s*["\']?(#[0-9a-fA-F]{3,6}|rgb\([^)]+\))', text)
    counts: Counter = Counter()
    for c in raw:
        hx = _normalize_css_color(c)
        if not hx or _is_near_white(hx) or _is_near_black(hx) or _is_low_saturation_gray(hx):
            continue                                          # drop plates + the dark text/neutrals
        rgb = _hex_to_rgb(hx)
        if not rgb:
            continue
        r, g, b = rgb
        q = f"#{min(r // 24 * 24 + 12, 255):02x}{min(g // 24 * 24 + 12, 255):02x}{min(b // 24 * 24 + 12, 255):02x}"
        counts[q] += 1
    if not counts:
        return []
    total = sum(counts.values())
    out: list[dict[str, Any]] = []
    for hx, n in counts.most_common(6):
        out.append({"color": hx, "role": "logo_svg", "weight": round(6 + 8 * min(n / total, 0.75), 2)})
        if len(out) >= 3:
            break
    return out

def _logo_pixel_signals(logo_src: Optional[str], page_url: str) -> list[dict[str, Any]]:
    """Dominant PIXEL colors of the selected primary logo (raster PNG/JPG/WebP).

    The logo is the single strongest brand-color source (owner's rule: brand colors
    live in the logo + header + footer, not the page's photos) — but only inline-SVG
    fills were ever read, so a raster logo (Orange's PNG -> #FF7900) contributed
    NOTHING and photo colors could win the palette. Downloads ONE small image
    (SSRF-guarded, bounded, data: URIs decoded directly), samples opaque pixels,
    drops near-white (background plates / white wordmark ink) and rare colors, and
    emits up to 3 'logo_raster' signals weighted by pixel share. Never raises."""
    if not logo_src:
        return []
    src = str(logo_src)
    if src.startswith(("inline-svg:", "text-wordmark:")) or src.lower().endswith(".svg"):
        return []
    try:
        data: Optional[bytes] = None
        if src.startswith("data:"):
            import base64 as _b64
            head, _, b64 = src.partition(",")
            if "base64" in head:
                data = _b64.b64decode(b64)
        else:
            from urllib.parse import urljoin
            from urllib.request import Request
            url = urljoin(page_url or "", src)
            from scraper.url_utils import is_safe_public_url, open_public_url
            if not is_safe_public_url(url):
                return []
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with open_public_url(req, timeout=8) as resp:
                data = resp.read(2_000_000)
        if not data:
            return []

        import io
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img.thumbnail((96, 96))
        counts: defaultdict[tuple[int, int, int], int] = defaultdict(int)
        opaque = 0
        for r, g, b, a in img.getdata():
            if a < 128:
                continue
            opaque += 1
            if min(r, g, b) >= 232:          # near-white ink/plate — not a brand color
                continue
            counts[(r // 24 * 24 + 12, g // 24 * 24 + 12, b // 24 * 24 + 12)] += 1
        if not opaque or not counts:
            return []
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        out: list[dict[str, Any]] = []
        for (r, g, b), n in top[:6]:
            share = n / opaque
            if share < 0.04:                 # anti-aliasing fringes / noise
                continue
            out.append({"color": f"#{r:02x}{g:02x}{b:02x}", "role": "logo_raster",
                        "weight": round(4 + 8 * min(share, 0.75), 2)})
            if len(out) >= 3:
                break
        if not out:
            # White-ink logo with a small colored mark (measured on daturial: white
            # wordmark + tiny orange dots -> everything filtered). The most-common
            # SATURATED bucket IS the brand accent even at a small pixel share.
            def _sat(rgb):
                mx, mn = max(rgb), min(rgb)
                return 0.0 if mx == 0 else (mx - mn) / mx
            for (r, g, b), n in top:
                if n / opaque >= 0.01 and _sat((r, g, b)) >= 0.35:
                    out.append({"color": f"#{r:02x}{g:02x}{b:02x}",
                                "role": "logo_raster", "weight": 6.0})
                    break
        return out
    except Exception:  # noqa: BLE001 — palette enrichment must never break the scrape
        return []


def _build_brand_palette(raw_palette: list[ColorEntry], computed: dict) -> tuple[list[ColorEntry], dict[str, Any]]:
    color_signals = list(computed.get("color_signals") or [])
    color_signals.extend(_extract_svg_colors(computed.get("logo_candidates") or []))

    scores: defaultdict[str, float] = defaultdict(float)
    roles_by_color: defaultdict[str, set[str]] = defaultdict(set)
    background_colors: list[str] = []
    text_colors: list[str] = []

    signal_hexes = [
        _normalize_css_color(str(sig.get("color") or ""))
        for sig in color_signals
    ]
    supported_signal_hexes = [
        hx for hx in signal_hexes
        if hx and (
            _is_gold_or_brown(hx)
            or _is_near_black(hx)
            or (_luma(hx) is not None and (_luma(hx) or 1) < 0.16)
        )
    ]
    black_gold_system = _raw_palette_has_black_gold_system(raw_palette) or (
        any(hx and (_is_near_black(hx) or ((_luma(hx) or 1) < 0.16)) for hx in supported_signal_hexes)
        and any(hx and _is_gold_or_brown(hx) for hx in supported_signal_hexes)
    )

    # Raw screenshot palette is evidence, but not enough by itself for primary.
    # Background-like beige/gray colors are recorded as backgrounds and penalized.
    for entry in raw_palette:
        hx = _normalize_css_color(entry.hex)
        if not hx:
            continue

        dom = float(entry.dominance or 0.0)
        is_background_like = _is_background_like_color(hx)

        if dom >= 0.25 and is_background_like:
            background_colors.append(hx)
            scores[hx] -= 10 + (dom * 10)
        else:
            scores[hx] += min(dom, 0.20) * 8

        # Low-pixel-share brand accents should still be considered.
        if _is_gold_or_brown(hx):
            roles_by_color[hx].add("raw_brand_accent")
            scores[hx] += 18 + (dom * 45)
        elif _is_near_black(hx) or (_luma(hx) is not None and (_luma(hx) or 1) < 0.16):
            if black_gold_system:
                roles_by_color[hx].add("raw_brand_dark")
                scores[hx] += 12 + (dom * 30)
            else:
                scores[hx] += min(dom, 0.12) * 6
        elif not is_background_like and _saturation(hx) >= 0.22:
            roles_by_color[hx].add("raw_accent")
            scores[hx] += 8 + (dom * 20)

    for signal in color_signals:
        hx = _normalize_css_color(signal.get("color"))
        if not hx:
            continue

        role = str(signal.get("role") or "unknown")
        weight = float(signal.get("weight") or 1.0)
        roles_by_color[hx].add(role)

        if role in {"body_bg"}:
            background_colors.append(hx)
            scores[hx] -= 12
            continue

        if role in {"body_text", "nav_text", "header_text", "button_text",
                    "footer_text", "footer_link"}:
            text_colors.append(hx)
            # Text color alone is weak evidence. It can support a color already
            # seen as brand-like, but should not become primary by itself.
            scores[hx] += min(weight, 1.5)
            continue

        if role in {"header", "nav", "nav_bg", "footer"}:
            if _is_background_like_color(hx) and not (_is_near_black(hx) or _is_gold_or_brown(hx)):
                background_colors.append(hx)
                scores[hx] += weight * 1.2
            else:
                scores[hx] += weight * 5

        elif role == "button":
            if _is_near_white(hx) or _is_low_saturation_gray(hx):
                scores[hx] -= 6
                background_colors.append(hx)
            else:
                scores[hx] += weight * 5.5

        elif role in {"logo", "logo_svg", "logo_raster"}:
            scores[hx] += weight * 5

        else:
            scores[hx] += weight

        sat = _saturation(hx)

        if sat >= 0.35:
            scores[hx] += 6

        if _is_gold_or_brown(hx) and roles_by_color[hx] & {
            "logo",
            "logo_svg",
            "logo_raster",
            "header",
            "nav",
            "button",
            "nav_bg",
            "footer",
            "raw_brand_accent",
        }:
            scores[hx] += 14

        if _is_near_black(hx) and roles_by_color[hx] & {
            "logo",
            "logo_svg",
            "logo_raster",
            "header",
            "nav",
            "button",
            "nav_bg",
            "footer",
            "raw_brand_dark",
        }:
            scores[hx] += 12

        if _is_near_white(hx) and not roles_by_color[hx] & {"logo", "logo_svg", "logo_raster"}:
            scores[hx] -= 14

        if _is_low_saturation_gray(hx) and not roles_by_color[hx] & {"logo", "logo_svg", "logo_raster"}:
            scores[hx] -= 10

        if _is_background_like_color(hx) and not roles_by_color[hx] & {
            "logo",
            "logo_svg",
            "logo_raster",
            "raw_brand_accent",
            "raw_brand_dark",
        }:
            scores[hx] -= 5

    ranked = [(hx, sc) for hx, sc in scores.items() if sc > 0]
    ranked.sort(key=lambda x: x[1], reverse=True)

    brand_hex: list[str] = []

    for hx, _ in ranked:
        roles = roles_by_color[hx]

        is_supported_brand = bool(
            roles
            & {
                "logo",
                "logo_svg",
                "logo_raster",
                "header",
                "nav",
                "button",
                "nav_bg",
                "footer",
                "raw_brand_accent",
                "raw_brand_dark",
                "raw_accent",
            }
        )

        if hx in background_colors and not is_supported_brand:
            continue

        if _is_background_like_color(hx) and not is_supported_brand:
            continue

        if _is_brand_neutral(hx, roles):
            continue

        if hx not in brand_hex:
            brand_hex.append(hx)

        if len(brand_hex) >= MAX_PALETTE_COLORS:
            break

    # If a page clearly has a black/gold/brown system, ensure those colors are
    # retained even when background pixels dominate the screenshot.
    if black_gold_system:
        for entry in raw_palette:
            hx = _normalize_css_color(entry.hex)
            if not hx:
                continue

            if (_is_gold_or_brown(hx) or _is_near_black(hx)) and hx not in brand_hex:
                brand_hex.append(hx)

            if len(brand_hex) >= MAX_PALETTE_COLORS:
                break

    max_score = ranked[0][1] if ranked else 0.0
    total = sum(max(scores[hx], 0.0) for hx in brand_hex) or 1.0

    brand_palette = [
        ColorEntry(
            hex=hx,
            dominance=round(max(scores[hx], 0.0) / total, 4),
        )
        for hx in brand_hex
    ]

    accent_colors = [
        hx
        for hx in brand_hex
        if _saturation(hx) >= 0.30 or _is_gold_or_brown(hx)
    ]

    neutral_colors = [
        hx
        for hx in {*(e.hex for e in raw_palette), *scores.keys()}
        if _is_near_black(hx) or _is_near_white(hx) or _is_low_saturation_gray(hx)
    ]

    background_dominance = sum(
        float(e.dominance or 0.0)
        for e in raw_palette
        if (_normalize_css_color(e.hex) or e.hex) in background_colors
        or _is_background_like_color(e.hex)
    )

    info = {
        "primary_brand_color": brand_hex[0] if brand_hex else None,
        "accent_colors": accent_colors[:4],
        "neutral_colors": list(dict.fromkeys(neutral_colors))[:6],
        "background_colors": list(dict.fromkeys(background_colors))[:6],
        "text_colors": list(dict.fromkeys(text_colors))[:6],
        "max_score": max_score,
        "has_color_signals": bool(color_signals),
        "background_dominance": background_dominance,
    }

    return brand_palette, info
# ---------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------

def build_visual_identity(
    screenshot_bytes: bytes,
    computed: dict,
    page_url: str,
) -> VisualIdentity:
    computed = computed or {}
    raw_palette = extract_palette_from_screenshot(screenshot_bytes)
    logo_candidates = _score_logo_candidates(computed.get("logo_candidates") or [], computed, page_url)
    primary_logo = _choose_primary_logo(logo_candidates, _brand_terms(computed, page_url))

    partner_logos = [c for c in logo_candidates if c.classification in {"partner_logo", "sponsor_logo", "initiative_logo"}]
    authority_logos = [c for c in logo_candidates if c.classification == "government_logo"]
    co_branding_detected = bool((primary_logo and (partner_logos or authority_logos)) or len([
        c for c in logo_candidates if c.score >= 45 and c.classification in {"government_logo", "partner_logo", "sponsor_logo", "initiative_logo"}
    ]) >= 2)

    # The SELECTED primary logo's colors are the strongest brand signal — feed them into the palette
    # scoring. Raster logos contributed nothing before (_logo_pixel_signals); INLINE-svg fills were
    # captured via 'logo_svg'; an EXTERNAL .svg logo (ITI's ColoredLogo.svg -> brand RED) was missed
    # entirely, so the page background won — _logo_svg_signals now parses the svg file's colors.
    if primary_logo is not None:
        logo_signals = (_logo_pixel_signals(primary_logo.src, page_url)
                        + _logo_svg_signals(primary_logo.src, page_url))
        if logo_signals:
            computed = dict(computed)
            computed["color_signals"] = list(computed.get("color_signals") or []) + logo_signals

    brand_palette, palette_info = _build_brand_palette(raw_palette, computed)
    visual_warnings: list[str] = []
    visual_note: Optional[str] = None
    palette_note: Optional[str] = None

    if primary_logo is None:
        visual_note = "no_confident_primary_logo"
        visual_warnings.append("no_confident_primary_logo")
    if co_branding_detected:
        visual_warnings.append("co_branding_detected")
    if not brand_palette or palette_info["max_score"] < 25:
        palette_note = "weak_brand_palette_confidence"
        visual_warnings.append("weak_brand_palette_confidence")
    if raw_palette:
        top = raw_palette[0]
        top_hx = _normalize_css_color(top.hex) or top.hex

        dominated_by_bg = (
            top.dominance >= 0.45
            and (
                top_hx in palette_info["background_colors"]
                or _is_background_like_color(top_hx)
            )
        ) or palette_info.get("background_dominance", 0.0) >= 0.65
        if dominated_by_bg:
            visual_warnings.append("palette_dominated_by_background")
            if palette_note is None:
                palette_note = "palette_dominated_by_background"
    if not palette_info["has_color_signals"] and raw_palette and not brand_palette:
        palette_note = palette_note or "raw_palette_only"

    # Backward-compatible single-logo field.
    legacy_logo: Optional[LogoRecord] = None
    if primary_logo is not None and primary_logo.source_type != "text_wordmark":
        legacy_logo = LogoRecord(
            block_id=primary_logo.block_id,
            src=primary_logo.src,
            alt=primary_logo.alt,
            page_url=primary_logo.page_url,
            local_path=primary_logo.local_path,
        )

    # Backward-compatible color_palette now prefers the scored brand palette;
    # falls back to raw only when no brand palette exists.
    legacy_palette = brand_palette or raw_palette

    return VisualIdentity(
        color_palette=legacy_palette,
        body_bg=_normalize_css_color(computed.get("body_bg")),
        header_bg=_normalize_css_color(computed.get("header_bg")),
        primary_button_color=_normalize_css_color(computed.get("button_bg")),
        fonts={
            "body": computed.get("body_font"),
            "headings": computed.get("heading_font"),
            "buttons": computed.get("button_font"),
        },
        logo=legacy_logo,
        primary_logo=primary_logo,
        logo_candidates=logo_candidates,
        partner_logos=partner_logos,
        authority_logos=authority_logos,
        co_branding_detected=co_branding_detected,
        visual_extraction_note=visual_note,
        raw_palette=raw_palette,
        brand_palette=brand_palette,
        primary_brand_color=palette_info["primary_brand_color"],
        accent_colors=palette_info["accent_colors"],
        neutral_colors=palette_info["neutral_colors"],
        background_colors=palette_info["background_colors"],
        text_colors=palette_info["text_colors"],
        palette_extraction_note=palette_note,
        visual_warnings=list(dict.fromkeys(visual_warnings)),
    )
