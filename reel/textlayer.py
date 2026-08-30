"""Render the reel's TEXT layer as transparent PNGs via headless Chromium.

This reuses the poster's proven approach: the browser shapes Arabic correctly
(connected, RTL) where the bundled ffmpeg/libass does NOT. We render one
transparent 1080x1920 PNG per scene (logo + verbatim text, brand-styled) and the
compositor overlays each on its scene clip with a fade.

ZERO HALLUCINATION: every word comes verbatim from the storyboard (which came from
the profile). The logo is the brand's own scraped logo, fetched SSRF-guarded.
"""
from __future__ import annotations

import base64
import html as _html
import re
from pathlib import Path
from typing import Optional

from .schemas import ReelScene, Storyboard
# Reuse the poster's PURE color helpers so the reel's accent matches the poster's
# (vivid, from the real palette). No coupling to PosterBrief — these take rgb/hex.
from poster.template import (
    _hex_to_rgb, _luminance, _saturation, _legible_on_dark, _readable_on,
)

# Google Fonts: Cairo covers Arabic (so RTL text shapes), Space Grotesk + Inter
# give a modern Latin display/body. Arabic chars fall through to Cairo automatically.
_FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Cairo:wght@400;600;700;900&'
    'family=Oswald:wght@500;600;700&'
    'family=Space+Grotesk:wght@500;700&'
    # Elegant display faces for the luxury/premium tone theme (Latin serif + an Arabic
    # display serif so RTL brands read refined too, not a chunky Oswald condensed).
    'family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&'
    'family=Cormorant+Garamond:wght@500;600;700&'
    'family=Amiri:wght@400;700&'
    'family=Inter:wght@400;600&display=swap" rel="stylesheet">'
)

# A luxury/premium/fashion tone renders captions in a refined display SERIF instead of the
# chunky Oswald uppercase (owner: the jewelry reel's text read "سخيفة").
_ELEGANT_TONES = ("luxury", "premium", "elegant", "refined", "sophisticat", "boutique", "couture")


def _is_elegant_tone(tone: Optional[str]) -> bool:
    return any(k in (tone or "").lower() for k in _ELEGANT_TONES)


# Logo URLs whose mark is dark AND coloured: the artwork is kept and the renderer seats it on a
# light plate. A module-level set rather than a return value because `_logo_data_uri` is called
# from two places that both only want the URI — threading a tuple through them would change two
# signatures to carry one bit.
_PLATE_LOGOS: set = set()


def _knockout_if_dark(data: bytes) -> tuple[bytes, bool]:
    """(logo_bytes, needs_plate). The broadcast watermark rule, taught to see colour.

    FIX (topshoes reel frame): a DARK crest is invisible on dark footage, so it became a
    WHITE-KNOCKOUT silhouette. Correct for a monochrome mark — and destructive for a coloured
    one.

    OWNER, 2026-08-02, on the Al Fouad reel: «الفواتئد اللجو اتسحب منه الالوان». Measured: that
    logo is a RED heart with BLACK wordmark, mean luminance 0.32 — under the old threshold — so
    every frame of every reel carried a flat white silhouette where the brand's own colours
    should have been. The poster showed it correctly, which is how the loss was visible at all.

    A mark is knocked out only when it is dark AND essentially colourless. A dark mark that
    CARRIES BRAND COLOUR keeps its artwork, and the caller is told to seat it on a light plate
    instead — readability bought with a background, not with the brand's identity.
    """
    try:
        import io as _io
        from PIL import Image
        im = Image.open(_io.BytesIO(data)).convert("RGBA")
        px = [q for q in im.getdata() if q[3] > 40]
        if not px:
            return data, False
        lum = sum((0.2126 * q[0] + 0.7152 * q[1] + 0.0722 * q[2]) / 255 for q in px) / len(px)
        if lum >= 0.5:
            return data, False
        # Chroma, measured the cheap way: how far the average pixel's channels spread. A greyscale
        # crest sits near 0; a red heart pulls it up. 0.10 of full range is a colour a viewer sees.
        chroma = sum((max(q[:3]) - min(q[:3])) / 255 for q in px) / len(px)
        if chroma >= 0.10:
            return data, True          # keep the colours; ask for a plate behind them
        white = Image.new("RGBA", im.size, (250, 250, 252, 0))
        white.putalpha(im.split()[3])
        buf = _io.BytesIO()
        white.save(buf, format="PNG")
        return buf.getvalue(), False
    except Exception:  # noqa: BLE001
        return data, False


