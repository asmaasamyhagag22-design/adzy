"""Image providers for the Poster Studio (mirrors the LLM `Caller` pattern).

An `ImageProvider` generates the campaign-scene BACKGROUND and returns its PNG
path. Implementations never render text into the image — all poster text is
overlaid later in the HTML/CSS layer (Poster Studio decisions §5). The backend is
swappable so the pipeline can run against the real model (Vertex/Imagen) or an
offline stub without burning credits.

  ImageProvider          - the protocol
  VertexImagenProvider   - real: Google Imagen 4 Ultra via Vertex AI (ADC auth)
  StubImageProvider      - offline: a neutral gradient (no network, no Pillow)

`generate_background(...)` is kept as a thin backward-compatible wrapper around
VertexImagenProvider.
"""
from __future__ import annotations

from poster.paid_boundary import paid_call

import os
import uuid
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

DEFAULT_MODEL = "imagen-4.0-ultra-generate-001"
# Closest Imagen ratio to the 1080x1350 (4:5) poster canvas; CSS `cover` handles
# the small remainder. (Imagen offers 1:1, 9:16, 16:9, 3:4, 4:3.)
DEFAULT_ASPECT = "3:4"
DEFAULT_OUT = "outputs/posters/backgrounds"

# Universal composition contract appended to every real prompt. Enforces the
# Poster Studio decision (§2.2): the image is a real campaign scene with
# INTENTIONAL empty composition zones — one seamless image, never a collage or
# dimmed wallpaper. Text is never drawn by the image model (overlaid later).
_COMPOSITION_CONTRACT = (
    "Composition requirements: render ONE single, continuous photographic scene. "
    "Do NOT produce a collage, split-screen, diptych, grid, or multi-panel image; "
    "no internal seams, dividing lines, borders, frames, or picture-in-picture. "
    "CRITICAL — this OVERRIDES any request for calm or empty space: do NOT fill any part "
    "of the frame with a flat solid color field, a large flat color block, a hard-edged or "
    "color-blocked panel, or a diagonal band/wedge that divides the image. The reserved calm "
    "area must be a NATURAL part of the single photo (a softly-lit wall, a shallow-focus "
    "background, or atmospheric depth), only as large as the text needs, plus clean margins. "
    "The scene MUST fill the ENTIRE vertical frame edge-to-edge (FULL-BLEED): absolutely NO "
    "black bars, NO letterbox or pillarbox, NO cinematic bars, NO empty/solid borders or "
    "margins — the photographed scene reaches all four edges of a tall vertical portrait frame. "
    "CRITICAL — the strictest rule, it OVERRIDES everything else: the image MUST be 100% FREE of "
    "any TYPOGRAPHY or WRITTEN LANGUAGE. Absolutely NO text, words, letters, numbers, captions, "
    "subtitles, labels, watermarks, signatures, logos, UI, menus, price tags, billboards, posters, "
    "book covers, or signage — in ANY script (Latin, Arabic, or any other). Any surface that would "
    "normally carry writing — a sign, a phone/laptop screen, a product package, a banner, a wall — "
    "must be left BLANK, abstract, or out of focus. A SINGLE rendered letter ruins the asset; "
    "render people, places and objects ONLY, never text. "
    "Photorealistic and high-resolution: crisp focus, fine detail, natural lighting. Any PEOPLE "
    "must look REAL and lifelike — natural skin texture and facial features, natural eyes with "
    "catchlights, well-formed hands; NOT plastic, waxy, distorted, asymmetric, blurry, or "
    "AI-uncanny. Commercial editorial photography quality."
)


@runtime_checkable
class ImageProvider(Protocol):
    """Generates a background scene and returns the saved PNG path.

    Mirrors the LLM `Caller` protocol: a swappable image backend. Implementations
    MUST NOT draw text into the image (text is overlaid later in HTML/CSS).
    """
    name: str

    def generate(
        self,
        prompt: str,
        *,
        out_dir: str | Path = DEFAULT_OUT,
        aspect_ratio: str = DEFAULT_ASPECT,
    ) -> Path:
        ...


def _new_bg_path(out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out / f"bg_{uuid.uuid4().hex[:8]}.png"


class VertexImagenProvider:
    """Real provider: Google Imagen via Vertex AI.

    Auth = Application Default Credentials (gcloud); project/location come from
    constructor args or the env (GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION).
    The SDK is imported lazily so offline use of the stub needs no google-genai.
    """
    name = "vertex-imagen"

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        # Default Imagen 4 Ultra; override via IMAGEN_MODEL (e.g. imagen-4.0-generate-001)
        # when Ultra is capacity/quota-limited (429).
        self.model = model or os.getenv("IMAGEN_MODEL") or DEFAULT_MODEL

    def generate(
        self,
        prompt: str,
        *,
        out_dir: str | Path = DEFAULT_OUT,
        aspect_ratio: str = DEFAULT_ASPECT,
        bake_text: bool = False,
    ) -> Path:
        from google import genai
        from google.genai import types

        if not self.project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT not set. Add it to .env (the Vertex project ID, "
                "e.g. image-498715) and ensure `gcloud auth application-default login` was run."
            )

        # By default the image must be TEXT-FREE (text is overlaid later). When
        # bake_text=True the caller's prompt deliberately includes baked typography,
        # so we skip the no-text composition contract.
        full_prompt = (prompt or "").strip()
        if not bake_text:
            full_prompt += "\n\n" + _COMPOSITION_CONTRACT

        client = genai.Client(vertexai=True, project=self.project, location=self.location)
        resp = paid_call(client.models.generate_images,
            model=self.model,
            prompt=full_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                output_mime_type="image/png",
                safety_filter_level="BLOCK_ONLY_HIGH",
                person_generation="ALLOW_ADULT",
            ),
        )
        imgs = getattr(resp, "generated_images", None) or []
        if not imgs:
            reason = getattr(resp, "rai_filtered_reason", "unknown")
            raise RuntimeError(f"Imagen returned no image (safety filter? reason={reason}).")

        path = _new_bg_path(out_dir)
        path.write_bytes(imgs[0].image.image_bytes)
        return path


class StubImageProvider:
    """Offline provider: a calm neutral gradient rendered via Playwright.

    No network, no credits, no Pillow — so the full poster pipeline (and tests)
    can run without Vertex. The prompt is ignored by design.
    """
    name = "stub"

    def __init__(self, top: str = "#21303f", bottom: str = "#0b0f16"):
        self.top = top
        self.bottom = bottom

    def generate(
        self,
        prompt: str,
        *,
        out_dir: str | Path = DEFAULT_OUT,
        aspect_ratio: str = DEFAULT_ASPECT,
    ) -> Path:
        from poster.render_playwright import render_html_to_png, POSTER_W, POSTER_H

        html = (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            f"html,body{{margin:0;padding:0;width:{POSTER_W}px;height:{POSTER_H}px;}}"
            f".bg{{width:{POSTER_W}px;height:{POSTER_H}px;"
            f"background:linear-gradient(160deg,{self.top} 0%,{self.bottom} 100%);}}"
            "</style></head><body><div class='bg'></div></body></html>"
        )
        path = _new_bg_path(out_dir)
        render_html_to_png(html, path)
        return path


def generate_background(
    prompt: str,
    *,
    out_dir: str | Path = DEFAULT_OUT,
    project: Optional[str] = None,
    location: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = DEFAULT_ASPECT,
) -> Path:
    """Backward-compatible helper: real Vertex/Imagen generation."""
    return VertexImagenProvider(project=project, location=location, model=model).generate(
        prompt, out_dir=out_dir, aspect_ratio=aspect_ratio
    )
