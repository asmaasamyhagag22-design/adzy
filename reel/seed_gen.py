"""FULLY-GENERATED reel scenes (owner creative ruling, 2026-07-12).

Real photos EXIT the display path — no raw site photo appears inside a reel. Their role
EXPANDS instead: they become (a) GENERATION CONDITIONING — every scene seed is generated
9:16, conditioned on the brand's real place/product photos (the poster's C2 REAL-PLACE
mechanism), the BrandCreativeDNA style and the evidence-derived locale — and (b) JUDGING
REFERENCES — motion-QA's place-fidelity verdict still judges the generated world AGAINST
the real photo; the reference just stopped being visible.

SCOPE BOUNDARY (defense-critical): this covers REEL SCENES — an EXPRESSIVE surface,
generated-but-grounded. It does NOT touch evidence surfaces: real product photos in
commerce posters, composited real logos, verbatim copy. "Real stays real" remains the law
there. Anti-hallucination stays fully armed here (seed gate -> motion QA -> place
fidelity) — that stack is what makes "generated" different from "invented".

Generator: the same gemini-image call the poster one-shot uses (quota-aware retries
included) — Imagen edit models are not enabled on this project (measured 404).
Fail-closed: a seed that fails the art-critic gate twice falls back to the REAL photo via
the legacy blur-contain path (never ship a bad generated frame just to obey a style rule).
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def build_seed_prompt(veo_prompt: str, *, brand_name: str = "", dna_lines: str = "",
                      locale_line: str = "", n_refs: int = 0) -> str:
    """The conditioned still-frame prompt: the scene lives INSIDE the brand's real world."""
    place_block = ""
    if n_refs:
        place_block = (
            f"REAL PLACE (mandatory): the {n_refs} attached photo(s) are this brand's REAL "
            "premises/world, photographed on site. Set THE SCENE inside this real place — "
            "match its architecture, interiors, materials, era, upkeep and lighting mood so "
            "the frame unmistakably lives THERE. Do NOT paste or reproduce the photo itself; "
            "compose a FRESH cinematic frame that clearly belongs to the same real world.\n"
        )
    return (
        "A VERTICAL 9:16 photoreal cinematic FIRST FRAME of a brand film scene — a real "
        "camera still, not a poster and not an illustration.\n"
        f"THE SCENE: {(veo_prompt or '').strip()}\n"
        + place_block
        + (f"{locale_line}\n" if locale_line else "")
        + (f"The brand's visual world (themes and imagery, never colour tints): {dna_lines}\n"
           if dna_lines else "")
        + "ABSOLUTE RULES: no text, letters, numbers, logos, watermarks or signage anywhere "
        "in the frame; natural TRUE-TO-LIFE colours and warm realistic skin tones (no colour "
        "cast); ONE clear focal point, clean composition with natural headroom for vertical "
        "video; believable, professionally lit, ad-grade."
    )


def _ref_bytes(url: Optional[str]) -> Optional[tuple[bytes, str]]:
    if not url:
        return None
    try:
        from reel.video_provider import _load_reference_image
        loaded = _load_reference_image(str(url))
        return (loaded[0], loaded[1] if len(loaded) > 1 else "image/jpeg") if loaded else None
    except Exception:  # noqa: BLE001
        return None


