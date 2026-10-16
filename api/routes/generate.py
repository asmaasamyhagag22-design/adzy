"""Image generation routes (gated by require_user at mount time).

  POST /api/generate            (JSON)      text->image
  POST /api/generate/upload     (multipart) + a logo file
  POST /api/generate/reference  (multipart) + a reference photo
  POST /api/edit/inpaint        (multipart) image + mask  (stub: returns the image unchanged)
  POST /api/calendar/generate-image (JSON)  -> {image_url} (persisted on the calendar entry)

All return the frontend's shape. Degrade-safe: a free branded placeholder unless
BASEERA_ENABLE_PAID_IMAGES=1 (see api/webapp_images.py). Never touches reel.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

from fastapi import Depends, APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .auth import require_user
from ..http_errors import as_http, reraise_as_http
from ..persistence import (CalendarPostMissing, CapabilityNotWired,
                           GalleryWriteFailed, get_persistence)
from ..upload_limits import read_bounded
from .. import webapp_images as images

router = APIRouter()
logger = logging.getLogger(__name__)


def _brand_or_404(domain_key: str, user_id: str = "") -> dict:
    """Scoped like every other brand read — another account's brand resolves to 404."""
    brand = get_persistence().visible_brand(domain_key=domain_key, user_id=user_id or "")
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


# An uploaded logo or product photo is bounded, read — and then reaches no renderer, because
# neither `generate_image` nor `_render_real` takes an image argument. Wiring it in is new work.
# Saying so is not, and the alternative is a user who uploaded their real product being handed one
# the model imagined, by a system whose whole claim is that it does not imagine. Same shape as
# `_INPAINT_NOTE` below: the brand's own language, chosen with `images.brand_language`.
_IGNORED_UPLOAD_NOTE = {
    "ar": "الصورة اللي رفعتيها لسه مش داخلة في التوليد — البوستر اتعمل من بروفايل البراند بس.",
    "fr": "L'image envoyée n'alimente pas encore la génération — le visuel vient du profil de marque.",
    "es": "La imagen subida aún no alimenta la generación — el visual viene del perfil de marca.",
}
_IGNORED_UPLOAD_DEFAULT = ("Your uploaded image does not feed the generation yet — this visual was "
                           "built from the brand profile alone.")


