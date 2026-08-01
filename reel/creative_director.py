"""Gemini 2.5 Pro creative director for the reel.

The user wants more than "animate a photo + stamp static text". They want a real
creative DIRECTOR: send the business identity + the REAL photos to Gemini 2.5 Pro, and
let it design a complete, engaging reel — for EACH scene a rich Veo 3.1
image-to-video prompt (cinematic MOTION and life inside the scene: people moving,
ambient action, camera movement — not just a zoom), a punchy VOICE-OVER line, and
an optional short on-screen caption — opening with a scroll-stopping hook, built to
earn likes/shares and convert viewers into customers.

Opus SEES the real photos (vision), so each scene's motion is true to what's
actually in that photo. Discipline: the VISUALS stay real (real photos) and no
factual claim may be invented (no fake awards/numbers/certifications) — but the copy
is allowed to be persuasive and evocative, grounded in the provided identity.

No Gemini caller (creds/SDK) -> None (honest-degrade), never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-pro"


class CreativeScene(BaseModel):
    image_index: int                       # which real photo to bring to life
    veo_prompt: str                        # rich image-to-video prompt for Veo 3.1
    voiceover: str = ""                     # narration line (brand language)
    voiceover_delivery: str = ""           # emotion/performance note for this line
    on_screen_text: str = ""               # short kinetic caption (may be empty)
    duration_s: float = 4.0
    # --- creative-contract fields (additive; the gate + render layers consume these) ---
    hero_product: bool = False             # this scene concretely shows the product/service
    product_overlay: str = ""              # "" | "none" | "real_screen" | "real_photo" (post-composite)
    caption_role: str = ""                 # "" | name | benefit | proof | cta | layer
    sfx: str = ""                          # SFX cue for this beat: whoosh | impact | chime | ...
    sound_mood: str = ""                   # per-scene mood token in the sound arc
    arc_beat: str = ""                     # which narrative_arc beat THIS scene serves


class CreativeReel(BaseModel):
    concept: str = ""                       # the creative idea in one line
    hook: str = ""                          # scroll-stopping opener
    music_mood: str = ""                    # suggested soundtrack vibe
    cta: str = ""                           # closing call to action (verbatim from brand if possible)
    language: str = "en"
    images: list[str] = Field(default_factory=list)   # the real photos the model saw, in index order
    scenes: list[CreativeScene] = Field(default_factory=list)
    model: str = _DEFAULT_MODEL
    # --- creative-contract fields (additive) ---
    dialect: str = ""                       # locked register/dialect for the whole VO
    sound_design: str = ""                  # the mood-progression arc (Phase C render consumes it)
    brand_unique_claim: str = ""            # the one thing only this brand can say (grounded)
    # --- creative STRATEGY (2026-07-20 owner ruling: structure is testable, taste is not) ---
    creative_pov: str = ""                  # the ONE feeling the viewer leaves with
    narrative_arc: list[str] = Field(default_factory=list)   # >=3 ordered story beats
    visual_motif: str = ""                  # the recurring visual thread (must appear in >=2 scenes)


class _ReelResponse(BaseModel):
    """The RAW structured shape the director returns (no images/model — those are code-set)."""
    concept: str = ""
    hook: str = ""
    music_mood: str = ""
    cta: str = ""
    language: str = ""
    dialect: str = ""
    sound_design: str = ""
    brand_unique_claim: str = ""
    creative_pov: str = ""
    narrative_arc: list[str] = Field(default_factory=list)
    visual_motif: str = ""
    scenes: list[CreativeScene] = Field(default_factory=list)


def _v(profile: dict, key: str) -> str:
    f = profile.get(key)
    if isinstance(f, dict):
        f = f.get("value")
    return str(f).strip() if f else ""


def _brand_unique_block(profile: dict) -> str:
    """The grounded differentiators only THIS brand can credibly claim — fed to the director so the
    reel says something specific, not generic (the [SAY THE ONE THING…] contract). Reuses the
    Ledger-gated brand-signal strengths + other_unique_insights; every line is verified sourced, so
    nothing is fabricated. Empty string on any failure (the director must never break on grounding)."""
    try:
        from grounding.ledger import EvidenceLedger
        from competitor.brand_signals import strengths_from_profile
        ledger = EvidenceLedger.from_profile(profile)
        items: list[str] = [s.text for s in strengths_from_profile(profile, ledger)]
        for ins in (profile.get("other_unique_insights") or []):
            t = str((ins.get("value") if isinstance(ins, dict) else ins) or "").strip()
            if t and t not in items and all(v.sourced for v in ledger.audit_text(t)):
                items.append(t)
    except Exception:  # noqa: BLE001
        return ""
    items = [t for t in items if t][:5]
    if not items:
        return ""
    return ("BRAND-UNIQUE (only THIS brand can credibly say — use >=1 in the copy, grounded):\n- "
            + "\n- ".join(items))


def _identity_block(profile: dict) -> str:
    """A compact identity brief for Opus — reuses the domain-adaptive schema when
    available so the director understands THIS vertical, not a generic one."""
    lines = [f"Business: {_v(profile, 'name')}"]
    for k, lbl in (("category", "Category"), ("tagline", "Tagline"),
                   ("description", "About"), ("audience_type", "Audience"),
                   ("tone_of_voice", "Tone"), ("pricing_posture", "Pricing")):
        val = _v(profile, k)
        if val:
            lines.append(f"{lbl}: {val}")
    offerings = [o.get("name") for o in (profile.get("offerings") or []) if isinstance(o, dict) and o.get("name")]
    if offerings:
        lines.append("Offerings: " + "; ".join(offerings))
    vps = [(x.get("value") if isinstance(x, dict) else x) for x in (profile.get("value_propositions") or [])]
    vps = [str(v).strip() for v in vps if v]
    if vps:
        lines.append("Value props: " + "; ".join(vps))

    # FIX-D4: the brand's evidence-derived country reaches the director — the reel used to be
    # cast with ZERO locale (generic-Western people for a .eg government institute).
    try:
        from poster.locale import country_of
        _country = country_of(profile)
        if _country:
            lines.append(f"Country/market: {_country} — people, features, dress and settings "
                         f"authentic to {_country}; never default to generic Western casting.")
    except Exception:  # noqa: BLE001
        pass

    # Domain-adaptive attributes (model-generated, grounded) — the richest signal.
    try:
        from business_profile.domain_schema import build_domain_profile
        dp = build_domain_profile(profile)
        if dp:
            lines.append(f"Specific vertical: {dp.vertical}")
            for f in dp.fields:
                lines.append(f"- {f.label}: {f.value}")
    except Exception:
        pass

    unique = _brand_unique_block(profile)
    if unique:
        lines.append(unique)
    return "\n".join(lines)


def _image_parts(image_urls: list[str], *, max_images: int, max_side: int = 640) -> tuple[list[tuple[bytes, str]], list[str]]:
    """Download + downscale each real photo to (jpeg_bytes, mime) for the Gemini caller's
    `images=` argument. Returns (parts, used_urls) — only the images we could actually fetch,
    IN ORDER, so parts[i] corresponds to real-photo index i."""
    import io
    from reel.video_provider import _load_reference_image
    parts: list[tuple[bytes, str]] = []
    used: list[str] = []
    for u in image_urls[:max_images]:
        loaded = _load_reference_image(u)
        if not loaded:
            continue
        data, _mime = loaded
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82)
            data = buf.getvalue()
        except Exception:
            pass
        parts.append((data, "image/jpeg"))
        used.append(u)
    return parts, used


# Vertical/tone modes. The old single prompt hard-coded FOOD dynamics ("steam/smoke, flames,
# mouth-watering excitement") into every reel — wrong for a jewelry house or a clinic. The motion
# vocabulary, delivery examples, and imagery now swap by mode so each reel is true to its brand.
_ELEGANT_CATS = {"jewelry", "jewellery", "fashion", "accessories", "watches", "couture"}
_FOOD_CATS = {"restaurant", "cafe", "bakery", "food", "fast_food", "patisserie", "coffee_shop"}
_BEAUTY_CATS = {"beauty", "cosmetics", "cosmetic", "skincare", "haircare", "salon", "spa", "personal_care"}
_BEAUTY_KEYWORDS = ("skin", "hair", "face", "lip", "cosmetic", "cream", "serum", "cleanser", "mist",
                    " oil", "shampoo", "makeup", "lotion", "beauty", "salon", "fragrance", "perfume")


def _is_beauty(profile: dict) -> bool:
    """A beauty/skincare/haircare brand — often typed just 'ecommerce', so detect it from the
    offerings/description keywords (>=2 hits). This vertical is the MOST people-driven (someone
    applying the product), so it must not fall into the bland 'generic' motion."""
    text = (_v(profile, "description") + " " + _v(profile, "tagline") + " " + " ".join(
        str(o.get("name") if isinstance(o, dict) else o) for o in (profile.get("offerings") or []))).lower()
    return sum(1 for k in _BEAUTY_KEYWORDS if k in text) >= 2


def _vertical_mode(profile: dict) -> str:
    """'beauty' (person applying the product), 'elegant' (luxury/jewelry, worn), 'food' (savoured),
    else 'generic' — every mode now demands a PERSON using the product with real energy."""
    cat = _v(profile, "category").lower()
    tone = _v(profile, "tone_of_voice").lower()
    if cat in _BEAUTY_CATS or _is_beauty(profile):
        return "beauty"
    if tone == "luxury" or cat in _ELEGANT_CATS:
        return "elegant"
    if cat in _FOOD_CATS:
        return "food"
    return "generic"


# Shared rule for EVERY mode: a real PERSON enters frame and USES the product, energetic/TikTok-native
# motion — the OLD guidance ("refined", "never spectacle", "slow push-in", "true to that exact photo")
# made Veo only move the camera over a still, which read as a static zoom (owner: "الصورة متزومة
# مبتهزش"). The PRODUCT stays exactly as shown (it's the i2v seed); only the person/action is generated.
_MOTION_TAIL = (" The product's IDENTITY must stay exactly as shown — same shape, label, colour and "
                "proportions — even as a hand lifts, tilts and operates it; only its position/pose may "
                "change, never its design. ADD the person and the action AROUND it. Show only "
                "PHYSICALLY LOGICAL use — if the product "
                "has a cap / lid / pump / dropper, the person REMOVES or FLIPS it BEFORE dispensing, and "
                "operates it the way it really works; NEVER an impossible action (pressing a sealed pump, "
                "pouring from a closed bottle). Real, energetic movement in every second — NOT a slow "
                "zoom, NOT a static pan. Add no fake text, logos, or signage.")
_MOTION_GUIDANCE = {
    "beauty": (
        "- veo_prompt: a vivid, TikTok-native Veo 3.1 IMAGE-TO-VIDEO prompt where a real PERSON uses "
        "THIS exact product: a hand enters frame, picks it up and applies it (sprays the mist into "
        "flowing hair, smooths the oil through strands, works the cleanser on fresh skin), and the "
        "model REACTS — a satisfied smile, hair catching the light and swinging, glowing skin. "
        "Tactile, quick natural gestures, a snappy push-in or whip-pan." + _MOTION_TAIL
    ),
    "elegant": (
        "- veo_prompt: a vivid Veo 3.1 IMAGE-TO-VIDEO prompt where a poised model WEARS/holds THIS "
        "exact piece and it comes ALIVE — a hand turning it into the light, the piece on skin as she "
        "moves, an admiring glance, fabric and light shifting, a confident dynamic camera. Refined "
        "but ALIVE, real movement." + _MOTION_TAIL
    ),
    "food": (
        "- veo_prompt: a vivid Veo 3.1 IMAGE-TO-VIDEO prompt where a real person ENJOYS this exact "
        "dish — a hand reaches in and lifts/pours/plates it, a satisfied bite, rising steam, sauce "
        "glistening, energetic appetite-driven motion." + _MOTION_TAIL
    ),
    "generic": (
        "- veo_prompt: a vivid Veo 3.1 IMAGE-TO-VIDEO prompt where a real PERSON picks up and USES "
        "this exact product and reacts genuinely — hands working, natural gestures, a dynamic camera "
        "with real energy and life." + _MOTION_TAIL
    ),
}
_DELIVERY_EG = {
    "beauty": "'fresh and upbeat', 'confident glow', 'delighted reaction', 'warm friendly invitation'",
    "elegant": "'hushed reverence', 'quiet confidence', 'warm elegant invitation', 'proud heritage'",
    "food": "'intrigued, slow build', 'mouth-watering excitement', 'warm proud invitation'",
    "generic": "'confident and bold', 'upbeat and energetic', 'warm proud invitation'",
}


def _locked_register(language: str, profile: Optional[dict] = None) -> str:
    """The one dialect/register locked for the whole voice-over ([ONE VOICE]). Universal + evidence
    driven: the REEL_ARABIC_DIALECT=fusha override wins; otherwise the Arabic register follows the
    brand's EVIDENCED country (poster.locale) when a profile is given — a Saudi brand locks Khaleeji,
    not Masri; with no profile it keeps the pilot default (Egyptian colloquial). Any other language
    locks to a conversational register of that language."""
    lang = (language or "en").lower()
    if lang.startswith("ar"):
        pref = (os.environ.get("REEL_ARABIC_DIALECT") or "").strip().lower()
        if pref in ("fusha", "msa", "standard"):
            return "Modern Standard Arabic (فصحى) — formal, consistent"
        if profile is not None:
            from poster.locale import arabic_region_of, arabic_dialect_for
            reg = arabic_region_of(profile)
            if reg and reg != "egypt":
                return f"{arabic_dialect_for(profile)} — natural spoken register, one voice"
        return "Egyptian colloquial (عامية مصرية) — natural spoken Masri"
    return f"conversational {lang.split('-')[0]}"


def _locale_primer(profile: dict) -> str:
    """The measured-market casting instruction, derived — never a hardcoded nationality (D-8).
    `poster.locale.brand_locale` already resolves the country from the profile's own evidence and
    returns the primer the poster and the seeds use; the director now reads the SAME line, so all
    three surfaces cast from one measured signal."""
    try:
        from poster.locale import brand_locale
        _c, primer = brand_locale(profile)
        return str(primer or "")
    except Exception:  # noqa: BLE001 — no locale evidence -> no casting line, never a crash
        return ""


def _system_prompt(n_scenes: int, language: str, mode: str = "generic",
                   featured: Optional[str] = None, compliance: str = "",
                   product_photo_anchored: bool = False, register: str = "",
                   locale_primer: str = "", trend_context: str = "") -> str:
    motion = _MOTION_GUIDANCE.get(mode, _MOTION_GUIDANCE["generic"])
    deliveries = _DELIVERY_EG.get(mode, _DELIVERY_EG["generic"])
    register_note = (f"LOCKED DIALECT — write EVERY voiceover line in: {register}. One register, no "
                     f"drift (the [ONE VOICE] contract).\n\n" if register else "")
    # Ad-safety, ONE source (poster.contracts.compliance_for): a clinic/beauty reel must obey the
    # same policy as its poster — no before/after, no clinical claims, no guaranteed results. Empty
    # for categories with no rule beyond brand-safety, so most reels are unchanged.
    compliance_line = (f"{compliance}\n\n" if compliance else "")
    # «عايزة بوستر أو ريل متأثر بالترند» — a REAL, sourced market signal the owner toggled on.
    # It may shape the angle only; it must never introduce claims the Ledger has not seen.
    trend_line = (("CURRENT MARKET SIGNAL (the owner asked this reel to ride it — let it shape "
                   "the ANGLE and framing, never invent facts from it):\n"
                   + str(trend_context).strip() + "\n\n")
                  if str(trend_context or "").strip() else "")
    if featured and product_photo_anchored:
        # ONE picked product WITH its real photo anchoring the pool: SHOTS of the SAME item.
        featured_line = (
            f"FEATURED PRODUCT: {featured}. This reel advertises ONLY this product — EVERY scene is "
            "the SAME product (REAL PHOTO index 0), varied by SHOT and ACTION (macro detail, in-hand "
            "pickup, real in-use application, satisfied result), NEVER a different product.\n\n")
        image_rule = "- image_index: ALWAYS 0 — the SAME featured product; vary the shot/action, not the product.\n"
        seq_rule = ("Anchor EVERY scene on the ONE featured product (index 0); vary the SHOT and the "
                    "ACTION, not the item — a fast sequence of different angles/uses of the SAME product.")
        check_seq = "on the SAME featured product with varied shots"
    elif featured:
        # A picked product/offering with NO dedicated photo (a course, a service): the MESSAGE
        # stays locked on it, but the visuals move through DIFFERENT real brand photos — the
        # owner's NTI reel (2026-07-12) seeded one generic 'about' photo into all 6 scenes
        # because the old rule forced index 0 even when photo 0 was not the product.
        featured_line = (
            f"FEATURED OFFERING: {featured}. This reel advertises ONLY this offering — every line "
            "of copy is about it. No dedicated product photo exists, so ground each scene in a "
            "DIFFERENT attached real photo whose content fits that scene's message.\n\n")
        image_rule = ("- image_index: the real photo whose content fits THIS scene — use a "
                      "DIFFERENT photo per scene when several exist; never repeat one photo for "
                      "every scene.\n")
        seq_rule = ("Keep every scene's MESSAGE locked on the featured offering while the visuals "
                    "move through the brand's real photos — a varied, alive sequence, never one "
                    "static image repeated.")
        check_seq = "message-locked on the featured offering across varied real photos"
    else:                            # whole-brand: a distinct product per scene
        featured_line = ""
        image_rule = ("- image_index: which REAL photo to bring to life — a different SHOT/angle of the "
                      "ONE hero when more than one photo exists, not an unrelated product.\n")
        seq_rule = ("Keep the ONE hero product present in most scenes; use a DISTINCT photo per scene "
                    "only when it shows that hero from another angle or in-use — a fast SEQUENCE of hero "
                    "shots, never a montage of unrelated products.")
        check_seq = "across hero-anchored shots"
    framing = (
        "FRAMING: you compose for a 1080x1920 VERTICAL (9:16) frame. Compose vertical-first — the "
        "product FULLY visible with headroom and safe margins, centered or in the lower two-thirds "
        "with negative space above for motion/text. On the establishing and CTA scenes keep the "
        "product WHOLE with safe margins — never let a wide/landscape framing ACCIDENTALLY crop the "
        "hero. A DELIBERATE macro crop on a detail shot (nozzle, label, texture) is welcome for the "
        "hook; that crop is intentional, not an accident.\n\n")
    from poster.contracts import CRAFT_CONTRACT
    from reel.reel_contracts import REEL_CRAFT_CONTRACTS
    return (
        # CRAFT BAR, not a persona primer (Batch 4): a fixed "15-year TikTok director" homogenized
        # every brand to hype pacing — the register is now DERIVED FROM THE BRAND. The spine below is
        # unchanged; only the opener changed.
        CRAFT_CONTRACT + "\n\n"
        # The reel craft contracts — the 8 hard rules a creative director enforces (product visible,
        # arresting muted hook, one CTA, one voice/dialect, sound arc, caption strategy, brand-specific,
        # feasible). The coherence gate re-checks each in code.
        + REEL_CRAFT_CONTRACTS + "\n\n"
        + register_note
        + trend_line
        + "Design a vertical 9:16 ad reel that turns THIS brand's REAL features into short, impactful "
        "stories that SELL, understanding the audience's consumer psychology. The reel's ENERGY and "
        "REGISTER must MATCH THIS BRAND — a luxury house is unhurried and reverent; a youth brand is "
        "fast and playful; a clinic is calm and credible. Do NOT default every brand to high-energy "
        "TikTok pacing. Be OBSESSED with realism: reject visual hallucination and physically-"
        "impossible actions.\n\n"
        + featured_line
        # OWNER RULING (2026-07-20): the plans were "correct" but had no point of view — scenes
        # COLLECTED, not directed. The owner also ruled that TASTE has no ground truth and cannot be
        # tested, while STRATEGY can: so the fix is three STRUCTURAL commitments made BEFORE any
        # scene exists, each of which the coherence gate re-checks in code (POV present + specific,
        # arc >= 3 beats walked in order by the scenes, motif actually recurring in >= 2 prompts).
        + "CREATIVE STRATEGY — decide this BEFORE you write a single scene. A director makes ONE "
        "creative decision and every shot serves it; a shot-collector writes correct scenes that add "
        "up to nothing. Commit to all three:\n"
        "- creative_pov: the ONE feeling a viewer leaves with, in a specific sentence — a moment, "
        "not an adjective. \"the quiet relief of one bill replacing a drawer of invoices\" is a point "
        "of view; \"high quality\", \"premium experience\", \"جودة عالية\" are what EVERY brand says "
        "and will be REJECTED.\n"
        "- narrative_arc: 3-5 ordered beats — the STORY that carries the feeling, which the POV alone "
        "cannot say. Use a pattern that fits this brand's truth (problem -> discovery -> "
        "transformation; expectation -> surprise -> proof; ritual -> reveal -> belonging) or name your "
        "own beats. Then EVERY scene declares which beat it serves in its arc_beat field, the scenes "
        "walk the beats IN ORDER (two scenes may share one beat), scene 1 opens on the FIRST beat and "
        "the final scene lands on the LAST.\n"
        "- visual_motif: one recurring visual thread (a colour, an object, a gesture, a light "
        "quality) that WRITE IT INTO the veo_prompt of at least 2 scenes — a motif that exists only "
        "in this field is not a motif. Derive it from the brand's OWN evidence: its real photos, "
        "palette and world.\n"
        "The AD SPINE below is WHAT each scene must communicate; the strategy above is HOW the story "
        "moves and feels. Every scene serves both at once.\n\n"
        + "You are given a business's identity and its REAL photos (you can see them). Design a "
        f"{n_scenes}-scene reel that ADVERTISES this brand with the proven AD SPINE — the scenes MUST "
        "follow it in order:\n"
        "(1) HOOK — scene 0, the FIRST 2 SECONDS: open on FAST motion built on a proven hook TYPE (the "
        "common mistake, 3 reasons, a bold before/after, stop-doing-X, or a provocative question), the "
        "product ON SCREEN within 2s, a person's FACE reacting, AND a punchy MACRO detail of the "
        "product's hero feature (label, cap, texture) — this DELIBERATE macro is the one place a tight "
        "crop belongs; it stops the scroll. The macro must come from a REAL PHOTOGRAPH of the brand's "
        "world; a hardware/repair close-up that misreads the business (a screwdriver on a circuit "
        "board for a TRAINING institute) is a failed hook.\n"
        "IMAGE BANS (absolute): NEVER select an image that is a text banner, a poster, a screenshot, "
        "a diagram, or a logo collage / partners wall — those are not footage; prefer the real "
        "photographs of the brand's people, space and work.\n"
        # OWNER-CAUGHT (2026-07-20, nanocare: 'صور ثابتة بتتغير' — the paid reel read as a
        # slideshow): the Veo-prompt tail already bans slow zooms, but the SCENE DESIGN itself
        # chose calm concepts ('mesmerizing macro of powder dissolving'), so calm went in and
        # calm came out. Energy is a DESIGN-time property — demand it here, at the source.
        "MOTION ENERGY (every scene, non-negotiable): every scene's visual concept must contain a "
        "VISIBLE PHYSICAL ACTION a viewer can SEE happening — a hand opening/pouring/applying, a "
        "person turning/reaching/reacting, an object mid-motion. NEVER an ambient still-life, "
        "NEVER a 'mesmerizing' static macro, NEVER scenery that could pass as a photograph. Test: "
        "pause the clip at ANY second — something must be caught mid-action. Brand register sets "
        "the TEMPO of that action (calm≠static: a luxury pour is slow but it still POURS).\n"
        "(2) WHAT IT IS — the STRANGER TEST: by scene 2 the VOICEOVER must make a first-time viewer "
        "understand WHO the brand is, WHAT IT DOES in plain words (the category — 'a tech-training "
        "institute', 'a hair-care brand'), AND the SPECIFIC named product OR service this reel is "
        "about. A reel that never says what the brand does, or never names the offering, has FAILED.\n"
        "(3) BENEFIT — the core reason to choose it, drawn ONLY from the identity (value props / "
        "offerings), shown CONCRETELY: a PRODUCT mid-use in a real routine (sprayed into hair, applied "
        "on skin), OR a SERVICE being delivered in its real setting (a learner coding in the lab, a "
        "graduate hired at work, a client being served) — never an abstract explainer, never a logo on "
        "a blank wall.\n"
        "(4) PROOF — if the identity has real reviews, testimonials, ratings or customer reactions, "
        "show that social proof (a satisfied real customer reacting); otherwise a grounded "
        "differentiator. NEVER fabricate ratings or numbers.\n"
        "(5) CTA — the LAST scene: a clean, HELD hero shot of the product where the on-screen CTA "
        "caption and the voiceover say the SAME short action line (an action + a reason to act now, "
        "e.g. 'Shop the mist today'). The reel AUTO-APPENDS a branded end-card with the real logo, so "
        "DESCRIBE no text, button, or logo in the footage — Veo renders none; the caption carries the "
        "CTA words.\n"
        "HARD RULE: by scene 2 a first-time viewer must know exactly WHAT BRAND, WHAT IT DOES, and WHAT "
        "PRODUCT OR SERVICE this "
        "advertises — if not, the reel has FAILED. Anchor the WHOLE reel on ONE concrete hero product/"
        "offering (the product in REAL PHOTO index 0 when one is featured); do NOT narrate store "
        "locations, kiosks, branches, or a vague brand montage. Keep ONE tone and look from the hook "
        "through the CTA — any visual disconnect makes the viewer leave. If the identity signals "
        "HERITAGE, weave that in — never invent history.\n\n"
        + framing
        + "MOTION & PEOPLE (this is a TikTok-style ad, not a slideshow): EVERY scene must have real,"
        " energetic MOTION and — wherever the product is used or worn — a real PERSON in frame using"
        " or reacting to it. NEVER a slow zoom or a static pan over a still. " + seq_rule + " (The "
        "final CTA scene is the ONE exception: a clean, held hero composition with only a subtle "
        "settle is fine there; every earlier scene must carry real energetic motion.)\n\n"
        "SCENE VARIETY (owner: 'the background must NOT stay behind me the whole reel'): EVERY scene is "
        "a DIFFERENT setting, angle and moment — a fast SEQUENCE of distinct shots. NEVER hold ONE "
        "backdrop behind the person for the whole reel. If only one real photo exists, still CHANGE the "
        "framing, distance, action and implied location each scene so each reads as a NEW shot, never a "
        "frozen wallpaper.\n\n"
        "ONE CONTINUOUS SCENARIO (owner 2026-07-12: 'الريل مش سيناريو' — a montage is a FAILED reel): "
        "the reel tells ONE story with the SAME protagonist(s) and the same coherent world from hook "
        "to CTA. Each veo_prompt CONTINUES the story — restate the recurring person by the same "
        "description ('the same woman in her 30s with curly hair, now ...') and keep wardrobe, "
        "lighting mood and world consistent. Distinct shots, one thread: hook (the pain) → the brand's "
        "answer → it working in real life → the transformation → CTA.\n\n"
        "SCREENS & MARKS (hard): any screen/monitor/phone display inside a scene shows ABSTRACT "
        "glowing graphics or blurred UI with NO readable text or numbers (video models render junk "
        "pseudo-text — the exact failure the poster gate rejects). EXCEPTION for a screen/app product: "
        "keep the generated screen abstract AND set that scene's product_overlay=\"real_screen\" so the "
        "REAL product screen is composited in post — that is how a software product is actually SHOWN "
        "(see [PRODUCT ON SCREEN]); never let 'abstract screen' become the whole ad. NEVER depict any "
        "company's logo or trademark — no third-party marks (swooshes, fruit logos, telecom brands) "
        "AND not the brand's own (the end-card composites the REAL logo); no branded badges on "
        "clothing or objects.\n\n"
        # AGENCY PROMPT FORMULA — the owner's spec for how each veo_prompt is engineered.
        "WRITE EACH veo_prompt WITH THE AGENCY FORMULA — SUBJECT + STYLE + CAMERA + LIGHTING + MOTION, "
        "concrete, never lazy:\n"
        # CASTING IS PART OF THE SUBJECT (D-8: derived from the brand's measured market, never
        # hardcoded). MEASURED (Abu Auf, 2026-07-25): the seeds carried brand_locale but the
        # veo_prompts the director wrote did not, and the video model animates PEOPLE from these
        # prompts — the owner's own words for the result: «هنود معرفش ليه». A person in an
        # Egyptian brand's ad is Egyptian; the primer below says so from evidence.
        + ("- CASTING (mandatory in every people-bearing prompt): "
           + locale_primer.strip() + "\n" if locale_primer.strip() else "")
        + "- SUBJECT: a SPECIFIC person + the exact product (say 'a woman in her 30s with natural curly "
        "hair', never just 'a person'); the product stays EXACTLY as in the real photo.\n"
        "- STYLE: pick and state ONE — realistic, cinematic, or hand-held UGC.\n"
        "- CAMERA: an EXPLICIT move — Macro close-up, tracking shot, hand-held, a quick dolly-in, or a "
        "whip-pan (this gives life, not a boring zoom).\n"
        "- LIGHTING: state it — soft natural light, soft diffused light, or high-contrast studio light.\n"
        "- MOTION: real action in every second; ONE CLEAR action per scene — do NOT cram many details "
        "into one scene.\n"
        "BANNED WORDS — never write 'slow zoom', 'refined', or 'dreamy'; use 'dynamic motion', 'clear "
        "focus', 'real-life usage', or 'hand-held' instead.\n\n"
        "For EACH scene:\n"
        + image_rule
        + motion + "\n"
        f"- voiceover: one short, natural narration line in {language} that carries the story and "
        "sells the moment. PACE BUDGET (hard rule — a rushed narration is unintelligible): the line "
        "must fit its scene at a natural speaking pace, AT MOST 2 words per second of duration_s "
        "(a 4s scene fits ~8 words) and NEVER more than 8 words total. Fewer words land harder "
        "(owner: 'الكلام أوفر') — a scene MAY be spoken-silent (voiceover: '') to let the footage "
        "breathe, but ONLY when its sfx/sound_mood clearly carries it (never a scene with no voice "
        "AND no sound — that is dead air). Keep ONE locked dialect across every line (see [ONE "
        "VOICE]). TTS-FRIENDLY: write numbers as words in the narration language "
        "(أربع شهور, not ٤ or 4), no abbreviations or Latin acronyms unless they are the brand's "
        "own name. WRITE FOR THE EAR, not the eye (owner: the flat read starts at the writing): "
        "direct address to the viewer, spoken rhythm, and PUNCTUATION AS PERFORMANCE — the hook "
        "and the CTA end with «!», a rhetorical question ends with «؟». The TTS voice reads "
        "these marks; a line closed with a flat period is read flat.\n"
        "- voiceover_delivery: the EMOTION/performance in a few words "
        f"(e.g. {deliveries}). REAL, warm, HUMAN feeling — but MEASURED: never flat/robotic and never "
        "melodramatic or over-acted (owner: 'no over-the-top emotion'). Vary it across scenes.\n"
        "- on_screen_text: the reel is CAPTION-DRIVEN (most people watch MUTED), so MOST scenes carry "
        "a SHORT kinetic caption — each at most 4-5 words, ONE idea, verbatim/grounded, never a "
        "sentence, and it must stay on screen LONG ENOUGH TO READ. Scene 0 names the brand + what it "
        "is (a MUTED viewer instantly knows what this advertises); the BENEFIT and PROOF scenes carry "
        "a 2-4 word key-benefit / proof word (e.g. '85% get hired', 'Job-ready in 9 months'); the "
        "LAST scene a 2-4 word CTA that reads the SAME as its spoken voiceover CTA line. A purely "
        "visual scene MAY leave on_screen_text empty, but do NOT leave the reel mostly text-free.\n"
        "- duration_s: 2-4 — a FAST TikTok cut for a caption-free scene; a CAPTIONED scene needs >=3s "
        "(>= words*0.4 + 1.5) so it can be READ (owner: 'the text goes by too fast'). If a caption "
        "would need more than ~7 words, SPLIT it into two scenes rather than speeding it up.\n"
        # hero_product IS THE MOTION SWITCH (v3): true -> the brand's REAL product references are
        # attached to that scene's still AND the motion contract pins the pack while the camera
        # moves; false -> no product refs and a hard no-readable-text ban. So the flag must mark
        # EVERY scene the product is physically in — "at least one" left the v2 reel with
        # invented props in most scenes («فين منتجات أبو عوف»).
        "- hero_product: TRUE for EVERY scene where the real product/service is physically in "
        "frame (held, opened, poured, on the table, in the bag) — not just the one hero shot. "
        "FALSE only for scenes with NO product in frame at all (a mood/pain/place shot). A false "
        "scene must contain no product, no packaging and no readable text of any kind.\n"
        "- product_overlay: \"real_screen\" when the product is a screen/app and its real screen "
        "should be composited in post; else \"\".\n"
        "- caption_role: for a captioned scene, one of name | benefit | proof | cta | layer (scene 0 "
        "= name, last scene's CTA caption = cta).\n"
        "- sfx: the sound this beat actually MAKES, written in ENGLISH as a short description a "
        "sound-effects model can render — describe the real object and action in THIS scene, close "
        "mic, no music (e.g. 'crisp bite into a chocolate bar with crunchy nuts', 'plastic pharmacy "
        "cap clicking open', 'fabric sliding off a rail'). English because it is a parameter for an "
        "audio model, not audience-facing copy — never translate it. On a pure transition beat the "
        "bare words whoosh (hard motion) | impact (reveal) | chime (pay-off) are still valid. Use "
        "\"\" when the beat is genuinely silent.\n"
        "- sound_mood: this scene's mood in the arc (e.g. 'tense', 'warm build', 'confident').\n\n"
        "DISCIPLINE: be creative and persuasive, but invent NO facts — no fake awards, ratings, "
        "numbers, or certifications. Every factual claim must come from the identity provided. The CTA "
        "line is COPY you WRITE (an action + a reason to act now); only its factual claims (offer, "
        "price, benefit) must be grounded — use the brand's real tagline if it has one, else write a "
        "short on-brand CTA without inventing fake scarcity. "
        "The reel must feel polished, human, and authentic to THIS brand's vertical.\n"
        # LOGIC CHECK — the owner's self-validation step against impossible physics.
        "SELF-CHECK (LOGIC CHECK) before returning — silently verify, then FIX: (a) is EVERY scene "
        "PHYSICALLY POSSIBLE in reality? no impossible action (a sealed pump cannot be pressed, a "
        "closed bottle cannot pour, no magic); (b) the STRANGER TEST — the reel NAMES the brand, says "
        "WHAT IT DOES (the category), NAMES the specific product OR service, states >=1 real benefit, "
        "and ends on the CTA — using ONLY identity facts; AND every scene is a DIFFERENT setting (no "
        "single backdrop held the whole reel); (c) most "
        "scenes show a real PERSON using/reacting to the product with visible motion (not a camera "
        "move over a still) " + check_seq + ". If ANY of these fails, REWRITE that scene before "
        "returning.\n\n"
        + compliance_line +
        "Return ONLY a JSON object, no prose, no markdown fences:\n"
        '{"concept":"...","hook":"...","music_mood":"...","cta":"...","language":"' + language + '",'
        '"dialect":"...","sound_design":"...","brand_unique_claim":"...",'
        '"scenes":[{"image_index":0,"veo_prompt":"...","voiceover":"...","voiceover_delivery":"...",'
        '"on_screen_text":"...","duration_s":3,"hero_product":true,"product_overlay":"",'
        '"caption_role":"name","sfx":"","sound_mood":"..."}]}'
    )


def design_creative_reel(
    profile: dict,
    image_urls: list[str],
    *,
    n_scenes: int = 6,
    language: Optional[str] = None,
    featured_product: Optional[str] = None,
    caller=None,
    user_note: Optional[str] = None,
    photo_subjects: Optional[list[str]] = None,
    fix_note: Optional[str] = None,
    trend_context: Optional[str] = None,
) -> Optional[CreativeReel]:
    """Gemini 2.5 Pro designs the full creative reel from the identity + real photos (it SEES the
    photos — natively multimodal). When `featured_product` is set, the WHOLE reel is about that ONE
    product (several shots of the same item). Returns None on any failure (no caller/creds/images).
    `caller` is injectable for tests; it defaults to default_caller(strong=True).
    `photo_subjects` (aligned to the attached photos) tells the director what each photo depicts so
    it can pick the product shot for the hero moment. `fix_note` appends explicit corrections for a
    coherence-gate retry (the [BRACKET] contracts still apply)."""
    urls = [u for u in (image_urls or []) if isinstance(u, str) and u.startswith(("http://", "https://"))]
    # ZERO PHOTOS NO LONGER ABORTS. This used to `return None`, and the caller's response was a
    # silent fall to the template storyboard — which is exactly how the Abu Auf reel shipped with
    # no story and no products after the photo gate rejected 1/1 images. With no photos the
    # director designs from the identity + brand DNA (the seeds are generated stills anyway, and
    # the ungated reference pool supplies real product identity downstream).
    no_photos = not urls
    if caller is None:
        from business_profile.llm import default_caller
        caller = default_caller(strong=True)          # Gemini 2.5 Pro (the complex vision/copy step)
    if caller is None:
        logger.info("creative_director: no Gemini caller (creds/SDK missing); skipping")
        return None

    # FIX-D4: the DOMINANT site language, not languages[0] — that list is often sorted
    # alphabetically, so 'en' beat 'ar' and an Egyptian brand got ENGLISH captions.
    langs = profile.get("languages") or []

    def _lang_key(entry) -> tuple[str, float]:
        if isinstance(entry, dict):
            return str(entry.get("code") or ""), float(entry.get("proportion") or 0.0)
        code = str(getattr(entry, "code", None) or entry or "")
        return code, float(getattr(entry, "proportion", 0.0) or 0.0)

    dominant = ""
    if langs:
        pairs = [_lang_key(x) for x in langs]
        pairs = [(c, p) for c, p in pairs if c]
        if pairs:
            dominant = max(pairs, key=lambda cp: cp[1])[0] if any(p > 0 for _, p in pairs) \
                else pairs[0][0]
    # THE AUDIENCE, NOT THE CRAWL (owner, 2026-08-01: «صيدلية في مصر وعندها عربي — عملت
    # إنجليزي ليه؟»). `dominant` is the language of the pages we happened to FETCH, so crawling a
    # bilingual store's English edition makes every ad English — the system inferring a fact from
    # evidence it produced itself. `reel/audience.py` ranks what the SITE declares (hreflang) and
    # where its customers ARE above that. An explicit `language=` still wins: a caller asking for
    # a specific cut is not guessing.
    _aud = ""
    try:
        from reel.audience import audience_language_code
        _aud = audience_language_code(profile)
    except Exception:  # noqa: BLE001
        _aud = ""
    lang = language or _aud or dominant or "en"

    parts, used = _image_parts(urls, max_images=n_scenes + 4) if urls else ([], [])
    if not parts and not no_photos:
        # Photos existed but none could be fetched — same design decision as zero photos.
        logger.warning("creative_director: could not fetch any real photo; designing from identity")
        no_photos = True

    feat = (f"\n\nFEATURED PRODUCT (advertise ONLY this): {featured_product}. Every scene is the SAME "
            "product — vary the shot/action, not the item." if featured_product else "")
    note_line = ""
    if user_note and str(user_note).strip():
        # HITL refine loop: the studio user's change request steers the NEXT plan; grounding
        # rules in the system prompt still apply unchanged.
        note_line = ("\n\nUSER CHANGE REQUEST (must be honored): "
                     + str(user_note).strip())
    # What each attached photo depicts, so the director picks the product shot for the hero moment
    # (the labels are computed by image_select but were otherwise discarded).
    subj_line = ""
    if photo_subjects:
        subj_line = "\n\nATTACHED PHOTOS (what each shows):\n" + "\n".join(
            f"- photo {i} shows: {str(s).strip()}" for i, s in enumerate(photo_subjects[:len(used)])
            if str(s or "").strip())
    fix_block = ""
    if fix_note and str(fix_note).strip():
        fix_block = ("\n\nCORRECTIONS REQUIRED — fix ALL of these from your previous attempt, "
                     "introduce no new violations:\n" + str(fix_note).strip())
    if no_photos:
        photos_line = ("\n\nNO REAL PHOTOS ARE ATTACHED. Design every scene from the business "
                       "identity and brand DNA above; set image_index to 0 in every scene. The "
                       "scenes will be rendered as generated stills conditioned on the brand's "
                       "real assets downstream — describe each scene's product and world in "
                       "concrete, brand-specific detail so that conditioning has something exact "
                       "to hold onto.")
    else:
        photos_line = (f"\n\n{len(used)} real photos are attached in index order "
                       f"0..{len(used) - 1}. Design the reel.")
    user = ("BUSINESS IDENTITY:\n" + _identity_block(profile) + feat + subj_line + photos_line)
    user = user + note_line + fix_block

    register = _locked_register(lang, profile)
    try:
        from poster.contracts import compliance_for
        resp, usage = caller(
            _system_prompt(n_scenes, lang, _vertical_mode(profile), featured=featured_product,
                           compliance=compliance_for(_v(profile, "category")),
                           product_photo_anchored=bool(featured_product) and len(used) == 1,
                           register=register, locale_primer=_locale_primer(profile),
                           trend_context=trend_context or ""),
            user, _ReelResponse, group_name="creative_director", images=parts,
        )
    except Exception as e:  # noqa: BLE001 — a call failure must never break the reel pipeline
        logger.warning("creative_director: Gemini call failed: %s", e)
        return None

    scenes: list[CreativeScene] = []
    for s in (getattr(resp, "scenes", None) or []):
        try:
            idx = int(getattr(s, "image_index", 0))
        except (TypeError, ValueError):
            idx = 0
        idx = max(0, min(idx, len(used) - 1))
        prompt = str(getattr(s, "veo_prompt", "")).strip()
        if not prompt:
            continue
        scenes.append(CreativeScene(
            image_index=idx, veo_prompt=prompt,
            voiceover=str(getattr(s, "voiceover", "")).strip(),
            voiceover_delivery=str(getattr(s, "voiceover_delivery", "")).strip(),
            on_screen_text=str(getattr(s, "on_screen_text", "")).strip(),
            duration_s=float(getattr(s, "duration_s", 4.0) or 4.0),
            hero_product=bool(getattr(s, "hero_product", False)),
            product_overlay=str(getattr(s, "product_overlay", "")).strip(),
            caption_role=str(getattr(s, "caption_role", "")).strip(),
            sfx=str(getattr(s, "sfx", "")).strip(),
            sound_mood=str(getattr(s, "sound_mood", "")).strip(),
            arc_beat=str(getattr(s, "arc_beat", "")).strip(),
        ))
    if not scenes:
        return None
    return CreativeReel(
        concept=str(getattr(resp, "concept", "")).strip(),
        hook=str(getattr(resp, "hook", "")).strip(),
        music_mood=str(getattr(resp, "music_mood", "")).strip(),
        cta=str(getattr(resp, "cta", "")).strip(),
        language=lang, images=used, scenes=scenes,
        dialect=register or str(getattr(resp, "dialect", "")).strip(),   # code-locked register wins
        sound_design=str(getattr(resp, "sound_design", "")).strip(),
        brand_unique_claim=str(getattr(resp, "brand_unique_claim", "")).strip(),
        creative_pov=str(getattr(resp, "creative_pov", "")).strip(),
        narrative_arc=[str(b).strip() for b in (getattr(resp, "narrative_arc", None) or [])
                       if str(b).strip()],
        visual_motif=str(getattr(resp, "visual_motif", "")).strip(),
        model=getattr(usage, "model", "") or _DEFAULT_MODEL,
    )


def _safe_json_object(raw: str):
    if not raw:
        return None
    s = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start:end + 1]
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None