def _logo_data_uri(url: Optional[str]) -> Optional[str]:
    """SSRF-guarded fetch of the brand logo -> data URI for inlining. None on any
    failure or non-raster source (the reel renders fine without it)."""
    if not url or url.startswith("text-wordmark:"):
        return None
    if url.startswith("data:"):
        return url
    low = url.lower().split("?")[0]
    try:
        from scraper.url_utils import is_safe_public_url
        if not is_safe_public_url(url):
            return None
        from poster.template import _open_image_url
        data = _open_image_url(url, timeout=12).read()
        if not data:
            return None
        # SVG is allowed: Chromium renders it, and an <img>-referenced SVG runs in
        # secure static mode (no scripting). Recovers SVG-only brand logos.
        mime = "image/png"
        if low.endswith(".svg"):
            mime = "image/svg+xml"
        else:
            # البند 2: key a white-matted raster logo to alpha FIRST — this is the single
            # choke point feeding BOTH the endcard and the in-scene overlay, and it also
            # un-skews _logo_mean_luma (a white card used to read as a "light" logo).
            from .image_quality import white_key_logo
            keyed = white_key_logo(data)
            if keyed is not data:
                data, mime = keyed, "image/png"
            knocked, needs_plate = _knockout_if_dark(data)
            if needs_plate:
                # A dark COLOURED mark: the artwork survives and readability comes from a plate
                # behind it, not from erasing the brand. The flag rides on the data URI so the one
                # caller that builds the CSS can act on it without a second fetch.
                _PLATE_LOGOS.add(url or "")
            if knocked is not data:
                data, mime = knocked, "image/png"      # white variant re-encodes as PNG
            else:
                for ext, m in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                               (".webp", "image/webp"), (".png", "image/png")):
                    if ext in low:
                        mime = m
                        break
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except Exception:
        return None


def _accent(storyboard: Storyboard) -> str:
    """The brand accent: the MOST SATURATED palette color with enough luminance to read
    on the dark lower scrim (vivid, not a muted tan) — mirrors the poster's `_brand_accent`
    over `[primary_color] + palette_hex`. White only if the palette is empty."""
    pal = [
        str(c) for c in ([storyboard.primary_color] + list(storyboard.palette_hex or []))
        if c and str(c).startswith("#")
    ]
    legible = [c for c in pal if _luminance(_hex_to_rgb(c)) >= 0.16]
    cand = legible or pal
    if cand:
        return max(cand, key=lambda c: _saturation(_hex_to_rgb(c)))
    return "#FFFFFF"


def _esc(text: str) -> str:
    return _html.escape((text or "").strip())


# Phone / email / URL: inherently left-to-right. In an RTL reel these get
# bidi-mangled (digits/segments reorder) unless isolated as LTR.
_LTR_ATOM = re.compile(
    r"^\s*(\+?\d[\d\s\-()./]{4,}\d|[^\s@]+@[^\s@]+\.[^\s@]+|https?://\S+|www\.\S+)\s*$"
)


def _atom(text: str) -> str:
    """Escape, and isolate phone/email/URL atoms as LTR so they render correctly
    inside an RTL reel."""
    esc = _esc(text)
    if text and _LTR_ATOM.match(text):
        return f'<span dir="ltr" style="unicode-bidi:isolate;display:inline-block">{esc}</span>'
    return esc


