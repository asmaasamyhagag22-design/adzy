"""Image generation for the web app — degrade-safe by design.

Under D-5 (paid Gemini/Vertex/Imagen calls 403 here), real generation can't run, so the DEFAULT is a
free, deterministic, on-brand PLACEHOLDER rendered with Pillow (brand colors + the copy). Real
generation via the existing `poster.pipeline.generate_poster` is opt-in behind
`BASEERA_ENABLE_PAID_IMAGES=1` (so nothing spends by accident); any failure there falls back to the
placeholder. Never touches reel/Veo.

Every generator returns `{image_b64, filename, headline, subtext, cta}` (what the frontend reads).
Calendar images are also saved to disk and served by URL (see api/routes/generate.py).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from api.text_shaping import has_rtl, shape_for_pillow, wrap_shaped
from api.user_auth import webapp_dir

logger = logging.getLogger(__name__)


class CanonicalPosterQARejected(RuntimeError):
    """An inspected/unverified candidate was refused; carry its safe QA projection."""

    def __init__(self, decision):
        self.decision = decision
        super().__init__("Canonical poster was not accepted by authoritative visual QA: "
                         + decision.explanation)


def paid_images_enabled() -> bool:
    return os.environ.get("BASEERA_ENABLE_PAID_IMAGES") == "1"


# ---------------------------------------------------------------------
# Pillow placeholder (free, deterministic, on-brand)
# ---------------------------------------------------------------------

def _hex_to_rgb(h: str, default=(42, 26, 31)) -> tuple:
    h = (h or "").lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def _font(size: int):
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _luminance(rgb) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0


# Fallback copy, keyed on the brand's MEASURED language with a universal default (D-8 — the same
# config-with-universal-default pattern as competitor/market_definition._BEST_SEED). A hardcoded
# English "Learn more" on an Arabic brand's card is exactly the country/language hardcode D-8 bans.
_DEFAULT_CTA = {
    "ar": "اعرف المزيد", "fa": "بیشتر بدانید", "ur": "مزید جانیں", "he": "מידע נוסף",
    "fr": "En savoir plus", "es": "Más información", "de": "Mehr erfahren", "tr": "Daha fazla",
}
_PREVIEW_NOTE = {
    "ar": "معاينة · فعّلي توليد الصور للنسخة النهائية",
    "fr": "aperçu · activez la génération d'images pour le rendu final",
    "es": "vista previa · activa la generación de imágenes para el render final",
}
_PREVIEW_NOTE_DEFAULT = "preview · enable image generation for the final render"


def brand_language(brand: dict) -> str:
    """The brand's own leading language code, measured — never assumed."""
    prof = brand.get("_profile") or {}
    for langs in (prof.get("languages"), brand.get("languages")):
        for entry in (langs or []):
            code = entry.get("code") if isinstance(entry, dict) else entry
            code = str(code or "").strip().lower()[:2]
            if code:
                return code
    return ""


