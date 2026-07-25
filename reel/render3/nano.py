"""The Nano Banana image generator — reference-conditioned seeds (§10: Imagen -> Nano Banana).

Model resolution (D-R3.1, probed live at build time):
  fast lane  gemini-3.1-flash-image-preview  @ location=global   (seeds, character sheet)
  pro lane   gemini-3-pro-image-preview      @ location=global   (REAL_CONTENT legible text)
  legacy     gemini-2.5-flash-image          @ us-central1       (fallback)
Env overrides: NANO_FAST_MODEL / NANO_PRO_MODEL. The RESOLVED model id is logged per request
(pack 🌐 requirement). Quota-aware retry reuses the poster's measured backoff.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FAST = [("gemini-3.1-flash-image-preview", "global"),
         ("gemini-2.5-flash-image", "us-central1")]
_PRO = [("gemini-3-pro-image-preview", "global")] + _FAST

_CLIENTS: dict = {}


def _client(location: str):
    from google import genai
    if location not in _CLIENTS:
        _CLIENTS[location] = genai.Client(
            vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"], location=location)
    return _CLIENTS[location]


def lanes(pro: bool = False) -> list[tuple[str, str]]:
    """The (model, location) preference order, env-overridable."""
    if pro:
        env = os.environ.get("NANO_PRO_MODEL")
        return ([(env, "global")] if env else []) + _PRO
    env = os.environ.get("NANO_FAST_MODEL")
    return ([(env, "global")] if env else []) + _FAST


def generate_with_refs(prompt: str, out_path: "str | Path", *,
                       refs: Optional[list[tuple[bytes, str]]] = None,
                       pro: bool = False, log=logger.info) -> Path:
    """One reference-conditioned still. Refs are attached IN ORDER (image 1 = identity
    reference sheet, image 2 = real location, image 3 = real screenshot — §4). Falls down
    the lane list on 404 (model unavailable); quota-aware backoff on 429/5xx. Raises when
    every lane fails — the caller decides, nothing ships unmeasured."""
    from google.genai import types

    from poster.oneshot import _is_retryable   # the measured backoff classifier

    contents: list[Any] = []
    for data, mime in (refs or []):
        if data:
            contents.append(types.Part.from_bytes(data=data, mime_type=mime or "image/png"))
    contents.append(prompt)

    # SPEND GUARD: meter each generated still and refuse if over the monthly Vertex cap.
    from vertex_spend import guard_image, record
    _mtag = "gemini-3-pro-image-preview" if pro else "gemini-3.1-flash-image-preview"
    est_usd = guard_image(1, _mtag, detail=Path(out_path).name)

    import time as _time
    last: Optional[Exception] = None
    for model, location in lanes(pro):
        cl = _client(location)
        for i, delay_s in enumerate((0, 20, 45)):
            if delay_s:
                _time.sleep(delay_s)
            try:
                resp = cl.models.generate_content(
                    model=model, contents=contents,
                    config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]))
                for cand in (resp.candidates or []):
                    for part in (cand.content.parts or []):
                        inline = getattr(part, "inline_data", None)
                        if inline and (inline.mime_type or "").startswith("image/"):
                            out = Path(out_path)
                            out.parent.mkdir(parents=True, exist_ok=True)
                            data = inline.data
                            if isinstance(data, str):
                                import base64
                                data = base64.b64decode(data)
                            out.write_bytes(data)
                            # PRICE THE LANE THAT SERVED, not the one that was requested.
                            # MEASURED: `est_usd` above is computed from the REQUESTED flag, so a
                            # pro request that 404s and is rescued by flash used to land in the
                            # ledger as model=flash at the pro price — a 3x over-report on every
                            # rescued still. `guard_image` keeps the requested price: worst-case
                            # is correct for the budget CHECK, only the RECORD must be true.
                            from vertex_spend import est_image_cost_usd
                            record("image", 1, est_image_cost_usd(1, model),
                                   model=model, detail=out.name)  # metered, honestly
                            log(f"[nano] {model}@{location} -> {out.name} "
                                f"({len(refs or [])} ref(s))")   # resolved id per request
                            return out
                raise RuntimeError(f"{model} returned no image")
            except Exception as e:  # noqa: BLE001
                last = e
                code = getattr(e, "status_code", None)
                if code == 404 or "NOT_FOUND" in str(e)[:120]:
                    log(f"[nano] {model}@{location} unavailable (404) -> next lane")
                    break                       # next lane immediately
                if not _is_retryable(e) or i == 2:
                    log(f"[nano] {model}@{location} failed: {type(e).__name__}")
                    break
    raise RuntimeError(f"nano generation failed on every lane: {type(last).__name__}: "
                       f"{str(last)[:160]}")