# --- Kinetic entrance (frame-sequence) -------------------------------------
# Per-element animation descriptors, driven by the in-page __seek(t) JS so the
# Playwright capture is FRAME-PERFECT and deterministic (no reliance on the
# browser's wall-clock animation timeline). Easing is computed in JS; transforms
# touch ONLY opacity + translateY/scale, NEVER the text content/font — so Arabic
# glyph shaping/joining is identical in every frame (transform is compositing).
_ENTRANCE_S = 1.2                 # entrance window; the compositor holds the last frame after
_HEAD_ANIM = 'data-anim data-d="0" data-dur="0.26" data-dy="46" data-ease="back" data-sc="0.94"'
_LW_STAGGER = 0.07                # per-word delay for the hero lockup
_ITEM_BASE, _ITEM_STAGGER = 0.30, 0.15   # sublines: first delay + per-line stagger
_CTA_ANIM = 'data-anim data-d="0.55" data-dur="0.40" data-dy="44" data-ease="expo"'
_LOGO_ANIM = 'data-anim data-d="0.05" data-dur="0.40" data-dy="0" data-ease="expo"'


def _anim_attrs(delay: float, dur: float, dy: float, ease: str, sc: float = 1.0) -> str:
    return (f'data-anim data-d="{delay:.3f}" data-dur="{dur:.3f}" '
            f'data-dy="{dy:.0f}" data-ease="{ease}" data-sc="{sc:.3f}"')


def _highlight_last_word(headline: str) -> str:
    """Escape the headline and wrap its LAST token in the accent span (one accent word —
    the editorial 'pop'). Verbatim text, CSS-only emphasis (zero-hallucination)."""
    words = _esc(headline).split(" ")
    if len(words) > 1:
        return " ".join(words[:-1]) + f' <span class="hl">{words[-1]}</span>'
    return f'<span class="hl">{words[0]}</span>' if words and words[0] else ""


def _highlight_keyword(headline: str, *, keywords) -> str:
    """Accent the word that CARRIES the line — the product / hook word — not blindly the last one.

    Cheat-sheet #2: the whole line sits quiet and the one keyword pops in the brand colour. The
    old rule accented whatever token happened to come last, so «قشرة الشعر مش قدر محتوم» put its
    pop on «محتوم» while the subject of the ad sat unlit.

    Matching is WORD-START, never substring — "lab" matched inside "available" earlier today, the
    third hit of that class in one session — and prefix matching still reaches inflected forms
    («قشرة» opens «بالقشرة» after its clitics). Falls back to the last word, so every headline
    keeps exactly one accent.
    """
    import re as _re
    toks = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    words = (headline or "").split(" ")
    hit = -1
    for i, w in enumerate(words):
        stem = _re.sub(r"^[\W]+|[\W]+$", "", w.lower(), flags=_re.UNICODE)
        bare = _re.sub(r"^(?:ال|بال|لل|وال|فال|كال|و|ف|ب|ل)", "", stem)
        for k in toks:
            if stem.startswith(k) or bare.startswith(k) or k.startswith(stem) and len(stem) > 2:
                hit = i
                break
        if hit >= 0:
            break
    if hit < 0:
        return _highlight_last_word(headline)
    esc = [_esc(w) for w in words]
    esc[hit] = f'<span class="hl">{esc[hit]}</span>'
    return " ".join(esc)


def _split_hook(headline: str) -> tuple[list[str], str]:
    """Split a headline into a big HERO (the leading 1-3 words, up to ~18 chars) and a small
    SUPPORT line (the rest). A short headline (<=3 words) is ALL hero, no support. Verbatim
    words only — a subset, never reworded/reordered — so it's a pure visual hierarchy, not a
    content change (zero-hallucination)."""
    words = [w for w in (headline or "").split(" ") if w]
    if len(words) <= 3:
        return words, ""
    hero: list[str] = []
    chars = 0
    for w in words:
        nxt = chars + (1 if hero else 0) + len(w)
        if hero and (len(hero) >= 3 or nxt > 18):
            break
        hero.append(w)
        chars = nxt
    return hero, " ".join(words[len(hero):])


