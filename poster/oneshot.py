"""ONE-SHOT designed poster — PILOT (step ب of the TrendPulse `media/` review, 2026-07-03).

TrendPulse's posters look "designed" because a Gemini IMAGE model composes the WHOLE creative —
layout + typography + graphics — in one shot, instead of our photo + HTML text overlay. Our
overlay architecture exists because Imagen garbles Arabic; the nano-banana-class Gemini image
models render text far better — but "better" is a claim to MEASURE, not assume.

This module is now the poster's PRIMARY engine + its FIDELITY GATE — the measured pass earned it
the default `engine="oneshot"` (poster/schemas.py); the classic bg + HTML-overlay path remains a
fallback (`engine="classic"`). `benchmark/measure_oneshot_poster.py` measured its Arabic/English
verbatim-text fidelity (rule 2) before it shipped as the default.

GROUNDING: the text the model is asked to render is OUR already-Ledger-gated copy, passed
VERBATIM. The new risk is the model ALTERING/garbling that copy inside the image — so every
render is verified: a vision model reads the rendered image back and `text_fidelity` compares
what was rendered against what was requested (Arabic-aware normalization). A render that fails
the gate must never ship.

Vertex notes (probed live 2026-07-03 on project image-498715): `gemini-3-pro-image-preview` and
`gemini-3.1-flash-image-preview` resolve ONLY on location="global"; `gemini-2.5-flash-image`
also works on us-central1.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

DEFAULT_ONESHOT_MODEL = os.getenv("ONESHOT_IMAGE_MODEL", "gemini-3.1-flash-image-preview")
DEFAULT_LOCATION = "global"

# tashkeel (064B-065F) + superscript alef (0670) + tatweel (0640) + Quranic marks (06D6-06ED)
_TASHKEEL_RE = re.compile("[ً-ٰٟـۖ-ۭ]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _norm_text(s: str) -> str:
    """Arabic-aware normalization for verbatim comparison: strip tashkeel/tatweel, unify
    alef/ya/ta-marbuta glyph variants (a transcriber/renderer legitimately writes ة as ه),
    drop punctuation, collapse whitespace, casefold. Rendering that survives THIS normalization
    unchanged is 'verbatim' for the gate; any dropped/added/altered WORD is not."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = _TASHKEEL_RE.sub("", s)
    s = (s.replace("أ", "ا").replace("إ", "ا")   # أ / إ -> ا
         .replace("آ", "ا").replace("ٱ", "ا")    # آ / ٱ -> ا
         .replace("ى", "ي").replace("ئ", "ي")    # ى / ئ -> ي
         .replace("ؤ", "و")                                 # ؤ -> و
         .replace("ة", "ه"))                                # ة -> ه
    s = _PUNCT_RE.sub(" ", s)
    return " ".join(s.casefold().split())


def _identity_rules(n_identity_refs: int) -> str:
    """The attached face is a PERSON to keep, not merchandise to composite.

    Without this the very next block tells the model every attachment is a "REAL PRODUCT PROP" with
    the first one as the hero product — so the brand's presenter would be placed on a counter like
    packaging. The lock wording follows `reel/render3/prompts.character_block`, which is the phrasing
    measured to hold an identity across renders in this repo."""
    if not n_identity_refs:
        return ""
    return (
        "IDENTITY REFERENCE: attached image 1 is the brand's PRESENTER — a real person, NOT a "
        "product and NOT a prop. Render the SAME person in this scene: face, hair and build must "
        "match the reference exactly. Do not change their face, their hair, or their identity in "
        "any way, and do not place any label, packaging or text on them. They are a human being in "
        "the scene, lit and posed naturally for the composition."
    )


