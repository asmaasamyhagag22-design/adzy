"""Universal technical-quality gate for scraped content images.

WHY (measured, not guessed)
---------------------------
The reel animates a brand's scraped `content_images`. But a brand's "content images" are
frequently NOT usable photos: tiny category thumbnails (MEASURED on Orange Egypt: 298x175 /
221x130 px — upscaling those to a 1080x1920 reel = a blurry "dumb frame"), wide banners with
baked text, or near-blank product-on-white graphics. The vision curator judges CONTENT ("is
this on-brand?" — and an Orange banner IS on-brand) but NOT technical USABILITY. So garbage
gets animated. This gate is the missing deterministic, UNIVERSAL technical filter (size /
aspect / blankness) — no vertical logic, no brand-specific hacks.

CALIBRATION (measured across good- and bad-photo brands, before choosing thresholds)
-----------------------------------------------------------------------------------
    elkbabgi (real photos 960-2000px)  -> KEEP 12/12
    digilians (1000-1024px)            -> KEEP 4/5  (the 1 reject is a Facebook tracking pixel)
    Orange Egypt (298x175 thumbnails)  -> KEEP 0/12 (-> the reel routes to generated b-roll)
The dominant, well-separated signal is short-side resolution (Orange max 237px vs elkbabgi
min 800px); ratio/blankness are safety nets for a large-but-useless banner.

Pure/deterministic for `assess_photo`; `filter_usable_photos` fetches (SSRF-guarded, injectable)
and never raises.
"""
from __future__ import annotations

import io
from typing import Callable, Optional

# Thresholds — MEASURED/calibrated (see module docstring). Universal, not vertical.
_MIN_SHORT_SIDE = 500       # px: below this, upscaling to 1080x1920 visibly blurs
_MAX_RATIO = 2.6            # max long/short: above this it's a banner strip, not a photo
_MAX_WHITE_PCT = 88.0       # % near-white pixels: above this it's blank / product-on-white
_LUM_LO, _LUM_HI = 12.0, 244.0   # mean luminance: outside this it's near-black / near-white


def assess_photo(
    width: int, height: int,
    mean_lum: Optional[float] = None, white_pct: Optional[float] = None,
) -> tuple[bool, str]:
    """Pure: is this image a USABLE reel/poster background photo? -> (ok, reason).

    Only `width`/`height` are required (the dominant signal); `mean_lum`/`white_pct` are
    optional safety nets for a large-but-blank graphic."""
    if not width or not height or width <= 0 or height <= 0:
        return False, "no_dimensions"
    short = min(width, height)
    ratio = max(width, height) / short
    if short < _MIN_SHORT_SIDE:
        return False, f"too_small({short}px)"
    if ratio > _MAX_RATIO:
        return False, f"banner_ratio({ratio:.1f})"
    if white_pct is not None and white_pct > _MAX_WHITE_PCT:
        return False, f"near_blank({white_pct:.0f}%_white)"
    if mean_lum is not None and (mean_lum < _LUM_LO or mean_lum > _LUM_HI):
        return False, f"extreme_lum({mean_lum:.0f})"
    return True, "ok"


def measure_bytes(data: bytes) -> Optional[tuple[int, int, float, float]]:
    """(width, height, mean_lum, white_pct) from image bytes; None if undecodable.
    Luminance stats are taken from a tiny thumbnail so this is fast on large images."""
    if not data:
        return None
    try:
        from PIL import Image, ImageStat
        im = Image.open(io.BytesIO(data))
        im.load()
        w, h = im.size
        small = im.convert("L").resize((96, 96))
        mean = ImageStat.Stat(small).mean[0]
        px = list(small.getdata())
        white = sum(1 for v in px if v >= 238) / len(px) * 100.0
        return int(w), int(h), float(mean), float(white)
    except Exception:
        return None