def _emit(brand: dict, *, headline: str, subtext: str, cta: str, style: str,
          content_type: str, owner_id: str = "", ignored_upload: str = "",
          product_url: str = "", language: str = "auto", trend: bool = False,
          creative_contract=None) -> dict:
    # `product_url` is the owner's «بحط اللينك بتاعه»: an ad for ONE named item rather than for the
    # brand in general. `generate_poster` has accepted `product_image`/`product_name` all along —
    # this function was simply the wall they never got past, which is also why the
    # `product_name`/`product_description` form fields below reached nothing.
    # REFUSE BEFORE SPENDING. Checked here rather than at `record_gallery_entry`, because failing
    # after `generate_image` would bill the render and still return an error.
    _require_gallery_writes()
    persistence = get_persistence()
    execution = None
    if creative_contract is not None:
        execution = persistence.begin_canonical_poster_run(
            domain_key=str(brand.get("domain_key") or ""), user_id=owner_id,
            contract=creative_contract.model_dump(mode="json"))
    try:
        out = images.generate_image(
            brand, headline=headline, subtext=subtext, cta=cta,
            content_type=content_type, product_url=product_url,
            language=(language if language in ("ar", "en") else "auto"),
            trend=trend, creative_contract=creative_contract,
            generation_run_id=str((execution or {}).get("run_id") or ""),
            generation_attempt_id=str((execution or {}).get("attempt_id") or ""))
    except Exception as exc:
        if execution:
            persistence.fail_canonical_poster_run(
                run_id=str(execution["run_id"]), attempt_id=str(execution["attempt_id"]),
                user_id=owner_id, error=str(exc))
            if isinstance(exc, images.CanonicalPosterQARejected):
                decision = exc.decision
                raise HTTPException(status_code=502, detail={
                    "code": "CANONICAL_POSTER_GENERATION_FAILED",
                    "message": ("Visual QA could not verify this poster; it is not deliverable."
                                if decision.qa_status == "QA_UNVERIFIED" else
                                "Visual QA rejected this poster; it is not deliverable."),
                    "generation_run_id": str(execution["run_id"]),
                    "qa_status": decision.qa_status,
                    "accepted": False,
                    "reason_codes": list(decision.reason_codes),
                    "safe_alternative": "Review the strategy and QA limitations, then explicitly retry generation.",
                    "retryable": True,
                }) from exc
            raise HTTPException(status_code=502, detail={
                "code": "CANONICAL_POSTER_GENERATION_FAILED",
                "message": "The canonical poster provider did not produce an accepted image.",
                "generation_run_id": str(execution["run_id"]),
                "retryable": True,
            }) from exc
        raise
    if execution:
        from poster.vision_qa import poster_acceptance_from_output
        decision = poster_acceptance_from_output(
            out, expected_attempt_id=str(execution["attempt_id"]))
        if not decision.accepted or not decision.publishable:
            persistence.fail_canonical_poster_run(
                run_id=str(execution["run_id"]), attempt_id=str(execution["attempt_id"]),
                user_id=owner_id, error=decision.explanation)
            raise HTTPException(status_code=502, detail={
                "code": "CANONICAL_POSTER_GENERATION_FAILED",
                "message": "Visual QA did not accept this poster. Review the result before retrying.",
                "generation_run_id": str(execution["run_id"]),
                "qa_status": decision.qa_status,
                "accepted": False,
                "reason_codes": list(decision.reason_codes),
                "safe_alternative": "Review the strategy and QA limitations, then explicitly retry generation.",
                "retryable": True,
            })
    if ignored_upload:
        lang = images.brand_language(brand)
        out = {**out, "applied_upload": False,
               "note": _IGNORED_UPLOAD_NOTE.get(lang, _IGNORED_UPLOAD_DEFAULT)}
    # FAILING HERE MUST NOT RETURN 200 (§13). The render already happened and the bytes are
    # durable, but if the publication was not recorded the image is not in the gallery — handing
    # the caller a picture that will be gone on their next page load is the false success this
    # whole layer exists to make unreachable. The raw exception is chained for the server log and
    # never reaches the client, which is `http_errors`' standing rule.
    try:
        persisted = persistence.record_gallery_entry(entry={
            "image_b64": out["image_b64"], "filename": out["filename"],
            "brand_name": brand.get("organization_name", ""),
            "domain_key": brand.get("domain_key", ""),
            "content_type": content_type, "style": style,
            # provenance travels with the entry, so a disclosed image never loses its disclosure
            "is_preview": out.get("is_preview", True),
            "trust_violation": out.get("trust_violation", False),
            # Sparse metadata. Legacy assets are explicitly false; canonical assets carry the
            # complete inspectable lineage and exact compiled contract in this existing JSONB.
            "canonical": bool(out.get("canonical", False)),
            "strategy_grounded": bool(out.get("strategy_grounded", False)),
            "strategy_version_id": out.get("strategy_version_id", ""),
            "creative_contract_id": out.get("creative_contract_id", ""),
            "objective_id": out.get("objective_id", ""),
            "decision_id": out.get("decision_id", ""),
            "profile_snapshot_id": out.get("profile_snapshot_id", ""),
            "generation_run_id": out.get("generation_run_id", ""),
            "generation_attempt_id": out.get("generation_attempt_id", ""),
            "claim_ids_used": out.get("claim_ids_used", []),
            "creative_contract": out.get("creative_contract") or None,
            "poster_brief": out.get("poster_brief") or None,
            "strategy_verification": out.get("strategy_verification") or None,
            "repair_history": out.get("repair_history", []),
            "consumer_facing_disclosures": out.get("consumer_facing_disclosures", []),
            "why_this_poster": out.get("why_this_poster") or None,
            "visual_qa": out.get("visual_qa"),
            "acceptance_decision": out.get("acceptance_decision"),
            **images.qa_disclosure_from_output(out),
        }, user_id=owner_id)
        if persisted:
            out.update(persisted)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — translated, logged, never echoed to the client
        if execution:
            persistence.fail_canonical_poster_run(
                run_id=str(execution["run_id"]), attempt_id=str(execution["attempt_id"]),
                user_id=owner_id, error=str(exc))
        logger.exception("gallery write failed after a successful render: %s", exc)
        raise as_http(GalleryWriteFailed("the gallery entry was not recorded")) from exc
    return out