def _props_rules(n_products: int, n_places: int, product_placement: str = "") -> str:
    """The REAL-asset rules for the scene: the brand's own product photos as props, its own
    premises as the place, and — the 2026-07-20 owner fix — the PHYSICS of where the product sits.

    Owner: "البوستر بيحط علبة الدوا في أي مكان وبأي شكل مش مهتم هو ده مكان منطقي ولا لأ." The old
    text said only "place them naturally", which is not a rule, so a floating/mis-scaled/shadowless
    box passed. The placement rules below are phrased for ANY category (owner's D-8 correction: a
    box rests on a counter, but shoes sit on the ground, a car on the road, a sofa in a room — the
    universal is a NATURAL SUPPORT that suits the product's own kind, not "a surface").
    """
    parts: list[str] = []
    if n_products:
        # The owner's radical fix: real assets composite correctly (proven by the
        # logo) — so the products in the scene are the brand's REAL product PHOTOS,
        # with their REAL labels, not model-invented packaging.
        parts.append(
            f"REAL PRODUCT PROPS: the {n_products} image(s) attached AFTER the logo are "
            "the brand's REAL products, photographed from its own store. "
            # البند 3: the FIRST prop is THE featured product (the user picked it) — the model
            # used to be free to feature "one or two", so a secondary photo became the hero
            # and the poster showed the WRONG product.
            "The FIRST attached product is THE featured product — it must be the visible "
            "hero prop in the scene; any additional attached product is background support "
            "only. Place them naturally as physical props — packaging shape, "
            "label text, colors and proportions EXACTLY as photographed; do NOT redraw, "
            "restyle, relabel, translate or 'improve' them. NO other products, boxes or "
            "packaging may appear in the scene."
        )
        parts.append(
            "PRODUCT PLACEMENT (physics — the product must belong in the scene, not be pasted "
            "onto it):\n"
            "1. NATURAL SUPPORT: the product rests on whatever genuinely carries a product OF ITS "
            "KIND — a jar or box on a counter or shelf, footwear on the ground or a step, a "
            "vehicle on a road, furniture standing in a room, a garment worn or hung, a building "
            "on its own site, a handheld item in a hand mid-use. It NEVER floats, hovers, tilts "
            "unsupported, or sits on nothing.\n"
            "2. REALISTIC SCALE: sized correctly against the hand, furniture, person or objects "
            "beside it — a product larger or smaller than its real self ruins the shot.\n"
            "3. LIT BY THE SCENE: the SAME light direction, colour and softness as everything "
            "around it, with a contact shadow where it meets its support and any reflection the "
            "surface would truly produce.\n"
            "4. A PLACE WITH MEANING: put it where this kind of product is actually USED, kept or "
            "displayed in real life — not in a decorative void and not somewhere absurd for its "
            "category.\n"
            "5. LABEL TO CAMERA: angled so its real label reads clearly and undistorted; never "
            "turned away, warped, mirrored or cropped past legibility."
        )
        if (product_placement or "").strip():
            # The concept already decided the spot for THIS scene (poster/concept._placement_rule).
            # Quote it verbatim so the render obeys one considered decision instead of improvising
            # a new one per attempt.
            parts.append(
                "THE DECIDED PLACEMENT for this scene (follow it exactly, within the physics "
                f"above): {product_placement.strip()}"
            )
    if n_places:
        # FIX-C2: SERVICE brands (education/clinic/government/...) get their REAL premises
        # attached as the scene's PLACE instead of desk props — the anchor that stops the
        # render collapsing into interchangeable stock offices (measured on nti: real campus
        # photos existed and never reached generation).
        parts.append(
            f"REAL PLACE: the {n_places} image(s) attached AFTER the logo are this brand's "
            "REAL premises/environment, photographed on site. Set THE SCENE inside this real "
            "place — match its architecture, interiors, equipment, furniture and lighting so "
            "the poster unmistakably happens THERE. Do NOT invent a different location, and do "
            "NOT paste the photo as-is — compose a fresh scene that clearly lives in it."
        )
    return "\n".join(parts)


