"""Art director: turn the profile's REAL signals (name, category, description,
offerings, audience, tone, languages) into a rich, text-free Veo prompt per scene
— footage of people and places that match THIS business's actual identity.

Discipline (same as the poster's art director):
  * The VISUAL is evocative, on-brand b-roll steered by the scraped signals. It
    carries MOOD, not factual claims — facts live in the verbatim text overlay.
  * Strictly TEXT-FREE (Veo garbles baked text); reserves space for the overlay.
  * GROUNDED IN IDENTITY: a restaurant's cuisine/region is read from its own name +
    description + offerings (e.g. "Qasr Elkbabgi" + "Eastern" -> Egyptian/oriental
    grill, NOT generic Western fine-dining). We never invent a cuisine that isn't
    signalled by the brand's real text.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel

from poster.schemas import PosterBrief

# Cuisine/region signals (in the brand's OWN name/description/offerings) ->
# authentic Middle Eastern / Egyptian oriental footage instead of generic Western.
_ORIENTAL_TOKENS = (
    "kebab", "kbab", "kabab", "kbabgi", "kebabgi", "grill", "grilled", "mashwi",
    "mashawi", "mishwi", "shawarma", "shawerma", "shish", "mezze", "meze", "kofta",
    "oriental", "eastern", "egyptian", "egypt", "arabic", "arab", "lebanese",
    "syrian", "levantine", "koshary", "kushari", "tagine", "hummus", "falafel",
    "mandi", "middle eastern", "مشوي", "مشاوي", "كباب", "مشويات", "شرقي", "مصري",
)

# Regional/cultural signals — NO food words. Used ONLY to add an Egyptian /
# Middle Eastern people-and-setting cue for ANY vertical (education, clinic, ...)
# without implying a restaurant. Keeping these separate from _ORIENTAL_TOKENS is
# what stops "food" leaking into a Digilians/NTI education reel.
_MENA_TOKENS = (
    "egypt", "egyptian", "arabic", "arab", "oriental", "eastern", "middle eastern",
    "lebanese", "syrian", "levantine", "gulf", "saudi", "emirati",
    "مصر", "مصري", "مصرية", "عربي", "عربية", "شرقي", "الشرق",
)

_VERTICAL_SCENE = {
    "cafe": "a cozy specialty cafe — a barista crafting coffee, friends chatting over "
            "cups at warm wooden tables, morning light",
    "education": "a bright modern learning space — diverse young adults collaborating on "
                 "laptops, an instructor guiding a small group, screens of code, aspirational energy",
    "medical_clinic": "a clean reassuring modern clinic — a friendly doctor talking with a "
                      "patient, soft daylight, calm and professional",
    "clinic": "a clean reassuring modern clinic — a friendly clinician with a patient, soft daylight, calm",
    "skincare": "a serene beauty setting — radiant glowing skin in soft diffused light, "
                "elegant product textures, premium and clean",
    "beauty": "an elegant beauty studio — a confident client, soft light, refined premium mood",
    "retail": "a stylish retail space — shoppers browsing beautifully arranged products, bright and modern",
    "ecommerce": "lifestyle product moments — hands interacting with desirable products, clean bright styling",
    "fitness": "an energetic modern gym — people training with focus and momentum, dynamic light",
    "real_estate": "elegant modern interiors and architecture — people viewing bright, beautifully staged spaces",
}
_DEFAULT_SCENE = ("a professional modern workplace — a confident, collaborative team in a "
                  "bright clean space, purposeful energy")

# Camera beats — category-NEUTRAL on purpose. They must NOT contain restaurant/
# hospitality words ("food", "guests", "hospitable"): those leaked into EVERY
# vertical (a Digilians/NTI education reel got "food" from the offering/value/
# contact beats). Food belongs only in _restaurant_scene, never here.
_BEAT = {
    "intro": "Establishing wide shot, slow cinematic push-in that reveals the scene.",
    "offering": "Smooth gimbal medium shots showcasing the offering/subject up close, people engaging with it.",
    "value_prop": "Intimate shot on a person's genuine reaction and a meaningful detail, gentle slow motion.",
    "contact": "Warm welcoming shot — people connecting, inviting and approachable.",
    "outro": "Hero brand moment, slow elegant pull-back with a soft warm glow.",
}

_TEXT_FREE = (
    "Absolutely NO text, words, letters, numbers, logos, watermarks, or signage anywhere. "
    "Leave calm, clean negative space (center and lower third) for text added later. "
    "Vertical 9:16 framing, cinematic photoreal footage, natural lighting, shallow depth of "
    "field, gentle authentic motion. No on-screen captions."
)


def _field(profile: Optional[dict], key: str) -> str:
    if not profile:
        return ""
    v = profile.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return str(v).strip() if v else ""


def _ground_text(brief: PosterBrief, profile: Optional[dict]) -> str:
    """All the brand's own descriptive text, for cuisine/identity detection."""
    parts = [brief.business_name or "", brief.headline or "", brief.subheadline or "",
             _field(profile, "description"), _field(profile, "tagline")]
    for o in ((profile or {}).get("offerings") or []):
        if isinstance(o, dict) and o.get("name"):
            parts.append(str(o["name"]))
    return " ".join(p for p in parts if p).lower()