def render_placeholder(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                       size: tuple = (1080, 1350)) -> bytes:
    """A clean branded card. Deterministic, no network, no paid call.

    Every string is routed through `shape_for_pillow` before it is drawn: Pillow here has no raqm
    backend, so unshaped Arabic/Hebrew comes out isolated and reversed (measured — see
    api/text_shaping). RTL copy is also right-aligned, because a right-to-left card flush to the
    left margin reads as broken even when the glyphs are correct."""
    W, H = size
    colors = brand.get("brand_colors") or ["#2A1A1F"]
    bg = _hex_to_rgb(colors[0])
    accent = _hex_to_rgb(colors[1]) if len(colors) > 1 else (255, 255, 255)
    ink = (255, 255, 255) if _luminance(bg) < 0.55 else (20, 20, 20)

    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    margin = int(W * 0.09)

    lang = brand_language(brand)
    name_raw = (brand.get("organization_name") or "")
    hl = (headline or brand.get("tagline") or "").strip()
    st = (subtext or brand.get("description") or "").strip()
    cta = (cta or _DEFAULT_CTA.get(lang, "Learn more")).strip()
    # Direction is decided by the COPY that will actually be drawn, not by a country guess.
    rtl = has_rtl(f"{hl} {st} {name_raw} {cta}")

    def line(text: str, font, y: int, fill) -> None:
        """Draw one already-shaped line, flush to the reading-side margin."""
        x = (W - margin - d.textlength(text, font=font)) if rtl else margin
        d.text((x, y), text, font=font, fill=fill)

    # brand name (top). .upper() is a no-op for Arabic and would mangle nothing, but it is applied
    # only to the Latin case so an RTL wordmark keeps the shape the reshaper produced.
    name = name_raw if rtl else name_raw.upper()
    line(shape_for_pillow(name[:40]), _font(38), int(H * 0.07), ink)

    # headline (center, wrapped). Wrap BEFORE shaping — see wrap_shaped.
    hf = _font(88)
    y = int(H * 0.30)
    for text in wrap_shaped(hl, 18, 4):
        line(text, hf, y, ink)
        y += 96

    # subtext
    if st:
        y += 20
        sf = _font(34)
        for text in wrap_shaped(st, 42, 3):
            line(text, sf, y, ink)
            y += 46

    # CTA pill (bottom) — the pill hugs the shaped text, so it must be measured after shaping.
    cta_shaped = shape_for_pillow(cta)
    cf = _font(38)
    tb = d.textbbox((0, 0), cta_shaped, font=cf)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = 28
    by0 = int(H * 0.85)
    bx0 = (W - margin - tw - pad * 2) if rtl else margin
    d.rounded_rectangle([bx0, by0, bx0 + tw + pad * 2, by0 + th + pad * 2], radius=16, fill=accent)
    d.text((bx0 + pad, by0 + pad - tb[1]), cta_shaped,
           font=cf, fill=(255, 255, 255) if _luminance(accent) < 0.55 else (20, 20, 20))

    # tiny "placeholder" watermark so it's never mistaken for a finished paid render
    note = _PREVIEW_NOTE.get(lang, _PREVIEW_NOTE_DEFAULT)
    line(shape_for_pillow(note), _font(22), H - 46, ink)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------
# real generation (opt-in, paid) — reuses the existing poster pipeline
# ---------------------------------------------------------------------

@dataclass
class RealRender:
    """A finished paid render together with the pipeline's own verdict about it.

    `_render_real` used to return bare PNG bytes, which threw away `PosterGenResult.trust_violation`
    and `trust_note_ar` — the two fields the pipeline added *because* "the qa verdict alone was
    MEASURED being ignored downstream" (poster/pipeline.py, after nanocare shipped a fabricated
    product label silently). The web app was the first downstream to repeat that exact drop."""
    png: Optional[bytes]
    result: object = None


_QA_STATUSES = {"pass", "fail", "not_checked"}


def acceptance_disclosure(decision: object) -> dict:
    """Conservative compatibility projection of the canonical typed decision."""
    from poster.vision_qa import QA_FAIL, QA_PASS

    canonical = str(getattr(decision, "qa_status", "") or "")
    status = "pass" if canonical == QA_PASS else "fail" if canonical == QA_FAIL else "not_checked"
    return {
        "qa_status": status,
        "qa_failed": status == "fail",
        "qa_note": ("" if status == "pass"
                    else str(getattr(decision, "explanation", "") or "")),
        "canonical_qa_status": canonical or "QA_UNVERIFIED",
        "qa_executed": bool(getattr(decision, "qa_executed", False)),
        "qa_passed": bool(getattr(decision, "qa_passed", False)),
        "accepted": bool(getattr(decision, "accepted", False)),
        "publishable": bool(getattr(decision, "publishable", False)),
        "deliverable": bool(getattr(decision, "deliverable", False)),
    }


def qa_disclosure_for_verdict(qa: object = None) -> dict:
    """Legacy disclosure-only translation used by out-of-scope non-Poster consumers.

    Poster acceptance never calls this as authority: `generate_image`, routes, and persistence
    re-derive the typed decision from raw QA plus exact bytes.
    """
    overall = getattr(qa, "overall_pass", None)
    status = "pass" if overall is True else "fail" if overall is False else "not_checked"
    note = "" if status == "pass" else str(getattr(qa, "reason", "") or "")
    return {"qa_status": status, "qa_failed": status == "fail", "qa_note": note}