def build_oneshot_prompt(copy: dict, *, brand_name: str = "", palette_names: str = "",
                         dna_lines: str = "", rtl: bool = False, has_logo: bool = False,
                         heading_font: str = "", body_font: str = "",
                         single_message: str = "", visual_idea: str = "",
                         product_placement: str = "",
                         n_products: int = 0, n_places: int = 0,
                         locale_line: str = "", category: str = "",
                         n_identity_refs: int = 0) -> str:
    """The one-shot design brief. The copy fields are OUR gated text and must be rendered
    EXACTLY — and they are the ONLY drawable words, fenced between BEGIN TEXT / END TEXT.

    OWNER-CAUGHT (2026-07-19, "في شغل في البرومبت"): the image prompt used to embed the
    VERBATIM_RENDER_CONTRACT paragraph + quoted field labels — meta-instructions an image model
    cannot tell apart from copy, so contract words got PAINTED into posters. The contract now
    lives ONLY in the OCR read-back path; here the fence carries nothing but the copy strings,
    and role/styling hints stay outside it, unquoted."""
    headline = (copy.get("headline") or "").strip()
    sub = (copy.get("subheadline") or "").strip()
    cta = (copy.get("cta") or "").strip()
    parts = [
        "Design a COMPLETE social-media advertising poster (portrait 4:5, 1080x1350):",
        "an ad this brand would actually ship — intentional layout, strong typographic",
        "hierarchy, a designed background scene, generous negative space. NOT a photo with",
        "text pasted on top.",
    ]
    if brand_name:
        parts.append(f"Brand: {brand_name}.")
    # ONE IDEA, not a collage (measured: without a concept the model crammed every
    # delivery trope — drone + robot + couch + floating products — into one frame).
    if single_message:
        parts.append("THE CONCEPT (visual direction — never words to draw): the poster expresses "
                     f"exactly THIS ONE idea: {single_message}.")
    if visual_idea:
        parts.append(f"THE SCENE (visual direction — never words to draw): {visual_idea}. "
                     "Execute THIS scene only.")
    parts.append(
        "ART DIRECTION (mandatory): ONE clear focal subject; at most 2-3 supporting "
        "elements; generous calm negative space — 'less is more', never a cluttered "
        "collage. Choose a clear layout: the text lives in ONE calm, low-detail zone "
        "(e.g. the top or bottom third) and the subject in another. TEXT CONTRAST is "
        "non-negotiable: the copy must be instantly readable at a glance — light text "
        "on a dark calm area or dark text on a light calm area; if needed, design a "
        "soft panel or gradient behind the text; NEVER place text over busy detail."
    )
    identity_rules = _identity_rules(n_identity_refs)
    if identity_rules:
        # BEFORE the props block, matching the attachment order: image 1 is the person.
        parts.append(identity_rules)
    props_rules = _props_rules(n_products, n_places, product_placement)
    if props_rules:
        parts.append(props_rules)
    if locale_line:
        # FIX-C3: evidence-derived country/culture primer (poster/locale.brand_locale) — the
        # anchor that stops generic-Western casting for a brand with a known market.
        parts.append(locale_line)
    parts.append(
        "BRAND INTEGRITY (mandatory): do NOT draw the brand's logo or name yourself — the real "
        "logo asset is composited onto the poster afterward"
        + (". The brand name may appear on the attached real products exactly as photographed"
           if n_products else "")
        + ". NEVER paint or print the brand name onto other objects, devices, robots, "
        "vehicles, bags, screens or signage — do NOT invent branded objects or services "
        "the brand may not have."
        + ("" if n_products else
           # OWNER-CAUGHT HALLUCINATION (topshoes, 2026-07-12): with no real product photo the
           # old wording LICENSED an invented 'generic unlabeled product' — the model painted a
           # hero sneaker the brand never made. Showing a fabricated product as the brand's
           # merchandise is a FALSE PRODUCT CLAIM, not design freedom.
           " NO real product photo is attached, so do NOT invent a hero product presented as "
           "the brand's merchandise (no fabricated shoe/bottle/device/dish 'from' this brand). "
           "Compose with the brand's real WORLD instead — hands at work, environment, texture, "
           "abstract shapes of the craft; incidental background objects must stay generic, "
           "out of focus and COMPLETELY UNLABELED (no text, no pseudo-text, no gibberish).")
        + " NEVER render any third-party logo or trademark — no swooshes, apple marks, "
        "telecom or fashion brand symbols on any object or clothing."
        + " Every object in the scene must belong to this business's real world — nothing "
        "that would look out of place in its actual category."
    )
    if palette_names:
        # A weak one-line palette hint was IGNORED by the model (measured: daturial's
        # scraped orange arrived in the brief and the render came out navy/teal). The
        # palette is a mandate with explicit roles, by NAME (raw hex in an image
        # prompt gets baked into the artwork as text — documented lesson).
        parts.append(
            f"COLOR SYSTEM — MANDATORY: build the entire design on the brand palette: "
            f"{palette_names}. The FIRST color is the brand's PRIMARY and must DOMINATE "
            "the poster as a LARGE color field — a background zone, a major panel, or the "
            "hero graphic area — so the design reads as THIS brand's color at a glance; it "
            "is NOT enough to tint only the small CTA button. Use the remaining palette "
            "colors for secondary accents. Neutrals (black/white/grey) may support but "
            "never replace or out-weigh the brand colors — do NOT let a neutral, nor a "
            "generic default blue/navy, become the dominant color. Do NOT invent a "
            "different color scheme."
        )
    if dna_lines:
        parts.append(f"The brand's own visual language (match it): {dna_lines}")
    if heading_font or body_font:
        # The brand's REAL website typography — steer the poster's type character toward
        # it (identity signal that was captured but never reached any generation call).
        fonts = " / ".join(f for f in (heading_font, body_font) if f)
        parts.append(
            f"Brand typography: the website uses '{fonts}' — design the poster's type "
            "in the same typographic character (weight, feel), so it reads as this brand."
        )
    # CS-02 residual closed: the ad-safety compliance line (one source: poster/contracts) now
    # reaches the ONESHOT engine too — a styling directive, outside the drawable-text fence.
    from poster.contracts import compliance_for
    _compliance = compliance_for(category)
    if _compliance:
        parts.append(_compliance)
    # Role/typography hints live OUTSIDE the fence and UNQUOTED — quoted labels used to get
    # painted into the artwork. The order maps 1:1 onto the quoted lines below.
    roles = []
    if headline:
        roles.append("the 1st quoted line is the headline — dominant hero typography")
    if sub:
        roles.append("the 2nd quoted line is the subheadline — smaller, supporting")
    if cta:
        roles.append(f"the {'3rd' if headline and sub else 'last'} quoted line is the "
                     "call-to-action — set on a pill button")
    if roles:
        parts.append("TYPOGRAPHY ROLES (styling only): " + "; ".join(roles) + ".")
    parts.append("TEXT TO RENDER — copy each quoted line below EXACTLY as written, letter for "
                 "letter, no word added, removed, translated or 'improved':")
    parts.append("BEGIN TEXT")
    for line in (headline, sub, cta):
        if line:
            parts.append(f'"{line}"')
    parts.append("END TEXT")
    if rtl:
        parts.append(
            "The text is ARABIC: right-to-left, correctly SHAPED AND CONNECTED letters "
            "(never isolated/disconnected glyphs, never mirrored), professional modern Arabic "
            "typography."
        )
    if has_logo:
        # The image model re-draws an attached logo (garbling Arabic script — measured on Azza
        # Fahmy: "عزة فهمي" came out "ةفهمص"). So we do NOT ask it to render the logo at all; the
        # REAL logo asset is composited in deterministically after. FIX-C1 (2026-07-11): the old
        # wording ("...the brand's real logo is placed into that space afterward") made the model
        # paint a literal PLACEHOLDER — a hard white rectangle — into the corner (measured on nti:
        # a white panel from the canvas edge, narrower than the composited logo). The corner is now
        # described as CONTINUOUS background and the placeholder failure mode is explicitly banned;
        # a deterministic corner gate in the pipeline retries any render that still paints a slot.
        corner = "top-left" if not rtl else "top-right"
        parts.append(
            f"Keep the {corner} corner as a plain, low-detail CONTINUATION of the same background "
            f"artwork — no logo, no brand mark, no text, no busy detail there. Do NOT paint any "
            f"placeholder in that corner: no white or blank box, no panel, rectangle, frame, badge "
            f"or color patch that differs from the surrounding background — not even a "
            f"SLIGHTLY lighter or darker zone; that corner must be IDENTICAL in color and "
            f"tone to the artwork around it. "
            f"Do NOT draw the brand's logo or wordmark anywhere in the image."
        )
    parts.append(
        "ABSOLUTELY NO other text, words, letters, numbers, watermarks or invented labels "
        "anywhere in the image — the ONLY renderable text is the quoted lines between "
        "BEGIN TEXT and END TEXT above. Nothing else in this brief is text to draw."
    )
    return "\n".join(parts)


