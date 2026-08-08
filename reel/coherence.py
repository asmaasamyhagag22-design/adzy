"""Reel coherence gate — the code-enforced half of the creative contracts (Phase B).

The director (reel/creative_director.py) is TOLD the 8 contracts; this gate RE-CHECKS them so a
plan that violates them never reaches the owner's preview. Hybrid, mirroring render3:
  - `coherence_lint`  — deterministic checks (worst-first, each with a fix), like `plan_eval`.
  - `critic_review`   — a cheap LLM "creative director" for the fuzzy axes; its verdict is
                        RECOMPUTED IN CODE (the g2 pattern) so the model can't self-pass.
  - `plan_reel_gated` — generate -> evaluate -> feed failures back as a fix_note and regenerate
                        (the prepare_render3 loop), keeping the best plan; bounded, never raises.

HITL sanctity: this blocks-with-retry only when AUTHORING a new plan. An owner-approved plan is
never regenerated — the caller runs the gate advisory-only there.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from reel.plan_eval import _fv, _tokens, evaluate_reel_plan

# --- lexical signals (universal; business-agnostic) ------------------------------------
_ABSTRACT_RE = re.compile(r"\b(abstract|blurred\s+ui|abstract\s+charts?|glowing\s+abstract|"
                          r"holographic|abstract\s+icons?)\b", re.I)
_CLICHE_HOOK_RE = re.compile(
    r"(stressed|tired|frustrated|worried|overwhelmed|exhausted)\b[^.]{0,40}"
    r"(laptop|desk|screen|computer|office)"
    r"|person\s+(at|in front of)\s+(a\s+)?(laptop|desk|computer)", re.I)
_INTRA_CUT_RE = re.compile(r"\b(quick\s+cuts?|cut\s+to|montage|series\s+of\s+shots|"
                           r"then\s+(he|she|it|they))\b", re.I)
_CTA_VERB_RE = re.compile(r"\b(shop|order|book|buy|call|visit|download|subscribe|register|"
                          r"apply|join|get|start|try|اطلب|احجز|اشتري|اتصل|سجل|حمّل|اشترك|زور|"
                          r"جرّب|ابدأ|قدّم)\b", re.I)
# Third-party mark ban (ad-safety, PD-1). Precise by design: a mark phrase must name an explicit
# logo/trademark/wordmark, so ordinary nouns ("orange", "apple") never trip it. A hard mark
# (swoosh / golden arches / ® / ™) is unambiguous on its own.
_MARK_PHRASE_RE = re.compile(
    r"\b([a-z][\w'’&.\-]{1,24})\s+(?:logos?|trademarks?|brand\s?marks?|wordmarks?)\b"
    r"|\blogos?\s+of\s+([a-z][\w'’&.\-]{1,24})", re.I)
_HARD_MARK_RE = re.compile(r"\b(swoosh|golden\s+arches)\b|[®™]", re.I)
# self-references that mean "the brand's OWN mark" — always allowed
_SELF_MARK_WORDS = {"brand", "brand's", "your", "our", "the", "its", "own", "company",
                    "company's", "his", "her", "their", "a", "an"}
# P-23: a scene can name a GLOBAL third-party brand WITHOUT the word "logo" ("Nike shoes", "an
# iPhone on the desk") — the phrase regex above misses that, so a competitor's trademark renders
# into the ad. A curated, UNIVERSAL list of unambiguous global marks closes it. DELIBERATELY
# excludes ordinary-noun brand names (apple/orange/shell/dove/visa) that would false-positive on
# the fruit/colour/word; the brand's OWN name is always exempt (checked against own_mark below).
_THIRD_PARTY_BRANDS = frozenset({
    "nike", "adidas", "puma", "reebok", "gucci", "prada", "rolex", "chanel", "versace",
    "iphone", "ipad", "macbook", "airpods", "samsung", "huawei", "xiaomi", "playstation",
    "xbox", "nintendo", "cocacola", "coca-cola", "pepsi", "starbucks", "mcdonalds", "mcdonald's",
    "kfc", "ferrari", "lamborghini", "porsche", "mercedes", "toyota", "netflix", "spotify",
})
_THIRD_PARTY_BRAND_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(b) for b in _THIRD_PARTY_BRANDS), key=len, reverse=True))
    + r")\b", re.I)


# Words that are pure marketing filler in ANY category — a POV built only from these says nothing
# ("high quality", "جودة عالية", "professional and modern"). Universal, not vertical: none of these
# names a feeling, a moment, or a thing. The brand's own nouns always survive the filter.
_POV_FILLER = frozenset({
    "high", "quality", "premium", "professional", "modern", "best", "great", "good", "amazing",
    "excellent", "luxury", "luxurious", "elegant", "beautiful", "trust", "trusted", "reliable",
    "experience", "feel", "feeling", "vibe", "vibes", "brand", "product", "service", "customer",
    "and", "the", "with", "for", "our", "your", "a", "an", "of", "that", "this", "is", "are",
    "جودة", "عالية", "عالي", "أفضل", "افضل", "احترافية", "احترافي", "مودرن", "حديث", "فاخر",
    "فخامة", "ثقة", "موثوق", "ممتاز", "رائع", "جميل", "تجربة", "إحساس", "احساس", "علامة",
    "منتج", "خدمة", "عميل", "مع", "في", "من", "على", "التي", "الذي",
})


class CoherenceReport(BaseModel):
    ok: bool = True
    score: int = 0
    issues: list[str] = []
    fix_note: str = ""
    critic_ran: bool = False


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _overlap(a: str, b: str) -> int:
    return len(_tokens(a) & _tokens(b))


# A scene the director describes as motionless. The MOTION CONTRACT already forbids this at
# render time, so a plan that asks for it puts the brief and the renderer in a fight the viewer
# pays for: MEASURED on a real plan, scene 6 read "a clean, static hero shot".
#
# The adjective and the noun are separated by up to two words on purpose — the phrase measured in
# production was NOT "static shot", and a pattern requiring them adjacent would have sailed past
# the exact brief it was written for.
_STATIC_RE = re.compile(
    r"\b(?:static|locked[- ]off|motionless|frozen|tripod)\b(?:\s+\w+){0,2}\s+"
    r"\b(?:shot|camera|frame|angle|composition)\b"
    r"|\blocked[- ]off\b|\bmotionless\b|\bstill\s+(?:shot|frame)\b"
    r"|\bno\s+camera\s+move(?:ment)?\b", re.I)

# Beat-one words that name a PROBLEM rather than a product. A story is desire plus obstacle; a
# reel whose first beat is already the solution has no second act to earn.
#
# BILINGUAL BY NECESSITY, not by courtesy (D-8). The arc is authored in the brand's own language,
# so an English-only list would flag every Arabic plan as "opening on the solution" — and a check
# that fires on everything is a check nobody can act on.
_TENSION_RE = re.compile(
    r"\b(problem|struggle|struggling|tired|exhaust\w*|pain\w*|frustrat\w*|doubt|worry|worried|"
    r"fear|missing|lose|losing|lost|hard|difficult|stuck|slow|fails?|failing|before|question|"
    r"why|wish|need|without|never|hurts?|heavy|drain\w*|crash)\b"
    r"|(?:مشكل|تعب|إرهاق|ارهاق|مرهق|وجع|ألم|الم|توجع|صعب|بطيء|قلق|خوف|ناقص|"
    r"من غير|بدون|مش قادر|ليه|قبل|زهق|ملل|تقل|حرقان|إمساك|امساك)", re.I)

# How uniform the scene lengths may be before the reel reads as a metronome. MEASURED on two real
# plans: 2.5/3.5/3.0/3.0/2.5/3.0 and 2.0/3.0/3.0/3.0/3.0/3.0 — every shot within a half-second of
# every other. Micro-cuts inside a scene cannot rescue a flat macro rhythm.
_MIN_DURATION_SPREAD = 0.60      # shortest scene must be <= 60% of the longest


def _craft_checks(scenes, caps, vos, veos, roles, arc_beats, fail) -> None:
    """Four things a human editor fixes without being asked, and no gate was asking.

    None of these are taste. Each one is a structural property that can be read off the plan, and
    each was found on a real plan that passed every existing check.
    """
    n = len(scenes)

    # A) ONE MESSAGE PER MOMENT. A caption that repeats the voice burns the one channel that could
    #    have carried something the voice cannot. MEASURED: a real plan's final scene had caption
    #    and voiceover as the identical string.
    #    THE CTA IS THE EXCEPTION, and it is not a loophole. On the closing beat you WANT the
    #    words reinforced — the viewer hears "order today" and reads it in the same instant, which
    #    is the one moment repetition earns its place. That rule predates this check —
    #    `test_cta_scene_caption_may_match_its_voiceover` — and this check was wrong to ignore it.
    same = [i + 1 for i in range(n)
            if caps[i] and vos[i] and _norm(caps[i]) == _norm(vos[i])
            and roles[i] != "cta" and i != n - 1]
    if same:
        fail(f"scene(s) {same}: the caption repeats the voiceover word for word — a burnt channel. "
             "Say it once, and give the caption what the voice cannot carry", 10)

    # B) ROOM TO BREATHE. Every scene captioned means the image never gets to speak. A viewer
    #    reading for the whole reel never looks at the product.
    captioned = sum(1 for c in caps if c)
    if n >= 4 and captioned == n:
        fail("every scene is captioned — nothing is left for the image alone. Leave at least one "
             "beat silent so the picture carries it", 8)

    # C) THE FIRST BEAT IS THE PROBLEM, not the product. Story is desire plus obstacle; a reel
    #    whose opening beat is already the solution has no second act to earn, which is why a plan
    #    can satisfy every other check and still read as six pleasant statements in a row.
    #    Checked only when an arc exists — its absence is already a harder failure above.
    if arc_beats:
        first = arc_beats[0]
        if not _TENSION_RE.search(first):
            fail(f"the first beat \"{first}\" opens on the solution — a story needs an obstacle "
                 "before it can resolve one. Open on what is WRONG for the viewer, not on what "
                 "the product does", 12)

    # D) RHYTHM. A flat duration profile is a metronome no amount of intra-scene cutting fixes.
    durs = [float(getattr(s, "duration_s", 0) or 0) for s in scenes]
    durs = [d for d in durs if d > 0]
    # >= 4 STANDS, and I tried to lower it on 2026-08-01 on a premise that does not hold. The
    # argument was "the default reel is 3 shots now, so this never fires on the real shape" — but
    # `_scenes_for(15, has_insert=True)` returns FOUR: three generated shots plus the rendered
    # product insert, and the plan the gate reads has all four. So the check does fire on the
    # shape the pipeline produces, and the only case left exempt is a 3-shot reel with no insert,
    # where three samples are genuinely too few to call a rhythm.
    #
    # Lowering it also broke eight tests, several of them "a clean plan passes" — the gate began
    # refusing plans this repo considers good. `test_rhythm_is_not_judged_on_a_three_shot_cut`
    # pins the exemption deliberately.
    if len(durs) >= 4:
        lo, hi = min(durs), max(durs)
        if hi > 0 and lo / hi > _MIN_DURATION_SPREAD:
            fail(f"the scene lengths are near-uniform ({lo:g}s-{hi:g}s) — the reel keeps time like "
                 "a metronome. Vary the beats: establish long, compress, then hold the last one", 8)

    # E) NO STATIC SHOT IN THE BRIEF. The motion contract bans it at render; asking for it in the
    #    plan sets the brief against the renderer.
    stat = [i + 1 for i, v in enumerate(veos) if _STATIC_RE.search(v or "")]
    if stat:
        fail(f"scene(s) {stat} are written as static/locked-off shots — the motion contract "
             "forbids that at render time, so the plan is fighting the renderer", 10)


def coherence_lint(reel: Any, *, profile: Optional[dict] = None, featured: bool = False,
                   require_strategy: bool = False) -> CoherenceReport:
    """Deterministic re-check of the creative contracts on a CreativeReel. Worst-first.

    ``require_strategy`` is set when AUTHORING a plan: a fresh plan must carry the creative
    strategy (POV / arc / motif), while a plan approved BEFORE that contract existed still lints
    (advisory) instead of being condemned retroactively.
    """
    scenes = list(getattr(reel, "scenes", None) or [])
    if not scenes:
        return CoherenceReport(ok=False, score=0, issues=["no scenes in the plan"],
                               fix_note="no scenes in the plan")
    issues: list[str] = []
    score = 100

    def fail(msg: str, cost: int) -> None:
        nonlocal score
        issues.append(msg)
        score -= cost

    caps = [(getattr(s, "on_screen_text", "") or "").strip() for s in scenes]
    vos = [(getattr(s, "voiceover", "") or "").strip() for s in scenes]
    roles = [(getattr(s, "caption_role", "") or "").strip().lower() for s in scenes]
    veos = [(getattr(s, "veo_prompt", "") or "") for s in scenes]

    # 0) THE FOUR CRAFT CHECKS (2026-07-28). Added after a director's critique of two real plans
    #    found that everything the gate DOES check was satisfied and the reels still read as
    #    automated sequences. These are the things a human editor fixes without being asked.
    _craft_checks(scenes, caps, vos, veos, roles,
                  [str(b).strip() for b in (getattr(reel, "narrative_arc", None) or [])
                   if str(b).strip()], fail)

    # 1) PRODUCT VISIBLE — at least one concrete hero-product moment.
    if not any(bool(getattr(s, "hero_product", False)) for s in scenes):
        fail("no hero-product scene — the product is never concretely shown "
             "(set hero_product=true on >=1 scene showing the product/service)", 25)

    # 2) ABSTRACT/BLUR ban — a scene whose subject is abstract/blurred UI (unless routed to a
    #    real-screen composite, where the abstract generated screen is intentional).
    for i, s in enumerate(scenes):
        if _ABSTRACT_RE.search(veos[i]) and (getattr(s, "product_overlay", "") or "") != "real_screen":
            fail(f"scene {i+1} hides the product behind abstract/blurred visuals — show the real "
                 f"thing (or set product_overlay=\"real_screen\")", 15)
            break

    # 2b) NO THIRD-PARTY MARKS (ad-safety): the director is told to keep third-party logos and
    #     trademarks out of frame; enforce it in CODE (PD-1), not the prompt alone. Only the
    #     brand's OWN mark may appear — the exact failure that would render a competitor's swoosh.
    own_mark = _tokens(_fv(profile or {}, "name"))
    for i, v in enumerate(veos):
        third = None
        for m in _MARK_PHRASE_RE.finditer(v):
            tok = (m.group(1) or m.group(2) or "").lower()
            base = tok.split()[0] if tok else ""
            if base and base not in _SELF_MARK_WORDS and base not in own_mark:
                third = tok
                break
        if not third:
            # a NAMED global brand in the frame (no "logo" word needed) — own-brand exempt.
            bm = _THIRD_PARTY_BRAND_RE.search(v)
            if bm:
                tok = bm.group(1).lower()
                base = re.split(r"[\s\-]", tok)[0]
                if tok not in own_mark and base not in own_mark:
                    third = tok
        if third or _HARD_MARK_RE.search(v):
            what = ("a third-party logo/trademark (swoosh / golden arches / ® / ™)"
                    if not third else f"a third-party mark (\"{third}\")")
            fail(f"scene {i+1} renders {what} — keep third-party logos and trademarks out of the "
                 f"frame; only the brand's OWN mark may appear", 12)
            break

    # 3) HOOK works muted — scene 0 carries a name caption or a hero product, and is not the cliché
    #    stressed-person-at-a-laptop opener.
    if not (caps[0] or bool(getattr(scenes[0], "hero_product", False))):
        fail("hook (scene 1) has no caption and no hero product — a muted viewer can't tell what "
             "this is", 15)
    if _CLICHE_HOOK_RE.search(veos[0]):
        fail("hook (scene 1) is the cliché stressed-person-at-a-laptop opener — open on something "
             "visually arresting that works muted", 15)

    # 4) ONE CTA — exactly one cta-role caption, on the LAST scene; caption/VO/reel.cta agree; no
    #    earlier competing CTA.
    cta_scenes = [i for i, r in enumerate(roles) if r == "cta"]
    reel_cta = (getattr(reel, "cta", "") or "").strip()
    if cta_scenes != [len(scenes) - 1]:
        fail("the CTA must be exactly one caption (caption_role=\"cta\") on the FINAL scene", 12)
    else:
        last_cap = caps[-1]
        if reel_cta and last_cap and _overlap(reel_cta, last_cap) == 0:
            fail("CTA split — the final caption and reel.cta say different things; make them the "
                 "same action line", 12)
        if reel_cta and vos[-1] and _overlap(reel_cta, vos[-1]) == 0 and _CTA_VERB_RE.search(vos[-1]):
            fail("CTA split — the final voice-over states a different action than the caption/CTA", 8)
    for i in range(len(scenes) - 1):
        if roles[i] == "cta" or (caps[i] and _CTA_VERB_RE.search(caps[i]) and reel_cta
                                 and _overlap(caps[i], reel_cta) == 0):
            fail(f"scene {i+1} issues a competing CTA before the end — only the final scene calls "
                 f"to action", 8)
            break

    # 5) ONE VOICE / NO DEAD AIR — a locked dialect, and no scene with neither voice nor sound.
    if not (getattr(reel, "dialect", "") or "").strip():
        fail("no locked dialect set on the reel (the voice can drift register)", 8)
    has_bed = bool((getattr(reel, "sound_design", "") or getattr(reel, "music_mood", "")).strip())
    for i, s in enumerate(scenes):
        silent = not vos[i] and not (getattr(s, "sfx", "") or "").strip() \
            and not (getattr(s, "sound_mood", "") or "").strip()
        if silent and not has_bed:
            fail(f"scene {i+1} is dead air — no voice, no sfx, no sound_mood (and no sound bed)", 10)
            break

    # 6) CAPTION STRATEGY — no caption verbatim-repeats its own voice-over; captioned scenes carry a
    #    role; captions stay short. The CTA scene is EXEMPT: its caption and voice SHOULD match (that
    #    is exactly the ONE-CTA rule — one action line, said and shown).
    for i in range(len(scenes)):
        if roles[i] == "cta":
            continue
        c, v = _norm(caps[i]), _norm(vos[i])
        if c and v and (c == v or c in v):
            fail(f"scene {i+1} caption repeats the voice-over verbatim — reinforce in different "
                 f"words or add a layer", 10)
            break
    missing_role = [i + 1 for i in range(len(scenes)) if caps[i] and not roles[i]]
    if missing_role:
        fail(f"captioned scene(s) {missing_role} have no caption_role", 5)
    long_caps = [i + 1 for i in range(len(scenes)) if len(caps[i].split()) > 5]
    if long_caps:
        fail(f"caption(s) on scene(s) {long_caps} are too long to read (>5 words)", 5)

    # 7) BRAND-SPECIFIC — a grounded brand-unique claim, actually used in the copy.
    claim = (getattr(reel, "brand_unique_claim", "") or "").strip()
    if not claim:
        fail("no brand_unique_claim — the reel says nothing only THIS brand can say", 15)
    else:
        copy_tok = set()
        for t in caps + vos + [getattr(reel, "hook", ""), reel_cta, getattr(reel, "concept", "")]:
            copy_tok |= _tokens(t)
        claim_tok = {t for t in _tokens(claim) if len(t) >= 3}
        if claim_tok and len(claim_tok & copy_tok) < 1:
            fail("the brand_unique_claim is declared but never appears in the voice/captions — "
                 "put it in the copy", 8)
        if profile is not None and not _claim_grounded(profile, claim):
            fail("the brand_unique_claim is not grounded in the identity (unsourced) — "
                 "use a real differentiator", 10)

    # 8b) CREATIVE STRATEGY — the owner's ruling (2026-07-20): "Taste ليس له Ground Truth... مستحيل
    #     تختبره. لكن Strategy يمكن." So NOTHING here judges whether the reel is good. Every check is
    #     a decidable structural property that a director's plan has and a shot-collector's does not:
    #     a declared POV, a story arc the scenes actually walk, and a motif that actually recurs.
    #     Skipped wholesale for pre-contract plans (all three fields empty) so an owner-APPROVED plan
    #     authored before this contract still lints.
    pov = (getattr(reel, "creative_pov", "") or "").strip()
    motif = (getattr(reel, "visual_motif", "") or "").strip()
    arc = [str(b).strip() for b in (getattr(reel, "narrative_arc", None) or []) if str(b).strip()]
    if pov or motif or arc or require_strategy:
        if not pov:
            fail("no creative_pov — the reel has no point of view: name the ONE feeling the viewer "
                 "leaves with", 15)
        elif _is_generic_pov(pov):
            fail(f"creative_pov \"{pov}\" is generic — every brand claims that; name a SPECIFIC "
                 f"feeling this reel creates", 12)
        if len(arc) < 3:
            fail("narrative_arc needs at least 3 beats — a POV names a feeling but not the STORY "
                 "(e.g. problem -> discovery -> proof)", 15)
        elif len(set(_norm(b) for b in arc)) != len(arc):
            fail("narrative_arc repeats a beat — each beat must move the story somewhere new", 10)
        else:
            beat_at = {_norm(b): i for i, b in enumerate(arc)}
            idx: list[int] = []
            bad_beat = []
            for i, s in enumerate(scenes):
                b = _norm(getattr(s, "arc_beat", "") or "")
                if b not in beat_at:
                    bad_beat.append(i + 1)
                else:
                    idx.append(beat_at[b])
            if bad_beat:
                fail(f"scene(s) {bad_beat} have no arc_beat from the declared narrative_arc "
                     f"{arc} — every scene must serve a beat", 12)
            elif idx != sorted(idx):
                fail(f"the scenes do not walk the narrative_arc in order (beats run {idx}) — "
                     f"reorder so the story moves forward", 12)
            elif idx and (idx[0] != 0 or idx[-1] != len(arc) - 1):
                fail(f"narrative_arc is not walked end to end — scene 1 must open on \"{arc[0]}\" "
                     f"and the final scene must land on \"{arc[-1]}\"", 10)
        if not motif:
            fail("no visual_motif — name the recurring visual thread that ties the scenes together",
                 10)
        elif not _motif_recurs(motif, veos):
            fail(f"visual_motif \"{motif}\" is declared but never written into the scenes — a motif "
                 f"must actually RECUR (appear in at least 2 scene prompts)", 10)

    # 8) FEASIBILITY — no intra-shot cuts / montages crammed into one generated scene.
    for i in range(len(scenes)):
        if _INTRA_CUT_RE.search(veos[i]):
            fail(f"scene {i+1} crams multiple shots (quick cuts / cut-to / montage) into one "
                 f"generated clip — split into separate scenes", 8)
            break

    # 9) CASTING FROM THE MEASURED MARKET (D-8: derived, never hardcoded). MEASURED (Abu Auf,
    # 2026-07-25): the veo_prompts put unspecified people into an Egyptian brand's reel and the
    # video model cast whoever — the owner: «هنود معرفش ليه». A prompt that stages a PERSON must
    # name the brand's own evidenced country; the gate's fix-note retry then corrects the plan.
    if profile:
        try:
            from poster.locale import country_of
            _country = str(country_of(profile) or "").strip()
        except Exception:  # noqa: BLE001
            _country = ""
        if _country:
            _person_re = re.compile(
                r"\b(woman|man|person|people|customer|model|barista|chef|pharmacist|worker|"
                r"hand|hands|she |he |girl|boy|family|friends)\b", re.I)
            for i, v in enumerate(veos):
                if _person_re.search(v) and _country.lower() not in v.lower():
                    fail(f"scene {i+1} stages a person without casting them from the brand's "
                         f"measured market ({_country}) — say it in the prompt", 9)
                    break

    score = max(0, score)
    return CoherenceReport(ok=not issues, score=score, issues=issues,
                           fix_note="; ".join(issues[:8]))


def _is_generic_pov(pov: str) -> bool:
    """A POV is generic when, after removing the marketing adjectives EVERY brand uses, nothing
    specific is left. Deliberately not a taste judgement: it measures whether the sentence carries
    any content words at all. Bilingual (AR/EN) because the director writes in the brand's language.
    """
    words = re.findall(r"[^\W\d_]+", pov.lower(), re.UNICODE)
    content = [w for w in words if w not in _POV_FILLER and len(w) > 2]
    return len(content) < 2


def _motif_recurs(motif: str, veos: list[str]) -> bool:
    """The motif's own content tokens must appear in >= 2 scene prompts. This is what makes the
    motif a THREAD rather than a line in the plan the render never sees."""
    toks = {t for t in _tokens(motif) if len(t) >= 4 and t not in _POV_FILLER}
    if not toks:
        return False
    hits = sum(1 for v in veos if toks & _tokens(v))
    return hits >= 2


def _claim_grounded(profile: dict, claim: str) -> bool:
    """Same policy as reel.grounding.grounded_captions: the claim's hard claims must resolve to real
    evidence. Fail-open on a ledger error (the deterministic lint already flagged the cheap classes)."""
    try:
        from grounding import EvidenceLedger
        led = EvidenceLedger.from_profile(profile)
        return all(v.sourced for v in led.audit_text(claim or ""))
    except Exception:  # noqa: BLE001
        return True


# ------------------------------------------------------------------------------------------
# LLM creative-director critic — the fuzzy axes; verdict RECOMPUTED IN CODE (g2 pattern).
# ------------------------------------------------------------------------------------------

class CriticVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hook_stops_scroll: bool = False
    works_muted: bool = False
    product_shown: bool = False
    dialect_consistent: bool = False
    one_continuous_voice: bool = False
    cta_unified: bool = False
    brand_specific: bool = False
    caption_strategy_ok: bool = False
    offenders: list[str] = []          # "axis: scene N — fix" lines
    verdict: str = "FAIL"              # recomputed in code, never trusted from the model


_CRITIC_SYSTEM = (
    "You are a senior creative director reviewing a short-form ad reel PLAN before it is rendered. "
    "Judge ONLY these axes and be strict — a real client would reject a weak reel:\n"
    "- hook_stops_scroll: does scene 1 stop the scroll (not a cliché like a stressed person at a "
    "laptop)?\n- works_muted: does scene 1 read with the SOUND OFF (visual + a name caption)?\n"
    "- product_shown: is the actual product/service concretely SHOWN, not hidden behind abstract/"
    "blurred visuals?\n- dialect_consistent: is every voice-over line in ONE register/dialect (no "
    "drift, e.g. colloquial jumping to formal)?\n- one_continuous_voice: does the voice read as one "
    "person's single thought, not glued taglines?\n- cta_unified: is there exactly ONE call to "
    "action, the same in the final caption and voice?\n- brand_specific: does it say something only "
    "THIS brand can say (not any competitor)?\n- caption_strategy_ok: do captions reinforce/add "
    "rather than verbatim-repeat the voice?\n"
    "For every FALSE axis add an offender line 'axis: scene N — one-line fix'. Set each boolean "
    "truthfully; the verdict field is ignored (recomputed in code)."
)


def critic_review(reel: Any, *, profile: Optional[dict] = None, caller: Any = None,
                  images: Optional[list] = None) -> Optional[CriticVerdict]:
    """One cheap LLM pass judging the fuzzy axes. Returns None when no caller (honest-degrade).
    The verdict is recomputed in code so the model cannot self-pass (the render3 g2 invariant)."""
    if caller is None:
        return None
    scenes = list(getattr(reel, "scenes", None) or [])
    plan = [f"CONCEPT: {getattr(reel, 'concept', '')}", f"HOOK: {getattr(reel, 'hook', '')}",
            f"CTA: {getattr(reel, 'cta', '')}", f"DIALECT: {getattr(reel, 'dialect', '')}",
            f"BRAND-UNIQUE CLAIM: {getattr(reel, 'brand_unique_claim', '')}",
            f"BRAND: {_fv(profile, 'name')} — {_fv(profile, 'category')}", ""]
    for i, s in enumerate(scenes, 1):
        plan.append(f"SCENE {i}: MOTION={getattr(s, 'veo_prompt', '')} | "
                    f"VOICE={getattr(s, 'voiceover', '')} | "
                    f"CAPTION={getattr(s, 'on_screen_text', '')} ({getattr(s, 'caption_role', '')})")
    try:
        resp, _u = caller(_CRITIC_SYSTEM, "\n".join(plan), CriticVerdict,
                          group_name="reel_critic", images=images or None)
    except Exception:  # noqa: BLE001 — a critic failure must never block plan authoring
        return None
    if resp is None:
        return None
    axes = (resp.hook_stops_scroll and resp.works_muted and resp.product_shown
            and resp.dialect_consistent and resp.one_continuous_voice and resp.cta_unified
            and resp.brand_specific and resp.caption_strategy_ok)
    return resp.model_copy(update={"verdict": "PASS" if axes else "FAIL"})


def evaluate_reel_coherence(reel: Any, *, profile: Optional[dict] = None, featured: bool = False,
                            critic_caller: Any = None, images: Optional[list] = None,
                            require_strategy: bool = False) -> CoherenceReport:
    """The full gate: deterministic lint always; the LLM critic when a caller exists. Merges the
    critic's failed axes + fixes into the report. Also folds in the legacy plan_eval stranger/caption
    checks so nothing that gate caught is lost."""
    rep = coherence_lint(reel, profile=profile, featured=featured,
                         require_strategy=require_strategy)
    issues = list(rep.issues)
    try:
        pe = evaluate_reel_plan(reel, profile=profile, featured=featured)
        for it in pe.issues:
            if it not in issues:
                issues.append(it)
    except Exception:  # noqa: BLE001
        pass
    critic_ran = False
    v = critic_review(reel, profile=profile, caller=critic_caller, images=images)
    if v is not None:
        critic_ran = True
        if v.verdict != "PASS":
            for off in (v.offenders or [])[:8]:
                if off and off not in issues:
                    issues.append(off)
            if not v.offenders:
                issues.append("creative-director critic rejected the plan")
    score = max(0, 100 - 10 * len(issues))
    return CoherenceReport(ok=not issues, score=score, issues=issues,
                           fix_note="; ".join(issues[:8]), critic_ran=critic_ran)


# ------------------------------------------------------------------------------------------
# the prevention loop — author -> gate -> corrective retry (the prepare_render3 pattern)
# ------------------------------------------------------------------------------------------

def plan_reel_gated(profile: dict, photos: list, *, n_scenes: int = 6, language: Optional[str] = None,
                    featured_product: Optional[str] = None, caller: Any = None,
                    critic_caller: Any = None, user_note: Optional[str] = None,
                    photo_subjects: Optional[list] = None, images: Optional[list] = None,
                    max_rounds: int = 2, log=None) -> tuple[Any, CoherenceReport]:
    """Author a reel plan and correct it against the coherence gate BEFORE it reaches the owner.
    Returns (best CreativeReel or None, report). Never raises; on exhaustion it returns the best
    plan WITH the surviving issues (a zero-spend text layer — ship-with-flags beats fail-closed)."""
    from reel.creative_director import design_creative_reel
    _log = log or (lambda *a: None)

    def _author(fix_note=None):
        return design_creative_reel(profile, photos, n_scenes=n_scenes, language=language,
                                    featured_product=featured_product, caller=caller,
                                    user_note=user_note, photo_subjects=photo_subjects,
                                    fix_note=fix_note)

    best, best_rep = None, None
    seen: list[str] = []
    fix_note = None
    for attempt in range(max(1, max_rounds) + 1):
        reel = _author(fix_note)
        if reel is None or not getattr(reel, "scenes", None):
            if best is not None:
                break
            return None, CoherenceReport(ok=False, score=0,
                                         issues=["the director could not design a plan"],
                                         fix_note="")
        rep = evaluate_reel_coherence(reel, profile=profile, featured=bool(featured_product),
                                      critic_caller=critic_caller, images=images,
                                      require_strategy=True)   # authoring: strategy is mandatory
        if best is None or rep.score > best_rep.score:
            best, best_rep = reel, rep
        if rep.ok:
            _log(f"[coherence] clean plan on attempt {attempt + 1}")
            return reel, rep
        seen = list(dict.fromkeys(seen + rep.issues))
        _log(f"[coherence] FAIL (attempt {attempt + 1}): {rep.issues} -> corrective retry")
        fix_note = "; ".join(seen[:8])
    return best, best_rep