def _restaurant_scene(ground: str) -> str:
    """Pick restaurant footage that matches the brand's real cuisine signals."""
    if any(tok in ground for tok in _ORIENTAL_TOKENS):
        return ("an authentic Egyptian / Middle Eastern restaurant — sizzling charcoal-grilled "
                "kebabs, mixed grills and kofta, generous oriental mezze and traditional dishes "
                "on the table, warm hospitable atmosphere, local Egyptian guests sharing the meal, "
                "oriental lanterns and traditional decor, NOT generic Western fine-dining")
    return ("an authentic local restaurant serving its signature dishes, warm welcoming "
            "atmosphere, real guests enjoying a shared meal, culturally true to the brand")


# Internal slug/segment labels that must NOT leak into a cinematic prompt (a Veo scene of
# "B2C experiencing services_b2c" is nonsense). Stripped to a human, visual phrasing.
_SEGMENT_CODE = re.compile(r"\b(b2[bcgx]|d2c|saas|paas|crm|erp|all|general|n/?a|unknown|other)\b", re.I)


def _humanize(text: str) -> str:
    """Turn a scraped slug/label into human words: `services_b2c` -> `services`,
    `Home-DSL` -> `Home DSL`. Drops internal segment codes (B2C/B2B/SaaS/...)."""
    s = re.sub(r"[_\-]+", " ", str(text or ""))
    s = _SEGMENT_CODE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _identity_scene(brief: PosterBrief, profile: Optional[dict]) -> str:
    """UNIVERSAL identity-derived b-roll subject — real people doing the brand's ACTUAL
    activity, built from the scraped category + top offerings + audience. No hardcoded
    vertical: a telecom / fintech / SaaS / logistics / ... brand (none in `_VERTICAL_SCENE`)
    gets a FIELD-RELEVANT scene from its own data instead of a generic office. (Restaurants
    keep their dedicated food scene; this is the no-template fallback.) Slug/segment labels are
    humanized so the prompt never reads as jargon. HONEST CEILING: a deterministic fallback
    can ground in the real category/offerings but can't INFER the visual activity from an opaque
    product name — that semantic leap (e.g. 'Orange PREMIER' -> people using phones) is the
    LLM art-director's job; this is the no-caller safety net."""
    category = _humanize(brief.category or _field(profile, "category"))
    audience_raw = (_field(profile, "audience_type") or "").strip()
    who = _humanize(audience_raw) or "people"
    # offerings are kept VERBATIM (real product names — humanizing could strip a real token
    # from a name like "SaaS Platform"); only the slug-prone category/audience is humanized.
    offerings = [str(o).strip() for o in (brief.offerings or [])[:2] if str(o).strip()]
    if not category and not offerings:
        return _DEFAULT_SCENE
    lead = (f"real {who} authentically experiencing {category}" if category
            else f"real {who} in a genuine moment with {brief.business_name}")
    if offerings:
        lead += " — a real-life moment that shows " + " and ".join(offerings)
    return (lead + ", documentary-real human emotion in a modern, true-to-life environment "
            "that genuinely fits this brand")


