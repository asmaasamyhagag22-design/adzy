"""Unified poster pipeline — ONE path shared by the CLI and the web app.

Per the build brief (Step A): the CLI and the API used to run different branches, so the
web app shipped old behaviour. Everything now flows through `generate_poster()`:

  profile
    -> load-or-build BrandCreativeDNA (the brand's learned visual language)
    -> build_creative_concept            (ONE message; Arabic copy; chips = proof_points)
    -> build_poster_brief + override copy from the concept
    -> build_design_spec                 (free-form layout, DNA-steered)
    -> build_llm_concept_prompt          (image in the DNA language, scene = concept.visual_idea)
    -> ADAPTIVE brand-anchored background: OUTPAINT a real scraped photo -> STYLE on the
       brand's real creatives -> text-to-image (Ultra->4.0) fallback  [or stub when no_image]
    -> render_poster_html -> PNG
    -> Vision QA gate                    (reject + regenerate on fail; bounded retries)

Two truth domains: FACTS stay grounded (profile-derived; copy is brand-voice paraphrase of
real facts — the design domain). DESIGN (layout / scene / language) is the creative domain.
Never raises for ordinary failures; degrades to deterministic fallbacks.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from poster.concept import CreativeConcept, brand_is_arabic, build_creative_concept
from poster.from_profile import build_poster_brief
from poster.schemas import PosterBrief, PosterDesignSpec
from poster.template import render_poster_html
from poster.render_playwright import render_html_to_png
from poster.variation import build_variation
from poster.vision_qa import PosterQAVerdict, poster_vision_qa

_FALLBACK_MODEL = "imagen-4.0-generate-001"


def _safe_log(msg: object) -> None:
    """Print pipeline progress WITHOUT ever crashing on a non-UTF-8 console (Windows cp1252
    raises on Arabic) — which previously surfaced as an HTTP 500 from the web app."""
    import sys
    try:
        print(msg)
    except Exception:
        try:
            sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", "replace"))
        except Exception:
            pass


@dataclass
class PosterGenResult:
    poster_path: str
    filename: str
    image_base64: str
    brief: PosterBrief
    spec: PosterDesignSpec
    concept: CreativeConcept
    brand_dna: Any
    background_path: str
    model_used: str
    prompt: str
    qa: PosterQAVerdict
    width: int = 1080
    height: int = 1350
    audit: Optional[dict] = None   # brand-safety trail (claim->source + gate remediation)
    engine: str = "classic"        # "classic" (bg + HTML overlay) | "oneshot" (Gemini-designed)
    # Set ONLY when every attempt carried a grounding-class defect and the least-bad one is
    # being returned: an explicit top-level disclosure, because the qa verdict alone was
    # MEASURED being ignored downstream (nanocare 2026-07-24: fabricated "Lifosemul Iron"
    # shipped silently). trust_note_ar is the client-facing Arabic line, ready to display.
    trust_violation: bool = False
    trust_note_ar: str = ""


def _fv(profile: dict, key: str) -> str:
    v = (profile or {}).get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return "" if v is None else str(v)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "brand").lower()).strip("_") or "brand"


def load_or_build_dna(profile: dict, caller: Any, *, cache_dir: str = "outputs/brandbooks") -> Any:
    """Build-or-load the BrandCreativeDNA: load the cached file for this brand, else build it
    once (Serper image search + Gemini vision) and cache. None on failure (pipeline proceeds
    with a generic — but still concept-driven, Arabic, safe — poster)."""
    try:
        from brand.creative_dna import build_creative_dna, load_creative_dna, save_creative_dna
    except Exception:
        return None
    path = Path(cache_dir) / f"{_slug(_fv(profile, 'name'))}_dna.json"
    if path.exists():
        try:
            return load_creative_dna(path)
        except Exception:
            pass
    if caller is None:
        return None
    try:
        dna = build_creative_dna(profile, caller=caller)
    except Exception:
        return None
    if getattr(dna, "used_vision", False):
        try:
            save_creative_dna(dna, path)
        except Exception:
            pass
    return dna


def _generate_imagen(prompt: str) -> tuple[str, str]:
    """Vertex Imagen Ultra -> lighter model on a capacity/quota (429). Returns (path, model)."""
    from poster.imagen_provider import VertexImagenProvider
    provider = VertexImagenProvider()
    try:
        return str(provider.generate(prompt)), provider.model
    except Exception:
        fb = VertexImagenProvider(model=_FALLBACK_MODEL)
        return str(fb.generate(prompt)), fb.model


def _maybe_upscale(path: str, *, log=_safe_log) -> str:
    """Upscale the generated background ~2x for crispness + sharper faces (owner: "الكوالتي
    محتاجة تتحسن"). Best-effort: returns the original path on any failure."""
    try:
        from poster.imagen_edit_provider import upscale_to_file
        out = upscale_to_file(path)
        if out and Path(out).exists():
            from PIL import Image
            log(f"[bg] upscaled -> {Image.open(out).size}")
        return out
    except Exception:
        return path


_LETTERBOX_LUM = 24      # MEASURED: the WE letterbox bars mean ~15 (max 17); real content >=31


def _trim_letterbox(path: str) -> str:
    """Crop near-black letterbox bars (top/bottom) from a generated background so it fills the
    poster canvas edge-to-edge. The STYLE / text-to-image model sometimes renders a LANDSCAPE
    scene centered in the portrait frame with dark bars (MEASURED on WE: a 896x1280 frame, bars
    mean~15 / max 17 — NOT pure black, so a per-pixel bbox at 16 missed them). Uses a ROW-MEAN
    detector (robust to stray bright pixels) at a calibrated threshold. Re-saves in place;
    no-op/safe on any failure. (Top/bottom only — letterbox in a portrait canvas; a legit dark
    edge on the sides is left alone to avoid over-cropping.)"""
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
        if (y1 - y0) < 0.30 * H:                        # guard: don't gut a legitimately dark scene
            return path
        im.crop((0, y0, W, y1)).save(path)
    except Exception:
        pass
    return path


def _best_usable_photo(profile: dict, *, fetch=None) -> Optional[str]:
    """The brand's first TECHNICALLY-usable scraped photo (for OUTPAINT), or None. Reuses the
    REAL reel.image_quality gate (size/ratio/blankness); max_keep=1 stops at the first hit."""
    urls = ((profile or {}).get("visual") or {}).get("content_images") or []
    if not urls:
        return None
    try:
        from reel.image_quality import filter_usable_photos
        keep = filter_usable_photos(urls, fetch=fetch, max_keep=1)
    except Exception:
        return None
    return keep[0] if keep else None


def _brand_style_refs(profile: dict, brand_dna: Any, *, limit: int = 2) -> list[str]:
    """Real brand creatives to condition a STYLE generation on (the universal floor): the
    brand's harvested REAL ads first (BrandCreativeDNA.references_seen), then its own scraped
    site photos. Capped at `limit` (Vertex: non-square signatures accept at most 2 refs)."""
    refs: list[str] = []
    seen: set[str] = set()
    for src in (getattr(brand_dna, "references_seen", None) or []):
        if isinstance(src, str) and src.startswith(("http://", "https://")) and src not in seen:
            refs.append(src); seen.add(src)
    for src in (((profile or {}).get("visual") or {}).get("content_images") or []):
        if isinstance(src, str) and src.startswith(("http://", "https://")) and src not in seen:
            refs.append(src); seen.add(src)
    return refs[:limit]


def _generate_nano_background(
    profile: dict, brand_dna: Any, prompt: str, *,
    out_dir: str = "outputs/posters", log=_safe_log,
    product_images: Optional[list] = None,
    identity_ref: Optional[tuple] = None,
) -> tuple[str, str]:
    """Text-free background via nano-banana (Gemini image), style-conditioned on the brand's REAL
    creatives. This is the classic overlay's image source now that every Vertex Imagen id 404s on
    the project — nano-banana renders only the SCENE, and the HTML overlay adds the gate-verified
    copy on top, so garbled in-image text (esp. Arabic) is never a risk here. Raises on failure so
    the caller can fall back.

    `product_images` — THE REAL ITEM, and the reason this parameter had to exist. Every
    `product_images` call site in this file used to sit inside `_try_oneshot`, so the classic path
    — now the default for every brand, and the only path for Arabic — asked the model for a scene
    containing a product it had never been shown. It invented one, every time, and the pipeline's
    own critic said so in those words: "clearly fake, AI-generated products with garbled text",
    "a fabricated product with nonsensical, garbled text on its label".

    The owner picked this model precisely because it preserves a reference: «عايزه المنتج يجي ذي
    ما هو بالظبط ع الويب سايت دي مفيهاش لعب … علشان كدا بستعمل نانو باننا لانه مميز ف كدا». It
    cannot preserve what it was never given.

    THE PRODUCT LEADS THE LIST. `poster/oneshot.py` caps attachments at two, so a brand style
    swatch ahead of the real item would silently push it out."""
    import poster.oneshot as oneshot
    refs: list[tuple[bytes, str]] = []
    picked: list[tuple[bytes, str]] = []
    for u in (product_images or []):
        data, mime = _image_bytes_from_url(u)
        if data:
            picked.append((data, mime))
    for u in _brand_style_refs(profile, brand_dna, limit=2):
        data, mime = _image_bytes_from_url(u)
        if data:
            refs.append((data, mime))
    refs = picked + refs
    if picked:
        log(f"[bg] nano-banana REAL PRODUCT ({len(picked)}) + style ({len(refs) - len(picked)})")
    elif refs:
        log(f"[bg] nano-banana STYLE ({len(refs)} real brand refs)")
    bg_prompt = (
        prompt.strip()
        + "\n\nCRITICAL: produce a BACKGROUND IMAGE ONLY — scene, texture, lighting, composition. "
        "Render NO text, letters, words, numbers, logos, captions or UI of any kind. Keep calm "
        "negative space where copy will be overlaid later. Photographic, premium, on-brand.")
    if picked:
        # «دي مفيهاش لعب» — the attachment is THE product, not a mood reference. Without saying so
        # the model treats a reference as inspiration and redraws the item, which is how a real
        # pharmacy's packaging came back as a fabricated bottle with a nonsense label.
        bg_prompt += (
            "\n\nATTACHED PRODUCT — REPRODUCE IT EXACTLY. The first attached image is the REAL "
            "product being advertised. Place it in the scene UNCHANGED: identical shape, "
            "proportions, colours, packaging, cap, and every label element exactly as attached. "
            "Do NOT redesign, restyle, relabel, recolour or 'improve' it, and do not invent any "
            "additional product. Light it to match the scene; change nothing else about it.")
    if identity_ref and identity_ref[0]:
        # THE CHOSEN FACE (owner, 2026-07-26: «الوش والشعر الكيرلي اللي اخترته اتغيروا — عايزة
        # الصورة اللي اخترتها تطلع بالظبط»). Same lesson as the product attachment one paragraph
        # up: an unexplained reference is treated as inspiration and the model draws a different,
        # prettier person. The instruction has to say it IS the person.
        bg_prompt += (
            "\n\nATTACHED PERSON — THE SAME PERSON, UNCHANGED. One attached image is this "
            "brand's chosen presenter. Put HER/HIM in the scene with an IDENTICAL face: same "
            "facial features and proportions, same skin tone, same hair colour, length and "
            "curl pattern, same age. Only pose, wardrobe and lighting may change to fit the "
            "scene. Do NOT beautify, restyle the hair, or substitute a different model.")
    out_path = Path(out_dir) / f"bg_{uuid.uuid4().hex[:8]}.png"
    # Go straight to nano-banana (the model that serves) instead of the retired id .env pins; the
    # generator's model-chain still covers us if this id ever drifts.
    nano = oneshot._MODEL_CHAIN[0]   # "gemini-2.5-flash-image"
    oneshot.generate_oneshot_poster(bg_prompt, out_path, model=nano, logo_bytes=None,
                                    product_images=refs or None,
                                    identity_ref=identity_ref or None)
    return str(out_path), f"{nano}/bg"


def _generate_background(
    profile: dict, brand_dna: Any, prompt: str, *,
    log=_safe_log, fetch=None, edit_provider=None, t2i=None,
    product_images: Optional[list] = None,
    identity_ref: Optional[tuple] = None,
) -> tuple[str, str]:
    """ADAPTIVE brand-anchored background, safe cascade (never raises here).

    ROOT (2026-07-24): the classic overlay's background is generated by NANO-BANANA (Gemini image),
    not Imagen — every Vertex Imagen id (imagen-3.0-capability / imagen-4.0-*) now 404s on the
    project. nano-banana renders the SCENE; the HTML overlay adds the copy. The legacy Imagen
    cascade below stays only as a last-ditch and as the seam tests inject into (`t2i`/`edit_provider`).

    Legacy cascade order (when injected): STYLE-condition on the brand's real creatives ->
    OUTPAINT a real scraped photo -> plain text-to-image.
    Returns (background_path, model_label). `edit_provider`/`t2i`/`fetch` are injectable for tests."""
    # Production path: nano-banana background. Tests inject `t2i` to stay offline -> skip this.
    if t2i is None:
        try:
            return _generate_nano_background(profile, brand_dna, prompt, log=log,
                                             product_images=product_images,
                                             identity_ref=identity_ref)
        except Exception as e:  # noqa: BLE001 — fall through to the legacy cascade, never crash here
            log(f"[bg] nano-banana background failed ({type(e).__name__}: {e}); -> legacy Imagen")

    t2i = t2i or _generate_imagen
    _prov = {"p": edit_provider}

    def _edit():
        if _prov["p"] is None:
            from poster.imagen_edit_provider import ImagenEditProvider
            _prov["p"] = ImagenEditProvider()
        return _prov["p"]

    # 1) UNDERSTAND -> GENERATE (preferred): a FRESH on-brand scene STYLE-conditioned on the
    #    brand's real creatives (harvested ads first, then its own scraped photos) — a NEW
    #    invented image every run in the brand's visual world, NOT a reused literal photo.
    refs = _brand_style_refs(profile, brand_dna)
    if refs:
        try:
            p = _edit().style(prompt, refs)
            log(f"[bg] mode=STYLE ({len(refs)} real brand refs)")
            return str(p), "imagen-3.0-capability-001/style"
        except Exception as e:  # noqa: BLE001 — fall through, never crash
            log(f"[bg] style failed ({type(e).__name__}: {e}); -> outpaint")

    # 2) FALLBACK: OUTPAINT a usable REAL scraped photo (literal max-fidelity) only when there
    #    are NO style refs / STYLE failed — kept as a safety net, no longer the default.
    photo = _best_usable_photo(profile, fetch=fetch)
    if photo:
        try:
            p = _edit().outpaint(photo, "")
            log("[bg] mode=OUTPAINT (real scraped photo, fallback)")
            return str(p), "imagen-3.0-capability-001/outpaint"
        except Exception as e:  # noqa: BLE001
            log(f"[bg] outpaint failed ({type(e).__name__}: {e}); -> text-to-image")

    p, model = t2i(prompt)
    log("[bg] mode=text-to-image (last resort)")
    return str(p), model


def _dna_wants_lockup(brand_dna) -> bool:
    """True when the brand's learned typographic character is BOLD/GRAPHIC — i.e. its real ads
    treat the headline as a designed lockup (heavy weight / outline / gradient / 3D). Then the
    template renders a 'lockup' headline (the "real ad design, not photo+text" direction) rather
    than plain text. Read from the BrandCreativeDNA; harmless False when there's no DNA."""
    text = " ".join(str(getattr(brand_dna, f, "") or "")
                    for f in ("typographic_character", "signature_moves")).lower()
    return any(k in text for k in (
        "bold", "outline", "gradient", "lockup", "graphic art", "3d", "custom", "heavy", "extreme"))


def _logo_bytes(logo_url: Optional[str]) -> tuple[Optional[bytes], str]:
    """brief.logo_url -> (bytes, mime) or (None, 'image/png'). Handles an inlined
    data: URI directly and http(s) via the renderer's SSRF-guarded fetch."""
    if not logo_url:
        return None, "image/png"
    try:
        uri = logo_url
        if not uri.startswith("data:"):
            from poster.template import _remote_image_data_uri
            uri = _remote_image_data_uri(uri) or ""
        if uri.startswith("data:"):
            head, _, b64 = uri.partition(",")
            mime = head.split(";")[0][5:] or "image/png"
            return base64.b64decode(b64), mime
    except Exception:  # noqa: BLE001
        pass
    return None, "image/png"


# البند 1 (owner-caught "فشل وهو موجود"): the canonical <slug>_poster.png used to be written
# ONCE at the very end (poster/__main__), so the 900s wall left orphaned poster_<uuid>.png files
# and a 404 dashboard. Every successful render now also refreshes this sink IMMEDIATELY — a later
# timeout still leaves a served poster. Set per-run by generate_poster(canonical_out=...).
_CANONICAL_SINK: dict = {"path": None}


def _refresh_canonical(attempt_path, *, passed: bool = True) -> None:
    """Publish an attempt as THE poster.

    `passed=False` means the render has not cleared the verbatim-text / placeholder gates yet.
    OWNER-FACING FAILURE (Volume Qatar, 2026-07-21): every attempt was published the instant it was
    generated, so a render the gate REJECTED for garbled copy ("speciaity coffee coffee", "Mall of
    Maqur") overwrote a good poster on disk. An unverified render may still be published when there
    is NOTHING else — the sink's original purpose (البند 1: a later timeout must leave something
    served) — but it must never replace an existing deliverable.
    """
    dst = _CANONICAL_SINK.get("path")
    if not dst:
        return
    try:
        if not passed and Path(dst).is_file():
            return                     # a verified poster already stands — don't downgrade it
        import shutil as _sh
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        _sh.copyfile(attempt_path, dst)
    except Exception:  # noqa: BLE001 — the sink must never break a render
        pass


# A pixel counts as "ink" above this edge magnitude (0-255) — text and graphics clear it easily,
# photographic gradients do not.
_EDGE_INK_LEVEL = 60

# CALIBRATED on real Volume Qatar renders (2026-07-21) — % of a logo-sized box carrying ink:
#   headline clipping the corner .. 10.1   <- must move the logo
#   canonical render's overlap ....  6.6   <- must move the logo
#   live render, collision at the box EDGE .. 3.5   <- must move the logo (critic: "collides with
#                                                     the headline, creating 'Vyour'")
#   clean corner over a dark photo   2.6   <- must NOT move it
#   flat / empty corner .......... 0.0-2.0
# 3.0 sits in the measured gap. An earlier 4.0 let the 3.5 edge-collision through — a headline
# entering ANY part of the footprint ruins the mark, however small a share of the box it takes.
_CORNER_TEXT_FLOOR = 3.0
_CORNER_BUSY_RATIO = 1.6


def _corner_energy(poster, box) -> float:
    """How occupied `box` is: the PERCENTAGE of pixels carrying a hard edge (text/graphics).

    Was the MEAN edge energy, which a PARTIAL overlap dilutes into silence — measured on the real
    Volume Qatar poster where the headline clipped only the right third of the logo box: mean 22.9
    vs 7.3 for a clean corner, but the canonical render's own overlap read 15.8, sitting on top of
    the 12.0 floor. The ink FRACTION separates the same cases cleanly:

        headline clipping the corner .. 10.1%   canonical overlap .. 6.6%
        clean corner over a photo ..... 2.6%    flat/empty corner .. 0.0-1.5%

    so any real text intrusion is caught however small a share of the box it covers.
    """
    from PIL import ImageFilter
    region = poster.crop(box).convert("L").filter(ImageFilter.FIND_EDGES)
    px = list(region.getdata())
    if not px:
        return 0.0
    return 100.0 * sum(1 for v in px if v > _EDGE_INK_LEVEL) / len(px)


def _pick_logo_corner(poster, lw: int, lh: int, *, margin: int, prefer_left: bool) -> str:
    """Which corner the mark goes in: "tl" | "tr" | "bl" | "br".

    OWNER-FACING FAILURE (Volume Qatar, 2026-07-21): the corner came from reading direction alone,
    so when the image model set a full-width headline across the top the logo was stamped over it —
    the art critic's words: "the brand logo is garbled and overlaps with the headline, making it
    completely unusable". Choosing between the two TOP corners was not enough either: a full-width
    headline occupies BOTH. All four corners are measured, and the mark goes where there is
    genuinely room. The reading-side TOP corner still wins whenever it is clear, so a well-composed
    poster keeps its intended layout.
    """
    try:
        W, H = poster.size
        pad = max(8, margin // 3)
        spots = {
            "tl": (margin, margin), "tr": (W - margin - lw, margin),
            "bl": (margin, H - margin - lh), "br": (W - margin - lw, H - margin - lh),
        }
        energy = {}
        for k, (x, y) in spots.items():
            energy[k] = _corner_energy(poster, (max(0, x - pad), max(0, y - pad),
                                                min(W, x + lw + pad), min(H, y + lh + pad)))
        pref = "tl" if prefer_left else "tr"
        # Preferred corner is clear enough -> keep the design's intent.
        if energy[pref] < _CORNER_TEXT_FLOOR:
            return pref
        # Otherwise take the emptiest corner, preferring the top row on a tie so the mark still
        # reads as a logo rather than a footer.
        order = ["tl", "tr", "bl", "br"] if prefer_left else ["tr", "tl", "br", "bl"]
        return min(order, key=lambda k: (round(energy[k], 1), order.index(k)))
    except Exception:  # noqa: BLE001 — placement aid must never break compositing
        return "tl" if prefer_left else "tr"


def _overlay_real_logo(poster_path: Path, logo_bytes: bytes, *, rtl: bool,
                       xy: Optional[list] = None) -> bool:
    """Composite the brand's REAL logo asset onto the finished poster's top corner — deterministic
    and pixel-exact, so an Arabic logo is NEVER garbled by the image model (which re-draws it:
    "عزة فهمي" → "ةفهمص"). The one-shot engine reserves this corner clean, so the mark is placed
    DIRECTLY — NO plate/box behind it (owner: "شيل الخلفية البيضة") — with only a soft drop-shadow
    for legibility. `xy` (optional design [x,y]) only picks which side; else the reading side.
    Best-effort; never raises."""
    try:
        import io as _io

        from PIL import Image, ImageFilter

        poster = Image.open(poster_path).convert("RGBA")
        W, H = poster.size
        # البند 2 (owner-caught): a JPG/white-matted logo has alpha=255 everywhere, so the
        # transparent-margin trim below never fires and a WHITE CARD lands on the poster.
        # Key near-white to alpha first (guarded: transparent logos byte-stable, white MARKS
        # on colored cards untouched) — reel/textlayer does the same for endcard + in-scene.
        from reel.image_quality import white_key_logo
        logo_bytes = white_key_logo(logo_bytes)
        logo = Image.open(_io.BytesIO(logo_bytes)).convert("RGBA")
        bbox = logo.split()[3].getbbox()            # trim transparent margins
        if bbox:
            logo = logo.crop(bbox)
        tw = int(W * 0.22)
        scale = tw / max(1, logo.width)
        th = int(logo.height * scale)
        cap = int(H * 0.075)
        if th > cap:
            scale = cap / max(1, logo.height)
            tw, th = max(1, int(logo.width * scale)), cap
        logo = logo.resize((max(1, tw), max(1, th)), Image.LANCZOS)

        margin = int(W * 0.045)
        try:
            fx = float(xy[0]) if xy and len(xy) >= 2 else None
        except Exception:
            fx = None
        prefer_left = (not rtl) if fx is None else (fx < 0.5)
        # MEASURE the corner instead of trusting the layout: the model puts the headline where it
        # likes, and a logo stamped on top of it ruins the poster (owner-caught on Volume Qatar).
        corner = _pick_logo_corner(poster, logo.width, logo.height,
                                   margin=margin, prefer_left=prefer_left)
        left = corner in ("tl", "bl")
        try:  # disclose the decision — a logo on the headline must be diagnosable from the log
            _pad = max(8, margin // 3)
            _rx = W - margin - logo.width
            _le = _corner_energy(poster, (max(0, margin - _pad), max(0, margin - _pad),
                                          min(W, margin + logo.width + _pad),
                                          margin + logo.height + _pad))
            _re = _corner_energy(poster, (max(0, _rx - _pad), max(0, margin - _pad),
                                          min(W, _rx + logo.width + _pad),
                                          margin + logo.height + _pad))
            _safe_log(f"[logo] corner={corner} (left_energy={_le:.1f} right_energy={_re:.1f})")
        except Exception:  # noqa: BLE001
            pass
        px = margin if left else (W - margin - logo.width)
        py = margin if corner in ("tl", "tr") else (H - margin - logo.height)

        # FIX-C1b (measured on topshoes: a dark crest on the dark navy header = INVISIBLE
        # logo): the classic path's adaptive contrast rule, ported. High contrast -> no
        # plate, shadow only (unchanged). LOW contrast (|Δlum| < 0.28) -> a soft FROSTED
        # contrasting badge behind the mark (blurred glass + muted tint + rounded corners)
        # — never a hard white box (the owner's 2-month plate stays dead).
        try:
            from poster.template import _luminance
            opix = [p for p in logo.getdata() if p[3] > 40]
            logo_lum = (sum(_luminance(p[:3]) for p in opix) / len(opix)) if opix else None
            pad = 16
            bx0, by0 = max(0, px - pad), max(0, py - pad)
            bx1 = min(W, px + logo.width + pad)
            by1 = min(H, py + logo.height + pad)
            region = poster.crop((bx0, by0, bx1, by1)).convert("RGB")
            small = region.copy()
            small.thumbnail((40, 40))
            bpx = list(small.getdata())
            bg_lum = (sum(_luminance(p) for p in bpx) / len(bpx)) if bpx else None
            low_contrast_dark_logo = (logo_lum is not None and bg_lum is not None
                                      and abs(logo_lum - bg_lum) < 0.28 and logo_lum < 0.5)
        except Exception:  # noqa: BLE001 — legibility aid must never break compositing
            low_contrast_dark_logo = False
        if low_contrast_dark_logo:
            # NO rectangle, NO glow soup (owner rejected the grey badge; a halo dies on
            # thin ornate marks). The real-designer move: the WHITE KNOCKOUT variant —
            # the mark's exact silhouette filled near-white (what brand kits ship for
            # dark grounds). Crisp, premium, box-free; the normal soft shadow adds depth.
            knockout = Image.new("RGBA", logo.size, (250, 250, 252, 0))
            knockout.putalpha(logo.split()[3])
            logo = knockout

        # Soft drop-shadow ONLY (no plate): a blurred dark copy of the logo's OWN shape, so
        # the mark reads without a box around it (and gives the knockout variant depth).
        alpha = logo.split()[3]
        shadow = Image.new("RGBA", (logo.width + 44, logo.height + 44), (0, 0, 0, 0))
        tint = Image.new("RGBA", logo.size, (0, 0, 0, 165))
        shadow.paste(tint, (22, 26), mask=alpha)
        shadow = shadow.filter(ImageFilter.GaussianBlur(9))
        poster.alpha_composite(shadow, (px - 22, py - 22))
        poster.alpha_composite(logo, (px, py))

        poster.convert("RGB").save(poster_path)
        return True
    except Exception:  # noqa: BLE001 — the poster ships fine without the overlay
        return False


def _corner_placeholder_detected(poster_path, *, rtl: bool) -> bool:
    """FIX-C1 deterministic gate: True when the RAW one-shot render painted a literal
    placeholder 'slot' for the logo — a near-uniform rectangle in the reserved corner that
    sharply differs from the rest of the top band (measured on nti: a hard white panel,
    canvas-edge to y≈125, narrower than the composited logo). Tile-based: the top band
    (y 0..12%H) is split into 12 columns; a slot = a tile in the LOGO-side half that is
    near-uniform AND far from the band's median tile luminance. A legitimately light
    full-width header keeps a light median -> no false positive. Never raises."""
    try:
        from PIL import Image, ImageStat
        im = Image.open(poster_path).convert("L")
        W, H = im.size
        band_h = max(8, int(H * 0.12))
        n = 12
        tw, th = W // n, band_h // 2                 # 12 cols x 2 rows: a tile fits INSIDE a slot
        means, stds = [], []                          # index = row * n + col
        for r in range(2):
            for i in range(n):
                t = im.crop((i * tw, r * th, (i + 1) * tw, (r + 1) * th))
                st = ImageStat.Stat(t)
                means.append(st.mean[0])
                stds.append(st.stddev[0])
        median = sorted(means)[len(means) // 2]
        cols = range(n // 2, n) if rtl else range(0, n // 2)    # the logo-side half
        # 30 (was 48): the model learned to paint a SUBTLE grey-purple panel a few tones
        # off the navy (measured on topshoes — visible, ugly, under the old bar). The
        # band-median rule still protects legitimately light full-width headers.
        return any(stds[r * n + i] < 16 and abs(means[r * n + i] - median) > 30
                   for r in range(2) for i in cols)
    except Exception:  # noqa: BLE001 — the gate must never break generation
        return False


def _image_bytes_from_url(url: Optional[str]) -> tuple[Optional[bytes], str]:
    """Fetch ONE remote image via the renderer's SSRF-guarded fetch -> (bytes, mime)."""
    if not url:
        return None, "image/jpeg"
    try:
        from poster.template import _remote_image_data_uri
        uri = _remote_image_data_uri(url) or ""
        if uri.startswith("data:"):
            head, _, b64 = uri.partition(",")
            mime = head.split(";")[0][5:] or "image/jpeg"
            return base64.b64decode(b64), mime
    except Exception:  # noqa: BLE001
        pass
    return None, "image/jpeg"


# SERVICE categories whose content images are BUILDINGS / PEOPLE, not products — compositing them
# as scene "props" produces clutter + a hallucinated framed face (owner: the ITI poster). A
# whole-brand poster for these attaches NO props. Any other / unknown category keeps the prop path
# (product/food brands), and a PICKED product always attaches regardless of category.
_SERVICE_NO_PROP_CATS = {"clinic", "hospital", "education", "government", "agency",
                         "services_b2b", "services_b2c", "professional_services",
                         "real_estate", "fitness", "nonprofit", "automotive"}


def _profile_category(profile) -> str:
    cat = (profile or {}).get("category") if isinstance(profile, dict) else None
    if isinstance(cat, dict):
        cat = cat.get("value")
    return str(getattr(cat, "value", cat) or "").lower()


def _gather_product_props(profile, caller, log,
                          product_image: Optional[str] = None) -> tuple[list, list[str], str]:
    """The brand's REAL photos attached to the one-shot composite, with their ROLE (the
    owner's radical fix: a real asset composites correctly — like the logo — instead of the
    model inventing packaging or a stock office).

    role='prop'  — product/food brands: content images are the PRODUCTS, placed as props.
    role='place' — SERVICE brands (education/clinic/government/...): content images are the
    brand's REAL premises (post FIX-B they actually are: campus/facility photos, not junk),
    attached as the scene's PLACE. FIX-C2 replaces the old hard bail-out that attached
    NOTHING for services — the mechanism that condemned every service poster to stock.

    `product_image` (user-PICKED product) is always the primary prop regardless of category.
    Quality-gated (reel.image_quality), max 2, SSRF-guarded fetch. Each photo's REAL text is
    OCR'd so those lines become ALLOWED extras in the fidelity gate. ([], [], role) on any
    failure — never raises."""
    role = "prop"
    try:
        picked = [product_image] if product_image else []
        if not picked and _profile_category(profile) in _SERVICE_NO_PROP_CATS:
            role = "place"
        urls = list(((profile or {}).get("visual") or {}).get("content_images") or [])
        if picked:
            urls = [u for u in urls if u != product_image]     # de-dup; picked leads
        if not urls and not picked:
            return [], [], role
        from reel.image_quality import filter_usable_photos
        gated = filter_usable_photos(urls, max_keep=2) if urls else []
        keep = (picked + gated)[:2]                            # picked product first, then best other
        props: list = []
        allowed: list[str] = []
        import poster.oneshot as oneshot
        for u in keep[:2]:
            data, mime = _image_bytes_from_url(u)
            if not data:
                # البند 3 (owner-caught, nanocare): the PICKED product's photo failing to fetch
                # used to be a silent `continue` — an unrelated content image then became the
                # hero and the poster showed the WRONG product. No silent substitution: if the
                # user picked an image and it can't be fetched, attach NOTHING (feature by name
                # only — honest) rather than someone else's box.
                if product_image and u == product_image:
                    log("[oneshot] picked product image failed to fetch — attaching NO props "
                        "(never a substitute product)")
                    return [], [], role
                continue
            props.append((data, mime))
            if caller is not None:
                allowed += oneshot.read_rendered_text(data, caller)
        if props:
            log(f"[oneshot] {len(props)} real {role} photo(s) attached "
                f"({len(allowed)} real text lines allowed)")
        return props, allowed, role
    except Exception:  # noqa: BLE001
        return [], [], role


def _try_oneshot(profile, brief, concept, brand_dna, caller, *, arabic, audit,
                 out_dir, variation, max_retries, log,
                 qa_caller=None, product_image: Optional[str] = None,
                 identity_image: Optional[tuple] = None, identity_persona: str = "",
                 prompt_override: Optional[str] = None) -> Optional[PosterGenResult]:
    """ONE-SHOT engine: a Gemini image model composes the ENTIRE creative (layout +
    typography + graphics) in one call — the measured pilot (poster/oneshot.py:
    Arabic fidelity 5/5) earned it this pipeline mode; same idea as TrendPulse's
    static_post but with the REAL logo attached and a BLOCKING verbatim gate.

    The rendered text is the SAME Ledger-gated concept copy. A render that fails the
    character-exact OCR fidelity gate NEVER ships — bounded retries, then None so the
    caller falls back to the classic overlay pipeline. Needs a multimodal caller (the
    gate reads the render back) + a Vertex project; otherwise skipped."""
    import os
    if caller is None or not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        log("[oneshot] needs a vision caller + GOOGLE_CLOUD_PROJECT; skipping")
        return None
    try:
        import poster.oneshot as oneshot
        from poster.art_director import _palette_names
        from poster.template import default_design_spec
        from poster.variation import concept_variation_cue

        copy = {"headline": brief.headline or "", "subheadline": brief.subheadline or "",
                "cta": brief.cta_text or ""}
        expected = {k: v for k, v in copy.items() if v.strip()}
        # The FULL learned design language (was 3 fields / 400 chars — too thin for the
        # render to read as THIS brand): imagery + composition + typography character +
        # motifs + signature moves. Colour stays governed by the palette mandate.
        dna_lines = ""
        if brand_dna is not None:
            vals = []
            for f in ("imagery_style", "mood", "layout_philosophy",
                      "composition_patterns", "typographic_character",
                      "motifs", "signature_moves"):
                v = getattr(brand_dna, f, None)
                if v:
                    vals.append(v if isinstance(v, str) else ", ".join(map(str, v)))
            dna_lines = "; ".join(vals)[:800]
        # The brand's REAL photos as composited assets (products as props, or the brand's
        # premises as the scene's PLACE) — their OCR'd text lines become ALLOWED extras below.
        product_imgs, allowed_lines, asset_role = _gather_product_props(
            profile, caller, log, product_image=product_image)
        from poster.locale import brand_locale
        _country, locale_line = brand_locale(profile)

        prompt = oneshot.build_oneshot_prompt(
            copy, brand_name=brief.business_name or "",
            palette_names=_palette_names(brief.palette_hex),
            dna_lines=dna_lines, rtl=arabic, has_logo=bool(brief.logo_url),
            heading_font=(brief.heading_font or ""), body_font=(brief.body_font or ""),
            single_message=(concept.single_message or ""),
            visual_idea=(concept.visual_idea or ""),
            product_placement=(getattr(concept, "product_placement", "") or ""),
            n_products=(len(product_imgs) if asset_role == "prop" else 0),
            n_places=(len(product_imgs) if asset_role == "place" else 0),
            # Tell the prompt a PERSON is attached, or `_props_rules` describes the brand's
            # presenter as merchandise and the model composites a face onto a counter.
            n_identity_refs=1 if identity_image else 0,
            locale_line=locale_line, category=str(brief.category or ""))
        cue = concept_variation_cue(variation)
        if cue:
            prompt += "\n" + cue   # per-run design diversity (mood/lighting/composition)
        # HITL (owner law 2026-07-12): the user-approved prompt is FINAL — it replaces the
        # assembled brief verbatim. Every render gate below (verbatim-copy fidelity, corner
        # slot, brand-lines, QA critic) still runs on the RESULT, so the edit box can never
        # bypass validation; generation only reaches here via an explicit EXECUTE.
        if prompt_override and prompt_override.strip():
            prompt = prompt_override.strip()
            log("[oneshot] HITL prompt override in effect (user-approved)")

        logo_bytes, logo_mime = _logo_bytes(brief.logo_url)
        brand_tokens = [t.lower() for t in re.findall(r"[A-Za-z؀-ۿ]{3,}",
                                                      brief.business_name or "")]
        allowed_norm = [oneshot._norm_text(l) for l in allowed_lines if str(l).strip()]

        def _is_asset_text(line: str) -> bool:
            """True when a rendered line matches the REAL label text of an attached
            product photo (allowed — correct by construction, the owner's rule:
            'أهم حاجة الكتابة صح')."""
            n = oneshot._norm_text(line)
            return bool(n) and any(n in a or a in n for a in allowed_norm)

        best: Optional[PosterGenResult] = None
        for attempt in range(1, max(1, max_retries + 1) + 1):
            out_path = Path(out_dir) / f"poster_{uuid.uuid4().hex[:8]}.png"
            try:
                # Do NOT attach the logo to generation — the model garbles it (esp. Arabic).
                # The prompt reserves a clean corner; we composite the REAL logo in below.
                oneshot.generate_oneshot_poster(prompt, out_path, logo_bytes=None,
                                                product_images=product_imgs,
                                                identity_ref=identity_image)
            except Exception as e:  # noqa: BLE001
                log(f"[oneshot] attempt {attempt} generation failed: {type(e).__name__}: {e}")
                continue
            # Publish as UNVERIFIED: keeps البند 1's promise (a timeout still leaves a poster)
            # without letting a not-yet-gated render replace a good one.
            _refresh_canonical(out_path, passed=False)
            # FIX-C1: reject a render whose reserved corner carries a painted placeholder
            # slot (the white box the owner has fought for 2 months) BEFORE compositing.
            if logo_bytes and _corner_placeholder_detected(out_path, rtl=arabic):
                log(f"[oneshot] attempt {attempt}: corner placeholder slot detected -> retry")
                continue
            if logo_bytes and _overlay_real_logo(out_path, logo_bytes, rtl=arabic):
                log("[oneshot] real logo composited (deterministic, crisp)")
            seen = oneshot.read_rendered_text(out_path, caller)
            verdict = oneshot.text_fidelity(expected, seen)
            # VISUAL brand fabrication gate (owner caught it: the model painted the
            # brand name onto an invented delivery ROBOT — a false visual claim).
            # The corner logo legitimately reads as 1-2 lines; MORE brand-name lines
            # mean the name was painted onto objects -> that render never ships.
            # A line matching an attached REAL asset's own text is always allowed
            # (a real bag/pack CAN carry the brand — correctly, by construction).
            brand_lines = sum(1 for l in seen
                              if any(t in l.lower() for t in brand_tokens)
                              and not _is_asset_text(l)) if brand_tokens else 0
            # Garbled invented-packaging labels show up as several extra lines; the
            # REAL labels of the attached products are allowed.
            junk_extras = [l for l in verdict["extra_lines"]
                           if brand_tokens and not any(t in l.lower() for t in brand_tokens)
                           and not _is_asset_text(l)]
            log(f"[oneshot] attempt {attempt}: fidelity={verdict['rate']} "
                f"brand_lines={brand_lines} junk_extras={len(junk_extras)}")
            # `verified` — not `rate` — because those are three states and only one may open this
            # gate. A run with nothing to check used to score 1.0 and pass, which is how an image
            # of scrambled Arabic could ship past the gate whose entire job is reading the copy
            # back. Failed and could-not-be-checked both continue to the next attempt, and after
            # the attempts run out the classic overlay renders the same copy correctly.
            if not verdict.get("verified") or brand_lines > 2 or len(junk_extras) >= 3:
                continue    # hard gates: verbatim copy / no painted branding / no label junk

            # ART-CRITIC pass (was classic-path only): clutter, weak contrast,
            # competing focal points — retry when the failure is image-fixable,
            # keep the BEST attempt by score.
            qa = poster_vision_qa(out_path, caller=qa_caller, brand_dna=brand_dna,
                                  arabic=arabic,
                                  # product-authenticity context (topshoes fix): with no real
                                  # product attached, an invented hero product must FAIL.
                                  expect_real_products=bool(product_imgs),
                                  # the REAL mark was composited pixel-exactly, so the critic must
                                  # judge its visibility — not its letterforms (a stylised wordmark
                                  # is the brand's design; MEASURED on Volume Qatar).
                                  logo_composited=bool(logo_bytes))
            result = PosterGenResult(
                poster_path=str(out_path), filename=Path(out_path).name,
                image_base64=base64.b64encode(Path(out_path).read_bytes()).decode("ascii"),
                brief=brief, spec=default_design_spec(brief), concept=concept,
                brand_dna=brand_dna, background_path=str(out_path),
                model_used=oneshot.DEFAULT_ONESHOT_MODEL, prompt=prompt, qa=qa,
                audit=audit, engine="oneshot",
            )
            if best is None or qa.score > best.qa.score:
                best = result
            _refresh_canonical(out_path, passed=True)   # cleared the verbatim + placeholder gates
            # PERMISSIVE, DELIBERATE (P6 Tier-4 — the tri-state makes it visible, not silent): a
            # poster whose art-critic could not run still ships rather than blocking the deliverable
            # on a Vertex blip. `is not False` states that; the old `qa.overall_pass or not
            # qa.checked` read a fabricated True. qa.overall_pass is None => NOT_CHECKED, and
            # PosterResult carries `checked` so a caller can tell "clean" from "never looked".
            if qa.overall_pass is not False:
                return result
            log(f"[oneshot] attempt {attempt}: art-critic fail (score={qa.score}) "
                f"{qa.reason[:100]}")
            if not _qa_image_fixable(qa):
                return result           # re-rolling the image won't fix it — ship best
        return best
    except Exception as e:  # noqa: BLE001 — the engine must never break poster generation
        log(f"[oneshot] engine error ({type(e).__name__}: {e}); falling back to classic")
        return None


# The client-facing disclosure when the least-bad attempt still lies (owner principle:
# «الضعف المُفصح عنه درع»). Owner's OPEN decision D-7: refuse outright vs disclose — until
# she rules, the result ships flagged + noted, never silently.
_TRUST_NOTE_AR = ("كل محاولات التوليد رجعت بعيب موثوقية في المشهد (ليبل منتج أو علامة "
                  "اترسمت غلط). دي أفضل محاولة، بس ماتتنشرش من غير مراجعة بصرية.")


def _excuse_expected_brand(verdict, product_name: str):
    """Clear `third_party_mark` when the "third party" IS the product the owner asked for.

    ROOT (Al Fouad 2026-07-26): she pasted a La Roche-Posay link, the poster showed exactly that
    product, and the hard gate failed it as a third-party mark — so a correct ad shipped stamped
    untrustworthy. Applied to the VERDICT so every reader (trust check, attempt ranking, the
    client disclosure) sees one consistent story, and `overall_pass` is recomputed rather than
    left contradicting its own flags. Unrelated marks are untouched."""
    try:
        from poster.vision_qa import third_party_is_expected
        if not (getattr(verdict, "third_party_mark", False) and product_name):
            return verdict
        if not third_party_is_expected(getattr(verdict, "reason", ""), product_name=product_name):
            return verdict
        cleared = verdict.model_copy(update={"third_party_mark": False})
        if cleared.overall_pass is False and not _trust_violation(cleared)                 and int(cleared.score or 0) >= 7 and cleared.logo_ok:
            cleared = cleared.model_copy(update={"overall_pass": True})
        return cleared
    except Exception:  # noqa: BLE001 — an excuse that fails leaves the strict verdict standing
        return verdict


def _trust_violation(v: PosterQAVerdict) -> bool:
    """A verdict that would make the poster LIE — a fabricated product/label presented as the
    brand's merchandise, another company's mark, or copy whose script is broken. These are
    grounding failures, not taste failures: a lower-scored honest attempt must always beat a
    prettier fabrication. MEASURED (nanocare 2026-07-24): the image model redrew "Liposomal
    Iron" as "Lifosemul Iron"; QA caught it and the score-ranked best returned it anyway."""
    return ((not v.script_wellformed) or bool(v.invented_product)
            or bool(v.third_party_mark))


# What the art critic complains about, and which part of the design answers it. Matched on the
# critic's own words because that verdict is free-form prose, not an enum — the alternative would
# be asking the model for a taxonomy it was never given.
_TYPOGRAPHY_COMPLAINTS = (
    "typography", "typeface", "font", "lettering", "drop shadow", "drop-shadow",
    "text treatment", "outline", "kerning", "legib", "readab",
)
_HIERARCHY_COMPLAINTS = (
    "hierarchy", "focal", "composition", "layout", "cluttered", "balance",
    "where to look", "eye does not", "competing",
)
# Treatments and layouts to move TOWARD when the current one was criticised. Ordered plainest
# first: the complaint is nearly always that the design is doing too much.
_CALMER_TREATMENTS = ("block", "stacked_hero", "highlight")


def _revise_spec_for(spec, verdict, *, attempt: int = 0):
    """Change the DESIGN in response to what the critic actually said.

    MEASURED (Al Fouad, 2026-07-25): `spec` was computed once, before the retry loop, and every
    attempt rendered with it. So the critic said

        attempt 0: "clearly fake, AI-generated products with garbled text"
        attempt 1: "Amateur typography with heavy drop shadows and a weak visual hierarchy"
        attempt 2: "inconsistent and misspelled brand logo ('HARMACIES')"

    and the typography was byte-identical on the next attempt, because only the BACKGROUND was
    redrawn. A retry that cannot change what was criticised is not a retry — it is the same poster
    with different pixels behind it.

    Deliberately NARROW. An image complaint ("fake AI products") leaves the design alone: shuffling
    the layout because the model drew a bad bottle would be noise, and noise in a retry loop is how
    three attempts become three unrelated posters none of which is the best one. Never raises — a
    verdict we cannot read simply yields the spec unchanged, which is the old behaviour."""
    try:
        reason = str(getattr(verdict, "reason", "") or "").lower()
        if not reason:
            return spec
        typo = any(k in reason for k in _TYPOGRAPHY_COMPLAINTS)
        hier = any(k in reason for k in _HIERARCHY_COMPLAINTS)
        if not (typo or hier):
            return spec

        update: dict = {}
        if typo or hier:
            current = getattr(spec, "headline_treatment", "")
            options = [t for t in _CALMER_TREATMENTS if t != current]
            if options:
                update["headline_treatment"] = options[attempt % len(options)]
        if hier:
            from poster.template import _LAYOUTS, _LOGO_CORNER_BY_LAYOUT
            layouts = [l for l in _LAYOUTS if l != getattr(spec, "layout", "")]
            if layouts:
                nxt = layouts[attempt % len(layouts)]
                update["layout"] = nxt
                if nxt in _LOGO_CORNER_BY_LAYOUT:
                    update["logo_corner"] = _LOGO_CORNER_BY_LAYOUT[nxt]
        return spec.model_copy(update=update) if update else spec
    except Exception:  # noqa: BLE001 — a revision failure must never cost the attempt
        return spec


def _attempt_rank(r: "PosterGenResult") -> tuple[int, int]:
    """Best-attempt ordering: honesty first, THEN beauty. Score only breaks ties within the
    same honesty class."""
    return (0 if _trust_violation(r.qa) else 1, int(r.qa.score or 0))


def _qa_image_fixable(v: PosterQAVerdict) -> bool:
    """True when a QA failure is something REGENERATING THE IMAGE can fix (off-brand colour,
    candid look, baked Latin, a cluttered/competing-focal composition). A pure layout clip or
    a low-res logo is handled deterministically elsewhere — re-rolling the image won't change
    those, so we don't loop on them."""
    return (v.candid_violation or (not v.on_brand_color) or v.has_latin_text
            or (not v.single_focal) or v.invented_product or v.third_party_mark
            # an illogically placed product (floating / mis-scaled / no contact shadow) is a
            # generation failure, so re-rolling the image is exactly the right remedy.
            or (not v.placement_logical))


def _verified_external_headline(headline_override, concept_headline, ledger):
    """H6 grounding gate for an EXTERNAL headline.

    A ``headline_override`` — from ``--headline``, a content-calendar hook OR **topic**
    (the calendar's `topic` field is not gated in the strategy layer), or a trend — would
    otherwise replace the concept's Ledger-gated headline and render VERBATIM, bypassing
    the poster's own grounding gate (a calendar topic like "Egypt's #1 pharmacy" would
    ship unverified). Verify the override against the Evidence Ledger; an UNSOURCED
    falsifiable claim falls back to the grounded concept headline (or drops), never the
    fabricated line. Pure/testable; offline (no LLM).

    Returns ``(headline_to_use, remediation | None)``.
    """
    ho = (headline_override or "").strip()
    concept_h = (concept_headline or "").strip() or None
    if not ho:
        return concept_h, None                       # no override -> concept headline (unchanged)

    def _ungated(reason: str):
        """FAIL-CLOSED (P6 #3): we could not verify the override, so we do not render it. Fall
        back to the concept headline — which IS Ledger-gated — or drop. Both triggers used to
        `return ho, None`, shipping the exact line this gate exists to stop: its own docstring
        names "Egypt's #1 pharmacy" as the failure, and a test pinned that it shipped."""
        return concept_h, {
            "field": "headline_override",
            "original_text": ho,
            "unsourced_claims": ["gate_unavailable"],
            "action": "replaced_with_grounded_concept" if concept_h else "dropped",
            "note": reason,
        }

    if ledger is None:
        return _ungated("no Evidence Ledger could be built — the override was never verified")
    try:
        unsourced = [v for v in ledger.audit_text(ho) if not v.sourced]
    except Exception as exc:  # noqa: BLE001 — a gate error must never break rendering
        return _ungated(f"the Evidence Ledger errored ({type(exc).__name__}) — override unverified")
    if not unsourced:
        return ho, None                              # override is grounded -> use it
    rem = {
        "field": "headline_override",
        "original_text": ho,
        "unsourced_claims": sorted({v.claim.kind for v in unsourced}),
        "action": "replaced_with_grounded_concept" if concept_h else "dropped",
    }
    return concept_h, rem


def generate_poster(
    profile: dict[str, Any],
    *,
    caller: Any = None,
    qa_caller: Any = None,
    variation: Optional[dict] = None,
    brand_dna: Any = None,
    use_dna: bool = True,
    use_edit: bool = True,
    upscale: bool = True,
    no_image: bool = False,
    headline_override: Optional[str] = None,
    trend_context: Optional[str] = None,
    max_qa_retries: int = 2,
    out_dir: str = "outputs/posters",
    engine: str = "classic",
    language: str = "auto",
    product_image: Optional[str] = None,
    product_name: Optional[str] = None,
    prompt_override: Optional[str] = None,
    canonical_out: Optional[str] = None,
    # The brand's SELECTED presenter, as bytes. Its own parameter on purpose: `product_images` is
    # capped at two and everything downstream — the prompt's props block and the art critic's
    # `expect_real_products` — treats those attachments as merchandise. A face is a person.
    identity_image: Optional[tuple] = None,
    identity_persona: str = "",
    log=_safe_log,
) -> PosterGenResult:
    """The one pipeline. `caller` = a (multimodal) Gemini caller; `qa_caller` defaults to it.

    `engine="oneshot"` composes the whole creative with a Gemini image model (per-run
    layout diversity, designed typography) behind a BLOCKING verbatim-text gate; any
    gate failure falls back to this classic overlay path — never an error.

    `language`: "ar" | "en" force the OUTPUT copy language (owner's choice);
    "auto" (default) infers from the brand as before."""
    _CANONICAL_SINK["path"] = canonical_out
    qa_caller = qa_caller if qa_caller is not None else caller
    if language in ("ar", "en"):
        arabic = language == "ar"
    else:
        arabic = brand_is_arabic(profile)
    variation = variation or build_variation()

    # 1) Brand's learned visual language (cached per brand).
    if brand_dna is None and use_dna:
        brand_dna = load_or_build_dna(profile, caller)
    log(f"[concept] dna={'yes' if getattr(brand_dna,'used_vision',False) else 'no'} arabic={arabic}")

    # 1b) FRESH sourced copy material: live web research about the brand (was CLI-only
    #     behind --research, so the WEB pipeline recycled the same tagline-derived copy
    #     every run — the owner's "طريقة الكتابة ثابتة"). Best-effort; its facts join
    #     the concept prompt AND the grounding ledger (so a research-sourced number in
    #     the copy resolves instead of being blanked). None -> behavior unchanged.
    research = None
    if caller is not None:
        try:
            from competitor.search_providers import get_default_search_provider
            from poster.art_director import _persona_lines
            from poster.brand_research import research_brand
            provider = get_default_search_provider()
            if provider is not None:
                research = research_brand(
                    _fv(profile, "name") or "",
                    homepage_url=str(_fv(profile, "source_url") or ""),
                    persona=_persona_lines(profile),
                    provider=provider, caller=caller)
                if research is not None and not research.used:
                    research = None
                if research is not None:
                    log(f"[research] {len(research.facts)} sourced facts, "
                        f"{len(research.angles)} angles")
        except Exception as exc:  # noqa: BLE001 — research must never break the poster
            log(f"[research] skipped ({type(exc).__name__})")
            research = None

    # 2) ONE creative concept -> the copy is built FROM it (coherent, brand-language).
    #    enforce_grounding=True: the Evidence Ledger gate softens any falsifiable claim
    #    (number/year/certification/ranking) that isn't backed by the brand's real evidence.
    concept = build_creative_concept(profile, caller=caller, brand_dna=brand_dna,
                                     featured_product=product_name,
                                     variation=variation, enforce_grounding=True,
                                     arabic=arabic, research=research,
                                     trend_context=trend_context)
    log(f"[concept] msg={concept.single_message!r} headline={concept.headline!r} "
        f"chips={concept.proof_points}")

    # 3) Brief, with copy OVERRIDDEN by the concept (headline/sub/cta/chips all one idea).
    # H6: an external headline_override (--headline / calendar hook OR topic / trend) must
    # pass the SAME grounding gate as the concept copy — otherwise a calendar `topic` that
    # is ungated in the strategy layer prints verbatim. Verify it; on an unsourced claim
    # fall back to the grounded concept headline.
    _hl_ledger = None
    try:
        from grounding import EvidenceLedger
        _research_dump = (research.model_dump()
                          if (research is not None and hasattr(research, "model_dump")) else None)
        _hl_ledger = EvidenceLedger.from_profile(profile, research=_research_dump)
    except Exception:  # noqa: BLE001 — grounding must never break the poster
        _hl_ledger = None
    headline, _hl_rem = _verified_external_headline(headline_override, concept.headline, _hl_ledger)
    if _hl_rem is not None:
        log(f"[grounding] external headline carried unsourced claim(s) "
            f"{_hl_rem['unsourced_claims']} -> {_hl_rem['action']}")
    brief = build_poster_brief(profile, headline_override=headline)
    updates: dict = {}
    if concept.subheadline:
        updates["subheadline"] = concept.subheadline
    if concept.cta:
        updates["cta_text"] = concept.cta
    if concept.proof_points:
        updates["offerings"] = concept.proof_points[:3]
    if updates:
        brief = brief.model_copy(update=updates)

    # 3b) Brand-safety audit trail (Step 3c): the (claim->source) proof of the SHIPPED copy +
    #     the gate's remediation log + explicit scoping. Built once (independent of the
    #     image/QA loop); written as a sidecar next to the poster that ships.
    try:
        from poster.audit import build_poster_audit
        audit = build_poster_audit(profile, concept, brief)
    except Exception:  # noqa: BLE001 — the trail must never break poster generation
        audit = None

    def _emit(r: PosterGenResult) -> PosterGenResult:
        if r.audit:
            # P-42: record the SHIPPED attempt's vision-QA verdict in the sidecar (was in-memory only).
            try:
                from poster.audit import attach_vision_qa
                attach_vision_qa(r.audit, r.qa)
            except Exception:  # noqa: BLE001 — the trail must never break poster generation
                pass
            try:
                Path(r.poster_path).with_suffix(".audit.json").write_text(
                    json.dumps(r.audit, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            # Client-facing Ad Compliance Sheet, derived from the same trail (pure
            # transform — never fabricated; skipped when the trail is unusable).
            try:
                from grounding.compliance import build_compliance_sheet
                sheet = build_compliance_sheet(r.audit)
                if sheet:
                    Path(r.poster_path).with_suffix(".compliance.json").write_text(
                        json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return r

    # 3c) ONE-SHOT engine (opt-in): the whole creative composed by the Gemini image
    #     model — same gated copy, per-run design diversity. Ships ONLY on a verbatim
    #     OCR gate pass; otherwise falls through to the classic path below.
    # ARABIC NO LONGER PREFERS THE ENGINE THAT CANNOT WRITE ARABIC.
    #
    # This used to read `engine == "oneshot" or (bool(brief.logo_url) and arabic)` — an Arabic
    # brand with a logo was routed to the engine that draws its OWN type. MEASURED 2026-07-25 on
    # Abu Auf: three attempts, `fidelity=0.0` every one, then the fallback. Three image calls
    # (~$0.36) and minutes of latency spent to arrive where the run would have started, and when
    # the fidelity gate leaked, gibberish shipped instead (see poster/oneshot.text_fidelity).
    #
    # The problem the preference was solving — the classic STYLE background baking a garbled
    # Arabic logo (Azza Fahmy "عزة فهمي"→"ةفهمص") — is handled by compositing the REAL mark
    # deterministically, which both paths now do.
    #
    # Explicit engine="oneshot" still tries it: the capability is un-preferred, not removed, and
    # the day the image model learns Arabic this is one word to change back.
    prefer_oneshot = engine == "oneshot"
    if prefer_oneshot:
        r = _try_oneshot(profile, brief, concept, brand_dna, caller, arabic=arabic,
                         audit=audit, out_dir=out_dir, variation=variation,
                         max_retries=max_qa_retries, log=log, qa_caller=qa_caller,
                         product_image=product_image, prompt_override=prompt_override,
                         identity_image=identity_image, identity_persona=identity_persona)
        if r is not None:
            return _emit(r)
        log("[oneshot] gate not passed / unavailable -> classic overlay pipeline")

    # 4) Free-form, DNA-steered composition.
    from poster.art_director import build_design_spec, build_llm_concept_prompt
    spec = build_design_spec(brief, caller, profile=profile, variation=variation, brand_dna=brand_dna)
    # GUARANTEE the concept's elements render, but SCALE the count TO THE ARCHETYPE so each
    # looks like its kind of ad AND a side/upper editorial box doesn't overflow + clip the CTA
    # (MEASURED on WE: forcing headline+sub+3 chips+CTA into a top-anchored side third clipped
    # the CTA). An editorial lockup shows FEW elements; a B2B proof shows the evidence bullets.
    arche = getattr(spec, "marketing_archetype", None)
    must_show = ["logo", "headline"]
    if arche == "magazine_editorial":
        pass                                   # the massive lockup is the hero: logo+headline+cta
    elif arche in ("typographic_anchor", "product_hero"):
        if brief.subheadline:
            must_show.append("sub")            # one supporting line, no chips
    else:                                       # proof_and_trust / None: the evidence bullets ARE the point
        if brief.subheadline:
            must_show.append("sub")
        if brief.offerings:
            must_show.append("offerings")
    if brief.cta_text:
        must_show.append("cta")
    spec = spec.model_copy(update={"show": must_show})
    # DNA-driven TYPOGRAPHY: if the brand's real ads treat the headline as a bold designed
    # lockup, render it that way (gradient+outline) instead of plain text on the photo.
    if _dna_wants_lockup(brand_dna):
        spec = spec.model_copy(update={"headline_treatment": "lockup"})
        log("[concept] typography=lockup (DNA)")

    # 5) Generate image + render + VISION QA gate (regenerate the image on a fixable fail).
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result: Optional[PosterGenResult] = None
    best: Optional[PosterGenResult] = None      # highest-scoring attempt seen
    for attempt in range(max_qa_retries + 1):
        run_var = variation if attempt == 0 else build_variation()
        if no_image:
            from poster.imagen_provider import StubImageProvider
            bg_path, model_used, prompt = str(StubImageProvider().generate("")), "stub", "(stub)"
        else:
            prompt = build_llm_concept_prompt(
                brief, caller, profile=profile, spec=spec, brand_dna=brand_dna,
                variation=run_var, scene_idea=concept.visual_idea,
            )
            # ADAPTIVE brand-anchored background (outpaint real photo -> style on real ads ->
            # text-to-image). `use_edit=False` forces the legacy pure text-to-image path.
            if use_edit:
                bg_path, model_used = _generate_background(
                    profile, brand_dna, prompt, log=log,
                    product_images=([product_image] if product_image else None),
                    identity_ref=identity_image)
            else:
                bg_path, model_used = _generate_imagen(prompt)
            # Deterministic letterbox safety net: crop any black bars so the background fills
            # the canvas edge-to-edge regardless of what the image model rendered.
            bg_path = _trim_letterbox(bg_path)
            # Quality: upscale ~2x for crispness + sharper, more believable faces.
            if upscale:
                bg_path = _maybe_upscale(bg_path, log=log)

        filename = f"poster_{uuid.uuid4().hex[:8]}.png"
        poster_path = str(render_html_to_png(render_poster_html(brief, bg_path, spec=spec), out / filename))
        _refresh_canonical(poster_path)   # البند 1
        verdict = poster_vision_qa(poster_path, caller=qa_caller, brand_dna=brand_dna, arabic=arabic)
        verdict = _excuse_expected_brand(verdict, product_name or "")
        log(f"[qa] attempt={attempt} pass={verdict.overall_pass} checked={verdict.checked} "
            f"latin={verdict.has_latin_text} clip={verdict.cta_clipped} "
            f"color={verdict.on_brand_color} candid={verdict.candid_violation} :: {verdict.reason}")
        result = PosterGenResult(
            poster_path=poster_path, filename=filename,
            image_base64=base64.b64encode(Path(poster_path).read_bytes()).decode("ascii"),
            brief=brief, spec=spec, concept=concept, brand_dna=brand_dna,
            background_path=str(bg_path), model_used=model_used, prompt=prompt, qa=verdict,
            audit=audit,
        )
        if best is None or _attempt_rank(result) > _attempt_rank(best):
            best = result           # honesty outranks score — see _attempt_rank
        # PERMISSIVE, DELIBERATE (P6 Tier-4): `is not False` — ship on PASS or NOT_CHECKED, block
        # only on an explicit FAIL. Same reasoning as the oneshot branch above; the difference from
        # the old `verdict.overall_pass or not verdict.checked` is that NOT_CHECKED is now visible
        # as None instead of masquerading as True.
        if no_image or verdict.overall_pass is not False:
            return _emit(result)                # passed, or could not gate -> ship it
        if attempt < max_qa_retries and _qa_image_fixable(verdict):
            # Answer what was CRITICISED, not just re-roll the background. Until now `spec` was
            # fixed before this loop, so a "weak typography" verdict was followed by an identical
            # typographic treatment on the next attempt (measured across all three Al Fouad
            # attempts). A design complaint now moves the design; an image complaint still only
            # redraws the image.
            revised = _revise_spec_for(spec, verdict, attempt=attempt)
            if revised is not spec and (revised.headline_treatment != spec.headline_treatment
                                        or revised.layout != spec.layout):
                log(f"[qa] design revised: treatment={spec.headline_treatment}"
                    f"->{revised.headline_treatment} layout={spec.layout}->{revised.layout}")
                spec = revised
            log("[qa] FAIL (image-fixable) -> regenerating...")
            continue
        break

    # No attempt fully passed the art bar -> return the best by _attempt_rank (honesty
    # first, then score), with its honest verdict attached so the caller can flag it.
    assert best is not None
    if not best.qa.overall_pass:
        log(f"[qa] no attempt passed; returning best (score={best.qa.score}): {best.qa.reason}")
    if _trust_violation(best.qa):
        # Every attempt lied somewhere. The verdict alone was MEASURED being ignored
        # downstream, so the disclosure is stamped TOP-LEVEL (D-7 pending: refuse vs disclose).
        import dataclasses as _dc
        best = _dc.replace(best, trust_violation=True, trust_note_ar=_TRUST_NOTE_AR)
        log("[qa] TRUST violation in the returned best -> disclosed on the result "
            f"(invented_product={best.qa.invented_product} third_party={best.qa.third_party_mark} "
            f"script={best.qa.script_wellformed})")
    return _emit(best)


def build_generation_preview(profile: dict, *, caller: Any = None,
                             product_image: Optional[str] = None,
                             language: str = "auto",
                             raw_facts: Optional[str] = None) -> dict:
    """HITL step 1 (owner law 2026-07-12): assemble the EXACT one-shot generation brief —
    concept + copy + palette + DNA + real-asset roles + locale — WITHOUT touching any image
    API. Cheap text-only calls (concept build); props are counted but never OCR'd (caller
    is withheld from the gather). What the user approves here is what runs verbatim via
    `prompt_override`, so preview==execution by construction."""
    if caller is None:
        from business_profile.llm import default_caller
        caller = default_caller(strong=True)
    arabic = language == "ar" if language in ("ar", "en") else brand_is_arabic(profile)
    brand_dna = load_or_build_dna(profile, caller)
    concept = build_creative_concept(profile, caller=caller, arabic=arabic,
                                     raw_facts=raw_facts)
    brief = build_poster_brief(profile)
    brief = brief.model_copy(update={
        "headline": concept.headline or brief.headline,
        "subheadline": concept.subheadline or brief.subheadline,
        "cta_text": concept.cta or brief.cta_text})
    import poster.oneshot as oneshot
    from poster.art_director import _palette_names
    from poster.locale import brand_locale
    dna_lines = ""
    if brand_dna is not None:
        vals = []
        for f in ("imagery_style", "mood", "layout_philosophy", "composition_patterns",
                  "typographic_character", "motifs", "signature_moves"):
            v = getattr(brand_dna, f, None)
            if v:
                vals.append(v if isinstance(v, str) else ", ".join(map(str, v)))
        dna_lines = "; ".join(vals)[:800]
    product_imgs, _lines, asset_role = _gather_product_props(profile, None, _safe_log,
                                                             product_image=product_image)
    _country, locale_line = brand_locale(profile)
    copy = {"headline": brief.headline or "", "subheadline": brief.subheadline or "",
            "cta": brief.cta_text or ""}
    prompt = oneshot.build_oneshot_prompt(
        copy, brand_name=brief.business_name or "",
        palette_names=_palette_names(brief.palette_hex), dna_lines=dna_lines,
        rtl=arabic, has_logo=bool(brief.logo_url),
        heading_font=(brief.heading_font or ""), body_font=(brief.body_font or ""),
        single_message=(concept.single_message or ""),
        visual_idea=(concept.visual_idea or ""),
        product_placement=(getattr(concept, "product_placement", "") or ""),
        n_products=(len(product_imgs) if asset_role == "prop" else 0),
        n_places=(len(product_imgs) if asset_role == "place" else 0),
        locale_line=locale_line, category=str(brief.category or ""))
    return {"prompt": prompt, "copy": copy, "engine": "oneshot", "arabic": arabic}