def is_collage_bytes(data: bytes) -> bool:
    """FIX-D2 deterministic screen: does this image look like a LOGO GRID / partner collage
    (many items separated by white gutters on a light ground)? The vision curator bans these
    by CONTENT; this is the fail-closed net for when no caller is available (measured: the
    nti reel zoomed a 1000x2252 partner logo-wall because the curator was a no-op). Signal:
    a light image whose 96x96 grayscale shows >=3 alternating white/non-white ROW bands and
    >=2 column bands — full-width white gutters that real photographs essentially never have.
    Pure; False on any decode failure (never blocks a real photo by accident)."""
    if not data:
        return False
    try:
        from PIL import Image
        small = Image.open(io.BytesIO(data)).convert("L").resize((96, 96))
        px = list(small.getdata())
        white_pct = sum(1 for v in px if v >= 235) / len(px) * 100.0
        if white_pct < 45.0:
            return False

        def bands(line_means: list[float]) -> int:
            """Count alternating non-white bands separated by near-white gutters."""
            n = 0
            in_band = False
            for m in line_means:
                if m < 225 and not in_band:
                    n, in_band = n + 1, True
                elif m >= 235 and in_band:
                    in_band = False
            return n

        rows = [sum(px[r * 96:(r + 1) * 96]) / 96.0 for r in range(96)]
        cols = [sum(px[r * 96 + c] for r in range(96)) / 96.0 for c in range(96)]
        return bands(rows) >= 3 and bands(cols) >= 2
    except Exception:  # noqa: BLE001
        return False


def is_qr_code_bytes(data: bytes) -> bool:
    """P-90 deterministic screen: does this image look like a QR CODE / dense B&W matrix barcode?
    (the owner's "زوّم على QR code" bug — animated because the no-caller fallback only screened
    collages). Signal: near-SQUARE, strongly BIMODAL black/white (little midtone), with a high
    density of black<->white transitions along BOTH axes — the module grid a real photograph never
    has. Pure; False on any decode failure (never blocks a real photo). Conservative thresholds:
    a real photo is not bimodal, and a logo/wordmark has far fewer per-row transitions."""
    if not data:
        return False
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("L")
        w, h = im.size
        if not w or not h:
            return False
        if max(w, h) / min(w, h) > 1.35:               # QR/matrix codes are essentially square
            return False
        g = 64
        px = list(im.resize((g, g)).getdata())
        n = len(px)
        dark = sum(1 for v in px if v < 80)
        light = sum(1 for v in px if v > 200)
        # bimodal: nearly every pixel near-black or near-white, with a real share of dark modules
        if (dark + light) / n < 0.80 or not (0.15 <= dark / n <= 0.75):
            return False

        def transitions(line) -> int:
            b = [0 if v < 128 else 1 for v in line]
            return sum(1 for i in range(1, len(b)) if b[i] != b[i - 1])

        rows = [px[r * g:(r + 1) * g] for r in range(g)]
        cols = [[px[r * g + c] for r in range(g)] for c in range(g)]
        avg_row = sum(transitions(r) for r in rows) / g
        avg_col = sum(transitions(c) for c in cols) / g
        return avg_row >= 8.0 and avg_col >= 8.0        # a dense module grid on both axes
    except Exception:  # noqa: BLE001
        return False


_LETTERBOX_LUM = 24      # MEASURED (poster.pipeline): letterbox bars mean ~15 (max 17); real content >=31


