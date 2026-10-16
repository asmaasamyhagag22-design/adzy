"""AI influencer — greenfield feature, degrade-safe.

The frontend flow: create a portrait (persona knobs) -> validate it -> generate a poster featuring
the influencer + a product. All three are paid image operations with no existing pipeline, and they
403 under D-5, so the DEFAULT is a free Pillow placeholder (a persona/poster card) — the whole flow
works end to end, honestly watermarked "preview". Real generation is deferred behind
BASEERA_ENABLE_PAID_IMAGES (there is no in-repo influencer-image pipeline yet). Never touches reel.

Each function returns a PATCH to shallow-merge into the brand's `ai_influencer` block, which the
frontend reads: {has_portrait, portrait_url, has_poster, poster_url, validated, config{...}}.
"""
from __future__ import annotations

import io
import textwrap
from typing import Optional

from PIL import Image, ImageDraw

from . import webapp_images as images
from .text_shaping import has_rtl, shape_for_pillow, wrap_shaped

# The influencer preview is its own line (not the image placeholder's), localized on the brand's
# MEASURED language with a universal default — D-8.
_PREVIEW_NOTE = {
    "ar": "معاينة المؤثر · فعّلي توليد الصور للنسخة النهائية",
    "fr": "aperçu de l'influenceur · activez la génération d'images",
    "es": "vista previa del influencer · activa la generación de imágenes",
}
_PREVIEW_NOTE_DEFAULT = "AI influencer preview · enable image generation for the final render"

# The caption above is only true while generation is OFF. Stamped unconditionally it told a user
# who had already set BASEERA_ENABLE_PAID_IMAGES=1 to go and enable it — the exact wording
# `webapp_images` was corrected for. And the flag is not even the blocker here: no influencer image
# pipeline exists in this repo, so turning it on changes nothing. Name the real reason.
_NO_PIPELINE_NOTE = {
    "ar": "معاينة المؤثر · رندر المؤثر الحقيقي لسه مش متوفر",
    "fr": "aperçu de l'influenceur · le rendu réel n'est pas encore disponible",
    "es": "vista previa del influencer · el render real aún no está disponible",
}
_NO_PIPELINE_DEFAULT = "AI influencer preview · real influencer rendering is not available yet"
_NO_PIPELINE_REASON = "no influencer image pipeline yet"


def _note_for(brand: dict) -> str:
    """The caption stamped on the placeholder, in the brand's language and true either way."""
    lang = images.brand_language(brand)
    if images.paid_images_enabled():
        return _NO_PIPELINE_NOTE.get(lang, _NO_PIPELINE_DEFAULT)
    return _PREVIEW_NOTE.get(lang, _PREVIEW_NOTE_DEFAULT)


def _disclosure() -> dict:
    """Machine-readable twin of the caption — the caption is pixels, so nothing could branch on it.
    Mirrors the fields `webapp_images.generate_image` already returns."""
    return {"is_preview": True,
            "degraded_reason": _NO_PIPELINE_REASON if images.paid_images_enabled() else ""}


def _persona_description(cfg: dict) -> str:
    """A human sentence from the persona knobs the frontend sends."""
    bits = []
    for key in ("age", "gender", "skin", "hair_length", "hair_color", "hair_style",
                "expression"):
        v = str(cfg.get(key) or "").strip()
        if v and v.lower() not in ("none", "any", ""):
            bits.append(v.replace("_", " "))
    if str(cfg.get("beard") or "").lower() in ("yes", "true", "1"):
        bits.append("with a beard")
    if str(cfg.get("glasses") or "").lower() in ("yes", "true", "1"):
        bits.append("wearing glasses")
    persona = str(cfg.get("persona") or "").strip()
    desc = ", ".join(bits)
    return f"{persona} — {desc}" if persona and desc else (persona or desc or "brand persona")


