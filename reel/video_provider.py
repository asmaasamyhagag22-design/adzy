"""Video providers for the Reel Studio (mirrors the poster's ImageProvider).

A `VideoProvider` generates ONE text-free scene clip and returns its mp4 path.
Like the poster's image providers, NONE of them draw text — all words are overlaid
later by the compositor (so Arabic shapes correctly; Veo, like Imagen, garbles
baked text).

  VideoProvider        - the protocol
  VeoProvider          - real: Google Veo 3.1 (Gemini API key OR Vertex AI)
  StubVideoProvider    - offline: animated brand-palette gradient (ffmpeg, no creds)

Two ways to authenticate VeoProvider (it auto-detects):
  * EASY  - a Gemini API key from Google AI Studio: set GEMINI_API_KEY (or
            GOOGLE_API_KEY). No gcloud needed.
  * Vertex - GOOGLE_CLOUD_PROJECT + ADC (`gcloud auth application-default login`).

Model defaults to Veo 3 GA (`veo-3.0-generate-001`) — the model actually provisioned
on this project (MEASURED: `veo-3.1-*-preview`/`veo-3.1-generate-001` 404 NOT_FOUND on
project image-498715). Override with REEL_VIDEO_MODEL once a 3.1 model is provisioned.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .ffmpeg_tools import hex_to_ffmpeg, run_ffmpeg

DEFAULT_ASPECT = "9:16"
# Veo 3.1 GA — GCP deprecated veo-3.0-generate-001 and recommends veo-3.1-generate-001.
# The earlier 404 was the `-preview` suffix; the GA `-generate-001` is the supported id.
# Override via REEL_VIDEO_MODEL. (Runs on Vertex via ADC + GOOGLE_CLOUD_PROJECT.)
DEFAULT_VEO_MODEL = "veo-3.1-generate-001"
# Veo accepts only these clip lengths (seconds).
_VEO_DURATIONS = (4, 6, 8)


def _reference_images(types, refs):
    """Google's INGREDIENTS-TO-VIDEO payload: the people/objects that must stay the same shot to
    shot. `refs` is a list of `(bytes, mime)`; returns None when there is nothing to pass.

    THE PROBLEM IT IS FOR. Veo re-invents a person from the prompt every clip, so a film's
    protagonist drifts between shots — measured on the NTI reel, where the woman in shot 1 is not
    the woman in shot 4. Google documents exactly one mechanism for this: "Provide reference images
    of a scene, character, object, or style to maintain a consistent aesthetic across multiple
    shots." The SDK exposes two kinds and we use ASSET — STYLE is a look reference, not an identity.

    HONEST SCOPE. The measured cause of NTI's drift was NOT this: three of its four scenes fell
    back to REAL stock photos as seeds (the seed gate rejected every generated still on text), so
    three different women were seeded on purpose. That is fixed at the seed prompt. This is the
    second line of defence — it keeps the identity when the seeds ARE consistent and Veo is the
    thing that would otherwise drift.
    """
    out = []
    for item in (refs or []):
        try:
            data, mime = item
            if not data:
                continue
            out.append(types.VideoGenerationReferenceImage(
                image=types.Image(image_bytes=data, mime_type=mime or "image/jpeg"),
                reference_type=types.VideoGenerationReferenceType.ASSET,
            ))
        except Exception:  # noqa: BLE001 — one unusable reference never costs the render
            continue
    return out or None


def _videos_config(types, dur, reference_images=None):
    """Veo config. person_generation='ALLOW_ADULT' so Veo actually RENDERS the PERSON the creative-
    director prompt asks for (a hand/model using the product) — without it Veo suppresses people and
    falls back to a camera-only move over the still (the 'static zoom' the owner saw). Falls back to
    the minimal config if the pinned SDK build rejects a field.

    THE MOTION CONTRACT REACHES VEO HERE (2026-07-28). `reel/motion_contract.py` opens by saying it
    exists so the contract is "inherited by every video provider", and warns in its own words that
    "a contract written inside one class is a contract the next provider ships without". It was
    then applied in exactly one place — inside `KlingProvider` — and Veo received neither the
    positive clause nor the negative one. So every Veo render since has been generated at the
    provider's defaults with nothing forbidding a locked-off camera: the slideshow the contract was
    written to prevent, and the owner's rule 1. The module warned about itself and was right.
    `negative_prompt` is a real `GenerateVideosConfig` field on this SDK build — checked, not
    assumed — and the try/except keeps older builds working."""
    from reel.motion_contract import MOTION_NEGATIVE
    base = dict(aspect_ratio=DEFAULT_ASPECT, number_of_videos=1, duration_seconds=dur)
    # ASK FOR 1080p. `GenerateVideosConfig.resolution` defaults to None (checked against the
    # installed SDK, not assumed), which means Veo's own default — 720p. Every clip was therefore
    # generated at 720x1280 and then scaled up to the 1080x1920 canvas the compositor, the text
    # layer and the delivery envelope all work in, so the whole film has been running one upscale
    # behind its own format. It is also why `cinematic.py`'s reframe ladder had to cap at 1.48x:
    # punching into an already-upscaled frame softens twice.
    #
    # Ordered fallback, same discipline as the fields below it — an SDK build or a model that
    # rejects `resolution` keeps rendering at the old default rather than failing the run.
    # The identity references ride the RICHEST config only. If this SDK build or this model does
    # not know the field, the very next fallback drops them rather than the whole render.
    _refs = {"reference_images": reference_images} if reference_images else {}
    for kw in ({"person_generation": "ALLOW_ADULT", "negative_prompt": MOTION_NEGATIVE[:2000],
                "resolution": "1080p", **_refs},
               {"person_generation": "ALLOW_ADULT", "negative_prompt": MOTION_NEGATIVE[:2000],
                "resolution": "1080p"},
               {"person_generation": "ALLOW_ADULT", "negative_prompt": MOTION_NEGATIVE[:2000]},
               {"person_generation": "ALLOW_ADULT"},
               {}):
        try:
            return types.GenerateVideosConfig(**kw, **base)
        except (TypeError, ValueError):
            continue
    return types.GenerateVideosConfig(**base)


@runtime_checkable
class VideoProvider(Protocol):
    """Generates a text-free scene clip and returns the saved mp4 path.

    `reference_image` (optional) is a scraped brand photo (local path or public
    http(s) URL) used to GROUND generation: real providers seed image-to-video
    from it so the clip resembles the brand's actual imagery. Providers that can't
    condition on an image MUST ignore it gracefully (never fail because of it).
    """
    name: str

    def generate(
        self,
        prompt: str,
        *,
        out_path: Path,
        duration_s: float,
        width: int,
        height: int,
        palette: Optional[list[str]] = None,
        reference_image: Optional[str] = None,
    ) -> Path:
        ...


def _new_clip_path(out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out / f"clip_{uuid.uuid4().hex[:8]}.mp4"


def _mime_for(ctype: str, low_src: str) -> Optional[str]:
    """Pick a raster image MIME from a Content-Type header and/or the src suffix.
    Returns None for SVG / unknown (Veo needs a raster seed)."""
    if "svg" in ctype or low_src.endswith(".svg"):
        return None
    if "jpeg" in ctype or "jpg" in ctype or low_src.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if "webp" in ctype or low_src.endswith(".webp"):
        return "image/webp"
    if "gif" in ctype or low_src.endswith(".gif"):
        return "image/gif"
    if "png" in ctype or low_src.endswith(".png"):
        return "image/png"
    # Default to JPEG — the common case for a hero photo with no usable hint.
    return "image/jpeg"


def _edge_bg_color(img) -> tuple[int, int, int]:
    """The seed's own background colour, sampled from its four border strips (a 1px average of
    each edge, then averaged). A studio product shot on white/grey returns that exact tone, so
    padding with it is SEAMLESS — no visible band."""
    w, h = img.size
    tb = max(1, h // 12)
    lr = max(1, w // 12)
    strips = [
        img.crop((0, 0, w, tb)),            # top
        img.crop((0, h - tb, w, h)),        # bottom
        img.crop((0, 0, lr, h)),            # left
        img.crop((w - lr, 0, w, h)),        # right
    ]
    from PIL import Image
    cols = [s.resize((1, 1), Image.LANCZOS).getpixel((0, 0)) for s in strips]
    n = len(cols)
    return tuple(int(sum(c[i] for c in cols) / n) for i in range(3))  # type: ignore[return-value]


_OUTPAINT_CACHE: dict = {}


def _outpaint_seed(data: bytes, width: int, height: int):
    """Extend a REAL landscape photo to full 9:16 with Imagen outpainting — the real photo is
    preserved, only the border is generated (design domain; motion-QA setting_faithful still
    judges the clip against the ORIGINAL seed). Cents per UNIQUE seed, cached for the run.
    None without Vertex creds or on any failure (caller falls back to blur-contain)."""
    import hashlib
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return None
    key = hashlib.md5(data).hexdigest() + f":{width}x{height}"
    if key in _OUTPAINT_CACHE:
        return _OUTPAINT_CACHE[key]
    try:
        import tempfile
        from poster.imagen_edit_provider import ImagenEditProvider
        with tempfile.TemporaryDirectory(prefix="seed_op_") as td:
            out = ImagenEditProvider().outpaint(data, "", target=(width, height), out_dir=td)
            b = Path(out).read_bytes()
        _OUTPAINT_CACHE[key] = b
        return b
    except Exception:  # noqa: BLE001
        return None


def _to_vertical_seed(data: bytes, *, width: int = 768, height: int = 1366) -> tuple[bytes, str]:
    """Make a FULL-FRAME 9:16 seed so Veo i2v doesn't letterbox a landscape photo.

    Default 'auto' (owner 2026-07-12: "الفريم كله كأنه زوم مقصقص" — the old cover default
    center-cropped every landscape real photo to a 37-42% sliver, MEASURED on NTI's own
    photos, so the whole reel inherited a cropped, zoomed-in world): portrait-ish sources
    (ratio <= 0.8, crop loss <= ~30%) keep the sharp cover look; landscape sources OUTPAINT
    to full 9:16 (the real photo preserved, only the border generated), degrading to a
    blur-contain (whole photo visible over its own blurred copy). Explicit REEL_SEED_FILL
    still wins: cover / pad / blur / outpaint / none.
    Returns (jpeg_bytes, 'image/jpeg'); falls back to the input on any error."""
    mode = (os.getenv("REEL_SEED_FILL") or "auto").lower()
    if mode == "none":
        return data, "image/jpeg"
    try:
        import io
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
        img = Image.open(io.BytesIO(data)).convert("RGB")

        def _blur_contain():
            bg = ImageOps.fit(img, (width, height), method=Image.LANCZOS)
            bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(40))).enhance(0.55)
            fg = img.copy()
            fg.thumbnail((width, height), Image.LANCZOS)
            bg.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
            return bg

        def _outpaint_or_blur():
            ob = _outpaint_seed(data, width, height)
            if ob:
                c = Image.open(io.BytesIO(ob)).convert("RGB")
                if c.size != (width, height):
                    c = ImageOps.fit(c, (width, height), method=Image.LANCZOS)
                return c
            return _blur_contain()

        if mode == "auto":
            if img.width / max(1, img.height) <= 0.8:
                canvas = ImageOps.fit(img, (width, height), method=Image.LANCZOS)
            else:
                canvas = _outpaint_or_blur()
        elif mode == "outpaint":
            canvas = _outpaint_or_blur()
        elif mode == "pad":
            fg = img.copy()
            fg.thumbnail((width, height), Image.LANCZOS)
            canvas = Image.new("RGB", (width, height), _edge_bg_color(img))
            canvas.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
        elif mode == "blur":
            canvas = _blur_contain()
        else:  # cover (explicit only, since 'auto' became the default)
            # FIX-D3 crop safety: a STRONGLY-landscape source (banner-ish, often carrying
            # baked text) center-cropped to 9:16 keeps ~1/3 of its width and truncates words
            # mid-letter (measured on nti: 'TRAINING' rendered as 'TRAING'). Those CONTAIN
            # over the image's own edge color instead; real photos keep the sharp cover look.
            if img.width / max(1, img.height) > 1.6:
                fg = img.copy()
                fg.thumbnail((width, height), Image.LANCZOS)
                canvas = Image.new("RGB", (width, height), _edge_bg_color(img))
                canvas.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
            else:
                canvas = ImageOps.fit(img, (width, height), method=Image.LANCZOS)
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=88)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, "image/jpeg"


def _load_reference_image(src: str, *, timeout: int = 10) -> Optional[tuple[bytes, str]]:
    """Load a reference image as (bytes, mime). Accepts a local file path or a
    public http(s) URL (SSRF-guarded at fetch time — same discipline as the poster
    logo fetch: certifi first, unverified fallback ONLY on a cert-chain error, for
    a passive credential-free image GET). Returns None on any failure or for SVG."""
    if not src:
        return None
    s = str(src).strip()
    low = s.lower()

    local = Path(s)
    try:
        if local.is_file():
            data = local.read_bytes()
            mime = _mime_for("", low)
            return (data, mime) if (data and mime) else None
    except OSError:
        pass

    if not low.startswith(("http://", "https://")):
        return None
    try:
        from scraper.url_utils import is_safe_public_url
        if not is_safe_public_url(s):
            return None
        import ssl
        from urllib.request import Request, urlopen
        req = Request(s, headers={"User-Agent": "Mozilla/5.0 (ReelStudio)"})
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        try:
            resp = urlopen(req, timeout=timeout, context=ctx)
        except Exception as exc:                       # noqa: BLE001
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                resp = urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
            else:
                raise
        with resp as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            data = r.read(8_000_000)
        mime = _mime_for(ctype, low)
        return (data, mime) if (data and mime) else None
    except Exception:
        return None


class VeoProvider:
    """Real provider: Google Veo (3.1 by default).

    Auth auto-detects: a Gemini API key (GEMINI_API_KEY / GOOGLE_API_KEY) uses the
    Gemini Developer API; otherwise GOOGLE_CLOUD_PROJECT + ADC uses Vertex AI. The
    SDK is imported lazily so offline stub use needs neither.

    LIVE-ONLY: needs credentials + a paid tier with Veo access; it cannot run in a
    sandbox. Generation is a long-running operation, so we submit then poll.
    """
    name = "veo"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        location: Optional[str] = None,
        model: Optional[str] = None,
        poll_seconds: int = 10,
        max_wait_seconds: int = 600,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.model = model or os.getenv("REEL_VIDEO_MODEL") or DEFAULT_VEO_MODEL
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds

    def _client(self):
        from google import genai
        if self.api_key:                                    # Gemini API (no gcloud)
            return genai.Client(api_key=self.api_key)
        if self.project:                                    # Vertex AI (ADC)
            return genai.Client(vertexai=True, project=self.project, location=self.location)
        raise RuntimeError(
            "No Veo credentials. Set GEMINI_API_KEY (from Google AI Studio) in .env, "
            "OR GOOGLE_CLOUD_PROJECT + run `gcloud auth application-default login`."
        )

    def generate(
        self,
        prompt: str,
        *,
        out_path: Path,
        duration_s: float,
        width: int,
        height: int,
        palette: Optional[list[str]] = None,
        reference_image: Optional[str] = None,
    ) -> Path:
        from google.genai import types

        # BUY COVERAGE, CUT WHAT THE EDIT WANTS — the shape every commercial is shot in.
        #
        # This was `min(_VEO_DURATIONS, key=lambda v: abs(v - duration_s))` — the NEAREST allowed
        # length — so a 5-second scene bought a 4-second clip and the edit had to make 5 seconds out
        # of 4. MEASURED 2026-08-01 on `[4.0, 5.0, 6.0, 5.5]`: the rendered scene is still the right
        # LENGTH (the compositor's `-t` and the overlay see to that), but its last second contains
        # **11% identical consecutive frames** against 0% in every fully-bought scene, and the third
        # shot of `intra_scene_chain` gets 0.67s of footage instead of 1.67s — 40% of what the pace
        # was designed around. With ceil the freeze measures 0%.
        #
        # Ceiling costs real money on scenes that are not already 4/6/8 (a 4,5,6,5 reel buys 22s
        # instead of 20s, +22% of the Veo bill for that render) and that is the honest trade: what
        # we were saving, we were paying for in a frozen tail and a truncated cut.
        dur = min([v for v in _VEO_DURATIONS if v >= duration_s] or [max(_VEO_DURATIONS)])

        # SPEND GUARD (2026-07-14): Veo is the single most expensive Google-AI op — meter it and
        # refuse (BEFORE any client/network work) if this month's metered renders would breach the
        # cap (VERTEX_MONTHLY_CAP_USD), so no reel burns credit invisibly. SpendRefused propagates
        # up as a clear render failure.
        from vertex_spend import guard_video, record
        # `reserve_usd` — what the scenes still to come already owe. A RETRY passes the
        # base cost of every unshot scene so it cannot take their footage; a first attempt
        # passes 0. See the measured NTI run in `vertex_spend.guard_video`.
        est_usd = guard_video(dur, detail=Path(out_path).name, model=self.model,
                              reserve_usd=float(getattr(self, 'reserve_usd', 0.0) or 0.0))
        print(f"[veo][spend] est ${est_usd:.2f} for {dur}s ({Path(out_path).name})", file=sys.stderr)

        client = self._client()

        # The positive half of the same contract. Scene description first, motion modifiers
        # appended once — leading with camera language produces a beautiful shot of nothing.
        from reel.motion_contract import apply_motion_contract
        moving_prompt = apply_motion_contract(str(prompt or ""))

        # WHO MUST STAY THE SAME PERSON, shot to shot. Built once per call so the retry below can
        # drop them without rebuilding anything else.
        _refs = _reference_images(types, getattr(self, "identity_refs", None))
        if _refs:
            # Google's guide is explicit that the prompt has to POINT AT the references —
            # "Using the provided images for the detective, the woman, and the office setting,
            # create a medium shot of…". A reference nobody mentions is a reference the model is
            # free to ignore.
            moving_prompt = ("Using the provided reference image(s) for the person, keep the SAME "
                             "individual — same face, hair and wardrobe — as the shot continues. "
                             + moving_prompt)

        def _submit(image_obj, refs=_refs):
            kwargs = dict(
                model=self.model,
                prompt=moving_prompt,
                config=_videos_config(types, dur, reference_images=refs),
            )
            if image_obj is not None:
                kwargs["image"] = image_obj           # image-to-video seed
            return client.models.generate_videos(**kwargs)

        def _submit_with_ref_fallback(image_obj):
            """Ask with the identity references; if the SERVER refuses the combination, ask again
            without them.

            `_videos_config`'s try/except only catches the pydantic constructor rejecting a field —
            it cannot see a 400 from the API, and image-to-video plus ingredients-to-video may well
            be mutually exclusive modes on a given model. A refused request generates nothing, so
            the retry costs one round-trip and no money; losing the whole clip would cost both.
            """
            if not _refs:
                return _submit(image_obj, refs=None)
            try:
                return _submit(image_obj, refs=_refs)
            except Exception as exc:  # noqa: BLE001
                print(f"[veo] identity references refused ({type(exc).__name__}); "
                      f"re-submitting without them", file=sys.stderr)
                return _submit(image_obj, refs=None)

        # GROUNDING (#4): seed from the brand's real scraped photo when available,
        # so the footage resembles the actual brand. Never let it break a render —
        # if loading or i2v submission fails, fall back to pure text-to-video.
        # The stderr notes make the i2v-vs-fallback path OBSERVABLE in the reel log
        # (so "the reel is grounded in the real image" is verifiable, not assumed).
        image_obj = None
        if reference_image:
            loaded = _load_reference_image(str(reference_image))
            if loaded:
                data, mime = loaded
                # Letterbox-free: reframe the seed to a full 9:16 canvas so Veo
                # outputs a full-frame vertical clip (no black bars on landscape photos).
                data, mime = _to_vertical_seed(data)
                try:
                    image_obj = types.Image(image_bytes=data, mime_type=mime)
                    print(f"[veo] image-to-video seed: {mime}, {len(data)} bytes "
                          f"from {str(reference_image)[:80]}", file=sys.stderr)
                except Exception:
                    image_obj = None
            else:
                print(f"[veo] reference image unusable ({str(reference_image)[:80]}); "
                      "text-to-video", file=sys.stderr)

        # IMAGE-TO-VIDEO ONLY (owner, 2026-07-28): «عايزه VEO من غير صور في الخلفية بس، مجرد إنه
        # بيحرك الصور اللي نانو بانانا بيديهاله». Veo's job is to ANIMATE the still this pipeline
        # already grounded, composited and gated — not to invent a scene from prose.
        #
        # Both escapes are closed on purpose. Text-to-video was reachable two ways: with no
        # reference image at all, and as the automatic fallback when i2v was rejected — and a
        # fallback that quietly changes what is being generated is the failure class this repo
        # keeps paying for. An invented scene is worse than a failed scene: the caller already
        # has a faithful still and a Ken Burns path to fall back to, and neither can be chosen
        # if this silently returns something that looks like a render.
        if image_obj is None:
            raise RuntimeError(
                "Veo is image-to-video only here: no usable seed frame was supplied"
                + (f" (reference {str(reference_image)[:80]} could not be loaded)"
                   if reference_image else " (no reference image at all)")
                + ". Generate the still first, or let the caller fall back to Ken Burns.")
        operation = _submit_with_ref_fallback(image_obj)

        waited = 0
        while not getattr(operation, "done", False):
            if waited >= self.max_wait_seconds:
                raise RuntimeError(f"Veo generation timed out after {waited}s.")
            time.sleep(self.poll_seconds)
            waited += self.poll_seconds
            operation = client.operations.get(operation)

        response = getattr(operation, "response", None) or getattr(operation, "result", None)
        vids = getattr(response, "generated_videos", None) or []
        if not vids:
            raise RuntimeError("Veo returned no video (safety filter, quota, or no access?).")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        gv = vids[0]
        video = getattr(gv, "video", gv)
        # Current SDK: download the file handle, then save / read bytes.
        try:
            client.files.download(file=video)
        except Exception:
            pass
        data = getattr(video, "video_bytes", None)
        if data:
            out_path.write_bytes(data)
        elif hasattr(video, "save"):
            video.save(str(out_path))
        else:
            raise RuntimeError("Veo video had no bytes to write.")
        record("veo", dur, est_usd, model=self.model, detail=Path(out_path).name)  # spend metered
        return out_path


class StubVideoProvider:
    """Offline provider: an animated gradient in the brand palette (ffmpeg only).

    No network, no credits, no Google creds — so the whole reel pipeline (and
    tests) runs locally. The prompt is ignored; the palette drives the colors.
    """
    name = "stub"

    def __init__(self, fallback_palette: Optional[list[str]] = None):
        self.fallback_palette = fallback_palette or ["#15314F", "#2E6E9E", "#0A1622"]

    def generate(
        self,
        prompt: str,
        *,
        out_path: Path,
        duration_s: float,
        width: int,
        height: int,
        palette: Optional[list[str]] = None,
        reference_image: Optional[str] = None,   # ignored: the stub has no model to seed
    ) -> Path:
        colors = [c for c in (palette or self.fallback_palette) if c][:3] or self.fallback_palette
        cparts = [f"c{i}={hex_to_ffmpeg(c)}" for i, c in enumerate(colors)]
        src = (
            f"gradients=s={width}x{height}:{':'.join(cparts)}"
            f":nb_colors={len(colors)}:speed=0.006:duration={duration_s:.2f}:rate=30:type=radial"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg([
            "-f", "lavfi", "-i", src,
            "-t", f"{duration_s:.2f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-preset", "veryfast",
            str(out_path),
        ])
        return out_path


# Ken Burns motion presets (slow zoom toward a focal region) — varied so a SINGLE
# real photo still feels alive across scenes. Each entry is (x_expr, y_expr); all
# zoom in slowly toward that region. Expressions are ffmpeg zoompan exprs over the
# pre-upscaled image (iw/ih are the big intermediate's dims; zoom is the per-frame z).
_KENBURNS_MOVES = (
    ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),   # 0: zoom into center
    ("iw/2-(iw/zoom/2)", "0"),                   # 1: drift toward the top
    ("iw/2-(iw/zoom/2)", "ih-(ih/zoom)"),        # 2: drift toward the bottom
    ("0", "ih/2-(ih/zoom/2)"),                   # 3: drift toward the left
    ("iw-(iw/zoom)", "ih/2-(ih/zoom/2)"),        # 4: drift toward the right
)


class KenBurnsProvider:
    """FAITHFUL provider: animates the business's REAL scraped photo(s) with a slow
    Ken Burns pan/zoom (ffmpeg `zoompan`). No model, no invented scenes — the reel
    literally comes from the actual place (the user's "REEL جاي من المكان اصلا").

    Cycles through the provided real images; varies the motion by call index so even
    one photo stays alive across scenes. Landscape photos are cover-cropped to the
    vertical frame. If an image can't be loaded (or none were given) it falls back to
    a brand-palette gradient so a render never breaks — but it NEVER fabricates a scene.
    """
    name = "kenburns"

    def __init__(
        self,
        images: Optional[list[str]] = None,
        *,
        fallback_palette: Optional[list[str]] = None,
    ):
        # Dedup-preserving order; drop blanks. These are real http(s)/local images.
        seen: set[str] = set()
        self.images = []
        for s in (images or []):
            s = (s or "").strip()
            if s and s not in seen:
                seen.add(s)
                self.images.append(s)
        self.fallback_palette = fallback_palette or ["#15314F", "#2E6E9E", "#0A1622"]
        self._call = 0  # advances per scene -> cycles image + motion preset

    def _gradient_fallback(self, out_path: Path, duration_s: float, width: int,
                           height: int, palette: Optional[list[str]]) -> Path:
        StubVideoProvider(self.fallback_palette).generate(
            "", out_path=out_path, duration_s=duration_s, width=width,
            height=height, palette=palette,
        )
        return out_path

    def generate(
        self,
        prompt: str,                              # ignored — the real photo IS the scene
        *,
        out_path: Path,
        duration_s: float,
        width: int,
        height: int,
        palette: Optional[list[str]] = None,
        reference_image: Optional[str] = None,
    ) -> Path:
        idx = self._call
        self._call += 1
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # The scene's own seed (reference_image, a real CONTENT photo) wins; the
        # provider's pool is the fallback. Try several (cycling) so an INTERMITTENT
        # CDN fetch failure on one photo yields ANOTHER real photo, not a gradient.
        candidates: list[str] = []
        if reference_image:
            candidates.append(str(reference_image))
        if self.images:
            n = len(self.images)
            candidates += [self.images[(idx + k) % n] for k in range(min(n, 4))]
        loaded = None
        for src in candidates:
            loaded = _load_reference_image(str(src))
            if loaded:
                break
        if not loaded:
            return self._gradient_fallback(out_path, duration_s, width, height, palette)

        data, mime = loaded
        # FIX-D3: normalize the seed to 9:16 FIRST (strongly-landscape sources contain
        # instead of center-cropping, so baked text is never truncated mid-word); the
        # vf's cover-crop then becomes a no-op scale for those frames.
        data, mime = _to_vertical_seed(data, width=width, height=height)
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "image/gif": ".gif"}.get(mime, ".jpg")
        img_path = out_path.with_suffix(ext + ".src")
        img_path.write_bytes(data)

        frames = max(2, int(round(duration_s * 30)))
        xexpr, yexpr = _KENBURNS_MOVES[idx % len(_KENBURNS_MOVES)]
        # Cover-crop the photo to the vertical frame, upscale (zoompan is smoother on
        # a big intermediate), then a slow ~12% zoom toward the preset's focal region.
        # Single quotes inside the expr are ffmpeg's own quoting (protect the commas).
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},scale=4000:-2,"
            f"zoompan=z='min(zoom+0.0011,1.12)':d={frames}:"
            f"x='{xexpr}':y='{yexpr}':s={width}x{height}:fps=30,"
            f"format=yuv420p"
        )
        try:
            run_ffmpeg([
                "-loop", "1", "-i", str(img_path), "-t", f"{duration_s:.2f}",
                "-vf", vf, "-r", "30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-profile:v", "high", "-preset", "medium", "-crf", "20",
                str(out_path),
            ])
        except Exception:
            return self._gradient_fallback(out_path, duration_s, width, height, palette)
        finally:
            try:
                img_path.unlink()
            except OSError:
                pass
        return out_path


class AimlVeoProvider:
    """Real provider via the AIML API gateway (https://aimlapi.com).

    Gives access to **Veo 3.1 image-to-video** (`google/veo-3.1-i2v`) WITHOUT Vertex
    provisioning (our project only has Veo 3.0 on Vertex), and Veo 3.1 renders NATIVE
    audio/voiceover straight from the prompt — so a voiceover line in the prompt is
    spoken in the clip (no separate TTS). Needs `AIML_API_KEY`. LIVE-ONLY (paid).

    Same `VideoProvider` contract as `VeoProvider`: one text-free scene clip per call.
    On ANY failure it RAISES, so the compositor's fallback (KenBurns over real photos ->
    gradient) takes over — a scene never silently degrades into a blank.
    """
    name = "aiml-veo"
    BASE_URL = "https://api.aimlapi.com/v2"
    DEFAULT_MODEL = "google/veo-3.1-i2v"

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None,
                 aspect_ratio: str = "9:16", poll_interval: int = 20,
                 max_polls: int = 60, timeout: int = 60):
        self.api_key = api_key or os.getenv("AIML_API_KEY")
        self.model = model or os.getenv("REEL_AIML_MODEL") or self.DEFAULT_MODEL
        self.aspect_ratio = aspect_ratio
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post_json(self, path: str, payload: dict) -> dict:
        import json
        import urllib.request
        req = urllib.request.Request(
            f"{self.BASE_URL}{path}", data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(), method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get_json(self, path: str, params: dict) -> dict:
        import json
        import urllib.parse
        import urllib.request
        req = urllib.request.Request(
            f"{self.BASE_URL}{path}?{urllib.parse.urlencode(params)}", headers=self._headers(),
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _submit(self, prompt: str, image_url: Optional[str]) -> str:
        payload = {"model": self.model, "prompt": prompt, "aspect_ratio": self.aspect_ratio}
        if image_url:
            payload["image_url"] = image_url           # image-to-video seed
        data = self._post_json("/video/generations", payload)
        gen_id = data.get("id") or data.get("generation_id")
        if not gen_id:
            raise RuntimeError(f"AIML submit returned no id: {str(data)[:160]}")
        return gen_id

    def _poll(self, gen_id: str) -> str:
        for _ in range(self.max_polls):
            time.sleep(self.poll_interval)
            data = self._get_json("/video/generations", {"generation_id": gen_id})
            status = str(data.get("status") or "")
            if status == "completed":
                # `video` may be a dict ({"url": ...}) OR a bare URL string, depending on
                # the gateway — handle both so a valid completed render isn't lost to an
                # AttributeError (which would silently degrade to the fallback).
                v = data.get("video")
                url = (v.get("url") if isinstance(v, dict) else v) or data.get("video_url")
                if not url:
                    raise RuntimeError("AIML completed but returned no video url")
                return url
            if status in ("failed", "error"):
                raise RuntimeError(f"AIML generation failed: {data.get('error')}")
        raise RuntimeError(
            f"AIML generation timed out after {self.max_polls * self.poll_interval}s"
        )

    def generate(
        self,
        prompt: str,
        *,
        out_path: Path,
        duration_s: float,
        width: int,
        height: int,
        palette: Optional[list[str]] = None,
        reference_image: Optional[str] = None,
    ) -> Path:
        if not self.api_key:
            raise RuntimeError("No AIML_API_KEY set for AimlVeoProvider.")
        import urllib.request
        # i2v needs a PUBLIC image URL; pass the seed only when it's http(s) (a scraped
        # photo). A local-file seed is skipped here -> text-to-video.
        ref = str(reference_image or "")
        image_url = ref if ref.startswith(("http://", "https://")) else None
        gen_id = self._submit(prompt, image_url)
        video_url = self._poll(gen_id)
        # The download URL comes from the third-party gateway response — guard it like
        # every other outbound fetch in this module: http(s) only (no file://), SSRF
        # gate, and a bounded read (a reel clip is a few MB; cap well above that).
        if not str(video_url).lower().startswith(("http://", "https://")):
            raise RuntimeError(f"AIML returned a non-http video url: {str(video_url)[:80]}")
        try:
            from scraper.url_utils import is_safe_public_url
            if not is_safe_public_url(video_url):
                raise RuntimeError("AIML video url failed the SSRF guard (private/loopback host)")
        except ImportError:
            pass
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(video_url, timeout=300) as r:
            out_path.write_bytes(r.read(200_000_000))   # ~200MB cap
        return out_path



def default_video_provider() -> VideoProvider:
    """Pick a video provider from the environment.

    EXPLICIT selection (REEL_VIDEO_BACKEND) always wins:
      REEL_FORCE_STUB=1                 -> offline stub (no creds, no cost)
      REEL_VIDEO_BACKEND=veo|vertex     -> Veo 3.1 on our Vertex project
      REEL_VIDEO_BACKEND=aiml           -> AIML gateway (Veo 3.1 i2v)
      REEL_VIDEO_BACKEND=stub           -> offline stub

    AUTO (no REEL_VIDEO_BACKEND): VEO, then AIML, then the stub.

    OWNER RULING 2026-07-28: «اشتغل بـVeo مش كلينج، شيل أي أثر لكلينج». Kling was the AUTO default
    from 2026-07-25 on a cost argument; it is gone — the provider, its credentials, its ledger
    writer and its tests. Veo bills $0.40/SECOND, so a 20s reel is roughly $8 a render and the
    spend guard in `VeoProvider.generate` is now the only thing between a run and a bill. That
    trade was made deliberately, with the number stated.

    A stale THIRD-PARTY key must NOT hijack the pipeline — AIML stays opt-in via
    REEL_VIDEO_BACKEND. (Runway was REMOVED 2026-07-05 by owner directive: a stale len-132
    RUNWAY_API_KEY had silently overridden Veo and returned HTTP 400 on every scene, so the
    reel fell back to Ken Burns stills — no real motion.)
    """
    if os.getenv("REEL_FORCE_STUB") == "1":
        return StubVideoProvider()
    backend = (os.getenv("REEL_VIDEO_BACKEND") or "").lower()

    # Explicit backend selection wins, whatever keys happen to be present.
    if backend in ("veo", "vertex"):
        return VeoProvider()
    if backend == "aiml":
        return AimlVeoProvider()
    if backend == "stub":
        return StubVideoProvider()

    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_CLOUD_PROJECT"):
        return VeoProvider()
    if os.getenv("AIML_API_KEY"):
        return AimlVeoProvider()
    return StubVideoProvider()