_CLIENTS: dict[str, Any] = {}   # retained — a temporary client gets GC'd mid-request (measured)


def _client(location: str):
    if location not in _CLIENTS:
        from google import genai
        _CLIENTS[location] = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=location,
        )
    return _CLIENTS[location]


def _is_retryable(e: Exception) -> bool:
    """Transient Google API failures worth waiting out: 429 quota bursts (Dynamic Shared
    Quota — a concurrent Veo render on the same project starves image calls) and 5xx."""
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    s = str(e)
    return code in (429, 500, 503) or "RESOURCE_EXHAUSTED" in s or "UNAVAILABLE" in s


# Image models retire underneath us. MEASURED 2026-07-21 on project radiant-octane-501919-v1:
# every Imagen id AND `gemini-3-pro-image-preview` (what .env pins) return 404 NOT_FOUND, while
# `gemini-2.5-flash-image` (nano banana) serves normally — so a client's poster died on a retired
# model id. The configured model is always tried FIRST; a 404 moves to the next one.
_MODEL_CHAIN = ("gemini-2.5-flash-image", "gemini-3.1-flash-image-preview",
                "gemini-2.5-flash-image-preview")


def _is_missing_model(e: Exception) -> bool:
    """A retired/unavailable model — retrying it can only waste time; try the next instead."""
    code = getattr(e, "code", None) or getattr(getattr(e, "response", None), "status_code", None)
    s = str(e)
    return code == 404 or "404" in s and "NOT_FOUND" in s