def _card(brand: dict, title: str, lines: list[str], *, avatar: bool) -> bytes:
    """The persona/poster placeholder card.

    Every string goes through `shape_for_pillow` and RTL copy is right-aligned, for the same
    measured reason as `webapp_images.render_placeholder`: Pillow has no raqm backend here, so
    unshaped Arabic draws isolated and reversed (see api/text_shaping)."""
    W, H = 1080, 1350
    colors = brand.get("brand_colors") or ["#22252b"]
    bg = images._hex_to_rgb(colors[0])
    accent = images._hex_to_rgb(colors[1]) if len(colors) > 1 else (210, 190, 160)
    ink = (255, 255, 255) if images._luminance(bg) < 0.55 else (20, 20, 20)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    margin = int(W * 0.09)
    rtl = has_rtl(" ".join([title, *lines]))

    def draw(text: str, font, y: int) -> None:
        x = (W - margin - d.textlength(text, font=font)) if rtl else margin
        d.text((x, y), text, font=font, fill=ink)

    draw(shape_for_pillow(title), images._font(40), int(H * 0.06))

    if avatar:
        cx, cy, r = W // 2, int(H * 0.34), 210
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
        # simple head+shoulders silhouette inside the circle
        hr = int(r * 0.42)
        d.ellipse([cx - hr, cy - hr - 30, cx + hr, cy + hr - 30], fill=bg)
        d.ellipse([cx - int(r * 0.75), cy + 30, cx + int(r * 0.75), cy + r + 120], fill=bg)

    y = int(H * 0.60)
    pf = images._font(46)
    for raw in lines:
        for line in wrap_shaped(raw, 34, 3):
            draw(line, pf, y)
            y += 58
        y += 14

    draw(shape_for_pillow(_note_for(brand)), images._font(22), H - 46)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _ai(brand: dict) -> dict:
    ai = dict(brand.get("ai_influencer") or {})
    ai.setdefault("config", {})
    ai.setdefault("candidates", {})
    return ai


# ---------------------------------------------------------------------------
# Candidates — "three people, pick one", paid one at a time
# ---------------------------------------------------------------------------

MAX_CANDIDATES = 3


class TooManyCandidates(RuntimeError):
    """The owner asked for a fourth face. Three is the choice she described."""


class UnknownCandidate(RuntimeError):
    """A selection referring to a face that is not in this brand's set."""