def hero_word_count(headline: str) -> int:
    """How many words land in the big HERO line (used to size it)."""
    return len(_split_hook(headline)[0])


def _lockup_headline(headline: str, word_delays=None) -> str:
    """A MINIMAL hero lockup: the lead 1-3 words ONLY (a reel carries LITTLE text), stacked and
    clean WHITE — no wordy support line, no coloured accent word that can clash with the footage.
    Verbatim words (a subset), CSS-only (none reordered/reworded → zero-hallucination)."""
    hero, _support = _split_hook(headline)
    hero = [_esc(w) for w in hero if w]
    if not hero:
        return ""
    # ZERO-LATENCY SYNC (cheat-sheet #2): when the narration's word timestamps are known, each
    # word pops at the instant it is SPOKEN; the fixed stagger is only the no-schedule fallback.
    delays = list(word_delays or [])
    def _d(i: int) -> float:
        return float(delays[i]) if i < len(delays) else i * _LW_STAGGER
    return "".join(
        f'<span class="lw" {_anim_attrs(_d(i), 0.34, 38, "expo")}>{w}</span>'
        for i, w in enumerate(hero)
    )


def _scene_html(scene: ReelScene, storyboard: Storyboard, width: int, height: int,
                logo_uri: Optional[str]) -> str:
    """Transparent text-layer HTML for one scene — a bottom-anchored caption with a strong
    legibility scrim (reads on ANY footage, incl. washed-out frames), a brand-accent SPINE
    that ties the whole cluster and gives it a designed identity, hero typography, and a
    clean structured treatment for list scenes (offerings/contact) instead of a plain dump."""
    rtl = storyboard.primary_dir == "rtl"
    dir_attr = "rtl" if rtl else "ltr"
    accent = _accent(storyboard)                       # vivid, from the real palette
    accent_rgb = _hex_to_rgb(accent)
    accent_on_dark = _legible_on_dark(accent_rgb)      # accent text/spine on the dark scrim
    chip_text = _readable_on(accent_rgb)               # text INSIDE the accent CTA chip
    # 2nd palette color for the lockup accent-word gradient (else solid accent) — poster parity
    _pal2 = [str(c) for c in ([storyboard.primary_color] + list(storyboard.palette_hex or []))
             if c and str(c).startswith("#") and str(c).lower() != accent.lower()]
    accent2 = _pal2[0] if _pal2 else accent

    elegant = _is_elegant_tone(storyboard.tone)
    lockup = scene.kind in ("intro", "outro")          # the hero scenes get a designed lockup
    if lockup and scene.headline:                      # size the HERO by its word count (huge)
        _nh = hero_word_count(scene.headline)
        _b = 0.205 if _nh <= 1 else 0.165 if _nh == 2 else 0.13
        head_px = round(width * (_b * 0.86 if elegant else _b))  # a serif is wider than Oswald
    else:
        short = len((scene.headline or "").split()) <= 3
        head_px = round(width * ((0.112 if short else 0.088) * (0.94 if elegant else 1.0)))
    if scene.headline:                                 # cap by the LONGEST word so it never overflows
        _lw = max((len(w) for w in scene.headline.split()), default=1)
        _cw = 0.74 if elegant else 0.60
        head_px = max(round(width * 0.06), min(head_px, int(width * 0.80 / max(1, _lw) / _cw)))
    item_px = round(width * 0.046)
    cta_px = round(width * 0.048)
    logo_h = round(width * 0.12)
    edge = "flex-end" if rtl else "flex-start"         # ragged edge falls toward center
    align = "right" if rtl else "left"
    spine = "border-right" if rtl else "border-left"   # accent spine on the leading edge
    spine_pad = "padding-right" if rtl else "padding-left"
    shadow = "0 2px 10px rgba(0,0,0,.9), 0 1px 3px rgba(0,0,0,.95)"
    # Negative letter-spacing BREAKS Arabic glyph joining (process.md) -> 0 for RTL.
    # LTR display type gets the tight tracking the brief asks for.
    head_track = "0" if rtl else "-0.5px"
    lock_track = "0" if rtl else "-1px"
    # Elegant (luxury) vs bold (default) type treatment — see _is_elegant_tone.
    head_family = "'Fraunces','Amiri','Cairo',serif" if elegant else "'Oswald','Cairo',system-ui,sans-serif"
    head_transform = "none" if elegant else "uppercase"
    head_weight = "600" if elegant else "700"
    head_style = "italic" if elegant else "normal"
    if elegant:
        head_track = "0.2px"
        lock_track = "0.3px" if rtl else "0.5px"
    lw_grad = ("linear-gradient(180deg,#fffdf6 0%,#efe4cf 100%)" if elegant
               else "linear-gradient(180deg,#ffffff 0%,#d7dee7 100%)")
    lw_stroke = "0.5px rgba(6,9,14,.30)" if elegant else "1.4px rgba(6,9,14,.72)"
    lw_shadow = ("drop-shadow(0 2px 10px rgba(0,0,0,.7)) drop-shadow(0 10px 28px rgba(0,0,0,.42))"
                 if elegant else
                 "drop-shadow(0 3px 8px rgba(0,0,0,.92)) drop-shadow(0 12px 26px rgba(0,0,0,.6))")
    acc_stroke = "0.4px rgba(6,9,14,.22)" if elegant else "1.2px rgba(6,9,14,.42)"
    acc_style = "italic" if elegant else "normal"

    # THE MARK STAYS ON SCREEN (owner, 2026-07-28: «عايزة لوجو البراند يبقى موجود طول الفيديو»).
    # It used to appear only on intro/outro/contact, so the middle of every reel — the part people
    # actually watch, and the part that gets clipped and reshared — carried no brand at all. A
    # persistent corner mark is ordinary broadcast practice; what changes between a bookend and a
    # body scene is PROMINENCE, not presence, so the body wears it quietly instead of shouting.
    show_logo = bool(logo_uri)
    logo_bug = show_logo and scene.kind not in ("intro", "outro", "contact")
    logo_pos = "right:64px" if rtl else "left:64px"
    # THE PLATE. A dark COLOURED mark keeps its artwork (see `_knockout_if_dark`) and buys its
    # readability from a soft light card behind it — the ordinary broadcast answer — instead of
    # being flattened to a white silhouette. Al Fouad's red heart is exactly this case.
    plate_css = ("background:rgba(255,255,255,.92);border-radius:14px;padding:12px 18px;"
                 "box-shadow:0 6px 22px -6px rgba(0,0,0,.45);"
                 if _PLATE_LOGOS else "")
    if logo_bug:
        # The quiet form: NOT re-animated on every cut — a mark that pops in six times reads as
        # six logos, not one brand. It rides the corner and stays put.
        #
        # SIZED BY LOOKING AT THE RENDER, not by taste. The first attempt used scale(.62) and
        # opacity(.72) and MEASURED on the live Nano Care reel it came out illegible — a few
        # grey pixels. "Present" has to mean readable or it is decoration pretending to be
        # branding. It also has to clear the clip's own letterboxing: at top:64px the mark landed
        # INSIDE the black bar baked into the footage, detached from the picture entirely.
        # FULL SIZE, NOT A SHRUNK ONE (owner, 2026-08-01: «اللوجو صغير جدا، انا عايز
        # بروفيشنال، واخد زاوية وباين معايا»). Two rounds of scaling it DOWN both came back
        # illegible on the render, which is the answer: a broadcast corner mark is not a small
        # logo, it is the logo, held quietly in a corner. So it keeps its full height and earns
        # its quietness from stillness and a touch of transparency instead of from size.
        logo_pos += ";top:150px;opacity:.92;transform-origin:" \
                    + ("right top" if rtl else "left top")
        logo_html = f'<div class="logo" style="{logo_pos}"><img src="{logo_uri}"></div>'
    else:
        logo_html = (f'<div class="logo" {_LOGO_ANIM} style="{logo_pos}{plate_css}">'
                     f'<img src="{logo_uri}"></div>' if show_logo else "")

    inner: list[str] = []
    if scene.headline:
        if lockup:                                     # each WORD carries its own kinetic beat
            inner.append(f'<div class="headline lockup">{_lockup_headline(scene.headline, getattr(scene, "caption_word_delays", None))}</div>')
        else:
            # Cheat-sheet #2: the accent goes on the word that CARRIES the line — the brand /
            # product token when the headline contains one — not blindly on the last word.
            _kw = [w for w in str(storyboard.business_name or "").split() if len(w) > 2]
            inner.append(f'<div class="headline" {_HEAD_ANIM}>'
                         f'{_highlight_keyword(scene.headline, keywords=_kw)}</div>')
    if scene.sublines:
        items = "".join(
            f'<div class="item" {_anim_attrs(_ITEM_BASE + i * _ITEM_STAGGER, 0.40, 26, "expo")}>'
            f'{_atom(s)}</div>'
            for i, s in enumerate(scene.sublines)
        )
        inner.append(f'<div class="items">{items}</div>')
    if scene.cta_text:
        inner.append(f'<div class="cta-wrap" {_CTA_ANIM}>'
                     f'<span class="cta">{_esc(scene.cta_text)}</span></div>')
    cluster = f'<div class="cluster">{"".join(inner)}</div>' if inner else ""

    return f"""<!doctype html><html dir="{dir_attr}" lang="ar"><head><meta charset="utf-8">
{_FONTS_LINK}
<style>
  html,body{{margin:0;padding:0;width:{width}px;height:{height}px;background:transparent;}}
  .stage{{position:relative;width:{width}px;height:{height}px;
    font-family:'Space Grotesk','Cairo',system-ui,sans-serif;color:#fff;overflow:hidden;
    --accent:{accent_on_dark};}}
  .scrim{{position:absolute;left:0;right:0;bottom:0;height:64%;
    background:linear-gradient(to top,
      rgba(6,9,14,.96) 0%, rgba(6,9,14,.92) 20%, rgba(6,9,14,.74) 42%,
      rgba(6,9,14,.34) 66%, rgba(6,9,14,0) 100%);}}
  .lower{{position:absolute;left:0;right:0;bottom:0;padding:0 84px 332px;box-sizing:border-box;
    display:flex;flex-direction:column;align-items:{edge};}}
  .cluster{{text-align:{align};max-width:86%;}}
  .cluster>*{{unicode-bidi:plaintext;}}
  .headline{{font-family:{head_family};font-weight:{head_weight};
    font-size:{head_px}px;line-height:1.04;letter-spacing:{head_track};text-transform:{head_transform};
    text-wrap:balance;text-shadow:{shadow};
    /* READABILITY ARMOR (research): a real black outline BEHIND the fill (paint-order) so heavy
       display captions survive ANY footage — fixes 'unreadable' plain white text. */
    -webkit-text-stroke:2px rgba(0,0,0,.92);paint-order:stroke fill;}}
  .headline .hl{{color:var(--accent);font-style:{head_style};font-size:1.2em;}}  /* cheat-sheet #2: the keyword is ~120% */
  /* designed LOCKUP (poster parity) for the hero scenes: stacked words, gradient fill +
     outline, the last word in the brand-accent gradient. */
  .headline.lockup{{display:flex;flex-direction:column;gap:2px;line-height:.98;
    letter-spacing:{lock_track};text-shadow:none;-webkit-text-stroke:0;}}
  .lockup .lw{{display:block;
    background:{lw_grad};
    -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;
    -webkit-text-stroke:{lw_stroke};
    filter:{lw_shadow};}}
  .lockup .lw.acc{{font-style:{acc_style};
    background:linear-gradient(180deg,{accent} 0%,{accent2} 100%);
    -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:transparent;
    -webkit-text-stroke:{acc_stroke};
    filter:drop-shadow(0 3px 8px rgba(0,0,0,.9));}}
  /* small SUPPORT line under the hero (hierarchy): readable, not uppercase, no gradient. */
  .lockup .hsub{{display:block;font-size:0.30em;font-family:'Inter','Cairo',system-ui,sans-serif;
    font-weight:600;letter-spacing:0;text-transform:none;line-height:1.2;margin-top:0.34em;
    color:#e9edf3;-webkit-text-fill-color:#e9edf3;-webkit-text-stroke:0;
    text-shadow:{shadow};filter:none;max-width:18ch;}}
  .items{{margin-top:4px;}}
  .item{{font-family:'Inter','Cairo',system-ui,sans-serif;font-weight:600;
    font-size:{item_px}px;line-height:1.5;margin:0.06em 0;letter-spacing:0.2px;
    text-shadow:{shadow};}}
  .cta-wrap{{margin-top:32px;}}
  .cta{{display:inline-block;font-family:'Space Grotesk','Cairo',sans-serif;font-weight:700;
    font-size:{cta_px}px;background:{accent};color:{chip_text};padding:0.54em 1.5em;
    border-radius:999px;letter-spacing:0.3px;box-shadow:0 10px 30px rgba(0,0,0,.55);}}
  .logo{{position:absolute;top:64px;filter:drop-shadow(0 2px 10px rgba(0,0,0,.65));}}
  .logo img{{height:{logo_h}px;max-width:42%;object-fit:contain;
    filter:drop-shadow(0 2px 14px rgba(0,0,0,.6));}}
  /* Kinetic entrance: every animated element starts hidden; __seek(t) drives it. */
  [data-anim]{{opacity:0;will-change:transform,opacity;}}
</style></head>
<body><div class="stage">{logo_html}<div class="scrim"></div><div class="lower">{cluster}</div></div>
<script>
(function(){{
  function _back(p){{var c1=1.70158,c3=c1+1;return 1+c3*Math.pow(p-1,3)+c1*Math.pow(p-1,2);}}
  function _cubic(p){{return 1-Math.pow(1-p,3);}}
  // ease-out-expo — the JS twin of cubic-bezier(0.16,1,0.3,1): fast snap in, long premium settle.
  function _expo(p){{return p>=1?1:1-Math.pow(2,-10*p);}}
  // Set every [data-anim] element's opacity + translateY/scale for global time t (seconds).
  // Transforms only — text content/shaping is untouched, so Arabic stays joined in every frame.
  window.__seek=function(t){{
    var els=document.querySelectorAll('[data-anim]');
    for(var i=0;i<els.length;i++){{
      var el=els[i],ds=el.dataset;
      var d=parseFloat(ds.d||'0'),dur=parseFloat(ds.dur||'0.4'),
          dy=parseFloat(ds.dy||'0'),sc=parseFloat(ds.sc||'1'),ease=ds.ease||'expo';
      var p=dur>0?(t-d)/dur:1; if(p<0){{p=0;}} if(p>1){{p=1;}}
      var e=(ease==='back')?_back(p):(ease==='expo')?_expo(p):_cubic(p);
      var op=(t-d)/(dur*0.6); if(op<0){{op=0;}} if(op>1){{op=1;}}
      var ty=dy*(1-e), s=sc+(1-sc)*e;
      el.style.opacity=op;
      el.style.transform='translateY('+ty.toFixed(2)+'px) scale('+s.toFixed(3)+')';
    }}
  }};
  window.__seek(0);
}})();
</script>
</body></html>"""