def _scene_base(brief: PosterBrief, ground: str, profile: Optional[dict] = None) -> str:
    cat = (brief.category or "").lower()
    if "restaurant" in cat or "cafe" in cat:
        # Cafe only if explicitly a cafe AND no strong food/grill signal.
        if "cafe" in cat and not any(t in ground for t in _ORIENTAL_TOKENS):
            return _VERTICAL_SCENE["cafe"]
        return _restaurant_scene(ground)
    for key, scene in _VERTICAL_SCENE.items():
        if key in cat:
            return scene
    # Unknown category -> UNIVERSAL identity scene (not a generic workplace).
    return _identity_scene(brief, profile)


class _BrandSceneResponse(BaseModel):
    scene: str
    # A CHARACTER ANCHOR: one recurring protagonist who appears in EVERY scene, so the
    # text-to-video reel stays coherent (the same human throughout) instead of Veo
    # inventing a different person each scene. Pure visual design (a b-roll cast choice),
    # NOT a factual claim about a real individual. Empty when the model omits it.
    character: str = ""


def _culture_line(profile, ground: str, *, scene_scope: bool) -> str:
    """FIX-D5: evidence-derived culture line (poster.locale) — the old lines hardcoded
    'Egyptian' for ANY Arabic/MENA-signal brand (wrong for a Saudi/Moroccan brand; the
    owner works universal). Country from evidence -> that country; Arabic signal with no
    country -> honest regional wording; nothing known -> ''."""
    try:
        from poster.locale import country_of
        country = country_of(profile)
    except Exception:  # noqa: BLE001
        country = ""
    langs = [str(l).lower()[:2] for l in ((profile or {}).get("languages") or [])]
    is_mena = ("ar" in langs) or any(t in ground for t in _MENA_TOKENS)
    if country:
        scope = ("every scene shows authentic people and real settings from " + country
                 if scene_scope else "depict authentic people and setting from " + country)
        return (f"The brand is from {country}: {scope}, culturally accurate "
                f"(NOT Western, NOT Gulf/Khaleeji, no cliches). ")
    if is_mena:
        return ("The brand is from an Arabic-speaking region: depict authentic regional "
                "people and setting, culturally accurate, no cliches. ")
    return ""


def build_brand_scene(
    brief: PosterBrief, profile: Optional[dict], caller: Optional[Any]
) -> tuple[Optional[str], Optional[str]]:
    """LLM art-director for the reel: invent ONE text-free, on-brand b-roll SCENE plus a
    recurring CHARACTER anchor, derived from the brand's VERBATIM persona — so two brands
    in the same category get DIFFERENT, identity-true footage AND a consistent protagonist
    across scenes (coherence; idea adopted from TrendPulse's character anchor).

    Returns (scene, character); (None, None) when no caller is given or the call fails (the
    caller then falls back to the deterministic _scene_base with no anchor). Both are purely
    VISUAL (no factual claims) and TEXT-FREE. Reuses the poster's _persona_lines block.
    """
    if caller is None:
        return None, None
    try:
        from poster.art_director import _persona_lines
        persona = _persona_lines(profile)
    except Exception:
        persona = ""
    ground = _ground_text(brief, profile)
    culture = _culture_line(profile, ground, scene_scope=False)
    system = (
        "You are an award-winning art director for premium vertical (9:16) marketing REELS. "
        "Invent ONE photoreal, TEXT-FREE b-roll scene for THIS specific brand: the setting, the "
        "people, and the action/mood that authentically show what this business does and who it "
        "serves. Show the brand's REAL activity IN ACTION — real people actively USING or "
        "experiencing its specific offerings, so the scene instantly reads as THIS exact field "
        "(NOT a generic office, NOT an abstract mood). Derive EVERYTHING from the brand persona "
        "provided — never a generic category template. " + culture +
        "ALSO define a 'character': ONE recurring protagonist (the brand's audience) who will "
        "appear in EVERY scene of the reel — describe them concretely and consistently (approx "
        "age, appearance, hair, attire), culturally authentic to the audience, so the same person "
        "carries the whole reel. "
        "Describe ONLY visuals (people, place, action, light, mood). ABSOLUTELY NO text, words, "
        "letters, numbers, logos, or signage. Cinematic, natural light, shallow depth of field, "
        "gentle motion, with calm negative space for text overlaid later. Scene 1-2 sentences; "
        "character one concrete phrase."
    )
    user = (
        f"Business: {brief.business_name}\n"
        f"Category: {brief.category}\n"
        f"Real offerings: {'; '.join([o for o in (brief.offerings or [])[:5] if o]) or (brief.category or '')}\n"
        f"Tone: {brief.tone or 'premium'}\n"
        + (f"\nBrand persona (verbatim from the real website):\n{persona}\n" if persona else "")
        + "\nReturn a vivid text-free 'scene' AND a recurring 'character' for this brand."
    )
    try:
        resp, _usage = caller(system, user, _BrandSceneResponse, group_name="reel_scene")
        scene = (getattr(resp, "scene", "") or "").strip()
        character = (getattr(resp, "character", "") or "").strip()
        return (scene or None), (character or None)
    except Exception:
        return None, None


