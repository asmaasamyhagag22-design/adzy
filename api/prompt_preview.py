# -*- coding: utf-8 -*-
"""Show what the system is ABOUT to ask for, before a single unit of spend.

OWNER: «فيتشر ان البرومبت يظهر قبل الجينريشن — اتأكد إنه شغال». It was not: the poster response
carries `provider_prompt` and `prompt`, but only AFTER the render, and the dashboard reads neither.
There was nothing anywhere that answered "what will this cost me and what exactly will it ask for"
before the money was gone.

FREE, DELIBERATELY. The obvious implementation calls the creative director and shows what it wrote
— but that is a paid model call, so a "preview before you spend" would itself be the spend. What
this returns instead is everything the system RESOLVES DETERMINISTICALLY before any model is
touched: the audience language and locked register, the look lock, the colour-grade family, the
tempo, the derived scene count and the reason for it, whether the last beat can be a real product
insert, and the full system prompt the director will be sent. All of it comes from the brand's own
evidence, and none of it bills.

That also makes it the only part of this pipeline that still answers while the Google project is
billing-blocked — which is exactly when a person most needs to know what the machine intended.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _dna_for(profile: dict) -> Any:
    """The cached brandbook, or None. NEVER builds one — that is a Serper + vision spend, and a
    preview that quietly costs money is the thing this module exists to avoid."""
    try:
        from poster.pipeline import load_or_build_dna
        return load_or_build_dna(profile, None)
    except Exception:  # noqa: BLE001
        return None


def reel_preview(profile: dict, *, language: str = "auto",
                 max_seconds: float = 15.0, n_scenes: int = 5) -> dict:
    """Every decision the reel makes before the director is called. Never raises."""
    out: dict[str, Any] = {"kind": "reel", "resolved": {}, "system_prompt": "", "notes": []}
    res = out["resolved"]

    def note(msg: str) -> None:
        out["notes"].append(msg)

    dna = _dna_for(profile)

    # --- who it is spoken to, and in what register -------------------------------------
    try:
        from reel.audience import audience_language_for
        lang_name, country, why = audience_language_for(profile)
        res["audience_language"] = lang_name
        res["country"] = country
        res["language_evidence"] = why
    except Exception as e:  # noqa: BLE001
        note(f"audience language unavailable ({type(e).__name__})")

    chosen = (language or "auto").lower()
    res["language_requested"] = chosen
    try:
        from reel.audience import audience_language_code
        res["language_effective"] = (chosen if chosen in ("ar", "en")
                                     else audience_language_code(profile) or "en")
    except Exception:  # noqa: BLE001
        res["language_effective"] = chosen if chosen in ("ar", "en") else "en"

    try:
        from reel.creative_director import _locked_register, _vertical_mode
        res["vertical_mode"] = _vertical_mode(profile)
        res["locked_register"] = _locked_register(res["language_effective"], profile)
    except Exception as e:  # noqa: BLE001
        note(f"register unavailable ({type(e).__name__})")

    # --- how it will look, and at what tempo --------------------------------------------
    try:
        from brand.look import look_lock
        res["look_lock"] = look_lock(brand_dna=dna, profile=profile)
    except Exception as e:  # noqa: BLE001
        note(f"look lock unavailable ({type(e).__name__})")
    try:
        from reel.grade import grade_for_brand, grade_filter
        g = grade_for_brand(brand_dna=dna, profile=profile)
        res["grade_family"] = g.get("family")
        res["grade_filter"] = grade_filter(brand_dna=dna, profile=profile)
    except Exception as e:  # noqa: BLE001
        note(f"grade unavailable ({type(e).__name__})")
    try:
        from reel.tempo import beat_grid_for_brand
        bg = beat_grid_for_brand(brand_dna=dna, profile=profile)
        res["bpm"] = bg.bpm
        res["beat_s"] = round(bg.beat_s, 4)
        res["tempo_reason"] = bg.reason
    except Exception as e:  # noqa: BLE001
        note(f"tempo unavailable ({type(e).__name__})")

    # --- how many shots, and what the last one is ---------------------------------------
    try:
        from reel.__main__ import _VEO_MIN_CLIP_S, _scenes_for
        from reel.insert_shot import INSERT_S, product_candidates
        cands = product_candidates(profile)
        has_insert = bool(cands)
        n = _scenes_for(float(max_seconds), int(n_scenes), has_insert=has_insert)
        res["scenes"] = n
        res["generated_shots"] = n - 1 if has_insert else n
        res["last_beat"] = ("a rendered product insert (no model, no spend)" if has_insert
                            else "generated (no real packshot to cut to)")
        res["scene_count_reason"] = (
            f"{max_seconds:g}s cut, {_VEO_MIN_CLIP_S:g}s minimum clip"
            + (f", minus {INSERT_S:g}s for the insert" if has_insert else ""))
        res["insert_candidates"] = len(cands)
        # What it will COST, from the same guard the render uses — before anything is bought.
        try:
            from vertex_spend import est_video_cost_usd
            res["est_video_usd"] = round(est_video_cost_usd(res["generated_shots"] * 6), 2)
            res["est_video_note"] = "upper bound: every generated shot bought at 6s"
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        note(f"scene plan unavailable ({type(e).__name__})")

    # --- the words the director will actually be sent -----------------------------------
    try:
        from reel.creative_director import _locale_primer, _system_prompt
        from poster.contracts import compliance_for
        cat = profile.get("category")
        cat = cat.get("value") if isinstance(cat, dict) else cat
        out["system_prompt"] = _system_prompt(
            int(res.get("scenes") or 4), res.get("language_effective") or "en",
            res.get("vertical_mode") or "generic",
            compliance=compliance_for(str(cat or "")),
            register=res.get("locked_register") or "",
            locale_primer=_locale_primer(profile))
    except Exception as e:  # noqa: BLE001
        note(f"system prompt unavailable ({type(e).__name__})")

    out["billable"] = False
    return out


def poster_preview(profile: dict, *, language: str = "auto") -> dict:
    """The poster's deterministic half: locale, look, palette, and the craft contract."""
    out: dict[str, Any] = {"kind": "poster", "resolved": {}, "system_prompt": "", "notes": []}
    res = out["resolved"]
    dna = _dna_for(profile)

    try:
        from poster.locale import brand_locale
        country, primer = brand_locale(profile)
        res["country"] = country
        res["casting_primer"] = primer
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"locale unavailable ({type(e).__name__})")
    try:
        from brand.look import look_lock
        res["look_lock"] = look_lock(brand_dna=dna, profile=profile)
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"look lock unavailable ({type(e).__name__})")
    try:
        from poster.product_composite import rank_candidates
        vis = profile.get("visual") or {}
        res["product_candidates"] = len(rank_candidates(vis.get("content_images") or []))
    except Exception:  # noqa: BLE001
        pass
    res["language_requested"] = (language or "auto").lower()
    try:
        from poster.contracts import CRAFT_CONTRACT
        out["system_prompt"] = CRAFT_CONTRACT
    except Exception as e:  # noqa: BLE001
        out["notes"].append(f"craft contract unavailable ({type(e).__name__})")

    out["billable"] = False
    return out


def preview_for(profile: dict, kind: str = "reel", **kw) -> dict:
    if str(kind).lower().startswith("post"):
        return poster_preview(profile, language=kw.get("language", "auto"))
    return reel_preview(profile, **kw)