def generate_scene_seeds(creative: Any, photos: List[str], *, profile: dict,
                         brand_dna: Any = None, out_dir: "str | Path" = "outputs/reels",
                         caller: Any = None,
                         log=logger.info) -> List[Tuple[Optional[str], Optional[str]]]:
    """One generated, gate-passed 9:16 seed per scene. Returns [(seed_path|None, real_ref_url)]
    aligned with creative.scenes — seed None means BOTH attempts failed the art-critic gate
    and the caller must fall back to the real photo (legacy path, logged loudly).
    Stats are logged for the cost report: generated / retried / fell_back."""
    from poster.oneshot import generate_oneshot_poster
    from reel.scene_qa import check_seed_frame

    dna_lines = ""
    if brand_dna is not None:
        bits = [str(getattr(brand_dna, f, "") or "").strip()
                for f in ("imagery_style", "mood", "motifs")]
        bits = [b if isinstance(b, str) else ", ".join(map(str, b)) for b in bits if b]
        dna_lines = "; ".join(bits)[:400]
    locale_line = ""
    try:
        from poster.locale import brand_locale
        _c, locale_line = brand_locale(profile)
    except Exception:  # noqa: BLE001
        locale_line = ""
    name = ""
    try:
        n = (profile.get("profile", profile) if isinstance(profile, dict) else {}).get("name")
        name = str(n.get("value") if isinstance(n, dict) else n or "")
    except Exception:  # noqa: BLE001
        name = ""

    out_root = Path(out_dir) / "_genseeds"
    out_root.mkdir(parents=True, exist_ok=True)
    stats = {"generated": 0, "retried": 0, "fell_back": 0}
    results: List[Tuple[Optional[str], Optional[str]]] = []
    ref_cache: dict = {}
    for i, sc in enumerate(getattr(creative, "scenes", []) or []):
        idx = int(getattr(sc, "image_index", 0) or 0)
        ref_url = photos[idx] if 0 <= idx < len(photos) else (photos[0] if photos else None)
        if ref_url not in ref_cache:
            ref_cache[ref_url] = _ref_bytes(ref_url)
        ref = ref_cache[ref_url]
        prompt = build_seed_prompt(getattr(sc, "veo_prompt", ""), brand_name=name,
                                   dna_lines=dna_lines, locale_line=locale_line,
                                   n_refs=1 if ref else 0)
        seed_path: Optional[str] = None
        # calibration #2 (owner: the raw distant building photo read as "قطع خالص"): a third
        # attempt drops the photo conditioning entirely — a clean on-brief scene beats a
        # world-breaking raw photo — before the caller ever considers a fallback.
        prompt_plain = build_seed_prompt(getattr(sc, "veo_prompt", ""), brand_name=name,
                                         dna_lines=dna_lines, locale_line=locale_line,
                                         n_refs=0)
        for attempt in (1, 2, 3):
            try:
                p = out_root / f"seed_{uuid.uuid4().hex[:8]}.png"
                a_prompt = (prompt if attempt == 1
                            else prompt + "\nNatural anatomy, realistic photographic light, "
                                 "one clean focal point." if attempt == 2
                            else prompt_plain)
                generate_oneshot_poster(
                    a_prompt,
                    p, logo_bytes=None,
                    product_images=([ref] if (ref and attempt < 3) else None))
                stats["generated"] += 1
                v = check_seed_frame(p, caller=caller, brand_hint=name)
                # FAIL-CLOSED (P6 #7): only an explicit PASS animates. This was
                # `(not v.checked) or v.overall_pass` — so no vision caller, an unreadable PNG, or
                # ONE Gemini timeout accepted the seed and spent Veo money on it, which is the
                # opposite of this module's "Fail-closed" promise. NOT_CHECKED (overall_pass=None)
                # now falls back to the real photo, exactly like a judged failure.
                if v.overall_pass is True:
                    seed_path = str(p)
                    break
                log(f"[seedgen] scene {i + 1} attempt {attempt} rejected ({v.reason})")
                stats["retried"] += 1
            except Exception as exc:  # noqa: BLE001 — a failed generation degrades below
                log(f"[seedgen] scene {i + 1} attempt {attempt} failed "
                    f"({type(exc).__name__}: {str(exc)[:90]})")
        if seed_path is None:
            stats["fell_back"] += 1
            log(f"[seedgen] scene {i + 1}: generated seed unavailable -> REAL photo "
                "(legacy blur-contain fallback; will show at HITL)")
        results.append((seed_path, ref_url))
    log(f"[seedgen] stats: {stats['generated']} image generations, "
        f"{stats['retried']} gate retries, {stats['fell_back']} real-photo fallbacks")
    return results
