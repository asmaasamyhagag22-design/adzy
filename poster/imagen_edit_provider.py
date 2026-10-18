"""Imagen EDIT provider — brand-anchored backgrounds via `imagen-3.0-capability-001`.

The plain text-to-image `VertexImagenProvider` drifts OFF-brand (generic AI stock people,
clichés, occasional baked text) because nothing anchors the SUBJECT/scene to the brand's
real visual world. This provider closes that with two GROUNDED edit modes (capability
MEASURED working on Vertex project image-498715 — 2026-06-27 probe):

  - style(...)    EDIT_MODE_STYLE    : generate a FRESH scene in the brand's OWN visual
                                        language, conditioned on the brand's REAL creatives
                                        (e.g. BrandCreativeDNA.references_seen). The universal
                                        floor — works even when the site has no usable photo.
  - outpaint(...) EDIT_MODE_OUTPAINT : keep a REAL brand photo and naturally extend it to
                                        fill the poster canvas. Maximum fidelity (the literal
                                        product/place is in the output) — for brands with a
                                        usable scraped photo (see reel.image_quality).

Discipline (matches VertexImagenProvider):
  * NEVER bakes text — all poster text is overlaid later in HTML/CSS.
  * Reference images are SSRF-guarded (is_safe_public_url) before fetch.
  * RAISES on failure so the pipeline can fall back to text-to-image — never a silent blank.
  * Lazy google-genai import; no new dependency (same SDK + ADC as the text-to-image path).
"""
from __future__ import annotations

from poster.paid_boundary import paid_call

import io
import os
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

# Reuse the text-to-image provider's shared constants (no-text composition contract, path
# helper, default canvas) so the two providers stay consistent and DRY.
from poster.imagen_provider import (
    _COMPOSITION_CONTRACT,
    _new_bg_path,
    DEFAULT_ASPECT,
    DEFAULT_OUT,
)

DEFAULT_EDIT_MODEL = "imagen-3.0-capability-001"

# Outpaint extends a real photo: we want a seamless continuation, NOT the poster's
# reserved-calm-zone language (that's for a generated background that hosts text).
_OUTPAINT_GUARD = (
    "Photorealistic, seamless continuation of the SAME single scene — same place, same "
    "lighting, same depth of field and color; one continuous photo with no seams, borders, "
    "frames, panels, or collage. Fill the ENTIRE frame edge-to-edge (full-bleed): NO black "
    "bars, NO letterbox or pillarbox, NO empty margins. Absolutely no text, words, letters, "
    "numbers, logos, or signage anywhere."
)

ImgSrc = Union[bytes, bytearray, str]


