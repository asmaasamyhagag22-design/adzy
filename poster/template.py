"""Build the poster HTML/CSS from a PosterBrief + a background image.

Design rules enforced here (per the redesign constraints):
- EVIDENCE-ONLY at the template layer: a missing/empty field is OMITTED, never
  fabricated. (Note: build_poster_brief still injects some fallback copy upstream;
  stripping that is the next, separate step.)
- Headline is rendered exactly ONCE.
- Typographic register is VARIED — natural-case brand + headline (serif), an
  uppercase letter-spaced kicker as a single accent, sans-serif body/chips. No
  global uppercasing.
- The background is a real visual; we apply a legibility scrim ONLY behind the
  lower text block, not a full-image darken.
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import re
from pathlib import Path
from typing import Optional

# Arabic + Hebrew + Arabic presentation forms — used to right-align RTL copy.
_RTL_RE = re.compile(
    r"[֐-׿؀-ۿ܀-ݏݐ-ݿࢠ-ࣿיִ-﷿ﹰ-﻿]"
)


def _is_rtl(text) -> bool:
    return bool(_RTL_RE.search(str(text or "")))

from poster.schemas import PosterBrief, PosterDesignSpec


def _data_uri(image_path) -> str:
    raw = Path(image_path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _open_image_url(url: str, timeout: int):
    """Open an image URL with a verified SSL context (certifi when available),
    falling back to an UNVERIFIED context on a cert-chain failure.

    Why the fallback is acceptable HERE: the target is already SSRF-guarded to a
    public host, this is a passive IMAGE fetch with no credentials sent, and many
    real sites (e.g. nti.sci.eg) ship an INCOMPLETE cert chain — browsers repair it
    via AIA fetching but Python's urllib does not, so a strict fetch drops the real
    logo to a text wordmark. The worst case of a MITM swapping a logo image is
    cosmetic, not a data/credential risk.
    """
    import ssl
    from urllib.request import Request
    from scraper.url_utils import open_public_url
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (PosterStudio)"})
    try:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        return open_public_url(req, timeout=timeout, context=ctx)
    except Exception as exc:                       # noqa: BLE001
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return open_public_url(
                req, timeout=timeout, context=ssl._create_unverified_context()
            )
        raise


def _remote_image_data_uri(url: str, timeout: int = 8) -> Optional[str]:
    """Fetch a remote raster logo and inline it as a data URI (deterministic
    render, no broken <img>). Returns None on any failure or for SVG.

    SSRF guard: the logo URL comes from user-supplied profile data, so we only
    fetch public http(s) hosts (no localhost/internal/metadata addresses).
    """
    from scraper.url_utils import is_safe_public_url
    if not is_safe_public_url(url):
        return None
    try:
        with _open_image_url(url, timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            data = r.read(4_000_000)
        low = url.lower().split("?")[0]
        if not data:
            return None
        # Chromium renders SVG natively; an SVG loaded via <img src="data:image/svg+xml…">
        # runs in the browser's secure static mode (no scripting, no external fetches),
        # so inlining the brand's own SSRF-guarded SVG logo is safe — and recovers
        # SVG-only brand logos the old Pillow-era pipeline had to drop.
        if "svg" in ctype or low.endswith(".svg"):
            mime = "image/svg+xml"
        elif "jpeg" in ctype or low.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif "webp" in ctype or low.endswith(".webp"):
            mime = "image/webp"
        elif "gif" in ctype or low.endswith(".gif"):
            mime = "image/gif"
        else:
            mime = "image/png"
        data, mime = _trim_logo_margins(data, mime)
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def _trim_logo_margins(data: bytes, mime: str) -> tuple[bytes, str]:
    """Key a baked white canvas to alpha, then crop the transparent border.

    WHITE-KEY FIRST — MEASURED (nanocare, 2026-07-24): the brand logo is a JPG (alpha=255
    everywhere), so its white canvas (a) shipped onto the poster as the owner's dead white
    plate, resurrected from inside the logo file, and (b) tricked `_logo_luminance` into
    judging "light logo" -> dark frosted badge -> the gray double-frame. The one-shot
    compositor already routes through `white_key_logo` (owner ruling "شيل خالص مش تطفية":
    border-connected flood-fill, guarded so real transparency and colored cards stay
    byte-stable) — the classic path now reuses the same primitive.

    THEN trim the transparent border so the mark FILLS its chip instead of floating small
    in a wide empty canvas (MEASURED: WE's we-logo has the mark on the right third of a
    transparent 496x140 canvas). No-op for SVG / on any failure."""
    if "svg" in mime:
        return data, mime
    try:
        import io
        from PIL import Image
        from reel.image_quality import white_key_logo
        keyed = white_key_logo(data)
        if keyed is not data:                    # keying happened -> PNG with alpha now
            data, mime = keyed, "image/png"
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        bbox = im.getbbox()                      # bounds of non-zero-alpha pixels
        if bbox and bbox != (0, 0, im.width, im.height):
            im = im.crop(bbox)
            out = io.BytesIO()
            im.save(out, format="PNG")
            return out.getvalue(), "image/png"
    except Exception:
        pass
    return data, mime


def _rekeyed_data_uri(uri: str) -> str:
    """The same white-key+trim for an ALREADY-INLINE logo. The webapp path hands the brief
    a data URI directly (skipping `_remote_image_data_uri`), and the white matte must not
    sneak through that door. SVG passes through; any failure returns the original."""
    try:
        head, _, b64 = uri.partition(",")
        if not b64 or "svg" in head[:40]:
            return uri
        mime = head.partition(":")[2].partition(";")[0] or "image/png"
        data, mime = _trim_logo_margins(base64.b64decode(b64), mime)
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return uri


# --- adaptive logo plate (no fixed white box; no blind removal) -----------------

def _logo_luminance(logo_src: Optional[str]) -> Optional[float]:
    """Mean WCAG luminance (0..1) of a raster logo's OPAQUE pixels. None for an SVG /
    non-data logo or any failure (the caller then falls back to the chip flag)."""
    try:
        if not logo_src or not logo_src.startswith("data:image") or "svg" in logo_src[:32]:
            return None
        import io
        from PIL import Image
        b64 = logo_src.partition(",")[2]
        im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        im.thumbnail((48, 48))
        px = [p for p in im.getdata() if p[3] > 40]      # opaque pixels only
        if not px:
            return None
        return sum(_luminance(p[:3]) for p in px) / len(px)
    except Exception:
        return None


def _logo_region_box(corner: Optional[str], logo_xy) -> tuple[float, float, float, float]:
    """The background region (fractions) sampled behind the logo, by corner or free [x,y]."""
    if logo_xy and len(logo_xy) >= 2:
        x = max(0.0, min(0.6, float(logo_xy[0])))
        y = max(0.0, min(0.8, float(logo_xy[1])))
        return (x, y, min(1.0, x + 0.4), min(1.0, y + 0.18))
    v, _, h = (corner or "top_left").partition("_")
    x0, x1 = (0.0, 0.42) if h == "left" else (0.58, 1.0)
    y0, y1 = (0.0, 0.20) if v == "top" else (0.80, 1.0)
    return (x0, y0, x1, y1)


def _bg_region_luminance(background_path: Optional[str], box) -> Optional[float]:
    """Mean luminance (0..1) of the background image inside `box` (fractions). None on error."""
    if not background_path:
        return None
    try:
        import io  # noqa: F401
        from PIL import Image
        im = Image.open(background_path).convert("RGB")
        W, H = im.size
        x0, y0, x1, y1 = box
        crop = im.crop((int(x0 * W), int(y0 * H),
                        max(int(x1 * W), int(x0 * W) + 1), max(int(y1 * H), int(y0 * H) + 1)))
        crop.thumbnail((40, 40))
        px = list(crop.getdata())
        return sum(_luminance(p) for p in px) / len(px) if px else None
    except Exception:
        return None



def _logo_style_for(*, logo_lum: float, bg_lum: float) -> str:
    """The mark's inline style: NO PLATE, EVER — a shadow sized to the ground behind it.

    OWNER RULING (2026-07-26): «انا عايزة اللوجو من غير خلفية وراه». Every plate variant this
    file has tried — the white chip, the dark chip, the frosted badge — reads as a sticker
    glued onto the poster, and the last one was measured putting a light tone on a light ground
    where it separated nothing at all. So the mark now sits directly on the image.

    Legibility still has to be earned, so it comes from the shadow instead: the closer the
    logo's luminance is to the background's, the harder that shadow works. A light logo casts
    a dark shadow, a dark logo a light one — the halo is the separation."""
    delta = abs(float(logo_lum) - float(bg_lum))
    # 0.55 apart -> a whisper; identical -> a real halo.
    t = max(0.0, min(1.0, (0.55 - delta) / 0.55))
    blur = round(8 + 22 * t, 1)
    alpha = round(0.35 + 0.45 * t, 2)
    if logo_lum >= 0.5:                       # light mark -> dark halo
        rgb = "0,0,0"
    else:                                     # dark mark -> light halo
        rgb = "255,255,255"
    return (f"background:transparent;box-shadow:none;"
            f"filter:drop-shadow(0 2px {blur}px rgba({rgb},{alpha}))")


def _adaptive_logo_style(logo_src, background_path, region_box) -> Optional[str]:
    """Measure the logo against what is behind it and return its inline style. None when the
    luminance cannot be judged (SVG / failure) — the caller then draws the mark plain."""
    logo_lum = _logo_luminance(logo_src)
    bg_lum = _bg_region_luminance(background_path, region_box)
    if logo_lum is None or bg_lum is None:
        return None
    return _logo_style_for(logo_lum=logo_lum, bg_lum=bg_lum)


def _esc(value) -> str:
    return _html.escape(str(value)) if value not in (None, "") else ""


def _accent(brief: PosterBrief) -> str:
    for color in [brief.primary_color, *(brief.palette_hex or [])]:
        if color and str(color).startswith("#"):
            return str(color)
    return "#c79a4b"


# --- contrast-aware color helpers (keep brand color, guarantee legibility) ---

def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = str(value).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (199, 154, 75)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def _luminance(rgb) -> float:
    def ch(v: float) -> float:
        x = v / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _mix(a, b, t):
    return tuple(a[i] * (1 - t) + b[i] * t for i in range(3))


def _readable_on(rgb) -> str:
    """Near-black or white text — whichever contrasts better with the given bg."""
    bg_lum = _luminance(rgb)
    dark_lum = _luminance((11, 15, 22))
    dark_contrast = (bg_lum + 0.05) / (dark_lum + 0.05)
    white_contrast = 1.05 / (bg_lum + 0.05)
    return "#0b0f16" if dark_contrast >= white_contrast else "#ffffff"


def _legible_on_dark(rgb) -> str:
    """Lighten a too-dark accent so it reads on the dark lower scrim; else keep it."""
    return _to_hex(rgb) if _luminance(rgb) >= 0.40 else _to_hex(_mix(rgb, (255, 255, 255), 0.62))


def _saturation(rgb) -> float:
    mx, mn = max(rgb), min(rgb)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _brand_accent(brief: PosterBrief, spec: Optional["PosterDesignSpec"] = None) -> str:
    """Pick the accent from the brand's REAL palette — and a VIVID one that reads on the
    dark scrim, not a muted tan that looks generic/off-scheme (owner: "the gold isn't from
    the scheme"). Chooses the most saturated palette color with enough luminance; falls
    back to the spec's choice, then the brand primary, then a neutral default."""
    pal = [
        str(c) for c in ([brief.primary_color] + list(brief.palette_hex or []))
        if c and str(c).startswith("#")
    ]
    legible = [c for c in pal if _luminance(_hex_to_rgb(c)) >= 0.16]
    cand = legible or pal
    if cand:
        return max(cand, key=lambda c: _saturation(_hex_to_rgb(c)))
    if spec is not None and spec.accent_hex and str(spec.accent_hex).startswith("#"):
        return str(spec.accent_hex)
    return _accent(brief)


# Fonts we never request from Google Fonts (system/generic/private). Any OTHER
# named family is requested best-effort; if it isn't a real Google Font the link
# simply no-ops and the curated default below applies.
_GENERIC_FONTS = {
    "system-ui", "-apple-system", "blinkmacsystemfont", "ui-sans-serif",
    "ui-serif", "ui-monospace", "sans-serif", "serif", "monospace",
    "inherit", "initial", "unset", "segoe ui", "arial", "helvetica",
    "times new roman", "tahoma", "verdana", "georgia", "roboto",
}


def _is_loadable_font(name: Optional[str]) -> bool:
    if not name:
        return False
    n = str(name).strip().strip("'\"")
    if n.lower() in _GENERIC_FONTS:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9 ]{2,40}", n))