def _require_gallery_writes() -> None:
    """Refuse the render outright when the configured backend cannot own the gallery entry."""
    store = get_persistence()
    if not store.supports_gallery_writes():
        raise as_http(CapabilityNotWired("gallery writes are not available on this backend"))


# ---- JSON: text -> image ----
class GenerateRequest(BaseModel):
    domain_key: str
    content_type: str = "post"
    style: str = ""
    headline: str = ""
    subtext: str = ""
    cta: str = ""
    user_context: str = ""
    mode: str = "text_to_image"
    language: str = "auto"         # "ar" | "en" | "auto" — the owner's choice, not an inference
    trend: bool = False            # «اضغط قبلها على ترند» — shape the creative with the top stored trend
    content_only: bool = False     # «الكونتنت بس» — caption alone, no image, zero render spend
    product_url: str = ""          # paste a product page; the poster is built around THAT item
    product_name: str = ""
    product_description: str = ""
    # Exact immutable subject. Blank deliberately means LEGACY, never "use whatever strategy".
    strategy_version_id: str = ""


class PosterContractRequest(BaseModel):
    domain_key: str
    strategy_version_id: str
    product_focus: str = ""
    offer_focus: str = ""
    occasion: str = ""
    format: str = "poster_1080x1350"
    style_preference: str = ""
    cta_preference: str = ""
    creative_angle: str = ""
    language: str = "auto"
    locale: str = ""


def _compile_contract(*, domain_key: str, strategy_version_id: str, user_id: str,
                      request: dict) -> tuple[object, object]:
    from poster.strategy_contract import (
        CreativeContractCompiler, CreativeContractError, CreativeRequest,
    )

    persistence = get_persistence()
    try:
        context = persistence.canonical_strategy_context(
            domain_key=domain_key, user_id=user_id, run_id=strategy_version_id)
    except CapabilityNotWired as exc:
        raise HTTPException(status_code=503, detail={
            "code": "CANONICAL_POSTER_REQUIRES_POSTGRESQL", "message": str(exc)}) from exc
    if context is None:
        raise HTTPException(status_code=404, detail={
            "code": "STRATEGY_VERSION_NOT_FOUND",
            "message": "The exact StrategyVersion is missing or not visible."})
    compiler = CreativeContractCompiler()
    try:
        contract = compiler.compile(context, request=CreativeRequest(**request))
    except CreativeContractError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code, "message": str(exc),
            "safe_alternative": exc.safe_alternative,
            "preflight_status": exc.preflight_status or None}) from exc
    return contract, compiler.poster_brief(contract)


@router.post("/poster/contract")
def poster_contract(req: PosterContractRequest,
                    user: dict = Depends(require_user)) -> dict:
    """Zero-provider preview: exact compiled contract and minimum Poster Brief."""
    _brand_or_404(req.domain_key, user["id"])
    contract, brief = _compile_contract(
        domain_key=req.domain_key, strategy_version_id=req.strategy_version_id,
        user_id=user["id"], request=req.model_dump(exclude={"domain_key", "strategy_version_id"}))
    return {"canonical": True, "creative_contract": contract.model_dump(mode="json"),
            "poster_brief": brief.model_dump(mode="json"),
            "preflight": contract.preflight.model_dump(mode="json")}


