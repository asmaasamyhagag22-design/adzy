"""POST /api/poster/from-profile — generate a poster from a BusinessProfile.

Unified on the ONE pipeline (`poster.pipeline.generate_poster`) shared with the CLI, so the
web app gets the FULL system: BrandCreativeDNA (build-or-load) -> creative concept (one
message, Arabic copy, chips from proof-points) -> free-form DNA-steered layout -> Imagen in
the brand's visual language -> Playwright render -> vision QA gate (regenerate on fail).

The route is a SYNC `def` on purpose: the renderer uses Playwright's SYNC API, which can't
run inside the asyncio event loop. FastAPI runs sync routes in a worker thread. The response
shape is unchanged, so the existing frontend (poster-studio-card) keeps working.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.auth import enforce_profile_bounds
from poster.pipeline import _FALLBACK_MODEL, generate_poster
from poster.schemas import (
    BackgroundImageResult,
    PosterArtDirection,
    PosterFromProfileRequest,
    PosterFromProfileResponse,
    PosterRenderResult,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/poster/from-profile", response_model=PosterFromProfileResponse)
def create_poster_from_profile(
    request: PosterFromProfileRequest,
) -> PosterFromProfileResponse:
    enforce_profile_bounds(request.profile)  # P-56: bound the client-supplied profile
    try:
        # Gemini drives the whole pipeline (concept + design + image + QA). None -> the
        # pipeline degrades to deterministic fallbacks.
        caller = None
        try:
            from business_profile.llm import default_caller
            caller = default_caller(strong=True)
        except Exception:
            caller = None

        res = generate_poster(request.profile, caller=caller, engine=request.engine,
                              language=request.language)

        art_direction = PosterArtDirection(
            provider_prompt=res.prompt,
            negative_prompt="",
            concept=(res.concept.single_message or "Creative-concept-driven poster"),
            category=res.brief.category or "brand",
            mood=res.concept.emotional_tone or "on-brand",
            # PosterArtDirection.layout is a legacy cosmetic enum; the real composition is
            # the free-form spec.text_box. Report a valid enum value.
            layout="magazine_cover",
            background_style="brand design language (Creative DNA)",
            safe_overlay_copy=res.brief.headline or "",
            color_notes=[
                f"concept: {res.concept.single_message}",
                f"chips: {', '.join(res.concept.proof_points)}",
                f"vision QA: pass={res.qa.overall_pass} ({res.qa.reason})",
            ],
            source_fields_used=["business_name", "category", "offerings", "palette", "logo"],
        )
        background = BackgroundImageResult(
            provider="gemini-oneshot" if res.engine == "oneshot" else "vertex-imagen",
            model=res.model_used,
            prompt=res.prompt,
            background_path=res.background_path,
            filename=Path(res.background_path).name,
            width=res.width,
            height=res.height,
            fallback_used=res.model_used == _FALLBACK_MODEL,
            fallback_reason=("Imagen Ultra capacity/quota (429); used lighter model"
                             if res.model_used == _FALLBACK_MODEL else None),
        )
        render = PosterRenderResult(
            poster_path=res.poster_path, filename=res.filename,
            width=res.width, height=res.height,
        )
        from api.webapp_images import acceptance_disclosure
        from poster.vision_qa import poster_acceptance_from_output
        visual_qa = res.qa.model_dump(mode="json") if hasattr(res.qa, "model_dump") else None
        decision = res.acceptance or poster_acceptance_from_output({
            "image_b64": res.image_base64,
            "filename": res.filename,
            "visual_qa": visual_qa,
        })
        disclosure = acceptance_disclosure(decision)
        # Brand-safety proof: expose the audit trail + the client-facing Ad
        # Compliance Sheet (was previously written to disk only). Best-effort —
        # a sheet failure must never block the poster.
        compliance_sheet = None
        try:
            from grounding.compliance import build_compliance_sheet
            compliance_sheet = build_compliance_sheet(res.audit)
        except Exception:  # noqa: BLE001
            compliance_sheet = None

        return PosterFromProfileResponse(
            brief=res.brief,
            art_direction=art_direction,
            background=background,
            render=render,
            image_base64=res.image_base64,
            audit=res.audit,
            compliance_sheet=compliance_sheet,
            engine_used=res.engine,
            visual_qa=visual_qa,
            acceptance_decision=decision.model_dump(mode="json"),
            canonical_qa_status=disclosure["canonical_qa_status"],
            qa_executed=disclosure["qa_executed"],
            qa_passed=disclosure["qa_passed"],
            accepted=disclosure["accepted"],
            publishable=disclosure["publishable"],
            deliverable=disclosure["deliverable"],
        )

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # P-58: log the detail server-side; return a generic message (no internal paths/strings).
        logger.exception("Poster generation failed")
        raise HTTPException(
            status_code=500, detail="Poster generation failed.",
        ) from exc