def _fetch_bytes(url: str, *, timeout: int = 15) -> Optional[bytes]:
    """SSRF-guarded http(s) image fetch (same discipline as reel.image_quality / logo fetch)."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    try:
        from scraper.url_utils import is_safe_public_url, open_public_url
        if not is_safe_public_url(url):
            return None
        import urllib.request
        hdr = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
        with open_public_url(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _detect_mime(data: bytes) -> str:
    try:
        from PIL import Image as PImage
        fmt = (PImage.open(io.BytesIO(data)).format or "PNG").upper()
    except Exception:
        return "image/png"
    return {"PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "image/jpeg")


def build_outpaint_canvas_and_mask(
    base_bytes: bytes, *, target: tuple[int, int] = (1024, 1280), coverage: float = 0.66,
) -> tuple[bytes, bytes, tuple[int, int, int, int]]:
    """Pure helper: center the real photo on a `target` canvas at `coverage` of the short
    side, return (canvas_png, mask_png, box=(ox,oy,nw,nh)). The mask is WHITE where the model
    should PAINT (the empty outpaint border) and BLACK over the KEPT real photo."""
    from PIL import Image as PImage, ImageDraw

    src = PImage.open(io.BytesIO(base_bytes)).convert("RGB")
    tw, th = target
    scale = min(tw * coverage / src.width, th * coverage / src.height)
    nw, nh = max(1, int(src.width * scale)), max(1, int(src.height * scale))
    src = src.resize((nw, nh))
    canvas = PImage.new("RGB", (tw, th), (0, 0, 0))
    ox, oy = (tw - nw) // 2, (th - nh) // 2
    canvas.paste(src, (ox, oy))
    mask = PImage.new("L", (tw, th), 255)                       # white = fill
    ImageDraw.Draw(mask).rectangle([ox, oy, ox + nw, oy + nh], fill=0)  # black = keep
    cb, mb = io.BytesIO(), io.BytesIO()
    canvas.save(cb, "PNG")
    mask.save(mb, "PNG")
    return cb.getvalue(), mb.getvalue(), (ox, oy, nw, nh)


def upscale_to_file(src_path: str, *, project=None, location=None, model=None, factor="x2") -> str:
    """Upscale a generated background ~2x for crispness + finer detail (sharper faces). Imagen
    upscale, MEASURED provisioned on image-498715 (imagen-3.0-generate-002). Overwrites src_path
    in place; returns it. NEVER raises — returns the original on any failure (no project / read
    error / model error), so it can't break poster generation."""
    try:
        data = Path(src_path).read_bytes()
        proj = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not data or not proj:
            return src_path
        from google import genai
        from google.genai import types as t
        client = genai.Client(vertexai=True, project=proj,
                              location=location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
        resp = paid_call(client.models.upscale_image,
            model=model or os.getenv("IMAGEN_UPSCALE_MODEL") or "imagen-3.0-generate-002",
            image=t.Image(image_bytes=data, mime_type=_detect_mime(data)),
            upscale_factor=factor,
            config=t.UpscaleImageConfig(output_mime_type="image/png"),
        )
        imgs = getattr(resp, "generated_images", None) or []
        if imgs and imgs[0].image.image_bytes:
            Path(src_path).write_bytes(imgs[0].image.image_bytes)
    except Exception:
        pass
    return src_path


class ImagenEditProvider:
    """Brand-anchored background generator via Imagen capability editing (Vertex/ADC)."""

    name = "vertex-imagen-edit"

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        model: Optional[str] = None,
        *,
        fetch: Optional[Callable[[str], Optional[bytes]]] = None,
    ):
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.model = model or os.getenv("IMAGEN_EDIT_MODEL") or DEFAULT_EDIT_MODEL
        self._fetch = fetch or _fetch_bytes
        self._cli = None   # cached genai.Client — MUST be retained (a temporary client gets

    # -- internals -------------------------------------------------------
    def _client(self):
        # ... closed mid-request, raising "Cannot send a request, as the client has been closed").
        from google import genai
        if not self.project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT not set. Add it to .env and run "
                "`gcloud auth application-default login` once (same auth as the text-to-image provider)."
            )
        if self._cli is None:
            self._cli = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._cli

    def _image(self, src: ImgSrc):
        """bytes | http(s) URL -> types.Image (SSRF-guarded fetch); None if unusable."""
        from google.genai import types as t
        data = bytes(src) if isinstance(src, (bytes, bytearray)) else self._fetch(src)
        if not data:
            return None
        return t.Image(image_bytes=data, mime_type=_detect_mime(data))

    def _save(self, resp, out_dir: str | Path) -> Path:
        imgs = getattr(resp, "generated_images", None) or []
        if not imgs:
            reason = getattr(resp, "rai_filtered_reason", "unknown")
            raise RuntimeError(f"Imagen edit returned no image (safety filter? reason={reason}).")
        path = _new_bg_path(out_dir)
        path.write_bytes(imgs[0].image.image_bytes)
        return path

    # -- Path 1: style conditioning --------------------------------------
    def style(
        self,
        prompt: str,
        style_refs: Sequence[ImgSrc],
        *,
        style_description: str = (
            "ONLY the brand's colour palette, lighting and overall mood. Do NOT reproduce or "
            "imitate any text, words, letters, numbers, logos, watermarks, captions, slogans or "
            "graphic typography from the reference images — the generated scene must be a clean, "
            "completely TEXT-FREE photograph"
        ),
        aspect_ratio: str = DEFAULT_ASPECT,
        out_dir: str | Path = DEFAULT_OUT,
        number_of_images: int = 1,
        max_refs: int = 2,   # Vertex: non-square (e.g. 3:4) signatures accept at most 2 refs
    ) -> Path:
        """Generate a FRESH on-brand scene conditioned on the brand's REAL creatives.
        `style_refs` = bytes or http(s) URLs (e.g. BrandCreativeDNA.references_seen)."""
        from google.genai import types as t

        images = []
        for s in (style_refs or []):
            im = self._image(s)
            if im is not None:
                images.append(im)
            if len(images) >= max_refs:
                break
        if not images:
            raise RuntimeError("style(): no usable style reference image (all unfetchable/blocked).")

        refs = [
            t.StyleReferenceImage(
                reference_image=im, reference_id=1,                 # one shared id => one style group
                config=t.StyleReferenceConfig(style_description=style_description),
            )
            for im in images
        ]
        full = (prompt or "").strip()
        if "[1]" not in full:
            full += " in the style of [1]."
        full += "\n\n" + _COMPOSITION_CONTRACT

        client = self._client()
        resp = paid_call(client.models.edit_image,
            model=self.model, prompt=full, reference_images=refs,
            config=t.EditImageConfig(
                edit_mode=t.EditMode.EDIT_MODE_STYLE,
                number_of_images=number_of_images, aspect_ratio=aspect_ratio,
                output_mime_type="image/png", safety_filter_level="BLOCK_ONLY_HIGH",
                person_generation="ALLOW_ADULT",
            ),
        )
        return self._save(resp, out_dir)

    # -- Path 2: outpaint a real photo -----------------------------------
    def outpaint(
        self,
        base: ImgSrc,
        prompt: str = "",
        *,
        target: tuple[int, int] = (1024, 1280),
        coverage: float = 0.66,
        out_dir: str | Path = DEFAULT_OUT,
        number_of_images: int = 1,
    ) -> Path:
        """Keep a REAL brand photo (`base` = bytes or http(s) URL) and naturally extend it to
        fill `target`. The real photo is preserved; only the border is generated."""
        from google.genai import types as t

        data = bytes(base) if isinstance(base, (bytes, bytearray)) else self._fetch(base)
        if not data:
            raise RuntimeError("outpaint(): could not load base image (unfetchable/blocked).")
        canvas, mask, _box = build_outpaint_canvas_and_mask(data, target=target, coverage=coverage)
        raw = t.RawReferenceImage(
            reference_image=t.Image(image_bytes=canvas, mime_type="image/png"), reference_id=0)
        mref = t.MaskReferenceImage(
            reference_image=t.Image(image_bytes=mask, mime_type="image/png"), reference_id=1,
            config=t.MaskReferenceConfig(
                mask_mode=t.MaskReferenceMode.MASK_MODE_USER_PROVIDED, mask_dilation=0.01),
        )
        full = ((prompt or "").strip() + "\n\n" + _OUTPAINT_GUARD).strip()
        client = self._client()
        resp = paid_call(client.models.edit_image,
            model=self.model, prompt=full, reference_images=[raw, mref],
            config=t.EditImageConfig(
                edit_mode=t.EditMode.EDIT_MODE_OUTPAINT,
                number_of_images=number_of_images,
                output_mime_type="image/png", safety_filter_level="BLOCK_ONLY_HIGH",
                person_generation="ALLOW_ADULT",
            ),
        )
        return self._save(resp, out_dir)