def qa_disclosure_from_output(output: dict) -> dict:
    """Re-derive from exact bytes and raw QA; never trust caller-supplied PASS flags."""
    from poster.vision_qa import poster_acceptance_from_output
    return acceptance_disclosure(poster_acceptance_from_output(output or {}))


try:                                         # module seam, so a test can stub the scrape
    from scraper.product_page import scrape_product_page
except Exception:  # noqa: BLE001 — Playwright may be unavailable; the feature degrades, nothing breaks
    scrape_product_page = None  # type: ignore[assignment]

_PRODUCT_NOTES = {
    "unsafe": "الرابط ده مش رابط عام صالح — اتجاهل، والصورة اتعملت من بيانات البراند العامة.",
    "unreachable": ("مقدرناش نفتح صفحة المنتج دي (الموقع رفض الطلب أو الصفحة مش موجودة) — "
                    "الصورة اتعملت من بيانات البراند العامة."),
    "empty": ("فتحنا الصفحة بس مالقيناش فيها بيانات منتج نقدر نستعملها (لا اسم ولا صورة) — "
              "الصورة اتعملت من بيانات البراند العامة."),
    "unavailable": ("قراءة روابط المنتجات مش متاحة في التشغيلة دي — الصورة اتعملت من بيانات "
                    "البراند العامة."),
}


def product_from_link(url: str):
    """(ProductInfo|None, note) for a user-pasted product URL. Never raises.

    THE POINT OF THE NOTE. This project has already been burned by accepting a user's input and
    dropping it: an uploaded logo was read, bounded and discarded, and `/generate/upload` still
    accepts `product_name`/`product_description` form fields that reach nothing. Someone who pastes
    a link and gets a generic poster back has been told, silently, that the link worked. So every
    way this can fail returns a sentence the client can read.

    SSRF: the URL comes from a browser. Same gate as every other user-supplied URL here."""
    u = str(url or "").strip()
    if not u:
        return None, ""
    # REJECT A FOREIGN SCHEME BEFORE NORMALISING, not after. `ensure_scheme` exists to turn
    # "shop.test/p/1" into "https://shop.test/p/1", and handed `file:///etc/passwd` it produces
    # something whose HOST is `file` — which then passes a public-IP check on any resolver that
    # answers for it. Exactly the hole already found and closed in `competitor/manual.py`; the
    # order of these two lines is the whole fix.
    scheme = u.split(":", 1)[0].lower() if ":" in u.split("/", 1)[0] else ""
    if scheme and scheme not in ("http", "https"):
        return None, _PRODUCT_NOTES["unsafe"]
    try:
        from scraper.url_utils import ensure_scheme, is_safe_public_url
        u = ensure_scheme(u)
        if not u.lower().startswith(("http://", "https://")) or not is_safe_public_url(u):
            return None, _PRODUCT_NOTES["unsafe"]
    except Exception:  # noqa: BLE001 — no gate available means we do not fetch, not that we risk it
        return None, _PRODUCT_NOTES["unsafe"]
    if scrape_product_page is None:
        return None, _PRODUCT_NOTES["unavailable"]
    try:
        info = scrape_product_page(u)
    except Exception:  # noqa: BLE001 — a bad link must never cost the client their image
        logger.exception("product link scrape failed: %s", u)
        return None, _PRODUCT_NOTES["unreachable"]
    if info is None:
        return None, _PRODUCT_NOTES["unreachable"]
    if not getattr(info, "usable", False):
        return None, _PRODUCT_NOTES["empty"]
    return info, ""