def _gf_param(name: str) -> str:
    return str(name).strip().strip("'\"").replace(" ", "+")


# Curated Google-Font pairings (heading, body, Arabic companion) — distinct
# personalities so DIFFERENT brands get DIFFERENT typography instead of one fixed
# look ("same text design every poster"). Every Arabic companion supports Arabic.
_FONT_PAIRINGS = [
    ("Space Grotesk", "Inter", "Readex Pro"),   # modern tech
    ("Archivo", "Archivo", "Cairo"),            # bold grotesque
    ("Sora", "Inter", "Cairo"),                 # geometric display
    ("Manrope", "Manrope", "Readex Pro"),       # clean rounded-sans
    ("Fraunces", "Inter", "Cairo"),             # editorial serif
    ("Poppins", "Poppins", "Tajawal"),          # friendly
    ("Outfit", "Inter", "Tajawal"),             # contemporary
    ("Bricolage Grotesque", "Inter", "Cairo"),  # characterful display
    ("Syne", "Inter", "Cairo"),                 # funky extended display
    ("Unbounded", "Inter", "Cairo"),            # bold rounded display
    ("Plus Jakarta Sans", "Plus Jakarta Sans", "Tajawal"),  # clean humanist
    ("DM Serif Display", "Inter", "Cairo"),     # high-contrast serif
    ("Familjen Grotesk", "Inter", "Readex Pro"), # neo-grotesque
    ("Epilogue", "Inter", "Tajawal"),           # geometric sans
]