def _render_portrait(brand: dict, persona: str, market: str) -> bytes:
    """The real thing: nano-banana, through the metered lane.

    `reel.render3.nano.generate_with_refs` is the repo's reference-conditioned generator — spend
    guarded (`guard_image` before, `record` after), lane failover, quota backoff. No refs on a
    FIRST portrait: there is no prior face to hold to yet, so this is the only unconditioned call
    in the flow. Every later render attaches this image and locks to it."""
    import tempfile
    from pathlib import Path

    from reel.render3.nano import generate_with_refs

    prompt = (
        "A photorealistic head-and-shoulders portrait of ONE person, facing the camera, neutral "
        "studio background, soft even lighting, natural skin texture, no text anywhere in the "
        f"image, no logos, no props.\n{persona}\n"
        + (f"{market}\n" if market else "")
        + "This is a casting portrait: the face must be clear, unobstructed and well lit, because "
          "it will be reused as the identity reference for later renders."
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portrait.png"
        generate_with_refs(prompt, out, refs=None, pro=False)
        return out.read_bytes()


def generate_candidate(brand: dict, cfg: dict) -> dict:
    """Produce ONE more candidate face and return the patch that adds it.

    The owner pays per face ("واحد الأول، والتاني والتالت بطلب"), so this never generates a set —
    it appends one, and refuses past `MAX_CANDIDATES` rather than quietly spending again."""
    from .influencer_identity import persona_from_brand, persona_from_owner

    ai = _ai(brand)
    if len(ai.get("candidates") or {}) >= MAX_CANDIDATES:
        raise TooManyCandidates(
            f"{MAX_CANDIDATES} candidates already exist — pick one, or remove one first.")

    profile = brand.get("_profile") or {}
    persona = persona_from_owner(cfg) or persona_from_brand(profile)
    market = ""
    try:
        from poster.locale import country_of
        market = country_of(profile)
    except Exception:  # noqa: BLE001
        market = ""

    png, is_preview, degraded = None, True, ""
    if images.paid_images_enabled():
        try:
            png = _render_portrait(brand, persona, market)
            is_preview = False
        except Exception as exc:  # noqa: BLE001 — the caller is TOLD why, never silently degraded
            degraded = f"{type(exc).__name__}: {exc}"[:200]
    if png is None:
        png = _card(brand, "AI INFLUENCER", [persona], avatar=True)
        is_preview = True
        degraded = degraded or (_NO_PIPELINE_REASON if images.paid_images_enabled() else "")

    import uuid
    cand_id = uuid.uuid4().hex[:8]
    url = images.save_generated_png(
        png, images.unique_asset_name(brand.get("domain_key", "brand"), "influencer_portrait"))
    entry = {"url": url, "persona": persona, "market": market,
             "is_preview": is_preview, "degraded_reason": degraded,
             "config": {k: v for k, v in (cfg or {}).items() if k != "domain_key"}}
    patch = {"candidates": {cand_id: entry}, "has_portrait": True}
    if not ai.get("selected_id"):
        # A DISPLAY pointer at the newest face, so the owner can see what she just paid for and
        # decide. Deliberately NOT `validated` and NOT `selected_id`: being visible is not being
        # chosen, and conflating the two is what let an approval slide onto a later portrait.
        #
        # Once a face IS chosen the pointer stops moving — caught by this module's own test. The
        # single legacy field cannot mean both "newest" and "chosen", and after a selection the
        # only honest reading is the chosen one, or the preview would show a face the poster will
        # not use.
        patch.update({"portrait_url": url, "is_preview": is_preview,
                      "degraded_reason": degraded})
    return {"ai_influencer": patch}


def select_candidate(brand: dict, candidate_id: str) -> dict:
    """Lock ONE candidate in as the brand's face.

    `validated` used to be a bare bool with no idea WHICH portrait it approved, so approving one
    face and then generating another authorised a poster for the wrong person. The selection is now
    an id, and the legacy fields are derived from it so they can never disagree."""
    ai = _ai(brand)
    cands = ai.get("candidates") or {}
    if candidate_id not in cands:
        raise UnknownCandidate(f"no candidate {candidate_id!r} for this brand")
    chosen = cands[candidate_id]
    return {"ai_influencer": {
        "selected_id": candidate_id,
        # Derived, never independently set — the old flat fields the frontend still reads.
        "portrait_url": chosen.get("url"),
        "has_portrait": True,
        "validated": True,
        "is_preview": chosen.get("is_preview", True),
        "degraded_reason": chosen.get("degraded_reason", ""),
        # Patch only the value selection derives.  Replaying the whole config read above can undo
        # independent owner edits that commit before this patch reaches BrandStore.
        "config": {"influencer_persona": chosen.get("persona", "")},
    }}


# C7: `selected_face_bytes` now lives in `api.webapp_images`, beside the generated-asset storage
# it reads through. Re-exported here as a MODULE-LEVEL name so existing callers
# (`inf.selected_face_bytes(...)`) and existing monkeypatches keep working unchanged, and so the
# functions below still resolve it through this module's globals.
#
# The direction is what matters: this module imports `webapp_images`, and `webapp_images` imports
# nothing back. Before, each imported the other, and whichever was imported first in a fresh
# process handed the second a half-built module — which is why `import api.webapp_influencer` on
# its own raised ImportError while `import api.main` happened to work.
selected_face_bytes = images.selected_face_bytes


def generate_portrait(brand: dict, cfg: dict) -> dict:
    persona = _persona_description(cfg)
    png = _card(brand, "AI INFLUENCER", [persona], avatar=True)
    url = images.save_generated_png(png, f"{brand.get('domain_key','brand')}_influencer_portrait")
    ai = _ai(brand)
    ai.update({
        "has_portrait": True, "portrait_url": url,
        "validated": False,                          # a new portrait needs re-approval
        "has_poster": False, "poster_url": None,
        "config": {**ai.get("config", {}), "influencer_persona": persona,
                   **{k: cfg.get(k) for k in cfg if k != "domain_key"}},
        **_disclosure(),
    })
    return {"ai_influencer": ai}


def validate_portrait(brand: dict, approved: bool) -> dict:
    ai = _ai(brand)
    ai["validated"] = bool(approved)
    return {"ai_influencer": ai}


def generate_influencer_poster(brand: dict, *, product_hint: str = "",
                               scene_description: str = "") -> dict:
    """A branded poster with THE SELECTED FACE in it.

    The chosen portrait travels as BYTES on `generate_poster(identity_image=…)`, so the person in
    the poster is the person the owner picked rather than a re-sampled stranger who matches a
    description. Until now the only thing that reached this step was the persona SENTENCE.

    NO SILENT SUBSTITUTION: if the face bytes cannot be read, this does not quietly fall back to
    "a poster of someone who fits the description" — that would answer a different question from
    the one the owner asked. The result is disclosed as a preview with the reason."""
    ai = _ai(brand)
    persona = ai.get("config", {}).get("influencer_persona", "brand persona")
    face, face_mime = selected_face_bytes(brand)

    png, is_preview, degraded = None, True, ""
    if images.paid_images_enabled():
        if not face:
            degraded = "no selected influencer face could be read — pick a look first"
        else:
            try:
                rendered = _render_influencer_poster(
                    brand, persona=persona, face=(face, face_mime),
                    product_hint=product_hint, scene_description=scene_description)
                png = rendered.png
                qa_disclosure = images.qa_disclosure_for_verdict(
                    getattr(rendered.result, "qa", None))
                is_preview = False
            except Exception as exc:  # noqa: BLE001
                degraded = f"{type(exc).__name__}: {exc}"[:200]
    if png is None:
        lines = [f"Featuring: {persona}"]
        if product_hint:
            lines.append(f"Product: {product_hint}")
        if scene_description:
            lines.append(f"Scene: {scene_description}")
        png = _card(brand, (brand.get("organization_name") or "BRAND").upper(), lines,
                    avatar=False)
        is_preview = True
        degraded = degraded or (_NO_PIPELINE_REASON if images.paid_images_enabled() else "")
        # The free fallback is not the paid candidate the pipeline may have inspected.
        qa_disclosure = images.qa_disclosure_for_verdict()

    url = images.save_generated_png(
        png, images.unique_asset_name(brand.get("domain_key", "brand"), "influencer_poster"))
    # Return only metadata produced by THIS job.  `ai` is the snapshot read before rendering; if it
    # is returned wholesale it can resurrect a candidate the owner reset, or overwrite a config
    # edit made while the image was rendering.
    return {"ai_influencer": {
        "has_poster": True,
        "poster_url": url,
        "config": {"product_hint": product_hint, "scene_description": scene_description},
        "is_preview": is_preview,
        "degraded_reason": degraded,
        **qa_disclosure,
    }}


def _render_influencer_poster(brand: dict, *, persona: str, face: tuple,
                              product_hint: str = "", scene_description: str = "") -> images.RealRender:
    """The real branded poster, through the existing poster pipeline with the face attached.

    Deliberately the WHOLE pipeline and not a bare image call: that is what puts the gate-verified
    Arabic copy, the real logo and the QA gates on the result. `identity_image` rides its own
    parameter down to `poster.oneshot`, where it leads the attachments and is described as a
    PERSON — never folded into `product_images`, which is capped at two and whose members the
    prompt and the art critic both treat as merchandise."""
    import base64

    from business_profile.llm import default_caller
    from poster.pipeline import generate_poster

    profile = brand.get("_profile") or {}
    if not profile:
        raise RuntimeError("no stored _profile for this brand — nothing to ground a render on")
    scene = "; ".join(x for x in (scene_description, f"presenting {product_hint}"
                                  if product_hint else "") if x)
    res = generate_poster(profile, caller=default_caller(strong=True), engine="oneshot",
                          domain_key=str(brand.get("domain_key") or ""),
                          language=images.brand_language(brand),
                          identity_image=face,
                          identity_persona=persona,
                          trend_context=scene or None)
    if not (res and res.image_base64):
        raise RuntimeError("the poster pipeline returned no image")
    return images.RealRender(png=base64.b64decode(res.image_base64), result=res)