def _render_real(brand: dict, *, headline: str, language: str, product=None,
                 trend_context: str = "", creative_contract=None,
                 generation_attempt_id: str = "") -> RealRender:
    """Real poster via poster.pipeline.generate_poster.

    Returns a RealRender whose `png` is None when the paid path could not deliver. Raises nothing:
    the CALLER decides what to tell the user, and must be told the reason (see `generate_image`).

    `product`: a `ProductInfo` read from a link the owner pasted. `generate_poster` has taken
    `product_image` / `product_name` all along — the webapp simply never passed them, so "make me
    an ad for THIS item" was not expressible.

    THE CHOSEN FACE rides `identity_image` (owner, 2026-07-26, looking at an Al Fouad poster
    minutes after picking a face from three candidates: «انا عايزة نفس الوش يظهر في البوستر»).
    The picker, the candidates and `selected_face_bytes` all existed; only the influencer's OWN
    poster endpoint passed them on, so the Studio's normal render — the one she actually uses —
    invented a different person every time."""
    profile = brand.get("_profile") or {}
    if not profile:
        raise MissingProfile("no stored _profile for this brand — nothing to ground a render on")
    from business_profile.llm import default_caller
    from poster.pipeline import generate_poster
    caller = default_caller(strong=True)
    face, face_mime = (None, "")
    try:
        face, face_mime = selected_face_bytes(brand)
    except Exception:  # noqa: BLE001 — no face is a normal state, never a failed render
        face, face_mime = None, ""
    # NO ENGINE OVERRIDE. This said engine="oneshot", which forced the model-draws-the-copy
    # path on every render — including Arabic, where it is measured to fail 100% of the time and
    # burn three image calls first. The pipeline picks the engine from the script.
    from poster.paid_boundary import validation_render_options
    res = generate_poster(profile, caller=caller, language=language,
                          **validation_render_options(),
                          # The STORED brand's own key is the authority for which
                          # brandbook this render may read (never the display name).
                          domain_key=str(brand.get("domain_key") or ""),
                          headline_override=headline or None,
                          trend_context=trend_context or None,
                          identity_image=((face, face_mime or "image/png") if face else None),
                          # EVERY candidate the product page showed, best-first: the og:image is
                          # often a composed banner that cannot be keyed, while the gallery holds
                          # the clean pack shot the composite needs.
                          product_image=((list(getattr(product, "images", None) or [])
                                          or [getattr(product, "image", "")])
                                         if product else None),
                          product_name=(getattr(product, "name", "") or None) if product else None,
                          # The item's OWN facts — its page description and the price exactly as
                          # shown — so the copy can sell the product (SPF, actives, price) and not
                          # just the shop. Empty when the link gave none; nothing is invented.
                          product_url=(getattr(product, "url", "") or "") if product else "",
                          product_description=(getattr(product, "description", "") or "") if product else "",
                          product_price=(getattr(product, "price", "") or "") if product else "",
                          product_currency=(getattr(product, "currency", "") or "") if product else "",
                          creative_contract=creative_contract,
                          generation_attempt_id=generation_attempt_id)
    import base64
    png = base64.b64decode(res.image_base64) if res and res.image_base64 else None
    return RealRender(png=png, result=res)


class MissingProfile(RuntimeError):
    """The brand has no evidenced profile, so no grounded render is possible."""


# ---------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------

def top_trend_context(brand: dict) -> tuple[str, dict, str]:
    """(trend_context, trend_used, note) from the brand's STORED trends — the ones serialized
    into `swot.trends` with their sources. «اضغط قبلها على ترند» works only if pressing it does
    something real: the plumbing (`generate_poster(trend_context=)` -> concept -> research) has
    existed all along and nothing in the webapp ever passed it.

    No stored trends -> ("", {}, an honest note) — a toggle that silently does nothing teaches
    the owner it is decorative."""
    trends = ((brand.get("swot") or {}).get("trends") or [])
    for t in trends:
        title = str((t or {}).get("title") or "").strip()
        if not title:
            continue
        url = str(t.get("url") or "").strip()
        terms = [str(x) for x in (t.get("matched_terms") or []) if x]
        ctx = title + (f" (terms: {', '.join(terms)})" if terms else "")
        return ctx, {"title": title, "url": url}, ""
    return "", {}, ("مفيش ترندات محفوظة للبراند ده لسه — شغّلي التحليل (SWOT) الأول وهيتسحبوا "
                    "بمصادرهم، وبعدها زرار الترند هيشتغل.")