def generate_oneshot_poster(prompt: str, out_path: str | Path, *,
                            model: str = DEFAULT_ONESHOT_MODEL,
                            location: str = DEFAULT_LOCATION,
                            logo_bytes: Optional[bytes] = None,
                            logo_mime: str = "image/png",
                            product_images: Optional[list[tuple[bytes, str]]] = None,
                            identity_ref: Optional[tuple[bytes, str]] = None,
                            client: Any = None) -> Path:
    """ONE generate_content call -> the complete designed poster PNG. The real brand logo is
    passed as an INPUT IMAGE part when available (these models composite an attached image;
    a text description could never reproduce the real mark), and `product_images` — the
    brand's REAL product photos — follow it as scene props with their REAL labels. Raises on
    failure — the caller decides the fallback; nothing here ships unmeasured."""
    from google.genai import types

    cl = client or _client(location)
    # SPEND GUARD: meter each generated poster and refuse if over the monthly Vertex cap.
    from vertex_spend import guard_image, record
    est_usd = guard_image(1, model, detail=Path(out_path).name)
    # IDENTITY FIRST, and on its own parameter. It leads because `reel/render3/nano.py` — the one
    # path in this repo measured to hold a face across renders — attaches refs before the prompt
    # with refs[0] as the identity. It is NOT part of `product_images`: that list is truncated to
    # two, and everything downstream treats its members as merchandise (see `_identity_rules`).
    contents: list[Any] = []
    if identity_ref and identity_ref[0]:
        contents.append(types.Part.from_bytes(data=identity_ref[0],
                                              mime_type=identity_ref[1] or "image/png"))
    contents.append(prompt)
    if logo_bytes:
        contents.append(types.Part.from_bytes(data=logo_bytes, mime_type=logo_mime))
    for data, mime in (product_images or [])[:2]:
        if data:
            contents.append(types.Part.from_bytes(data=data, mime_type=mime or "image/jpeg"))
    # QUOTA-AWARE RETRY (owner's failed studio run 2026-07-12): with Dynamic Shared Quota a
    # concurrent Veo reel render on the SAME project starves the image call into a 429 — the
    # old code died instantly instead of waiting out a burst that passes in ~1-2 minutes.
    import time as _time
    delays_s = (0, 20, 45, 90)
    resp = None
    candidates = [model] + [m for m in _MODEL_CHAIN if m != model]
    tried: list[str] = []
    last_exc: Optional[Exception] = None
    for cand_model in candidates:
        tried.append(cand_model)
        for i, delay_s in enumerate(delays_s):
            if delay_s:
                _time.sleep(delay_s)
            try:
                resp = cl.models.generate_content(
                    model=cand_model,
                    contents=contents,
                    config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
                )
                break
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if _is_missing_model(e):
                    break                    # retired here — the NEXT model, not the same one again
                if not _is_retryable(e) or i == len(delays_s) - 1:
                    raise
        if resp is not None:
            model = cand_model               # what actually rendered, for the spend record
            break
    if resp is None:
        raise RuntimeError(
            f"one-shot poster: no image model available (tried {', '.join(tried)})"
        ) from last_exc
    for cand in (resp.candidates or []):
        for part in (cand.content.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline and (inline.mime_type or "").startswith("image/"):
                out_path = Path(out_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                data = inline.data
                if isinstance(data, str):
                    import base64
                    data = base64.b64decode(data)
                out_path.write_bytes(data)
                record("image", 1, est_usd, model=model, detail=Path(out_path).name)  # metered
                return out_path
    raise RuntimeError(f"one-shot poster: {model} returned no image")


def read_rendered_text(image_path: "str | Path | bytes", caller: Any) -> list[str]:
    """Vision read-back: what text is ACTUALLY rendered in an image (the gate's evidence;
    also used to read the REAL label text off an attached product photo, so those lines
    become ALLOWED extras). Accepts a path or raw bytes. Returns the seen lines; [] on
    failure."""
    from pydantic import BaseModel

    class _SeenText(BaseModel):
        lines: list[str] = []

    try:
        data = image_path if isinstance(image_path, bytes) else Path(image_path).read_bytes()
        from poster.contracts import VERBATIM_RENDER_CONTRACT
        resp, _u = caller(
            VERBATIM_RENDER_CONTRACT + "\n"
            "You are a CHARACTER-EXACT OCR reader. You copy text pixel-for-pixel; you NEVER "
            "correct spelling, dialect, or grammar.",
            "List EVERY piece of text visible in this poster image, one entry per distinct "
            "text element, top to bottom, copied EXACTLY letter-for-letter as printed. "
            "CRITICAL for Arabic: preserve DIALECTAL spellings exactly (if the poster prints "
            "أكتر with ت, write أكتر — do NOT 'fix' it to أكثر); count the dots — ت has 2 "
            "dots, ث has 3; ة vs ه as printed. Empty list if there is no text.",
            _SeenText,
            group_name="oneshot_ocr",
            images=[(data, "image/png")],
        )
        return [str(l).strip() for l in (getattr(resp, "lines", []) or []) if str(l).strip()]
    except Exception:
        return []


def text_fidelity(expected: dict, seen_lines: list[str]) -> dict:
    """The gate verdict: each requested copy field must appear VERBATIM (normalized) in the
    rendered image, and any rendered line that matches no expected field is flagged as EXTRA
    text (reported for the measurement; the logo's own fine print will show up here too).
    Returns {'fields': {name: bool}, 'rate': float, 'verified': bool, 'extra_lines': [...]}.

    "NOTHING TO VERIFY" IS NOT "VERIFIED", and this used to return `rate=1.0` for it — a default
    the old docstring stated so plainly that it read as a decision rather than as the hole it was.
    MEASURED:

        text_fidelity({}, ["آخرياع شأفب الن عدبل"])  ->  rate 1.0

    and the caller's gate is `if verdict["rate"] < 1.0: continue`. So an empty expectation set
    opened the gate for any image at all, including one whose Arabic is scrambled glyph-soup —
    which is exactly what this gate exists to stop, because the image model cannot write Arabic
    (Abu Auf: three attempts, fidelity 0.0 every time) while the fallback behind it renders the
    same copy correctly (same brand, next run, 9/10 with `script_wellformed: true`).

    So there are THREE states, not two: verified, failed, and could-not-be-verified. `verified` is
    True only when there was something to check AND all of it was found. `rate` keeps its meaning
    for the caller that wants a number, and is 0.0 when nothing could be checked — an unverifiable
    render must never look like a perfect one."""
    seen_norm = [_norm_text(l) for l in (seen_lines or [])]
    joined = " ".join(seen_norm)
    fields: dict[str, bool] = {}
    for name, text in (expected or {}).items():
        text = (text or "").strip()
        if not text:
            continue
        fields[name] = _norm_text(text) in joined
    expected_norms = [_norm_text(t) for t in (expected or {}).values() if (t or "").strip()]
    extra = [l for l, n in zip(seen_lines or [], seen_norm)
             if n and not any(n in e or e in n for e in expected_norms)]
    rate = (sum(fields.values()) / len(fields)) if fields else 0.0
    verified = bool(fields) and rate >= 1.0
    return {"fields": fields, "rate": round(rate, 3), "verified": verified,
            "extra_lines": extra}