def _pairing_for(brief: PosterBrief, variation_seed: Optional[int] = None) -> tuple[str, str, str]:
    """Pick a type personality by a stable hash of the brand name (// 7 so it doesn't
    track the layout hash). `variation_seed` (per-run) is mixed in so the TYPOGRAPHY
    differs between runs of the same brand (owner: "the fonts don't change per poster");
    None keeps the stable per-brand pairing (backward-compatible)."""
    base = brief.business_name or "brand"
    if variation_seed is not None:
        base = f"{base}#font{variation_seed}"
    seed = int(hashlib.md5(base.encode("utf-8")).hexdigest(), 16)
    return _FONT_PAIRINGS[(seed // 7) % len(_FONT_PAIRINGS)]


def _fonts_head_and_stacks(
    brief: PosterBrief, rtl: bool, variation_seed: Optional[int] = None,
) -> tuple[str, str, str]:
    """(head_links, font_head_stack, font_body_stack).

    Loads a per-brand curated PAIRING (so the poster never looks like every other one,
    and never falls back to a boring system serif). ALSO best-effort loads the brand's
    OWN fonts on a SEPARATE <link> — kept separate so an invalid brand family (e.g. a
    private 'myfont2') can 400 without taking the pairing down. The brand font leads
    each stack, so it's used when it actually loads.
    """
    head_fam, body_fam, ar_fam = _pairing_for(brief, variation_seed)
    fams = sorted({head_fam, body_fam, ar_fam})
    q = "&".join(f"family={_gf_param(f)}:wght@400;500;700;800" for f in fams)
    head = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{q}&display=swap">'
    )
    bh = str(brief.heading_font or "").strip().strip("'\"")
    bb = str(brief.body_font or "").strip().strip("'\"")
    brand_fams = []
    if _is_loadable_font(bh):
        brand_fams.append(bh)
    if _is_loadable_font(bb) and bb.lower() != bh.lower():
        brand_fams.append(bb)
    if brand_fams:
        bq = "&".join(f"family={_gf_param(f)}:wght@400;700" for f in brand_fams)
        head += f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{bq}&display=swap">'

    head_brand = f"'{bh}', " if _is_loadable_font(bh) else ""
    body_brand = f"'{bb}', " if _is_loadable_font(bb) else ""
    if rtl:
        font_head = f"{head_brand}'{ar_fam}', '{head_fam}', Georgia, serif"
        font_body = f"{body_brand}'{ar_fam}', '{body_fam}', Arial, sans-serif"
    else:
        font_head = f"{head_brand}'{head_fam}', '{ar_fam}', Georgia, serif"
        font_body = f"{body_brand}'{body_fam}', '{ar_fam}', Arial, sans-serif"
    return head, font_head, font_body



def _fit_stacked_size(column_px: int, longest: int, n_words: int,
                      *, advance: float, floor: int, ceiling: int) -> int:
    """The size that fits the column in BOTH axes.

    MEASURED (Al Fouad, 2026-07-26): the size came from the longest WORD alone, so a 6-word
    Arabic headline stacked to ~615px of type, swallowed the middle of the poster and covered
    the hero product — «طريقة الكلام اللي في نص الصفحة مش حلو». Each word is its own line, so
    the number of words is a height, and a fit that ignores height is half a fit."""
    by_width = int(column_px / max(1, longest * advance))
    by_height = int((_CH * 0.44) / max(1, n_words) / 1.14)
    return max(floor, min(ceiling, by_width, by_height))


def _headline_block(
    brief: PosterBrief,
    rtl: bool,
    treatment: str = "stacked_hero",
    accent_word: str = "last",
    column_px: int = 940,
) -> str:
    """A DESIGNED headline, not a flat line.

    treatment="stacked_hero": each word on its own line, auto-sized so the longest
    word fills the text column, with one word in the accent color (the 'BUSINESS /
    IDEAS' treatment). treatment="block" (or a headline too long/short for a hero):
    a clean single-block <h1>.

    ZERO-HALLUCINATION: the words are the verbatim headline, only RE-STYLED — none
    added, removed, reordered, or reworded. (Uppercasing is applied to Latin text
    only via CSS; the underlying string is unchanged.)
    """
    text = (brief.headline or "").strip()
    words = text.split()
    longest = max((len(w) for w in words), default=0)

    # ARABIC IS TYPESET, NOT STACKED (owner, 2026-07-26: «طريقة الكتابة مستحيلة صعبة، بتبوّظ
    # المنتج تماماً»). One word per line reads as a designed lockup in Latin display type; in
    # Arabic it breaks the connected word-shapes the script is actually read by, forces a huge
    # size to fill the column, and lands the headline across the model and the product. RTL copy
    # goes to the wrapping block — the treatment stays exactly as designed for Latin.
    if rtl and treatment in ("lockup", "stacked_hero", "highlight"):
        treatment = "block"

    # 'highlight' — one word sits inside a brand-colored block (kinetic editorial flair).
    if treatment == "highlight" and 1 <= len(words) <= 9:
        n = len(words)
        idx = 0 if accent_word == "first" else n - 1
        parts = [
            (f'<span class="hl">{_esc(w)}</span>' if (i == idx and n > 1) else _esc(w))
            for i, w in enumerate(words)
        ]
        return f'<h1 class="headline hl-head">{" ".join(parts)}</h1>'

    # 'lockup' — a bold, DESIGNED graphic headline driven by the brand's typographic DNA
    # (heavy weight + gradient fill + outline + shadow, one word in the accent gradient), so
    # the headline reads as a custom AD LOCKUP, not plain text laid over a photo. Words are
    # stacked (each on its own line) and auto-fit to the column.
    if treatment == "lockup":
        n = len(words)
        if 1 <= n <= 6 and 0 < longest <= 22:
            max_size = 154 if column_px >= 800 else 120
            size = _fit_stacked_size(column_px, longest, n,
                                     advance=0.58, floor=58, ceiling=max_size)
            upper_cls = "" if rtl else " upper"
            spans = []
            for i, w in enumerate(words):
                is_accent = n > 1 and (
                    (accent_word == "last" and i == n - 1)
                    or (accent_word == "first" and i == 0)
                )
                spans.append(f'<span class="lw{" accent" if is_accent else ""}">{_esc(w)}</span>')
            return (
                f'<div class="lockup-headline{upper_cls}" style="font-size:{size}px">'
                f'{"".join(spans)}</div>'
            )
        return f'<h1 class="headline lockup-flat">{_esc(text)}</h1>'

    can_hero = 1 <= len(words) <= 5 and 0 < longest <= 20
    if treatment != "stacked_hero" or not can_hero:
        return f'<h1 class="headline">{_esc(text)}</h1>'

    # Auto-fit: the longest word should fill the AVAILABLE text column (which is
    # narrower in a side panel than a full-width band), so the hero is dramatic but
    # NEVER overflows the edge. ~0.62em average glyph advance for the bold display face.
    max_size = 136 if column_px >= 800 else 110
    size = _fit_stacked_size(column_px, longest, max(1, len(words)),
                             advance=0.6, floor=52, ceiling=max_size)
    upper_cls = "" if rtl else " upper"        # Arabic/Hebrew have no letter case
    n = len(words)
    spans = []
    for i, w in enumerate(words):
        is_accent = n > 1 and (
            (accent_word == "last" and i == n - 1)
            or (accent_word == "first" and i == 0)
        )
        accent = " accent" if is_accent else ""
        spans.append(f'<span class="hw{accent}">{_esc(w)}</span>')
    return (
        f'<div class="hero-headline{upper_cls}" style="font-size:{size}px">'
        f'{"".join(spans)}</div>'
    )


# --- Design-spec → layout. Each archetype places the brand mark + text block in a
# DIFFERENT composition, so two brands no longer share one canvas. The chosen
# layout also implies a `negative_space_zone` shared with the background prompt. ---

_LAYOUTS = (
    "bottom_band", "side_panel_left", "side_panel_right",
    "top_anchor", "center_editorial", "magazine_hero",
)
_ZONE_BY_LAYOUT = {
    "bottom_band": "bottom", "magazine_hero": "bottom",
    "side_panel_left": "left", "side_panel_right": "right",
    "top_anchor": "top", "center_editorial": "center",
}
_LOGO_CORNER_BY_LAYOUT = {
    "bottom_band": "top_left", "magazine_hero": "top_left",
    "side_panel_left": "top_left", "side_panel_right": "top_right",
    "top_anchor": "top_left", "center_editorial": "top_left",
}
_HERO_LAYOUTS = {"magazine_hero", "bottom_band", "side_panel_left", "side_panel_right"}
_FULL_SHOW = ["logo", "kicker", "headline", "sub", "offerings", "cta", "contact", "social"]
_MINIMAL_SHOW = ["logo", "headline", "cta"]


# HOW THE CALL TO ACTION IS DRAWN. Four real treatments, not four skins of one:
#   pill       a solid rounded button — loud, retail, what every poster used to get
#   block      a sharp-cornered slab — editorial, modern, reads as designed rather than clicked
#   outline    the brand colour as a hairline border on transparent — quiet, premium
#   text_arrow no button at all: the words with a rule under them and an arrow
# The last one is the point. A premium poster often HAS no button, and a system that can only
# draw buttons cannot make one.
_CTA_STYLES = ["pill", "block", "outline", "text_arrow"]


def default_design_spec(
    brief: PosterBrief, density: str = "minimal", variation_seed: Optional[int] = None,
) -> PosterDesignSpec:
    """A deterministic-but-VARIED design when no LLM spec is supplied.

    The layout is chosen by a stable hash of the brand name: DIFFERENT brands get
    DIFFERENT compositions. `variation_seed` (per-run, optional) is mixed into the hash
    so the SAME brand also varies between runs (owner: "every poster looks the same") —
    when omitted, the layout stays stable per brand (backward-compatible). The LLM
    art-director supplies a data-reasoned spec in the full pipeline.
    """
    base = brief.business_name or "brand"
    if variation_seed is not None:
        base = f"{base}#{variation_seed}"
    seed = int(hashlib.md5(base.encode("utf-8")).hexdigest(), 16)
    layout = _LAYOUTS[seed % len(_LAYOUTS)]
    show = _MINIMAL_SHOW if density == "minimal" else _FULL_SHOW
    # Vary the headline treatment too (decorrelated via // 13), so the TEXT design isn't
    # identical across brands — not just the layout.
    _treatments = ["stacked_hero", "highlight"] if layout in _HERO_LAYOUTS else ["block", "highlight"]
    treatment = _treatments[(seed // 13) % len(_treatments)]
    # Decorrelated again (// 29) so the button is not a passenger of the layout: two brands that
    # happen to share a composition must not also share a button.
    cta_style = _CTA_STYLES[(seed // 29) % len(_CTA_STYLES)]
    return PosterDesignSpec(
        cta_style=cta_style,
        layout=layout,
        logo_corner=_LOGO_CORNER_BY_LAYOUT[layout],
        headline_treatment=treatment,
        accent_word="last",
        text_align="center" if layout == "center_editorial" else "left",
        scrim_strength=0.82,
        show=list(show),
        negative_space_zone=_ZONE_BY_LAYOUT[layout],
        variation_seed=variation_seed,
    )


def _mirror_corner(corner: str, rtl: bool) -> str:
    if not rtl:
        return corner
    return corner.replace("left", "R").replace("right", "left").replace("R", "right")



# ONE GUTTER (reviewer 2026-07-26: «زرار CTA x=96، الهيدلاين x=106، اللوجو x=116 — تلات حدود
# شمال في 20 بكسل. لو متطابقين يبقى نظام؛ 20px = حاولنا ومظبطناهاش»). Every left/right offset of
# the logo, the text block and the button hangs on this single number.
_GUTTER_PX = 80

def _logo_corner_css(corner: str) -> str:
    v, _, h = corner.partition("_")
    vside = "top:54px;" if v == "top" else "bottom:54px;"
    hside = (f"left:{_GUTTER_PX}px;" if h == "left" else f"right:{_GUTTER_PX}px;")
    return vside + hside


def effective_scrim_strength(requested: float, background_path, text_box=None) -> float:
    """The scrim darkness the TEXT actually needs, measured against what is behind it.

    MEASURED (Al Fouad, 2026-07-25): the subheadline «اسألي صيادلة الفؤاد عن روتينك مجاناً…»
    renders at `#e6ecf2` over a bright white bathroom and is effectively invisible. The cause is a
    CONSTANT where a measurement belongs — `scrim_strength` defaults to 0.82 and is applied
    blindly, so the legibility backing is the same darkness whether the scene behind it is a night
    street or a white tiled wall. On the dark one 0.82 is plenty; on the light one near-white type
    lands on near-white pixels.

    The measurement was already here and already trusted: `_bg_region_luminance` + `_ink_on` give
    the LOGO a contrast-aware plate, which is why the logo always reads while the copy that carries
    the message does not.

    Bright region -> darker scrim, scaled by how bright. Dark region -> untouched, because
    over-darkening a scene that already carries type just destroys the photograph. Capped below
    fully opaque for the same reason: this is a legibility floor, not a black box.

    Degrades to `requested` on anything it cannot measure — an unreadable file must cost nothing.
    Deterministic and universal (D-8): arithmetic on pixels, no model, no market assumption."""
    try:
        req = float(requested)
    except (TypeError, ValueError):
        return requested
    if not background_path:
        return req
    box = text_box if (text_box and len(text_box) >= 3) else [0.05, 0.55, 0.90]
    x, y, w = float(box[0]), float(box[1]), float(box[2])
    region = (max(0.0, x), max(0.0, y), min(1.0, x + w), min(1.0, y + 0.38))
    lum = _bg_region_luminance(background_path, region)
    if lum is None:
        return req
    # The light copy needs a dark backing. Below this the scene is already doing the work.
    _FLOOR = 0.34
    if lum <= _FLOOR:
        return req
    # Scale the whole way to a near-opaque (but never opaque) backing as the scene approaches white.
    span = (lum - _FLOOR) / (1.0 - _FLOOR)
    needed = req + (0.96 - req) * min(1.0, max(0.0, span))
    return round(min(0.96, max(req, needed)), 3)


def _lower_css(layout: str, scrim_strength: float, align: str, rtl: bool) -> str:
    """The position + scrim CSS for the text block of one layout archetype.

    THE SCRIM HOLDS ACROSS THE TEXT, then fades (owner, 2026-07-26: «الكلام أبيض مش باين»).
    MEASURED on that poster: the pixels behind the white headline averaged 185/255 — about a
    1.7:1 contrast, unreadable — even though a scrim WAS applied. The gradient reached full
    strength at the very bottom EDGE and fell to zero going up, while the block's 130px top
    padding puts the biggest type near the TOP. The backing was under the margin, not under
    the words. The stops now hold `s_hi` through the copy and fade only in the last stretch,
    so a bright scene still gets a real plate and a dark one still keeps its photograph."""
    s_hi = round(max(0.0, min(1.0, scrim_strength)), 3)
    s_mid = round(s_hi * 0.8, 3)
    al = f"text-align:{align};"
    if layout in ("bottom_band", "magazine_hero"):
        pad = (f"300px {_GUTTER_PX}px 70px" if layout == "magazine_hero" else f"130px {_GUTTER_PX}px 70px")
        return (
            f".lower{{position:absolute;left:0;right:0;bottom:0;padding:{pad};{al}"
            f"background:linear-gradient(to top, rgba(8,12,18,{s_hi}) 0%, "
            f"rgba(8,12,18,{s_hi}) 72%, rgba(8,12,18,{s_mid}) 86%, "
            f"rgba(8,12,18,0) 100%);}}"
        )
    if layout == "top_anchor":
        return (
            f".lower{{position:absolute;left:0;right:0;top:0;padding:200px {_GUTTER_PX}px 96px;{al}"
            f"background:linear-gradient(to bottom, rgba(8,12,18,{s_hi}) 0%, "
            f"rgba(8,12,18,{s_hi}) 72%, rgba(8,12,18,{s_mid}) 86%, "
            f"rgba(8,12,18,0) 100%);}}"
        )
    if layout in ("side_panel_left", "side_panel_right"):
        on_left = layout.endswith("left")
        if rtl:
            on_left = not on_left
        if on_left:
            box = "left:0;right:auto;"
            grad = (f"linear-gradient(to right, rgba(8,12,18,{s_hi}) 0%, "
                    f"rgba(8,12,18,{s_hi}) 74%, rgba(8,12,18,{s_mid}) 88%, "
                    f"rgba(8,12,18,0) 100%)")
        else:
            box = "right:0;left:auto;"
            grad = (f"linear-gradient(to left, rgba(8,12,18,{s_hi}) 0%, "
                    f"rgba(8,12,18,{s_hi}) 74%, rgba(8,12,18,{s_mid}) 88%, "
                    f"rgba(8,12,18,0) 100%)")
        return (
            f".lower{{position:absolute;top:0;bottom:0;width:60%;{box}{al}"
            f"display:flex;flex-direction:column;justify-content:flex-end;"
            f"padding:80px {_GUTTER_PX}px 100px;background:{grad};}}"
        )
    if layout == "center_editorial":
        # A soft radial faded out at the horizontal extremes, so a full-width headline's
        # left/right ends sat over bright, busy photo areas and were unreadable (MEASURED
        # on ITI). A FULL-WIDTH vertical band (dark across the central third, fading top +
        # bottom) backs the whole text width — readable regardless of what's behind it,
        # while leaving the upper subject area and bottom margin clearer.
        return (
            f".lower{{position:absolute;inset:0;display:flex;flex-direction:column;"
            f"justify-content:center;align-items:center;padding:80px;text-align:center;"
            f"background:linear-gradient(to bottom, rgba(8,12,18,0) 0%, "
            f"rgba(8,12,18,{s_mid}) 24%, rgba(8,12,18,{s_hi}) 50%, "
            f"rgba(8,12,18,{s_mid}) 76%, rgba(8,12,18,0) 100%);}}"
        )
    return (
        f".lower{{position:absolute;left:0;right:0;bottom:0;padding:130px {_GUTTER_PX}px 70px;{al}"
        f"background:linear-gradient(to top, rgba(8,12,18,{s_hi}) 0%, "
        f"rgba(8,12,18,{s_hi}) 72%, rgba(8,12,18,0) 100%);}}"
    )


# --- FREE-FORM layout (Phase 2): the LLM places the text cluster + logo at continuous
# normalized coords instead of one of the 6 archetypes. CLAMPED to safe margins so a bad
# coordinate can never push content off-canvas; each text cluster gets its own soft scrim
# panel so it stays legible ANYWHERE on the image. ---

_CW, _CH = 1080, 1350


def _clampf(v: float, lo: float, hi: float) -> float:
    try:
        v = float(v)
    except Exception:
        return lo
    return max(lo, min(hi, v))


_SAFE_MARGIN = 42        # px every element must stay inside the frame by



def avoid_product_zone(text_box, product_zone, *, cluster_h: float = 0.22,
                       product_width: Optional[float] = None):
    """Move a free-form text cluster OFF the composited product.

    REVIEWER (2026-07-26): «النص غطّى المنتج… تغطية المنتج بالتايبوجرافي خطيئة أساسية» — the
    headline sat across the bottle and covered its SPF claim. The product lands at a DECIDED zone
    (`poster.product_placement`), so the copy no longer has to guess: if the cluster overlaps that
    rectangle, it is pushed to whichever side has more room, and only narrowed if it has to be.
    Boxes already clear of the product are returned untouched — the LLM's composition still
    decides everything this rule does not have to.

    `product_width` is the footprint the placement actually computed. Without it the old 0.40
    guess stands, which over-reserves for a slim bottle and under-reserves for a wide carton."""
    box = list(text_box or [])
    if len(box) < 3 or not product_zone:
        return box
    x, y, w = float(box[0]), float(box[1]), float(box[2])
    px, py, ph = (list(product_zone) + [0.5, 0.3, 0.4])[:3]
    pw = float(product_width) if product_width else 0.40
    overlaps_x = (x < px + pw) and (x + w > px)
    overlaps_y = (y < py + ph) and (y + cluster_h > py)
    if not (overlaps_x and overlaps_y):
        return box
    left_room = px
    right_room = 1.0 - (px + pw)
    if left_room >= right_room:
        w = max(0.34, min(w, left_room - 0.03))
        x = max(0.035, min(x, px - w - 0.02))
    else:
        w = max(0.34, min(w, right_room - 0.03))
        x = min(0.93 - w, px + pw + 0.02)
    return [round(x, 4), round(y, 4), round(w, 4)]


def _freeform_lower_css(text_box, scrim_strength: float, align: str,
                        archetype: Optional[str] = None) -> str:
    """Position the text cluster at a free [x,y,w] (normalized) with a soft, rounded scrim
    panel sized to it — readable on any background, a different composition every time, and
    NEVER clipped.

    LAYOUT SAFETY (brief Step G): the cluster grows DOWNWARD from `top` when placed high,
    but ANCHORS TO THE BOTTOM (a fixed safe margin) when placed in the lower half — so the
    last element (the CTA) is always fully inside the frame, whatever the text height. It's a
    flex column so the scrim panel hugs its content."""
    box = list(text_box or [])
    x = _clampf(box[0] if len(box) > 0 else 0.06, 0.035, 0.92)
    w = _clampf(box[2] if len(box) > 2 else 0.6, 0.34, 0.93)
    if x + w > 0.965:
        x = max(0.035, 0.965 - w)
    y = _clampf(box[1] if len(box) > 1 else 0.6, 0.04, 0.93)
    left, width = round(x * _CW), round(w * _CW)
    items = {"center": "center", "right": "flex-end"}.get(align, "flex-start")
    s_hi = round(max(0.0, min(1.0, scrim_strength)), 3)
    s_mid = round(s_hi * 0.72, 3)
    s_lo = round(s_hi * 0.34, 3)
    # EMERGING (product_hero, or any LOW text block): a FULL-WIDTH feathered base gradient — solid
    # at the bottom, fading up to transparent — so the text reads as EMERGING organically from the
    # base of the image. NO rounded card, NO box-shadow, NO side edges (owner: it looked "stuck
    # like a sticker"). The text cluster keeps the LLM's horizontal placement via side padding.
    emerging = (archetype == "product_hero") or (y >= 0.5)
    if emerging:
        pad_x = max(64, left)
        scrim = (
            f"background:linear-gradient(to top, rgba(8,12,18,{s_hi}) 0%, "
            f"rgba(8,12,18,{round(s_hi * 0.88, 3)}) 24%, rgba(8,12,18,{s_mid}) 50%, "
            f"rgba(8,12,18,{s_lo}) 76%, rgba(8,12,18,0) 100%);"
        )
        return (f".lower{{position:absolute; left:0; right:0; bottom:0; text-align:{align}; "
                f"display:flex; flex-direction:column; align-items:{items}; "
                f"padding:170px {pad_x}px {_SAFE_MARGIN}px {pad_x}px; box-sizing:border-box; "
                f"{scrim}}}")
    # Upper placement -> a soft RADIAL wash that melts into the image (a hint of legibility
    # backing, feathered to transparent at every edge — NOT a hard rounded-card with a drop-shadow).
    # The cluster carries the headline AND the subline AND the CTA. This wash used to reach
    # ZERO at its edges, so the bottom copy sat on bare bright photo — the same invisible-copy
    # complaint the fixed layouts were fixed for, in the branch that fix did not touch
    # («الكلام اللي تحت لسه فاتح»). It now keeps a legibility floor across the whole block and
    # feathers only at the outer rim.
    _floor = round(max(s_lo, s_hi * 0.42), 3)
    scrim = (
        f"background:radial-gradient(150% 150% at 50% 46%, rgba(8,12,18,{s_hi}) 0%, "
        f"rgba(8,12,18,{round(s_hi * 0.92, 3)}) 46%, rgba(8,12,18,{s_mid}) 72%, "
        f"rgba(8,12,18,{_floor}) 100%);"
    )
    common = (f"position:absolute; left:{left}px; width:{width}px; text-align:{align}; "
              f"display:flex; flex-direction:column; align-items:{items}; "
              f"padding:48px 56px; box-sizing:border-box; {scrim}")
    return f".lower{{{common} top:{round(y * _CH)}px;}}"


def _freeform_logo_css(logo_xy) -> str:
    """Anchor the brand mark at a free [x,y] (normalized), clamped so a ~360x96 mark stays
    fully on canvas."""
    xy = list(logo_xy or [])
    x = _clampf(xy[0] if len(xy) > 0 else 0.07, 0.03, 0.66)
    y = _clampf(xy[1] if len(xy) > 1 else 0.04, 0.03, 0.90)
    return f"top:{round(y * _CH)}px; left:{round(x * _CW)}px;"


def _freeform_column_px(text_box) -> int:
    """The usable text width inside the free box (box width minus padding)."""
    box = list(text_box or [])
    w = _clampf(box[2] if len(box) > 2 else 0.6, 0.34, 0.93)
    return max(220, round(w * _CW) - 68)


def render_poster_html(
    brief: PosterBrief,
    background_path: Optional[str] = None,
    spec: Optional[PosterDesignSpec] = None,
    density: str = "minimal",
    copy_ink: str = "#f5f3ef",
    product_zone: Optional[tuple] = None,
    product_width: Optional[float] = None,
) -> str:
    """`product_zone` is where the product was ACTUALLY placed for this poster
    (`poster.product_placement.decide_placement`, confirmed against the rendered scene). It used
    to be read from a module-level constant, which meant the copy fled a rectangle the product
    might not be in — true about some other poster, wrong about this one. None = no product.

    `copy_ink` is MEASURED by the caller against this very background
    (`poster.contrast_gate.ink_for_background`). MEASURED (v9): three attempts on a bright
    pharmacy scene scored 2.72, 2.01 and 1.16:1 in white — the remedy for a bright ground is
    not moving white copy, it is dark ink."""
    # The DESIGN spec drives the composition. When none is supplied we synthesize a
    # deterministic-but-varied one (per-brand hash) so output isn't one-size-fits-all;
    # the LLM art-director supplies a data-driven spec in the full pipeline.
    spec = spec or default_design_spec(brief, density)
    show = spec.show or _MINIMAL_SHOW
    # The marketing archetype (behavioral guide from the art-director) steers the renderer's
    # headline treatment + scrim style — without hardcoding the layout (coords stay the LLM's).
    arche = getattr(spec, "marketing_archetype", None)
    # Accent = the brand's most VIVID real palette color (legible on the scrim) — never a
    # muted/off-scheme tan, never a generic default.
    accent = _brand_accent(brief, spec)
    accent_rgb = _hex_to_rgb(accent)
    kicker_color = _legible_on_dark(accent_rgb)   # readable on the dark scrim
    cta_bg = accent                               # keep the brand color on the button
    cta_text = _readable_on(accent_rgb)           # white on a dark brand color, dark on a light one

    # Lockup-headline gradient (the DNA "internal gradient" treatment): the brand accent -> a
    # second vivid palette color, or a lighter shade of the accent when the palette has only one.
    _pal2 = [str(c) for c in ([brief.primary_color] + list(brief.palette_hex or []))
             if c and str(c).startswith("#") and str(c).lower() != accent.lower()]
    grad2 = _pal2[0] if _pal2 else _to_hex(_mix(accent_rgb, (255, 255, 255), 0.45))

    if background_path:
        bg_layer = f"url('{_data_uri(background_path)}') center/cover no-repeat"
    else:
        # --no-image path: a brand-palette gradient, no Pillow involved.
        second = (brief.palette_hex or ["#0e131b"])[-1]
        bg_layer = f"linear-gradient(135deg, {accent} 0%, {second} 100%)"

    # --- evidence-only blocks: built only when the field exists ---
    kicker_html = ""
    if brief.category:
        kicker_html = f'<div class="kicker">{_esc(brief.category.replace("_", " "))}</div>'

    sub_html = f'<p class="sub">{_esc(brief.subheadline)}</p>' if brief.subheadline else ""

    offerings_html = ""
    if brief.offerings:
        items = "".join(f"<li>{_esc(o)}</li>" for o in brief.offerings[:3] if o)
        if items:
            offerings_html = f'<ul class="offerings">{items}</ul>'

    cta_html = ""
    if brief.cta_text:
        # Big-brand creatives NEVER print a raw URL on the poster — the CTA is the
        # verb ("Shop now" / "احجز الآن"); the link lives in the post caption / bio,
        # not baked into the image. (brief.cta_url is still available to the caller
        # for a clickable button beside the PNG.)
        # Preserve authored casing and isolate Latin runs inside RTL instructions.
        latin_run = r"([A-Za-z][A-Za-z0-9]*(?:[.+/-][A-Za-z0-9]+)*)"
        cta_copy = ''.join(
            '<bdi dir="ltr">' + _esc(part) + '</bdi>'
            if re.fullmatch(latin_run, part) else _esc(part)
            for part in re.split(latin_run, brief.cta_text))
        cta_html = f'<div class="cta"><span class="cta-text" dir="auto">{cta_copy}</span></div>' 

    contact_html = f'<p class="contact" dir="ltr">{_esc(brief.contact_line)}</p>' if brief.contact_line else ""

    # Evidence-backed social profiles (platform labels; a PNG can't be clickable).
    social_html = ""
    if brief.social:
        labels = {
            "facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn",
            "youtube": "YouTube", "tiktok": "TikTok", "twitter": "X", "x": "X",
            "whatsapp": "WhatsApp", "telegram": "Telegram", "snapchat": "Snapchat",
            "pinterest": "Pinterest",
        }
        row = " · ".join(
            _esc(labels.get((s.platform or "").lower(), (s.platform or "link").capitalize()))
            for s in brief.social[:6]
        )
        social_html = f'<p class="socials">{row}</p>'

    # Brand mark: a real logo image (inlined) on a light plate; else wordmark text.
    logo_src = None
    if brief.logo_url and brief.logo_url.startswith("data:image"):
        logo_src = _rekeyed_data_uri(brief.logo_url)   # inline logos get the same white-key
    elif brief.logo_url and brief.logo_url.startswith(("http://", "https://")):
        logo_src = _remote_image_data_uri(brief.logo_url)
    # RTL: detect from the actual copy (Arabic headline/brand) -> right-align the
    # overlay and move the logo to the top-right.
    rtl = _is_rtl(brief.headline) or _is_rtl(brief.business_name)
    dir_attr = "rtl" if rtl else "ltr"
    lang_attr = "ar" if re.search(r"[\u0600-\u06ff]", brief.headline) else "und"
    # Horizontal alignment: centered layouts always center; otherwise RTL copy
    # right-aligns, else the spec's choice.
    if spec.layout == "center_editorial":
        align = "center"
    elif rtl:
        align = "right"
    else:
        align = spec.text_align or "left"
    align_cls = f" align-{align}" if align in ("center", "right") else ""

    # Typography: the website's own fonts when loadable, else a curated modern
    # default — never the boring system serif. The per-run variation seed (from the
    # spec) varies the pairing so the FONTS change between runs of the same brand.
    font_links, font_head, font_body = _fonts_head_and_stacks(brief, rtl, spec.variation_seed)
    # THE BUTTON IS A DESIGN DECISION. This block used to be one hardcoded pill on every poster of
    # every brand — the owner spotted it across four posters in a row. Each archetype is a real
    # treatment with its own weight; `text_arrow` deliberately draws NO button, because a premium
    # poster often has none and a renderer that can only draw buttons cannot make one.
    _cta_style = getattr(spec, "cta_style", "pill") or "pill"
    _cta_shadow = "box-shadow:0 16px 40px -10px rgba(0,0,0,.6);"
    if _cta_style == "block":
        cta_css = (f".cta{{display:inline-flex;align-items:center;gap:12px;background:{cta_bg};"
                   f"color:{cta_text};padding:24px 56px;border-radius:6px;{_cta_shadow}}}"
                   f".cta-text{{font-family:{font_body};font-weight:800;font-size:30px;"
                   "letter-spacing:.04em;text-transform:none;}")
    elif _cta_style == "outline":
        cta_css = (f".cta{{display:inline-flex;align-items:center;gap:12px;background:transparent;"
                   f"color:{cta_bg};padding:20px 48px;border-radius:999px;"
                   f"border:2.5px solid {cta_bg};}}"
                   f".cta-text{{font-family:{font_body};font-weight:700;font-size:27px;"
                   "letter-spacing:.02em;}")
    elif _cta_style == "text_arrow":
        # No plate, no fill. The words themselves, with a rule under them — so the CTA reads as
        # part of the writing rather than a control pasted onto a photograph.
        cta_css = (f".cta{{display:inline-flex;align-items:center;gap:14px;background:none;"
                   f"color:{copy_ink};padding:6px 0 10px 0;border:0;"
                   f"border-bottom:3px solid {cta_bg};}}"
                   f".cta-text{{font-family:{font_body};font-weight:800;font-size:30px;"
                   "letter-spacing:.01em;}"
                   # `\\2190` so PYTHON emits the two characters \2190 and CSS reads them as its
                   # own escape for U+2190 (←). Written `\2190`, Python ate it first: `\21` is a
                   # valid OCTAL escape (U+0011, a control char) and `90` stayed literal, so the
                   # CSS said content:'\x1190' and the poster rendered a missing-glyph box
                   # followed by "90". MEASURED on elabdfoods.com 2026-08-27 — the tofu box is
                   # visible in outputs/posters/elabd_poster_v2.png beside the CTA rule.
                   f".cta::after{{content:'\\2190';font-size:26px;color:{cta_bg};font-weight:900;}}")
    else:
        cta_css = (f".cta{{display:inline-flex;align-items:center;gap:12px;background:{cta_bg};"
                   f"color:{cta_text};padding:22px 52px;border-radius:999px;"
                   f"border:1.5px solid rgba(255,255,255,.28);{_cta_shadow}}}"
                   f".cta-text{{font-family:{font_body};font-weight:800;font-size:28px;"
                   "text-transform:none;letter-spacing:.01em;}")

    surface_css = ""
    if spec.copy_surface != "auto":
        surface = "#fbfaf8" if spec.copy_surface == "light" else "#171820"
        surface_css = f".lower{{background:{surface}!important;border-radius:20px;}}"
    typography_css = ".cta-text{unicode-bidi:isolate;} [dir=rtl] .cta-text{letter-spacing:normal;}"
    for selector, size in ((".headline,.hero-headline,.lockup-headline", spec.headline_font_size),
                           (".sub", spec.support_font_size), (".cta-text", spec.cta_font_size)):
        if size is not None:
            typography_css += f"{selector}{{font-size:{size}px!important;}}"

    # Layout-driven CSS. FREE-FORM (spec.text_box set) places the cluster + logo at
    # continuous coords (unbounded compositions); else one of the 6 fixed archetypes.
    # MEASURE THE BACKGROUND BEFORE DRAWING ON IT. The scrim used to be whatever the spec asked
    # for, which is how near-white copy ended up on a near-white bathroom wall. The logo has always
    # been given this treatment; the text that carries the message had not.
    # DARK INK CHANGES THE PHYSICS. A scrim exists to darken a bright ground so WHITE type
    # reads; with dark type measured as the better ink, darkening is exactly wrong — the copy
    # gets a light veil instead, and its halo flips from black to white.
    _dark_ink = str(copy_ink or "").lower() not in ("#fff", "#ffffff", "#f5f3ef", "#f3f7fb")
    _scrim = effective_scrim_strength(spec.scrim_strength, background_path, spec.text_box)
    if _dark_ink:
        _scrim = 0.0
    _ink_shadow = ("0 1px 6px rgba(255,255,255,.85)" if _dark_ink
                   else "0 2px 10px rgba(0,0,0,.55)")
    # The chips carry a plate; under dark ink a dark plate is black-on-black.
    chip_bg = "rgba(255,255,255,.72)" if _dark_ink else "rgba(10,14,20,.58)"
    chip_border = ("1px solid rgba(20,20,20,.18)" if _dark_ink
                   else "1px solid rgba(255,255,255,.20)")
    if spec.text_box:
        # THE COPY NEVER SITS ON THE PRODUCT (reviewer 2026-07-26). The product is composited at
        # a known zone, so the cluster is pushed off it before anything is laid out.
        _tb = spec.text_box
        try:
            _tb = (avoid_product_zone(list(spec.text_box), product_zone,
                                      product_width=product_width)
                   if product_zone else list(spec.text_box))
        except Exception:  # noqa: BLE001 — a layout nicety must never break a render
            _tb = spec.text_box
        lower_layout_css = _freeform_lower_css(_tb, _scrim, align, arche)
        logo_pos_css = _freeform_logo_css(spec.logo_xy)
    else:
        mark_corner = _mirror_corner(spec.logo_corner, rtl)
        lower_layout_css = _lower_css(spec.layout, _scrim, align, rtl)
        logo_pos_css = _logo_corner_css(mark_corner)

    # A short accent rule above the headline — a small piece of brand-colored
    # flair so the lower block reads designed, not a flat slab of text.
    accent_rule_html = '<div class="accent-rule"></div>'

    brand_html = ""
    if "logo" in show:
        if logo_src:
            # ADAPTIVE plate: contrast the logo against the real background behind it — no
            # plate when the mark already reads (natural integration), else a plate that
            # NO PLATE ON ANY PATH (owner ruling: «اللوجو من غير خلفية وراه»). The measured
            # style is a shadow; when luminance cannot be judged (SVG) the mark still gets a
            # neutral halo rather than the old dark chip — a box was never wanted, and a box
            # drawn because we could not measure is the worst version of it.
            region = _logo_region_box(None if spec.text_box else mark_corner,
                                      spec.logo_xy if spec.text_box else None)
            adaptive = _adaptive_logo_style(logo_src, background_path, region)
            if not adaptive:
                adaptive = _logo_style_for(
                    logo_lum=(0.85 if getattr(brief, "logo_chip", "light") == "dark" else 0.20),
                    bg_lum=0.5)
            chip_style = f' style="{adaptive}"' 
            brand_html = f'<img class="brand-logo"{chip_style} src="{logo_src}" alt="{_esc(brief.business_name)}">'
        else:
            brand_html = f'<div class="brand">{_esc(brief.business_name)}</div>'

    # The text column is narrower in a side panel (~520px) than a full-width band
    # (~920px); the hero auto-fit must know this or a long word overflows the edge.
    if spec.text_box:
        column_px = _freeform_column_px(spec.text_box)
    else:
        column_px = 520 if spec.layout in ("side_panel_left", "side_panel_right") else 920
    # ARCHETYPE -> headline treatment: editorial/promotional archetypes want a bold DESIGNED
    # lockup (the headline becomes the visual anchor); others keep the spec's treatment.
    treatment = spec.headline_treatment
    if arche in ("magazine_editorial", "typographic_anchor") and treatment != "lockup":
        treatment = "lockup"
    headline_html = _headline_block(
        brief, rtl, treatment, spec.accent_word, column_px=column_px
    )

    # Assemble the visible blocks per the spec's `show` list (decoration always),
    # filtering empties (a field with no evidence renders nothing — never fabricated).
    ordered: list[str] = []
    if "kicker" in show:
        ordered.append(kicker_html)
    # (accent rule removed — the art critic read the small floating bar as an orphan artifact)
    if "headline" in show:
        ordered.append(headline_html)
    if "sub" in show:
        ordered.append(sub_html)
    if "offerings" in show:
        ordered.append(offerings_html)
    if "cta" in show:
        ordered.append(cta_html)
    if "contact" in show:
        ordered.append(contact_html)
    if "social" in show:
        ordered.append(social_html)
    lower_inner = "\n      ".join(b for b in ordered if b)

    return f"""<!doctype html>
<html lang="{lang_attr}"><head><meta charset="utf-8">{font_links}<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:1080px; height:1350px; }}
  .canvas {{
    position:relative; width:1080px; height:1350px; overflow:hidden;
    background:#0e131b {bg_layer};
    font-family:{font_body}; color:{copy_ink};
  }}
  .brand {{
    position:absolute; max-width:420px;
    font-family:{font_head}; font-size:32px; font-weight:700; letter-spacing:.01em; color:{copy_ink};
    text-shadow:{_ink_shadow};
  }}
  .brand-logo {{
    position:absolute; max-height:104px; max-width:420px;
    width:auto; object-fit:contain; background:rgba(255,255,255,.96);
    padding:14px 22px; border-radius:16px; box-shadow:0 6px 22px rgba(0,0,0,.38);
  }}
  /* brand-colored flair bar above the headline */
  .accent-rule {{
    width:68px; height:6px; border-radius:4px; margin:0 0 22px;
    background:{accent}; box-shadow:0 2px 12px {accent}66;
  }}
  .lower.align-right .accent-rule {{ margin-left:auto; }}
  .lower.align-center .accent-rule {{ margin-left:auto; margin-right:auto; }}
  .kicker {{
    font-family:{font_body}; font-size:18px; font-weight:700;
    letter-spacing:.24em; text-transform:uppercase; color:{kicker_color}; margin-bottom:16px;
  }}
  .headline {{
    font-family:{font_head}; font-size:67px; line-height:1.05; font-weight:800;
    letter-spacing:-.012em; margin-bottom:24px; text-shadow:{_ink_shadow};
  }}
  /* stacked-hero headline (the 'BUSINESS / IDEAS' treatment) — a strong DISPLAY LOCKUP:
     heavy weight, tight leading, a crisp shadow for punch (art-critic: 'not a flat font'). */
  .hero-headline {{
    display:flex; flex-direction:column; gap:0; margin-bottom:24px;
    font-family:{font_head}; font-weight:800; line-height:.96; letter-spacing:-.02em;
    text-shadow:{_ink_shadow};
  }}
  .hero-headline .hw {{ display:block; }}
  .hero-headline.upper .hw {{ text-transform:uppercase; }}
  .hero-headline .hw.accent {{ color:{accent}; }}
  /* Arabic must NOT get negative tracking (it breaks the connected script) and needs more
     leading; weight 800 keeps the lockup punch. */
  [dir="rtl"] .headline {{ letter-spacing:normal; line-height:1.2; }}
  [dir="rtl"] .hero-headline {{ letter-spacing:normal; line-height:1.12; gap:2px; }}
  /* 'lockup' — a DESIGNED graphic headline (brand DNA: extremely bold, internal gradient,
     heavy outline, 3D shadow; the headline itself becomes a brand icon). Outlined gradient
     text so it reads as a custom ad lockup, not plain text over a photo. */
  .lockup-headline {{
    display:flex; flex-direction:column; gap:2px; margin-bottom:28px;
    font-family:{font_head}; font-weight:800; line-height:.92; letter-spacing:-.02em;
  }}
  .lockup-headline.upper .lw {{ text-transform:uppercase; }}
  .lockup-headline .lw {{
    display:block;
    background:linear-gradient(180deg, #ffffff 0%, #d7dee7 100%);
    -webkit-background-clip:text; background-clip:text;
    -webkit-text-fill-color:transparent; color:transparent;
    -webkit-text-stroke:1.6px rgba(6,9,14,.5);
    /* dual shadow: a tight dark halo for legibility over a BUSY area + a soft depth shadow */
    filter:drop-shadow(0 3px 8px rgba(0,0,0,.9)) drop-shadow(0 10px 24px rgba(0,0,0,.5));
  }}
  .lockup-headline .lw.accent {{
    background:linear-gradient(180deg, {accent} 0%, {grad2} 100%);
    -webkit-background-clip:text; background-clip:text;
    -webkit-text-fill-color:transparent; color:transparent;
    -webkit-text-stroke:1.4px rgba(6,9,14,.42);
    filter:drop-shadow(0 3px 8px rgba(0,0,0,.9)) drop-shadow(0 10px 24px {accent}66);
  }}
  .lockup-flat {{ -webkit-text-stroke:1.3px rgba(6,9,14,.40); }}
  [dir="rtl"] .lockup-headline {{ letter-spacing:normal; line-height:1.08; gap:4px; }}
  /* MEASURED (nanocare, 2026-07-24): -webkit-text-stroke outlines every GLYPH separately, and
     Arabic glyphs JOIN — so a seam gets drawn across every letter joint ("folded pipes") and the
     outline traces internal contours at terminals. Same failure family as the negative tracking
     above: per-glyph typography on a connected script. The gradient fill + drop-shadows were
     measured clean, so RTL keeps the designed look and drops ONLY the stroke. */
  [dir="rtl"] .lockup-headline .lw,
  [dir="rtl"] .lockup-headline .lw.accent,
  [dir="rtl"] .lockup-flat {{ -webkit-text-stroke:0; }}
  /* 'highlight' treatment — one word knocked out inside a clean brand-colored block.
     No tilt: a rotated sticker read amateurish over a photo (MEASURED on ITI). */
  .hl-head {{ line-height:1.32; }}
  .hl-head .hl {{
    display:inline-block; background:{accent}; color:{cta_text};
    padding:.06em .26em; border-radius:6px; margin:0 .04em;
    box-shadow:0 3px 14px rgba(0,0,0,.30);
  }}
  .sub {{
    font-family:{font_body}; font-size:24px; line-height:1.42;
    color:{copy_ink}; margin-bottom:30px; max-width:780px;
  }}
  .offerings {{ list-style:none; display:flex; flex-wrap:wrap; gap:12px; margin-bottom:34px; }}
  .lower.align-center .offerings {{ justify-content:center; }}
  .offerings li {{
    font-family:{font_body}; font-size:19px; font-weight:500; padding:10px 18px;
    background:{chip_bg}; border:{chip_border};
    border-radius:999px; color:{copy_ink}; backdrop-filter:blur(2px);
  }}
  /* A confident, high-contrast CTA button (art-critic: the pill was a tiny footnote). */
  /* CTA: a solid high-contrast button — a light hairline border + strong dark shadow so it
     pops on ANY background (QA: the chip read low-contrast over a busy scene). */
  {cta_css}
  .contact {{ font-family:{font_body}; font-size:18px; color:#cdd6df; margin-top:22px; }}
  .socials {{ font-family:{font_body}; font-size:18px; color:#aeb8c4; margin-top:12px; letter-spacing:.02em; }}
  /* layout-driven: text-block placement/scrim + brand-mark corner (per design spec) */
  {lower_layout_css}
  {typography_css}
  {surface_css}
  .lower.align-right[dir="rtl"] {{ align-items:flex-start; }}
  .brand, .brand-logo {{ {logo_pos_css} }}
</style></head>
<body>
  <div class="canvas">
    {brand_html}
    <div class="lower{align_cls}" dir="{dir_attr}">
      {lower_inner}
    </div>
  </div>
</body></html>"""
