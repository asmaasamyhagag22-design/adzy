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


def coherence_lint(reel: Any, *, profile: Optional[dict] = None,
                   featured: bool = False) -> CoherenceReport:
    """Deterministic re-check of the creative contracts on a CreativeReel. Worst-first."""
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

    # 8) FEASIBILITY — no intra-shot cuts / montages crammed into one generated scene.
    for i in range(len(scenes)):
        if _INTRA_CUT_RE.search(veos[i]):
            fail(f"scene {i+1} crams multiple shots (quick cuts / cut-to / montage) into one "
                 f"generated clip — split into separate scenes", 8)
            break

    score = max(0, score)
    return CoherenceReport(ok=not issues, score=score, issues=issues,
                           fix_note="; ".join(issues[:8]))


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
                            critic_caller: Any = None, images: Optional[list] = None) -> CoherenceReport:
    """The full gate: deterministic lint always; the LLM critic when a caller exists. Merges the
    critic's failed axes + fixes into the report. Also folds in the legacy plan_eval stranger/caption
    checks so nothing that gate caught is lost."""
    rep = coherence_lint(reel, profile=profile, featured=featured)
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
                                      critic_caller=critic_caller, images=images)
        if best is None or rep.score > best_rep.score:
            best, best_rep = reel, rep
        if rep.ok:
            _log(f"[coherence] clean plan on attempt {attempt + 1}")
            return reel, rep
        seen = list(dict.fromkeys(seen + rep.issues))
        _log(f"[coherence] FAIL (attempt {attempt + 1}): {rep.issues} -> corrective retry")
        fix_note = "; ".join(seen[:8])
    return best, best_rep