@router.post("/generate")
def generate(req: GenerateRequest, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if req.content_only:
        # «الكونتنت بس من غير ريل ولا صورة» — the caption alone. No renderer in the call path,
        # so zero image spend by construction; the trend toggle still shapes the words.
        from api.webapp_images import top_trend_context
        from api.webapp_social_copy import content_only as _content_only
        ctx, used, note = top_trend_context(brand) if req.trend else ("", {}, "")
        out = _content_only(brand.get("_profile") or {},
                            language=(req.language if req.language in ("ar", "en") else "auto"),
                            trend_context=ctx)
        out["trend_used"] = used or None
        out["trend_note"] = note
        return out
    contract = None
    if req.strategy_version_id:
        # Direct copy fields do not become facts by accident. Headline/user_context are a creative
        # angle; subtext is rejected as an attempted factual override unless it is allowlisted.
        contract, _brief = _compile_contract(
            domain_key=req.domain_key, strategy_version_id=req.strategy_version_id,
            user_id=user["id"], request={
                "product_focus": req.product_name,
                "style_preference": req.style,
                "cta_preference": req.cta,
                "creative_angle": req.user_context or req.headline,
                "requested_factual_claims": ([req.subtext] if req.subtext else []),
                "language": req.language,
            })
    return _emit(brand,
                 headline=("" if contract is not None else req.headline),
                 subtext=("" if contract is not None else req.subtext),
                 cta=(contract.cta_direction if contract is not None else req.cta),
                 style=req.style, content_type=req.content_type, owner_id=user["id"],
                 product_url=req.product_url,
                 language=(contract.language if contract is not None else req.language),
                 trend=(False if contract is not None else req.trend),
                 creative_contract=contract)


# ---- multipart: + a logo ----
@router.post("/generate/upload")
async def generate_with_logo(
    domain_key: str = Form(...),
    content_type: str = Form("post"),
    style: str = Form(""),
    headline: str = Form(""),
    subtext: str = Form(""),
    cta: str = Form(""),
    ai_text: str = Form("false"),
    product_url: str = Form(""),
    product_name: str = Form(""),
    product_description: str = Form(""),
    language: str = Form("auto"),
    logo: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user),
) -> dict:
    brand = _brand_or_404(domain_key, user["id"])
    if logo is not None:
        # Consumed and bounded. NOT forwarded anywhere: `generate_image` renders from the stored
        # profile and takes no image argument, so an uploaded logo reaches neither the placeholder
        # nor the paid path. The response now says so — see `_IGNORED_UPLOAD_NOTE`.
        await read_bounded(logo, field="logo")
    # OFF THE LOOP. `_emit` renders inline AND appends to the gallery through `record_history`,
    # which takes BrandStore's module RLock — the same lock `_storage_lock` holds — so any
    # competing brand write stalls it. MEASURED: a 1.5s competing write froze the loop for 1.451s.
    # The sibling sync route `/api/generate` already runs this exact function on a threadpool
    # thread, so running it off-loop here is the consistent behaviour, not a new one.
    return await asyncio.to_thread(
        lambda: _emit(brand, headline=headline, subtext=subtext, cta=cta, style=style,
                      content_type=content_type, owner_id=user["id"], product_url=product_url,
                      language=language,
                      ignored_upload="logo" if logo is not None else ""))


# ---- multipart: + a reference photo ----
@router.post("/generate/reference")
async def generate_with_reference(
    domain_key: str = Form(...),
    content_type: str = Form("post"),
    style: str = Form(""),
    headline: str = Form(""),
    subtext: str = Form(""),
    cta: str = Form(""),
    ai_text: str = Form("false"),
    reference_strength: str = Form("0.55"),
    product_url: str = Form(""),
    product_name: str = Form(""),
    product_description: str = Form(""),
    language: str = Form("auto"),
    reference_image: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user),
) -> dict:
    brand = _brand_or_404(domain_key, user["id"])
    if reference_image is not None:
        # Same as the logo above: bounded and consumed, but not plumbed into any renderer.
        await read_bounded(reference_image, field="reference_image")
    return await asyncio.to_thread(            # off the loop — see /generate/upload above
        lambda: _emit(brand, headline=headline, subtext=subtext, cta=cta, style=style,
                      content_type=content_type, owner_id=user["id"], product_url=product_url,
                      language=language,
                      ignored_upload="reference_image" if reference_image is not None else ""))