class _StoryResponse(BaseModel):
    # ONE recurring protagonist so the reel reads as a single story (not strangers per cut).
    character: str = ""
    # An ordered NARRATIVE arc of text-free scenes that EXPRESS the brand.
    scenes: list[str] = []
    # A SHORT on-screen caption per scene (aligned with `scenes`) that NARRATES the beat — so the
    # story is READABLE on the moving images (the owner: "فين القصة؟"). The footage can't tell the
    # story alone; the captions carry it. In the brand's audience language, ~2-5 words each.
    captions: list[str] = []
    # ONE SPOKEN line per scene (aligned with `scenes`) — Veo 3.1 renders it as NATIVE speech in
    # the video (the TrendPulse insight: the reel should TALK). Conversational, in the audience's
    # own dialect, ~5-12 words; complements the caption (never just repeats it).
    voiceovers: list[str] = []
    # A FRESH scroll-stopping opener for the reel's first seconds (2-5 words). Without it the
    # overlay hook was the profile's VERBATIM tagline — the same opening line on every reel of
    # the brand (the owner's "طريقة الكتابة ثابتة"). Narrative copy, Ledger-gated downstream.
    hook: str = ""
    # ONE narrator VOICE for the whole reel (gender + age + tone, matching the protagonist/
    # audience). Without it Veo cast a random voice PER SCENE — a male voice reading lines
    # written in feminine forms, switching mid-reel (the owner: "واحد بيتكلم بصيغة أنثى").
    narrator: str = ""


