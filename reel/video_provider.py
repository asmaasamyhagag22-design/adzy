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


def _videos_config(types, dur):
    """Veo config. person_generation='ALLOW_ADULT' so Veo actually RENDERS the PERSON the creative-
    director prompt asks for (a hand/model using the product) — without it Veo suppresses people and
    falls back to a camera-only move over the still (the 'static zoom' the owner saw). Falls back to
    the minimal config if the pinned SDK build rejects the field."""
    base = dict(aspect_ratio=DEFAULT_ASPECT, number_of_videos=1, duration_seconds=dur)
    try:
        return types.GenerateVideosConfig(person_generation="ALLOW_ADULT", **base)
    except (TypeError, ValueError):
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

        dur = min(_VEO_DURATIONS, key=lambda v: abs(v - duration_s))   # snap to 4/6/8

        # SPEND GUARD (2026-07-14): Veo is the single most expensive Google-AI op — meter it and
        # refuse (BEFORE any client/network work) if this month's metered renders would breach the
        # cap (VERTEX_MONTHLY_CAP_USD), so no reel burns credit invisibly. SpendRefused propagates
        # up as a clear render failure.
        from vertex_spend import guard_video, record
        est_usd = guard_video(dur, detail=Path(out_path).name, model=self.model)
        print(f"[veo][spend] est ${est_usd:.2f} for {dur}s ({Path(out_path).name})", file=sys.stderr)

        client = self._client()

        def _submit(image_obj):
            kwargs = dict(
                model=self.model,
                prompt=prompt,
                config=_videos_config(types, dur),
            )
            if image_obj is not None:
                kwargs["image"] = image_obj           # image-to-video seed
            return client.models.generate_videos(**kwargs)

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

        try:
            operation = _submit(image_obj)
        except Exception as exc:                      # noqa: BLE001
            if image_obj is None:
                raise
            print(f"[veo] image-to-video rejected ({type(exc).__name__}); "
                  "falling back to text-to-video", file=sys.stderr)
            operation = _submit(None)                 # i2v rejected -> text-to-video

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



# ---------------------------------------------------------------------------
# Kling — image-to-video (the owner's video stack from 2026-07-25)
# ---------------------------------------------------------------------------

_KLING_BASE = "https://api-singapore.klingai.com"
_KLING_SUBMIT = "/v1/videos/image2video"
_KLING_TEXT_SUBMIT = "/v1/videos/text2video"


class _UrllibKlingTransport:
    """The ONLY class here that touches the network (timeout-bounded), so every test injects a
    fake and nothing in the suite can reach Kling by accident. Same shape as the Meta seam."""

    def post(self, url, *, headers=None, json_body=None):
        import json as _json
        import urllib.request
        data = _json.dumps(json_body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            return _json.loads(r.read().decode("utf-8"))

    def get(self, url, *, headers=None):
        import json as _json
        import urllib.request
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=60) as r:
            return _json.loads(r.read().decode("utf-8"))

    def download(self, url, dest):
        import shutil
        import urllib.request
        from pathlib import Path as _P
        _P(dest).parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return dest