# ---- multipart: inpaint (NOT IMPLEMENTED — returns the input image and says so) ----
# Region editing needs a paid Imagen edit call (poster/imagen_edit_provider.py) and every Imagen
# model 404s on this project, so the request cannot be served. It used to return 200 with the
# original bytes and no other signal: the comment called that "an honest stub", but the honesty
# reached the developer reading the file, not the user who painted a mask and got their own picture
# back. `applied: false` + a note in their language is what makes it honest at the client.
_INPAINT_NOTE = {
    "ar": "تحرير جزء من الصورة لسه مش متاح — رجّعنا الصورة زي ما هي من غير تعديل.",
    "fr": "L'édition de zone n'est pas encore disponible — l'image est renvoyée inchangée.",
    "es": "La edición por región aún no está disponible — la imagen se devuelve sin cambios.",
}
_INPAINT_NOTE_DEFAULT = ("Region editing is not available yet — the image is returned unchanged, "
                         "with nothing applied.")


@router.post("/edit/inpaint")
async def inpaint(
    domain_key: str = Form(...),
    edit_prompt: str = Form(""),
    seed: str = Form("-1"),
    strength: str = Form("0.85"),
    language: str = Form(""),
    image: UploadFile = File(...),
    mask: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user),
) -> dict:
    brand = _brand_or_404(domain_key, user["id"])
    src = await read_bounded(image, field="image")
    if mask is not None:
        await read_bounded(mask, field="mask")
    lang = (language or "").strip().lower()[:2] or images.brand_language(brand)
    return {
        "image_b64": base64.b64encode(src).decode("ascii"),
        "filename": f"{domain_key}_edit.png",
        "applied": False,
        "note": _INPAINT_NOTE.get(lang, _INPAINT_NOTE_DEFAULT),
    }


# ---- JSON: a poster for one calendar day -> {image_url} ----
class CalendarImageRequest(BaseModel):
    domain_key: str
    date: str = ""
    #: The post being targeted. Preferred over `date`, which cannot identify a post when two are
    #: scheduled on the same day (§22).
    post_id: str = ""
    seed: int = -1


def _post_key(post: Optional[dict]) -> str:
    """Stable identity for a calendar row: its id when it has one, else the date it occupies.

    Legacy calendars predate post ids. Falling back to the date keeps those rows addressable —
    ambiguity among them is caught by the resolver BEFORE anything is rendered, so this fallback
    never has to disambiguate.
    """
    post = post or {}
    return str(post.get("post_id") or post.get("id") or f"date:{post.get('date') or ''}")