def _diverse_offering_sample(profile, brief, k: int = 6) -> list[str]:
    """Up to `k` offerings from the FULL profile, deduped by name prefix so one product
    family can't dominate the sample (nahdi: the brief's 3 slots were ALL coffee capsules
    -> the reel became a coffee ad). Falls back to the brief's own offerings."""
    names: list[str] = []
    try:
        for o in (profile or {}).get("offerings") or []:
            n = o.get("name") if isinstance(o, dict) else getattr(o, "name", None)
            n = (n or {}).get("value") if isinstance(n, dict) else getattr(n, "value", n)
            if n and str(n).strip():
                names.append(str(n).strip())
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        names = [o for o in (getattr(brief, "offerings", None) or []) if o]
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = " ".join(n.split()[:2]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
        if len(out) >= k:
            break
    return out


def arabic_dialect() -> str:
    """OWNER OPTION (2026-07-11): the Arabic register is a CHOICE, not a hardcode.
    REEL_ARABIC_DIALECT = 'masri' (Egyptian colloquial, the default for the pilot market)
    or 'fusha' (Modern Standard Arabic). Universal: other dialects can join the map."""
    import os
    return (os.environ.get("REEL_ARABIC_DIALECT") or "masri").strip().lower()


def _copy_language_label(language: str, profile: Optional[dict] = None) -> str:
    """The on-screen caption language instruction for the story prompt. 'auto' keeps the old
    behavior (the brand's audience language, inferred by the model). The Arabic register follows the
    brand's EVIDENCED country (poster.locale) when a profile is given — a Saudi brand's captions are
    Khaleeji, not Masri; the REEL_ARABIC_DIALECT=fusha override still wins; no profile -> pilot default."""
    if arabic_dialect() == "fusha":
        return {"ar": "Modern Standard Arabic (فصحى معاصرة سلسة وقريبة)",
                "en": "English"}.get(language[:2], language)
    if language[:2] == "ar" and profile is not None:
        from poster.locale import arabic_region_of, arabic_dialect_for
        reg = arabic_region_of(profile)
        if reg and reg != "egypt":
            return arabic_dialect_for(profile)
    return {"ar": "Egyptian Arabic (عامية مصرية راقية)",
            "en": "English"}.get(language, "the brand's audience language")


def _spoken_language_label(language: str, profile: Optional[dict] = None) -> str:
    """The voiceover language/dialect instruction. 'auto' -> the audience's own dialect. Arabic
    register follows the brand's EVIDENCED country (poster.locale) when a profile is given."""
    if arabic_dialect() == "fusha":
        return {"ar": "clear, warm Modern Standard Arabic (fusha)",
                "en": "natural conversational English"}.get(language[:2], language)
    if language[:2] == "ar" and profile is not None:
        from poster.locale import arabic_region_of, arabic_dialect_for
        reg = arabic_region_of(profile)
        if reg and reg != "egypt":
            return f"natural spoken {arabic_dialect_for(profile)}"
    return {"ar": "natural spoken Egyptian Arabic (Masri)",
            "en": "natural conversational English"}.get(language, "the audience's OWN dialect")


def build_brand_story(
    brief: PosterBrief, profile: Optional[dict], caller: Optional[Any], n: int = 5,
    brand_dna: Optional[Any] = None,
    language: str = "auto",
) -> tuple[Optional[str], list[str], list[str], list[str]]:
    """LLM director: a coherent SHORT-FILM STORY — `n` text-free scenes forming a narrative ARC
    that EXPRESSES this brand (its real persona, offerings, world), with ONE recurring protagonist,
    so the reel feels like THIS brand and tells a story (the owner: "هو حكاية بتعبّر عن البراند، مش
    صور ورا بعض"). Returns (character, scenes, captions, voiceovers, hook), lists aligned 1:1 with
    `scenes` (padded with ""): `captions` = a SHORT ~2-5-word on-screen line that NARRATES each beat
    so the story READS on the footage (the owner: "فين القصة؟"); `voiceovers` = ONE conversational
    SPOKEN line per scene that Veo 3.1 renders as native speech so the reel TALKS; `hook` = a FRESH
    2-5-word opener (replaces the verbatim-tagline hook that repeated on every reel); `narrator` =
    the ONE voice for the whole reel (gender/age/tone — Veo otherwise casts a random voice PER
    SCENE, switching gender mid-reel). All are design/narrative copy (overlaid / spoken), never
    baked as image text, and all must pass the Ledger gate downstream. (None, [], [], [], "", "")
    when no caller / on failure so the caller falls back to deterministic varied scenes. NATURAL colour, TEXT-FREE footage (the brand identity comes
    from the persistent LOGO + the on-brand SUBJECTS, never a colour dye or baked text)."""
    if caller is None:
        return None, [], [], [], "", ""
    try:
        from poster.art_director import _persona_lines
        persona = _persona_lines(profile)
    except Exception:
        persona = ""
    ground = _ground_text(brief, profile)
    culture = _culture_line(profile, ground, scene_scope=True)
    # The brand's learned VISUAL LANGUAGE (from its real ads) — themes/mood only, NOT colour/text.
    dna_lines = ""
    for attr in ("imagery_style", "mood", "photography_style", "motifs", "positioning"):
        v = getattr(brand_dna, attr, None) if brand_dna is not None else None
        if v:
            dna_lines += f"- {attr}: {v if isinstance(v, str) else ', '.join(map(str, v))}\n"

    system = (
        f"You are an award-winning director of premium vertical (9:16) brand REELS. A reel is a "
        f"SHORT FILM that tells ONE coherent STORY expressing the brand — NOT a row of random "
        f"clips. Write a {n}-scene narrative ARC (a hook that grabs in the first second, a build, "
        f"an emotional peak, then a brand pay-off) that authentically expresses THIS brand's world "
        f"and what it does for its people. ONE recurring PROTAGONIST (from the brand's real "
        f"audience) carries all the scenes so it reads as one story. "
        "IDENTITY ANCHOR (non-negotiable): the story's WORLD must be UNMISTAKABLY what this "
        "business IS — a viewer who misses every caption must still recognize the business's "
        "kind from the scenes alone (a pharmacy reel lives in the world of health, care and "
        "wellbeing — not a coffee ad). The FIRST and LAST scenes especially must place the "
        "viewer firmly in that world. The 'Real offerings' list is a SAMPLE of individual "
        "products; treat them as supporting props ONLY, NEVER as the story's subject — the "
        "subject is what the business as a whole does for people ('What the business IS' "
        "below). Ground EVERYTHING in the brand persona — its real value, audience and tone; "
        "be CREATIVE but NEVER leave the brand's world. " + culture +
        "Each scene = a vivid, photoreal, TEXT-FREE b-roll moment (people, place, action, light, "
        "emotion). NATURAL, true-to-life colour with WARM realistic human skin tones — absolutely "
        "NO single-colour tint, monochrome wash or coloured-gel lighting on people or scene. "
        "ABSOLUTELY NO text, words, letters, numbers, logos or signage in any image. Each scene 1-2 "
        "sentences, concrete and shootable."
    )
    desc = ""
    try:
        d = (profile or {}).get("description")
        desc = str((d or {}).get("value") if isinstance(d, dict) else (d or ""))[:350]
    except Exception:
        desc = ""
    # A DIVERSE offerings sample from the FULL profile — the brief caps at 3, and on a
    # marketplace those 3 can all be one product family (nahdi: 3x coffee capsules ->
    # the whole reel became a coffee ad). Dedup by name prefix, spread across kinds.
    sample = _diverse_offering_sample(profile, brief)
    user = (
        f"Brand: {brief.business_name}\nCategory: {brief.category}\n"
        + (f"What the business IS (the story's world MUST live here): {desc}\n" if desc else "")
        + f"Real offerings (a product SAMPLE — props only, not the subject): "
          f"{'; '.join(sample) or (brief.category or '')}\n"
        f"Tone: {brief.tone or 'premium'}\n"
        + (f"\nBrand persona (verbatim from the real website):\n{persona}\n" if persona else "")
        + (f"\nThe brand's visual language (learned from its real ads — themes/mood, do NOT copy "
           f"text or colour):\n{dna_lines}" if dna_lines else "")
        + f"\nReturn 'character' (the one recurring protagonist), 'scenes' ({n} text-free scenes "
          f"that tell the brand's story in order), 'captions' (ONE short on-screen line per "
          f"scene, aligned with 'scenes', ~2-5 words each, in {_copy_language_label(language, profile)}, "
          f"that NARRATES that beat so the story reads on the footage), and 'voiceovers' (ONE "
          f"spoken line per scene, aligned with 'scenes', ~5-12 words, warm and conversational "
          f"in {_spoken_language_label(language, profile)} — it will be SPOKEN aloud in the video), "
          f"'narrator' (ONE voice for the WHOLE reel: gender + age + tone, matching the "
          f"protagonist — e.g. 'a warm adult Egyptian woman, reassuring'), and 'hook' (ONE "
          f"fresh scroll-stopping OPENER for the reel's first seconds, 2-5 words, in "
          f"{_copy_language_label(language, profile)} — NOT the brand's tagline verbatim; a new line "
          f"each time). THE VOICEOVER LINES ARE ONE CONTINUOUS NARRATION: each line flows "
          f"from the previous one — a single narrator telling ONE story, never 5 unrelated "
          f"statements. Grammatical GENDER must be consistent with the narrator in every "
          f"line INCLUDING the hook (Arabic: never mix مذكر/مؤنث forms); address the viewer "
          f"in NEUTRAL/plural "
          f"forms. Hook, captions and voiceovers are pure narrative/emotive copy — NO "
          f"numbers, prices, rankings, superlatives, awards or certifications; NEVER invent "
          f"a fact."
    )
    try:
        resp, _u = caller(system, user, _StoryResponse, group_name="reel_story")
        raw_scenes = [str(s or "").strip() for s in (getattr(resp, "scenes", []) or [])]
        raw_caps = [str(c or "").strip() for c in (getattr(resp, "captions", []) or [])]
        raw_caps = (raw_caps + [""] * len(raw_scenes))[: len(raw_scenes)]  # align 1:1 with scenes
        raw_vos = [str(v or "").strip() for v in (getattr(resp, "voiceovers", []) or [])]
        raw_vos = (raw_vos + [""] * len(raw_scenes))[: len(raw_scenes)]
        trios = [(s, c, v) for s, c, v in zip(raw_scenes, raw_caps, raw_vos) if s]
        scenes = [s for s, _, _ in trios]                # drop empty scenes WITH their copy lines
        captions = [c for _, c, _ in trios]
        voiceovers = [v for _, _, v in trios]
        character = (getattr(resp, "character", "") or "").strip()
        hook = (getattr(resp, "hook", "") or "").strip()
        narrator = (getattr(resp, "narrator", "") or "").strip()
        return (character or None), scenes, captions, voiceovers, hook, narrator
    except Exception:
        return None, [], [], [], "", ""


# Marketing-archetype steering for the reel's GENERATED scene composition (poster parity).
_ARCHETYPE_SCENE = {
    "magazine_editorial": "Editorial, premium composition with generous negative space; the subject is one elegant focal point.",
    "product_hero": "The product/offering is the clear HERO — prominent, centered, hero-lit, in a clean uncluttered composition.",
    "typographic_anchor": "A bold, simple, graphic backdrop with calm negative space reserved for large overlaid text; minimal clutter.",
    "proof_and_trust": "Clean, structured, professional and trustworthy composition; orderly and credible.",
}


def build_scene_prompt(
    scene_kind: str, brief: PosterBrief, *, profile: Optional[dict] = None,
    base_scene: Optional[str] = None, character_anchor: Optional[str] = None,
    variation: Optional[dict] = None, archetype: Optional[str] = None,
) -> str:
    """A complete, text-free Veo prompt: on-identity people + place, brand color
    grade, culturally accurate, with room for the overlay. When `base_scene` is
    supplied (from the LLM art-director) it drives the scene; otherwise a
    deterministic per-category template is the fallback. `character_anchor` (the
    recurring protagonist) is repeated in EVERY scene so a text-to-video reel keeps
    the SAME person + look across scenes (coherence) instead of inventing a new one."""
    ground = _ground_text(brief, profile)
    base = base_scene or _scene_base(brief, ground, profile)
    beat = _BEAT.get(scene_kind, _BEAT["intro"])
    # Lead the color grade with the REAL primary brand color (not an unordered
    # swatch list), so the brand's signature hue actually drives the scene.
    primary = brief.primary_color or (brief.palette_hex[0] if brief.palette_hex else "")
    others = ", ".join(c for c in brief.palette_hex[:4] if c != primary)
    color_cue = (
        f"Lead the cinematic color grade with the brand's primary color {primary}"
        + (f", supported by {others}" if others else "") + ". "
    ) if primary else ""
    tone = (brief.tone or "").strip().lower()
    tone_phrase = f"{tone} mood, " if tone else ""

    # Regional cue ONLY (no food) — evidence-derived country (FIX-D5), so an education/
    # clinic brand gets ITS country's people + setting, never food and never a hardcoded one.
    culture = _culture_line(profile, ground, scene_scope=False) or "Culturally authentic to the brand. "

    # CONTINUITY: repeat the recurring protagonist + lock the look in EVERY scene, so a
    # text-to-video reel reads as one coherent story (same person, same grade) rather than
    # a different stranger each cut. Adopted from TrendPulse's character/style anchor.
    anchor = (character_anchor or "").strip()
    continuity = (
        f"CONTINUITY — the SAME single person appears in every scene of this reel: {anchor}. "
        "Keep their face, hair, build, and outfit IDENTICAL across all scenes, with consistent "
        "lighting and color grade. "
    ) if anchor else ""

    # Per-RUN variation (mood / lighting / energy) so the SAME brand's reel looks
    # different each render — mirrors the poster's variation engine (design-only).
    var_cue = ""
    if variation:
        try:
            from poster.variation import concept_variation_cue
            var_cue = concept_variation_cue(variation)
        except Exception:
            var_cue = ""
    var_phrase = f"{var_cue} " if var_cue else ""

    # Marketing archetype -> the scene's overall composition behavior (poster parity).
    arche_cue = _ARCHETYPE_SCENE.get((archetype or "").strip())
    arche_phrase = f"{arche_cue} " if arche_cue else ""

    return (
        f"{continuity}{beat} Scene: {base}. {arche_phrase}{culture}{tone_phrase}{color_cue}{var_phrase}"
        f"Premium, modern, documentary-real for the brand '{brief.business_name}'. {_TEXT_FREE}"
    )