def _n_entrance_frames(duration_s: float, fps: int, entrance_s: float = _ENTRANCE_S) -> int:
    """How many frames to capture for a scene's kinetic entrance: the entrance window
    (clamped to the scene length), at the video fps. The compositor HOLDS the last frame
    for the remainder, so we never capture the static tail."""
    ent = min(entrance_s, max(0.2, duration_s))
    return max(1, round(ent * fps))


def render_text_layers(
    storyboard: Storyboard,
    *,
    width: int,
    height: int,
    out_dir: Path,
    include_logo: bool = True,
) -> list[Path]:
    """Render one transparent PNG per scene at the FINAL (held) state — the entrance
    fully resolved. Kept for static/preview use; the compositor uses the kinetic
    `render_text_sequences` instead. Returns the paths in scene order."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    logo_uri = _logo_data_uri(storyboard.logo_url) if include_logo else None
    paths: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for i, scene in enumerate(storyboard.scenes):
                page = browser.new_page(
                    viewport={"width": width, "height": height}, device_scale_factor=1,
                )
                page.set_content(
                    _scene_html(scene, storyboard, width, height, logo_uri),
                    wait_until="networkidle",
                )
                try:
                    page.evaluate("async () => { await document.fonts.ready; }")
                except Exception:
                    pass
                page.evaluate("(t) => window.__seek(t)", 99.0)   # resolve to the held final state
                out = out_dir / f"text{i}.png"
                page.screenshot(
                    path=str(out), omit_background=True,
                    clip={"x": 0, "y": 0, "width": width, "height": height},
                )
                page.close()
                paths.append(out)
        finally:
            browser.close()

    return paths


def render_text_sequences(
    storyboard: Storyboard,
    *,
    width: int,
    height: int,
    fps: int,
    out_dir: Path,
    entrance_s: float = _ENTRANCE_S,
    include_logo: bool = True,
) -> list[str]:
    """Render the KINETIC text overlay as a per-scene PNG SEQUENCE (one folder per scene,
    frames `f0000.png` ... at `fps`). Each frame is captured by seeking the in-page __seek(t)
    driver to an exact timestamp, so the per-element stagger (headline pop -> sublines ->
    CTA) is FRAME-PERFECT and deterministic. Returns one ffmpeg image2 PATTERN string per
    scene (e.g. `.../seq0/f%04d.png`), in scene order. The compositor feeds each as an input
    stream and holds the last frame for the rest of the scene.

    Transforms touch only opacity/translateY/scale — never the text — so Arabic shaping and
    RTL joining are identical across ALL frames (zero-hallucination: text stays verbatim)."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    logo_uri = _logo_data_uri(storyboard.logo_url) if include_logo else None
    patterns: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for i, scene in enumerate(storyboard.scenes):
                seq_dir = out_dir / f"seq{i}"
                seq_dir.mkdir(parents=True, exist_ok=True)
                page = browser.new_page(
                    viewport={"width": width, "height": height}, device_scale_factor=1,
                )
                page.set_content(
                    _scene_html(scene, storyboard, width, height, logo_uri),
                    wait_until="networkidle",
                )
                try:
                    page.evaluate("async () => { await document.fonts.ready; }")
                except Exception:
                    pass
                n = _n_entrance_frames(scene.duration_s, fps, entrance_s)
                clip = {"x": 0, "y": 0, "width": width, "height": height}
                for f in range(n):
                    page.evaluate("(t) => window.__seek(t)", f / float(fps))
                    page.screenshot(
                        path=str(seq_dir / f"f{f:04d}.png"),
                        omit_background=True, clip=clip,
                    )
                page.close()
                patterns.append(str(seq_dir / "f%04d.png"))
        finally:
            browser.close()

    return patterns