@router.post("/calendar/generate-image")
def calendar_generate_image(req: CalendarImageRequest, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    # WHICH post — resolved, not guessed.
    #
    # This used to scan the calendar and take the FIRST entry whose date matched. With two posts on
    # one day that is a coin flip, and the caller cannot see it happen: a real, billable image comes
    # back for a post they did not choose, and the response is shaped exactly like success. The
    # resolver answers 404 for no match, the post for exactly one, and 409 for several — never a
    # guess. An empty date keeps the old "no entry, theme from the brand" behaviour.
    entry = None
    if req.post_id or req.date:
        try:
            entry = get_persistence().resolve_calendar_post(
                domain_key=req.domain_key, user_id=user["id"],
                post_id=req.post_id, date=req.date)
        except CalendarPostMissing:
            entry = None
        except Exception as exc:  # noqa: BLE001 — ambiguity/visibility become their own statuses
            reraise_as_http(exc)
            raise
    headline = (entry or {}).get("topic") or brand.get("tagline", "")
    # D-8: the fallback CTA follows the brand's MEASURED language; "Learn more" was a hardcoded
    # English button on every Arabic brand's calendar poster.
    cta = (entry or {}).get("cta") or ""

    # Same honest gate as /api/generate — this used to call `_render_real` directly, which both
    # bypassed the disclosure and would now hand back a RealRender where bytes were expected.
    out = images.generate_image(brand, headline=headline, cta=cta, content_type="calendar")
    png = base64.b64decode(out["image_b64"])
    # A regenerated day is a NEW creative.  A fixed date-derived URL overwrote image A before the
    # BrandStore mutation could bind image B's metadata; if that mutation failed, the old record
    # still said A while its URL now served B.  The existing non-guessable asset name keeps each
    # image immutable without introducing an asset registry.
    asset_name = images.unique_asset_name(
        req.domain_key, f"calendar_{(req.date or 'day')}")
    url = images.save_generated_png(png, asset_name)

    # persist the poster URL on the matching calendar entry so it survives a reload
    if entry is not None:
        poster = {
            "url": url,
            "filename": out["filename"],
            "is_preview": out["is_preview"],
            "trust_violation": out["trust_violation"],
            "visual_qa": out.get("visual_qa"),
            "acceptance_decision": out.get("acceptance_decision"),
            **images.qa_disclosure_from_output(out),
        }

        # The image may take long enough for analysis or a regenerated calendar to commit. Re-read
        # the LATEST calendar and attach to the row this request actually resolved, instead of
        # publishing `brand` — the stale snapshot loaded before rendering. Unrelated calendar rows
        # and unrelated brand fields are carried over from that latest committed state.
        store = get_persistence()
        latest = store.visible_brand(domain_key=req.domain_key, user_id=user["id"])
        if latest is None:
            raise HTTPException(status_code=404, detail="Brand not found.")
        target = _post_key(entry)
        content = dict(latest.get("content_calendar") or {})
        rows = [dict(row) for row in (content.get("calendar") or [])]
        for row in rows:
            # Matched on the resolved post's identity, not on the date: the date is what was
            # ambiguous in the first place, and re-matching by it here would reintroduce the guess
            # one line after the resolver removed it.
            if _post_key(row) == target:
                row["poster"] = poster
                break
        content["calendar"] = rows
        if store.record_enrichment(domain_key=req.domain_key, user_id=user["id"],
                                   kind="calendar",
                                   patch={"content_calendar": content}) is None:
            raise HTTPException(status_code=404, detail="Brand not found.")
    return {"image_url": url, "filename": out["filename"],
            "is_preview": out["is_preview"],
            "degraded_reason": out["degraded_reason"],
            "trust_violation": out["trust_violation"], "trust_note_ar": out["trust_note_ar"],
            "visual_qa": out.get("visual_qa"),
            "acceptance_decision": out.get("acceptance_decision"),
            **images.qa_disclosure_from_output(out)}


# --- The reel, on the dashboard's own auth ---
#
# WHY THIS EXISTS. The SPA's reel button posts to `/api/reel/from-profile`, which is mounted behind
# `require_api_key` (main.py:227) — a MACHINE credential. The SPA's `api()` helper only ever sends
# `Authorization: Bearer`, so with `BASEERA_API_KEY` configured the button returns 401 and the
# frontend's own error handler converts that into "Session expired" and LOGS THE USER OUT. Mid-demo.
#
# Today the key is not set, so `auth.py:57` falls to the loopback branch and the button works on the
# owner's laptop. That is luck, not design: the moment anyone sets the key — which is exactly what
# deploying properly means — the branch at `auth.py:54` is taken and it does NOT look at loopback.
#
# The fix is the one the rest of the dashboard already uses: a JWT-gated route beside every other
# paid webapp action, sharing `require_spend_budget` with them. `/api/reel/*` stays as the API-key
# surface for machine callers; sending the paid key to the browser instead would publish it to
# every visitor.
class ReelRequest(BaseModel):
    domain_key: str
    language: str = "auto"          # "ar" | "en" | "auto"
    max_seconds: float = 15.0       # the format the owner named; the scene count follows from it
    n_scenes: int = 5               # a CEILING — the route derives the real count
    product_url: str = ""           # paste a product page; the film is built around THAT item


@router.post("/reel", deprecated=True)
def reel(req: ReelRequest, user: dict = Depends(require_user)) -> dict:
    """LEGACY stored-profile renderer. Canonical authority lives at `/reel/canonical/prepare`."""
    from reel.schemas import ReelFromProfileRequest

    from .reel import create_reel_from_profile

    brand = _brand_or_404(req.domain_key, user["id"])
    profile = brand.get("_profile") or {}
    if not profile:
        raise HTTPException(status_code=409,
                            detail="This brand has no stored profile to direct a reel from.")
    out = create_reel_from_profile(ReelFromProfileRequest(
        profile=profile, language=req.language,
        max_seconds=req.max_seconds, n_scenes=req.n_scenes,
        hero_product_url=req.product_url))
    return out.model_dump() if hasattr(out, "model_dump") else out