def _kling_jwt(access_key: str, secret_key: str, *, now: Optional[int] = None) -> str:
    """Kling signs each request with a short-lived JWT built from the access/secret PAIR.

    The secret is a long-lived credential and must never travel in a header: it signs, it is not
    sent. Encoded here rather than pulled from a JWT library so the provider has no new dependency
    — the token is three base64url segments and an HMAC-SHA256, and writing it out keeps the
    "never send the secret" property visible at the point it matters.
    """
    import base64
    import hashlib
    import hmac
    import json as _json
    import time as _time

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    iat = int(now if now is not None else _time.time())
    header = _b64(_json.dumps({"alg": "HS256", "typ": "JWT"},
                              separators=(",", ":")).encode("utf-8"))
    # 30-minute validity, backdated 5s for clock skew — Kling's documented envelope.
    payload = _b64(_json.dumps({"iss": access_key, "exp": iat + 1800, "nbf": iat - 5},
                               separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


class KlingProvider:
    """Kling image-to-video: animate a still instead of inventing a scene from text.

    OWNER RULING 2026-07-25: «kling هعمل بيه فيديوهات بدل veo هطلع صور من نانو بانانا لايت
    واحركها» — stills from Nano Banana Lite, animated by Kling, replacing Veo text-to-video. The
    driver is cost: Veo bills $0.40/SECOND, and an uncapped Veo renderer is what produced this
    project's $228-in-four-days incident.

    IMAGE-TO-VIDEO IS THE POINT, so `reference_image` is not decoration here: given a still we
    submit to the i2v endpoint with that frame attached. With no still we fall back to t2v, which
    is honest but is not what this provider exists for.

    It implements the SAME `VideoProvider` protocol as Veo, so the compositor, its Ken Burns
    fallback and every QA gate downstream work unchanged — no parallel path.

    RAISES on failure, deliberately. `reel/compositor.py` already catches a provider error and
    falls back to Ken Burns stills; returning a path to nothing instead would defeat that.
    """
    name = "kling"

    def __init__(self, *, access_key: Optional[str] = None, secret_key: Optional[str] = None,
                 model: Optional[str] = None, transport: Any = None,
                 poll_seconds: int = 6, max_wait_seconds: int = 600,
                 base_url: Optional[str] = None):
        # ONE VALUE OR TWO. Kling's open platform issues an access/secret PAIR and signs a JWT with
        # it; some consoles hand out a single token instead. The owner has a single secret
        # («هو سكرت بس»), so `KLING_API_KEY` is accepted and sent as a plain bearer — the pair is
        # still preferred and still signs when both are present. Guessing an access key from a
        # secret would produce a token that fails on every call for no visible reason.
        self.access_key = access_key or os.getenv("KLING_ACCESS_KEY") or ""
        self.secret_key = (secret_key or os.getenv("KLING_SECRET_KEY")
                           or os.getenv("KLING_API_KEY") or "")
        self.single_token = bool(not self.access_key and self.secret_key)
        self.model = model or os.getenv("KLING_MODEL") or "kling-v1-6"
        self.transport = transport or _UrllibKlingTransport()
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds
        self.base_url = (base_url or os.getenv("KLING_BASE_URL") or _KLING_BASE).rstrip("/")

    # ---- auth ----
    def _headers(self) -> dict:
        if not self.secret_key:
            raise RuntimeError(
                "No Kling credentials. Set KLING_ACCESS_KEY + KLING_SECRET_KEY in .env, or "
                "KLING_API_KEY if your console issued a single token.")
        token = (self.secret_key if self.single_token
                 else _kling_jwt(self.access_key, self.secret_key))
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    # ---- the still ----
    @staticmethod
    def _image_field(reference_image: Optional[str]) -> Optional[str]:
        """Kling takes the frame as base64 or as a public URL. A local path is read and encoded;
        an http(s) URL is passed through."""
        if not reference_image:
            return None
        src = str(reference_image)
        if src.lower().startswith(("http://", "https://")):
            return src
        try:
            import base64
            from pathlib import Path as _P
            raw = _P(src).read_bytes()
        except Exception:  # noqa: BLE001 — an unreadable still is "no still", never a crash
            return None
        return base64.b64encode(raw).decode("ascii")

    def generate(self, prompt: str, *, out_path, duration_s: float, width: int, height: int,
                 palette: Optional[list] = None, reference_image: Optional[str] = None):
        from pathlib import Path as _P
        headers = self._headers()
        image = self._image_field(reference_image)
        # Kling bills in whole 5s/10s clips; ask for the tier that covers what was requested.
        seconds = 10 if float(duration_s or 5) > 5 else 5
        # THE MOTION CONTRACT (brief rule 1). Imported, not inlined: the provider is swappable and
        # a contract written inside one class is a contract the next provider ships without.
        # Before this the body carried no negative prompt and no adherence control at all, so an
        # i2v render of a still had nothing forbidding it from staying a still.
        from reel.motion_contract import (MOTION_CFG_SCALE, MOTION_NEGATIVE,
                                          apply_motion_contract)
        body = {"model_name": self.model,
                "prompt": apply_motion_contract(str(prompt or ""))[:2000],
                "negative_prompt": MOTION_NEGATIVE[:2000],
                "cfg_scale": MOTION_CFG_SCALE,
                "duration": str(seconds), "aspect_ratio": _kling_ratio(width, height)}
        if image:
            body["image"] = image
            path = _KLING_SUBMIT
            print("[kling] mode=i2v", file=sys.stderr)
        else:
            # T2V IS A REFUSAL HERE, NOT A DOWNGRADE. The owner's plan is stills animated by
            # Kling; a clip with no seed frame is the model inventing footage — the exact opposite.
            # MEASURED (Abu Auf, runs/kling_spend.jsonl): 3 of 5 scenes silently went t2v because
            # only intro/outro had a reference, and the only trace was a ledger row nobody reads
            # mid-run. Raising is safe: the compositor already catches a provider error and falls
            # back to Ken Burns over the seed frame — real footage, not invented.
            if os.getenv("KLING_ALLOW_T2V") != "1":
                raise RuntimeError(
                    "Kling t2v refused: this scene has no seed frame to animate. Generate a seed "
                    "still (the default pipeline does), or set KLING_ALLOW_T2V=1 to permit "
                    "text-to-video explicitly.")
            path = _KLING_TEXT_SUBMIT
            print("[kling] mode=t2v (KLING_ALLOW_T2V=1)", file=sys.stderr)

        res = self.transport.post(self.base_url + path, headers=headers, json_body=body)
        task_id = (((res or {}).get("data") or {}).get("task_id") or "")
        if not task_id:
            raise RuntimeError(f"Kling did not return a task id: {str(res)[:200]}")

        video_url = self._await_video(task_id, path, headers)
        out = _P(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.transport.download(video_url, out)
        _record_kling_spend(seconds=seconds, model=self.model, task_id=task_id,
                            image_to_video=bool(image))
        return out

    def _await_video(self, task_id: str, path: str, headers: dict) -> str:
        import time as _time
        deadline = _time.time() + max(0, self.max_wait_seconds)
        while True:
            res = self.transport.get(f"{self.base_url}{path}/{task_id}", headers=headers)
            data = (res or {}).get("data") or {}
            status = str(data.get("task_status") or "").lower()
            if status in ("succeed", "succeeded", "success"):
                videos = ((data.get("task_result") or {}).get("videos") or [])
                url = (videos[0] or {}).get("url") if videos else ""
                if not url:
                    raise RuntimeError("Kling reported success with no video url")
                return url
            if status in ("failed", "fail", "error"):
                raise RuntimeError(f"Kling task failed: {str(data.get('task_status_msg'))[:200]}")
            if _time.time() >= deadline:
                raise TimeoutError(
                    f"Kling task {task_id} still '{status}' after {self.max_wait_seconds}s")
            _time.sleep(max(0, self.poll_seconds))


def _kling_ratio(width: int, height: int) -> str:
    """Nearest ratio Kling accepts. A vertical reel is 9:16; anything square-ish is 1:1."""
    try:
        r = float(width) / float(height)
    except Exception:  # noqa: BLE001
        return "9:16"
    if r < 0.75:
        return "9:16"
    if r > 1.4:
        return "16:9"
    return "1:1"


# Published Kling pricing is per CLIP, not per second — the reason this stack is cheaper than Veo
# at $0.40/s. Configurable, because a price list is a fact about the vendor and not about us.
def _kling_usd(seconds: int) -> float:
    key = "KLING_USD_PER_10S" if seconds > 5 else "KLING_USD_PER_5S"
    default = "0.56" if seconds > 5 else "0.28"
    try:
        return float(os.getenv(key, default))
    except ValueError:
        return float(default)


def _record_kling_spend(*, seconds: int, model: str, task_id: str,
                        image_to_video: bool) -> None:
    """Every paid clip lands in a ledger. The monthly CAPS were lifted on the owner's instruction;
    the METERS were not, and a new paid path that recorded nothing would be the single place spend
    became invisible. Never raises — a ledger write must not cost a finished clip."""
    import json as _json
    import time as _time
    from pathlib import Path as _P
    try:
        led = _P(os.getenv("KLING_SPEND_LEDGER", "runs/kling_spend.jsonl"))
        led.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": _time.time(), "provider": "kling", "model": model,
               "duration_s": seconds, "usd": _kling_usd(seconds),
               "mode": "i2v" if image_to_video else "t2v", "task_id": task_id}
        with open(led, "a", encoding="utf-8") as f:
            f.write(_json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def default_video_provider() -> VideoProvider:
    """Pick a video provider from the environment.

    EXPLICIT selection (REEL_VIDEO_BACKEND) always wins:
      REEL_FORCE_STUB=1                 -> offline stub (no creds, no cost)
      REEL_VIDEO_BACKEND=veo|vertex     -> Veo 3.1 on our Vertex project
      REEL_VIDEO_BACKEND=aiml           -> AIML gateway (Veo 3.1 i2v)
      REEL_VIDEO_BACKEND=stub           -> offline stub

    AUTO (no REEL_VIDEO_BACKEND): KLING first whenever its credentials exist, then Veo, then
    AIML, then the stub. This docstring used to say Veo was the AUTO default and it CONTRADICTED
    the code below it — Kling took that position on 2026-07-25 on the owner's cost ruling. Anyone
    quoting the docstring would have told her a reel render bills $0.40/second when it does not.
    A stale THIRD-PARTY key must NOT hijack the pipeline — AIML stays opt-in via
    REEL_VIDEO_BACKEND. (Runway was REMOVED 2026-07-05 by owner directive: a stale len-132
    RUNWAY_API_KEY had silently overridden Veo and returned HTTP 400 on every scene, so the
    reel fell back to Ken Burns stills — no real motion.)
    """
    if os.getenv("REEL_FORCE_STUB") == "1":
        return StubVideoProvider()
    backend = (os.getenv("REEL_VIDEO_BACKEND") or "").lower()

    # Explicit backend selection wins, whatever keys happen to be present.
    if backend == "kling":
        return KlingProvider()
    if backend in ("veo", "vertex"):
        return VeoProvider()
    if backend == "aiml":
        return AimlVeoProvider()
    if backend == "stub":
        return StubVideoProvider()

    # AUTO: KLING FIRST when its credentials exist. The owner's 2026-07-25 ruling is that Kling
    # REPLACES Veo for video — stills from Nano Banana Lite, animated — and the driver is cost:
    # Veo bills $0.40/SECOND against Kling's per-clip price. Making it the default rather than an
    # opt-in backend means nothing has to remember to set a variable before every run, which is
    # exactly how a stale third-party key silently hijacked this pipeline once before (Runway,
    # removed 2026-07-05). Veo stays the fallback and is still reachable via REEL_VIDEO_BACKEND.
    if (os.getenv("KLING_ACCESS_KEY") and os.getenv("KLING_SECRET_KEY"))             or os.getenv("KLING_API_KEY"):
        return KlingProvider()
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_CLOUD_PROJECT"):
        return VeoProvider()
    if os.getenv("AIML_API_KEY"):
        return AimlVeoProvider()
    return StubVideoProvider()