def generate_image(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                   language: str = "auto", content_type: str = "post",
                   product_url: str = "", trend: bool = False,
                   creative_contract=None, generation_run_id: str = "",
                   generation_attempt_id: str = "") -> dict:
    """Return the image plus an honest account of HOW it was produced.

    Legacy calls degrade to a placeholder. Canonical calls fail closed and name their real
    PostgreSQL Run and Attempt, both created before the provider call.

    Beyond {image_b64, filename, headline, subtext, cta}:
      * `is_preview`    — True when this is the free placeholder, False for a real paid render. The
                          watermark used to be the only signal, and it told a user who had already
                          set BASEERA_ENABLE_PAID_IMAGES=1 to go and enable image generation.
      * `degraded_reason` — set ONLY when a paid render was requested and did not deliver. A silent
                          fallback answers "was an image produced?" while the user asked "was the
                          image I asked for produced?".
      * `trust_violation` / `trust_note_ar` — the poster pipeline's own top-level disclosure,
                          carried through instead of dropped (see RealRender)."""
    import base64

    if creative_contract is not None:
        from poster.strategy_contract import CreativeContractError
        status = str(getattr(getattr(creative_contract, "preflight", None), "status", "") or "")
        if status != "READY":
            raise CreativeContractError(
                "STRATEGY_NOT_CREATIVE_READY",
                f"Canonical poster preflight is {status or 'missing'}; provider call refused.",
                preflight_status=(status or "STRATEGY_NOT_CREATIVE_READY"))

    png: Optional[bytes] = None
    degraded_reason = ""
    trust_violation = False
    trust_note_ar = ""
    qa_disclosure = qa_disclosure_for_verdict()
    visual_qa = None
    acceptance = None

    # "Make me an ad for THIS product" — resolved BEFORE the render so its failure is a stated
    # note rather than a silently generic poster.
    product, product_note = product_from_link(product_url)

    # «متأثر بالترند» — the owner pressed Trend. The top stored trend (with its source) shapes
    # the concept; the response names it so she knows exactly what influenced the creative.
    trend_context, trend_used, trend_note = ("", {}, "")
    if trend:
        trend_context, trend_used, trend_note = top_trend_context(brand)

    if creative_contract is not None and not paid_images_enabled():
        raise RuntimeError(
            "Canonical poster generation requires BASEERA_ENABLE_PAID_IMAGES=1; "
            "no legacy preview fallback is allowed.")

    if paid_images_enabled():
        try:
            real = _render_real(brand, headline=headline, language=language, product=product,
                                trend_context=trend_context,
                                creative_contract=creative_contract,
                                generation_attempt_id=generation_attempt_id)
            png = real.png
            if png is None:
                degraded_reason = "the image model returned no image"
            res = real.result
            trust_violation = bool(getattr(res, "trust_violation", False))
            trust_note_ar = str(getattr(res, "trust_note_ar", "") or "")
            qa = getattr(res, "qa", None)
            visual_qa = qa.model_dump(mode="json") if hasattr(qa, "model_dump") else None
            from poster.vision_qa import poster_acceptance_from_output
            acceptance = poster_acceptance_from_output({
                "image_b64": base64.b64encode(png or b"").decode("ascii"),
                "filename": "pipeline-poster.png",
                "generation_attempt_id": generation_attempt_id,
                "visual_qa": visual_qa,
            }, payload=png or b"", expected_attempt_id=generation_attempt_id)
            qa_disclosure = acceptance_disclosure(acceptance)
        except MissingProfile as exc:
            degraded_reason = str(exc)
            logger.warning("paid render skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001 — degrade, but never in silence
            if creative_contract is not None:
                raise
            degraded_reason = f"{type(exc).__name__}: {exc}"[:200]
            logger.exception("real image generation failed; falling back to placeholder")

    if png is None:
        if creative_contract is not None:
            raise RuntimeError("Canonical poster pipeline returned no accepted image.")
        png = render_placeholder(brand, headline=headline, subtext=subtext, cta=cta)
        is_preview = True
        trust_violation, trust_note_ar = False, ""   # the placeholder makes no claims to violate
        # A verdict about an attempted paid candidate cannot label the different placeholder that
        # actually ships.  The placeholder itself was not inspected.
        from poster.vision_qa import poster_acceptance_from_output
        acceptance = poster_acceptance_from_output({
            "image_b64": base64.b64encode(png).decode("ascii"),
            "filename": "placeholder.png",
            "generation_attempt_id": generation_attempt_id,
            "visual_qa": None,
        }, payload=png, expected_attempt_id=generation_attempt_id)
        visual_qa = None
        qa_disclosure = acceptance_disclosure(acceptance)
    else:
        is_preview = False

    key = (brand.get("domain_key") or "brand")
    digest = hashlib.md5(png).hexdigest()[:10]
    result = {
        "image_b64": base64.b64encode(png).decode("ascii"),
        "filename": f"{key}_{content_type}_{digest}.png",
        "headline": headline,
        "subtext": subtext,
        "cta": cta,
        "is_preview": is_preview,
        "degraded_reason": degraded_reason,
        "trust_violation": trust_violation,
        "trust_note_ar": trust_note_ar,
        "visual_qa": visual_qa,
        "acceptance_decision": (
            acceptance.model_dump(mode="json") if hasattr(acceptance, "model_dump") else None),
        **qa_disclosure,
        # What we actually read from the pasted link, and why it did not work when it did not.
        "product": ({"name": product.name, "image": product.image, "price": product.price,
                     "currency": product.currency, "url": product.url} if product else None),
        "product_note": product_note,
        "trend_used": trend_used or None,
        "trend_note": trend_note,
        # «فوق الريل والبوست يبقى مكتوب كلام زي السوشيال» — every creative ships with the caption
        # to post it with. Failure degrades to a grounded tagline caption, never breaks the image.
        "social_caption": _caption_for(brand, language=language, trend_context=trend_context),
    }
    if creative_contract is not None:
        pipeline_result = real.result
        strategy_verification = dict(getattr(pipeline_result, "strategy_verification", None) or {})
        if not bool(strategy_verification.get("overall_pass")):
            raise RuntimeError("Canonical poster strategy verification did not pass.")
        if bool(getattr(pipeline_result, "trust_violation", False)):
            raise RuntimeError("Canonical poster was rejected for a trust violation.")
        from poster.vision_qa import poster_acceptance_from_output
        acceptance = poster_acceptance_from_output(
            result, payload=png, expected_attempt_id=generation_attempt_id)
        if not acceptance.accepted or not acceptance.publishable:
            raise CanonicalPosterQARejected(acceptance)
        result.update({
            "acceptance_decision": acceptance.model_dump(mode="json"),
            **acceptance_disclosure(acceptance),
        })
        result.update({
            "headline": str(getattr(getattr(pipeline_result, "brief", None), "headline", "") or ""),
            "subtext": str(getattr(getattr(pipeline_result, "brief", None), "subheadline", "") or ""),
            "cta": str(getattr(getattr(pipeline_result, "brief", None), "cta_text", "") or ""),
            "canonical": True,
            "strategy_grounded": True,
            "strategy_version_id": creative_contract.strategy_version_id,
            "creative_contract_id": creative_contract.creative_contract_id,
            "objective_id": creative_contract.objective_id,
            "decision_id": creative_contract.decision_id,
            "profile_snapshot_id": creative_contract.profile_snapshot_id,
            "generation_run_id": generation_run_id,
            "generation_attempt_id": generation_attempt_id,
            "claim_ids_used": list(getattr(pipeline_result, "claim_ids_used", None) or []),
            "creative_contract": creative_contract.model_dump(mode="json"),
            "poster_brief": dict(getattr(pipeline_result, "poster_strategy_brief", None) or {}),
            "strategy_verification": strategy_verification,
            "repair_history": list(getattr(pipeline_result, "repair_history", None) or []),
            "consumer_facing_disclosures": list(creative_contract.consumer_facing_disclosures),
            "why_this_poster": {
                "target": creative_contract.target,
                "message": creative_contract.primary_message,
                "proof": [v.statement for v in creative_contract.proof_points],
            },
        })
        from poster.claim_assessment import assess_copy, inventory, project_factual_acceptance
        result['authored_copy'] = inventory(getattr(pipeline_result, 'brief', None) or {})
        result['factual_assessment'] = assess_copy(
            brief=result['authored_copy'], contract=creative_contract,
            attempt_id=generation_attempt_id, payload=png).model_dump(mode='json')
        result = project_factual_acceptance(result, payload=png)
        from workflows.telemetry import Telemetry
        telemetry = Telemetry.from_env()
        telemetry.correlate(generation_attempt_id)
        assessment = result['factual_assessment']
        with telemetry.observation('poster_copy_assessment', metadata={
                'policy_version': assessment['policy_version'], 'method': assessment['method'],
                'copy_hash': assessment['copy_hash'], 'evidence_hash': assessment['evidence_hash'],
                'repair_count': len(result['repair_history'])}):
            telemetry.outcome('OK' if result['factual_eligible'] else 'BLOCKED')
        telemetry.flush()
    else:
        result.update({"canonical": False, "strategy_grounded": False})
    return result


def _caption_for(brand: dict, *, language: str, trend_context: str = "") -> dict:
    try:
        from api.webapp_social_copy import social_caption
        return social_caption(brand.get("_profile") or {}, language=language,
                              trend_context=trend_context)
    except Exception:  # noqa: BLE001 — a caption must never cost the client their image
        logger.exception("caption generation failed")
        return {}


# ---------------------------------------------------------------------
# on-disk store for URL-served images (calendar day posters)
# ---------------------------------------------------------------------

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."


def _generated_dir() -> Path:
    d = webapp_dir() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_asset_name(domain_key: str, kind: str) -> str:
    """A filename no other asset will take.

    MEASURED: influencer portraits were saved as `{domain_key}_influencer_portrait`, a FIXED name,
    so the second portrait overwrote the first — which defeats "generate three, pick one, keep the
    ones you rejected" before a model is ever called. The UI hid it behind a `?t=` cache-buster.

    The random part is not just a counter, because `/api/generated/{name}` is public with no auth by
    design (it serves only already-generated PNGs). A name derived from the brand slug alone would
    let anyone who can guess that slug fetch the brand's faces. Same shape the poster pipeline
    already uses for its renders (`poster_{uuid4().hex[:8]}`)."""
    import re
    import uuid
    safe_key = re.sub(r"[^A-Za-z0-9_-]+", "_", str(domain_key or "brand")).strip("_") or "brand"
    safe_kind = re.sub(r"[^A-Za-z0-9_-]+", "_", str(kind or "asset")).strip("_") or "asset"
    return f"{safe_key}_{safe_kind}_{uuid.uuid4().hex[:10]}"


def save_generated_png(png: bytes, name: str) -> str:
    """Persist a PNG and return its public URL path (/api/generated/<name>)."""
    safe = "".join(c if c in _SAFE else "_" for c in name)
    if not safe.endswith(".png"):
        safe += ".png"
    (_generated_dir() / safe).write_bytes(png)
    return f"/api/generated/{safe}"


def read_generated_png(name: str) -> Optional[bytes]:
    safe = "".join(c if c in _SAFE else "_" for c in name)
    p = _generated_dir() / safe
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def selected_face_bytes(brand: dict) -> tuple:
    """The chosen face as BYTES — what actually travels to a render.

    Until now the only thing that reached the poster was a sentence describing the person
    (`config.influencer_persona`), so "the same person" was a hope. Returns (None, "") when nothing
    is selected or the file is gone: the caller must then refuse to claim an influencer render
    rather than silently substituting a description.

    LIVES HERE (C7) because that is what it does: it reads a stored selection off the brand
    document and then fetches the ASSET, through `read_generated_png` directly above. Generated
    asset storage is this module's responsibility — `save_generated_png`, `read_generated_png`,
    `unique_asset_name` and `_generated_dir` are all here. Defining it in the influencer module
    meant this module had to import back from its own consumer, and that cycle made
    `api.webapp_influencer` unimportable in a fresh process: whichever of the two was imported
    first got a partially initialised copy of the other. `api.webapp_influencer` re-exports the
    name, so every existing caller and every monkeypatch keeps working.
    """
    ai = brand.get("ai_influencer") or {}
    # STRICTLY the selected candidate. `portrait_url` is a display pointer at the NEWEST face, so
    # falling back to it would render a poster starring whoever was generated last rather than
    # whoever the owner chose — the exact ambiguity the selection id exists to remove.
    chosen = (ai.get("candidates") or {}).get(ai.get("selected_id") or "")
    url = str((chosen or {}).get("url") or "")
    if not url:
        return None, ""
    data = read_generated_png(url.rsplit("/", 1)[-1])
    return (data, "image/png") if data else (None, "")


# The pasted hero link, resolved ONCE per URL. Two consumers need the answer — the insert needs
# the PACKSHOT and the director needs the NAME — and before this they asked separately: the insert
# matched the catalogue while the director was told nothing, so a link with no `--product-name`
# produced a film about the pharmacy with a spray glued to the last beat. One brand, one link, two
# lanes with different ideas of what the ad was about. The cache is what makes one source of truth
# affordable: the fallback path fetches a web page, and asking twice would fetch twice.
_HERO_CACHE: dict = {}


def hero_product_facts(profile: Optional[dict], hero_url: str) -> dict:
    """`{"name", "images", "price"}` for the product the owner pasted — never raises.

    THE CATALOGUE FIRST, because a catalogued item is already parsed, priced and free. Failing
    that the PAGE ITSELF is fetched, because "not in the catalogue" is not "not available":
    MEASURED on her real link, the catalogue held `/en/products/<latin-slug>` and she pasted
    `/products/<arabic-slug>` — the same shop, the same item, slugs that can never match. No
    matcher can bridge that; only a fetch can.

    An empty dict means the link resolved to nothing, and every caller must treat that as "no hero"
    rather than as "no product".
    """
    key = str(hero_url or "").strip()
    if not key:
        return {}
    if key in _HERO_CACHE:
        return _HERO_CACHE[key]
    out: dict = {}
    try:
        from scraper.catalog import canonical_product_url
        want = canonical_product_url(key)
        for item in ((profile or {}).get("catalog") or []):
            if not isinstance(item, dict):
                continue
            if canonical_product_url(str(item.get("url") or "")) == want:
                u = str(item.get("image_url") or "").strip()
                out = {"name": str(item.get("name") or "").strip(),
                       "images": [u] if u else [],
                       "price": str(item.get("price_text") or "").strip()}
                logger.info("[hero] hero product resolved from the catalogue: %s",
                            out["name"][:60])
                break
        else:
            logger.info("[hero] hero link is not in the catalogue — fetching the page")
            prod, note = product_from_link(key)
            imgs = [str(u).strip() for u in (list(getattr(prod, "images", None) or []) or
                                             [getattr(prod, "image", "")]) if str(u or "").strip()]
            if imgs:
                out = {"name": str(getattr(prod, "name", "") or "").strip(),
                       "images": imgs,
                       "price": str(getattr(prod, "price", "") or "").strip()}
                logger.info("[hero] hero fetched: %s", out["name"][:60])
            else:
                logger.info("[hero] hero link unusable: %s", str(note or "no image")[:80])
    except Exception as exc:  # noqa: BLE001 — a hero that will not resolve must not cost the reel
        logger.info("[hero] hero link could not be resolved (%s) — catalogue order stands",
                    type(exc).__name__)
        out = {}
    # ONLY A SUCCESS IS REMEMBERED. Caching a failure would turn one timed-out fetch into a hero
    # that stays unresolved for the life of the process — "absence read as success" with a memory.
    # A retry costs one request per render, which is the correct price for not shipping the wrong
    # product because the network blinked once.
    if out:
        _HERO_CACHE[key] = out
    return out