def trim_letterbox_png(path: str) -> str:
    """P-38: crop near-black letterbox bars (top/bottom) from a rendered image IN PLACE — the reel's
    counterpart to poster.pipeline._trim_letterbox (reel had none, so an end-card band could ship).
    ROW-MEAN detector (robust to stray bright pixels) at the same calibrated threshold. Guarded so a
    legitimately dark frame is never gutted (keeps >=30% of the height). No-op/safe on any failure."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        W, H = im.size
        small = im.convert("L").resize((16, 320))      # 320-row vertical resolution, fast
        sw, sh = small.size
        px = small.load()

        def row_mean(y: int) -> float:
            return sum(px[x, y] for x in range(sw)) / sw

        top = 0
        while top < sh and row_mean(top) < _LETTERBOX_LUM:
            top += 1
        bot = sh - 1
        while bot > top and row_mean(bot) < _LETTERBOX_LUM:
            bot -= 1
        if top == 0 and bot == sh - 1:
            return path                                 # no bars
        y0, y1 = round(top / sh * H), round((bot + 1) / sh * H)
        if (y1 - y0) < 0.30 * H:                        # guard: don't gut a legitimately dark frame
            return path
        im.crop((0, y0, W, y1)).save(path)
    except Exception:  # noqa: BLE001
        pass
    return path


def _default_fetch(url: str) -> Optional[bytes]:
    """SSRF-guarded http(s) fetch (same discipline as the poster/reel logo fetch)."""
    try:
        from scraper.url_utils import is_safe_public_url
        if not is_safe_public_url(url):
            return None
        import urllib.request
        hdr = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=15) as r:
            return r.read()
    except Exception:
        return None


def original_variant(url: str) -> Optional[str]:
    """The CDN ORIGINAL behind a thumbnail URL, when the URL itself says it is resized —
    size params in the query (?width=100, &height=330, ?w=, ?size=). MEASURED (topshoes,
    2026-07-12): the scraper stored Shopify `?width=100/330` thumbnails, this gate dropped
    all 10 real product photos, and the poster painted an INVENTED sneaker; the same URL
    without the param returns the 1024px original. None when nothing to strip."""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(url)
        if not parts.query:
            return None
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if k.lower() not in ("width", "height", "w", "h", "size")]
        if len(kept) == len(pairs):
            return None                                # no size param present
        return urlunsplit(parts._replace(query=urlencode(kept)))
    except Exception:  # noqa: BLE001
        return None


def filter_usable_photos(
    urls, *, fetch: Optional[Callable[[str], Optional[bytes]]] = None, max_keep: int = 12,
) -> list[str]:
    """Keep only the URLs that resolve to USABLE photos (order preserved). UNIVERSAL, never
    raises. `fetch(url) -> bytes|None` is injectable (default: SSRF-guarded http) so tests stay
    hermetic. An undecodable / unreachable / failing-gate image is silently dropped. A photo
    rejected as TOO SMALL whose URL carries a CDN size param is retried at the param-stripped
    ORIGINAL and, if that passes, the UPGRADED URL is kept (the T-7 thumbnail-site fix)."""
    fetch = fetch or _default_fetch

    def _gate(data: Optional[bytes]) -> tuple[bool, str]:
        m = measure_bytes(data)
        if not m:
            return False, "undecodable"
        ok, why = assess_photo(*m)
        if ok and data is not None and is_collage_bytes(data):
            return False, "collage"                    # FIX-D2: logo grids never animate
        return ok, why

    out: list[str] = []
    for u in (urls or []):
        if not isinstance(u, str) or not u.startswith(("http://", "https://")):
            continue
        ok, why = _gate(fetch(u))
        if not ok and why.startswith("too_small"):
            big = original_variant(u)
            if big:
                ok, _why2 = _gate(fetch(big))
                if ok:
                    u = big                            # keep the ORIGINAL, not the thumbnail
        if ok:
            out.append(u)
        if len(out) >= max_keep:
            break
    return out


def white_key_logo(png_bytes: bytes, *, tol: int = 18) -> bytes:
    """OWNER RULING (2026-07-20 "شيل خالص مش تطفية"): TRUE logo isolation. A JPEG/white-matted
    logo has alpha=255 everywhere, so the transparent-margin trim never fires and a WHITE CARD
    lands on the poster/endcard. This keys the BACKGROUND to alpha-0 via FLOOD-FILL FROM THE
    BORDERS — full removal, never a semi-transparent wash:

      - only the near-white region CONNECTED TO THE EDGES is background (a JPEG's anti-alias
        halo joins it and is removed too);
      - interior whites (a letter's eye, deliberate white inside the mark) are NOT connected
        to the border, so they SURVIVE opaque — a global key would hollow the logo out;
      - guarded: a logo with real transparency returns BYTE-STABLE; a colored card whose
        border is not near-white returns byte-stable (there the white may BE the mark).

    Deterministic, PIL-only BFS — logo-sized inputs (~200px) stay well under a millisecond
    budget. Hard alpha edge on purpose: at composite sizes it reads clean, and "fully gone"
    beats "softly dimmed" (the ramp this replaces produced the غسالة الرمادي the owner saw)."""
    import io as _io
    from collections import deque as _dq

    from PIL import Image

    try:
        img = Image.open(_io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:  # noqa: BLE001 — not an image we can read; never explode the pipeline
        return png_bytes
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 250:
        return png_bytes                    # real transparency exists -> not ours to touch
    w, h = img.size
    px = img.load()
    lo = 255 - tol

    def _bg(x: int, y: int) -> bool:
        r, g, b, _a = px[x, y]
        return r >= lo and g >= lo and b >= lo

    border = ([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
              + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
    if sum(1 for x, y in border if _bg(x, y)) < 0.9 * len(border):
        return png_bytes                    # border isn't a white card -> the white may BE the mark

    seen = bytearray(w * h)
    queue = _dq((x, y) for x, y in border if _bg(x, y))
    for x, y in queue:
        seen[y * w + x] = 1
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and _bg(nx, ny):
                seen[ny * w + nx] = 1
                queue.append((nx, ny))

    out = img.copy()
    opx = out.load()
    for y in range(h):
        row = y * w
        for x in range(w):
            if seen[row + x]:
                r, g, b, _a = opx[x, y]
                opx[x, y] = (r, g, b, 0)    # FULL removal — the owner's "شيل خالص"
    buf = _io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
