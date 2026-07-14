"""§1 — the Director call (D1 concept + D2 VO + D3 storyboard in ONE strict-JSON call)
and the G1 lint (code first, the Ledger as the VO<->EVIDENCE trace).

Hard rules R1-R8 live in the prompt verbatim; G1 re-checks the checkable ones in CODE so a
violating treatment never reaches spend. The VO trace uses ledger.audit_text — the
Zero-Hallucination moat extended into the reel (stronger than the pack's minimum LLM check;
logged as D-R3.4).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_ALLOWED_SCREEN = ("NONE", "OUT_OF_FOCUS", "ANGLED_AWAY")
_SMILE_RE = re.compile(r"smil|يبتسم|تبتسم|ابتسام|مبتسم", re.IGNORECASE)
_TARGET_DURATION_S = 30    # the reel's target runtime; per-shot durations must sum to ~this
_DURATION_TOL_S = 5        # tolerance either side (a 6-shot 3-8s reel lands 25-35s legitimately)


class Motif(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object: str
    state_act1: str
    state_act2: str
    state_act3: str


class ActPalette(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hex: List[str] = Field(min_length=2, max_length=2)
    grade: str


class ColorConstants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lens: str
    dof: str
    lighting: str
    grain: str


class ColorScript(BaseModel):
    model_config = ConfigDict(extra="forbid")
    act1: ActPalette
    act2: ActPalette
    act3: ActPalette
    constants: ColorConstants


class CharacterSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    age: int
    presenting_gender: str = ""            # "man" | "woman" — drives pronouns + the card render
    skin_tone: str
    face: str
    distinctive_features: List[str]
    eyes: str
    hijab: Optional[str] = None
    outfit_act1: str
    outfit_act3: str
    constant_accessory: str


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    act: int
    duration_s: int
    location: str
    action: str
    emotion: str
    story_function: str
    camera: str
    screen_rule: str
    motif_placement: str
    match_cut_out: str = ""


class ReelTreatment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    big_idea: str
    logline: str
    vo_script_ar: str
    motif: Motif
    color_script: ColorScript
    character_sheet: CharacterSheet
    locations: List[str]
    shots: List[Shot]


class ConceptStub(BaseModel):
    """One candidate idea (§1 per-run variety) — a one-line pitch, not a full treatment.
    `audience_signal_served` names the REAL grounded audience signal this concept embodies
    (domain-truth trace — the protagonist must be that person, not an invented off-domain one)."""
    model_config = ConfigDict(extra="forbid")
    big_idea: str
    logline: str
    motif_object: str
    protagonist_one_liner: str
    audience_signal_served: str = ""


class DirectorOutput(BaseModel):
    """The variety-mode D1 output: 3 distinct candidates, the agent's ranked pick, and the
    FULL treatment rendered for the pick only (owner directive 2026-07-13)."""
    model_config = ConfigDict(extra="forbid")
    concepts: List[ConceptStub] = Field(min_length=3, max_length=3)
    picked_index: int
    treatment: ReelTreatment
    direction_note: str = ""


# ---------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------

def _val(x: Any) -> str:
    if isinstance(x, dict):
        return str(x.get("value") or "")
    return str(x or "")


def evidence_bullets_from_profile(profile: dict) -> list[str]:
    """The EVIDENCE input (§1): verbatim grounded facts from the profile — the ONLY
    permissible claim source for the VO. Pure projection, no invention."""
    p = profile.get("profile", profile) if isinstance(profile, dict) else {}
    bullets: list[str] = []
    d = _val(p.get("description"))
    if d:
        bullets.append(d[:220])
    for vp in (p.get("value_propositions") or [])[:6]:
        t = _val(vp)
        if t:
            bullets.append(t[:200])
    for ts in (p.get("trust_signals") or [])[:6]:
        t = _val(ts)
        if t:
            bullets.append(t[:200])
    for off in (p.get("offerings") or [])[:8]:
        name = _val(off.get("name")) if isinstance(off, dict) else _val(off)
        price = _val(off.get("price_text")) if isinstance(off, dict) else ""
        if name:
            bullets.append(f"{name}{f' — {price}' if price else ''}"[:200])
    return bullets


_ROLE = """ROLE
You are the Creative Director of a ~30-second vertical (9:16) ad reel. You
produce ONE coherent film treatment — a single story — not a mood board. Your
output feeds an automated image+video pipeline; every field must be concrete,
visual, and unambiguous."""

_INPUTS = """INPUTS
CLIENT: {client_name} — {client_one_liner}
OFFER: {offer}
AUDIENCE: {audience}
AUDIENCE SIGNALS (real people this brand actually serves — the protagonist MUST be one of them):
{audience_signals}
GOAL / CTA: {goal_cta}
EVIDENCE (grounded facts — the ONLY permissible claim source): {evidence_bullets}
LOCALE: {locale}. Local people, streets and interiors; local-language signage
where natural. Never a generic Western stock look; never a mismatched region.

DOMAIN TRUTH (violating this is an invalid, ungrounded concept):
The protagonist must be a real person from AUDIENCE SIGNALS, and the story must show them
ENTERING or ADVANCING in THIS brand's actual field (as evidenced by OFFER + EVIDENCE). Never
invent an off-domain use case for the brand — e.g. a training institute in software / networks /
data-science does NOT teach a pharmacist to run a modern pharmacy; it produces network engineers,
developers, and analysts. Ground the person in what the brand truly does."""

# R1-R8 verbatim (unchanged by the variety directive) — shared by both prompt modes.
_HARD_RULES = """HARD RULES — violating any one makes the output invalid:
R1 ONE PROTAGONIST. Exactly one character is the visual subject of EVERY shot.
   Supporting people (instructor, colleague) may appear but never replace the
   protagonist as the subject of a shot.
R2 THREE-ACT ARC across {n_shots} shots (default 6, 4-6 s each):
   Act1 = tension (a real, visible problem), Act2 = turning point + effort,
   Act3 = transformation + payoff + CTA. Every shot carries a story_function
   and an emotion. The beat "person smiles at a laptop/phone" is BANNED
   everywhere except as the single final payoff shot.
R3 ONE MOTIF. One physical object appears in every shot and visibly evolves
   with the arc (define its state per act). The motif is the visual glue.
R4 COLOR SCRIPT. A deliberate 3-act palette progression (2 hex anchors + grade
   words per act) plus constants (lens, depth of field, lighting style, grain).
   Deliberate progression = thread; random jumps = noise.
R5 MATCH CUTS. For every consecutive shot pair, specify match_cut_out: how
   shot k ENDS (gesture / object / camera direction) so that shot k+1's
   opening visually continues it.
R6 SCREENS. screen_rule must be exactly one of these tokens:
   (a) NONE — no screen in this shot (default),
   (b) OUT_OF_FOCUS — content unreadable by design (bokeh glow only),
   (c) ANGLED_AWAY — screen not facing camera,
   (d) REAL_CONTENT:<desc> — name the exact mundane content (e.g. "dark
       terminal window, a few lines of white monospace text" / "network
       topology diagram with 5 labeled nodes").
   NEVER "futuristic interface", NEVER glowing abstract UI, NEVER implied text.
R7 CHARACTER SHEET SPEC. A complete physical description FROZEN for the whole
   reel: name, age, presenting_gender ("man" or "woman" — must match the
   protagonist), skin tone, face shape + exactly 2 distinctive facial features,
   eyes, hijab yes/no (a man has hijab null; if a woman wears one: exact color +
   pattern + wrap style), max 2 outfits (Act1 outfit, Act3 outfit — both
   precise), and one constant accessory tied to the motif.
R8 VO. One voice, Egyptian Arabic, 60-70 words, first person, matching the
   arc. Aim for ~65 words — COUNT the words before answering; short scripts
   are rejected. Every factual claim in the VO must trace to a bullet in
   EVIDENCE. If a claim is not in EVIDENCE, do not make it."""

_DIRECTOR_PROMPT = _ROLE + "\n\n" + _INPUTS + "\n\n" + _HARD_RULES + "\n\nReturn the structured treatment."

# Concept-lock mode (§5 HITL): after the owner PICKS one candidate, render the FULL treatment for
# EXACTLY that concept (fixing any G1 issues on retry), never re-inventing the story.
_DIRECTOR_LOCK_PROMPT = _ROLE + "\n\n" + _INPUTS + """

LOCKED CONCEPT — realize the FULL treatment for EXACTLY this owner-approved concept. Do NOT
invent a different story, motif, or protagonist; every HARD RULE still applies:
{concept_block}
""" + "\n" + _HARD_RULES + "\n\nReturn the structured treatment."

# Variety mode (§1 additions, owner 2026-07-13): a sampled creative direction + an AVOID
# list + R9 novelty; OUTPUT is 3 distinct candidates, the ranked pick, and the FULL treatment
# for the pick only. R1-R8 unchanged.
_DIRECTOR_CONCEPTS_PROMPT = _ROLE + "\n\n" + _INPUTS + """

CREATIVE DIRECTION (sampled for THIS run — strong defaults; deviate only with a
one-line reason in direction_note):
  narrative archetype : {archetype} — {archetype_desc}
  motif domain        : {motif_domain} — {motif_domain_desc}
  protagonist slot    : {protagonist_slot} — {protagonist_slot_desc}
AVOID (used before for this client — do NOT reuse or closely echo):
{avoid_list}

""" + _HARD_RULES + """
R9 NOVELTY. The big_idea, motif object, and protagonist must be substantially
   different from every AVOID entry — same domain is allowed, the same story is
   not. The 3 candidate concepts must also be mutually distinct (no near-duplicates
   among themselves).
R10 DOMAIN TRUTH. Every concept must obey DOMAIN TRUTH above: the protagonist is a
   real AUDIENCE SIGNAL person, entering/advancing in the brand's ACTUAL field.
   Name which signal in audience_signal_served. A concept that drifts off the
   brand's real field is invalid, however creative.

OUTPUT — return a DirectorOutput:
1. "concepts": EXACTLY 3 distinct candidate ideas built on the sampled creative
   direction (three genuinely different takes within it), each with big_idea,
   logline, motif_object, protagonist_one_liner, and audience_signal_served
   (the exact real signal the protagonist embodies).
2. "picked_index": the 0-based index of your best candidate, ranked by
   audience_fit, evidence_coverage, filmability, and novelty_vs_history.
3. "treatment": the FULL treatment (all R1-R8 fields) for the PICKED concept ONLY.
4. "direction_note": one line noting any deliberate deviation from the sampled
   direction, or "" if none."""


_DIRECTOR_SYSTEM = ("You are a world-class creative director. Output must satisfy every HARD "
                    "RULE. Be concrete and visual; never vague.")


def _locale_of(p: dict, override: str) -> str:
    if override:
        return override
    loc = _val(p.get("locale")) or _val(p.get("country"))
    return loc or "Egypt"


def _audience_signals(p: dict) -> str:
    sigs = [_val(s) for s in (p.get("audience_signals") or []) if _val(s)]
    return "\n".join(f"  - {s}" for s in sigs[:8]) if sigs else "  (none captured)"


def _base_fields(profile: dict, client_one_liner, offer, audience, goal_cta,
                 locale, bullets, n_shots) -> dict:
    p = profile.get("profile", profile) if isinstance(profile, dict) else {}
    return dict(
        client_name=_val(p.get("name")) or "the brand",
        client_one_liner=client_one_liner or _val(p.get("description"))[:160],
        offer=offer or "; ".join(b for b in bullets[:3]),
        audience=audience or _val(p.get("audience_type")) or "local consumers",
        audience_signals=_audience_signals(p),
        goal_cta=goal_cta or "drive applications / purchases now",
        evidence_bullets="\n- " + "\n- ".join(bullets) if bullets else "(none)",
        locale=_locale_of(p, locale),
        n_shots=n_shots,
    )


def concept_lock_block(concept: "ConceptStub") -> str:
    """Render a chosen ConceptStub as the LOCKED CONCEPT block for concept-lock mode."""
    return (f"  BIG IDEA: {concept.big_idea}\n"
            f"  MOTIF OBJECT: {concept.motif_object}\n"
            f"  PROTAGONIST: {concept.protagonist_one_liner}")


def direct_reel(profile: dict, *, caller: Any, client_one_liner: str = "",
                offer: str = "", audience: str = "", goal_cta: str = "",
                locale: str = "", n_shots: int = 6, concept_lock: str = "",
                fix_note: str = "",
                evidence_bullets: Optional[list[str]] = None) -> Optional[ReelTreatment]:
    """ONE Director call -> a validated ReelTreatment. Retries once on parse/validation
    failure (per the pack), then returns None — the caller fails loud, never guesses.

    `concept_lock` (a LOCKED CONCEPT block, e.g. from concept_lock_block) pins the story to an
    owner-approved candidate so the full treatment realizes THAT concept, not a fresh one.
    `fix_note` appends explicit corrections for a G1-feedback retry."""
    if caller is None:
        return None
    bullets = evidence_bullets if evidence_bullets is not None \
        else evidence_bullets_from_profile(profile)
    fields = _base_fields(profile, client_one_liner, offer, audience, goal_cta, locale,
                          bullets, n_shots)
    prompt = (_DIRECTOR_LOCK_PROMPT.format(concept_block=concept_lock, **fields)
              if concept_lock else _DIRECTOR_PROMPT.format(**fields))
    if fix_note:
        prompt += ("\n\nCORRECTIONS REQUIRED — fix ALL of these from your previous attempt, "
                   "introduce no new violations:\n" + fix_note)
    for attempt in (1, 2):
        try:
            resp, _u = caller(_DIRECTOR_SYSTEM, prompt, ReelTreatment,
                              group_name="render3_director")
            if resp and resp.shots:
                return resp
        except Exception as exc:  # noqa: BLE001
            logger.warning("director call failed (attempt %d): %s", attempt,
                           type(exc).__name__)
    return None


def direct_reel_concepts(profile: dict, *, caller: Any, direction: Any,
                         avoid: Optional[list[str]] = None, locale: str = "",
                         client_one_liner: str = "", offer: str = "", audience: str = "",
                         goal_cta: str = "", n_shots: int = 6,
                         evidence_bullets: Optional[list[str]] = None
                         ) -> Optional["DirectorOutput"]:
    """Variety-mode Director call: the sampled `direction` (a CreativeDirection) + an AVOID
    list -> 3 distinct candidates, the ranked pick, and the full treatment for the pick.
    Retries once, then None (fail loud)."""
    if caller is None or direction is None:
        return None
    bullets = evidence_bullets if evidence_bullets is not None \
        else evidence_bullets_from_profile(profile)
    avoid_block = ("\n".join(f"  - {a}" for a in (avoid or [])) or "  (none — first run)")
    prompt = _DIRECTOR_CONCEPTS_PROMPT.format(
        archetype=direction.archetype, archetype_desc=direction.archetype_desc,
        motif_domain=direction.motif_domain, motif_domain_desc=direction.motif_domain_desc,
        protagonist_slot=direction.protagonist_slot,
        protagonist_slot_desc=direction.protagonist_slot_desc,
        avoid_list=avoid_block,
        **_base_fields(profile, client_one_liner, offer, audience, goal_cta, locale,
                       bullets, n_shots))
    for attempt in (1, 2):
        try:
            resp, _u = caller(_DIRECTOR_SYSTEM, prompt, DirectorOutput,
                              group_name="render3_director")
            if resp and resp.concepts and resp.treatment and resp.treatment.shots \
                    and 0 <= resp.picked_index < len(resp.concepts):
                return resp
        except Exception as exc:  # noqa: BLE001
            logger.warning("director(concepts) call failed (attempt %d): %s", attempt,
                           type(exc).__name__)
    return None


# ---------------------------------------------------------------------------------
# G1 lint — code checks + the Ledger VO trace
# ---------------------------------------------------------------------------------

def _concept_tokens(s: str) -> set:
    return set(re.findall(r"[a-z؀-ۿ]{4,}", (s or "").lower()))


def concepts_distinct(output: "DirectorOutput", *, threshold: float = 0.6) -> List[str]:
    """G1 addition (§4): the 3 candidate concepts must be mutually distinct (no near-
    duplicates). Code check on big_idea + motif_object token overlap (Jaccard). Returns the
    list of near-duplicate issues (empty = distinct)."""
    issues: List[str] = []
    stubs = output.concepts
    for i in range(len(stubs)):
        for j in range(i + 1, len(stubs)):
            a = _concept_tokens(stubs[i].big_idea + " " + stubs[i].motif_object)
            b = _concept_tokens(stubs[j].big_idea + " " + stubs[j].motif_object)
            if a and b:
                jac = len(a & b) / len(a | b)
                if jac >= threshold:
                    issues.append(f"concepts {i} and {j} near-duplicate (jaccard {jac:.2f})")
    return issues


@dataclass
class G1Report:
    ok: bool
    issues: List[str] = field(default_factory=list)
    unsourced_vo_claims: List[str] = field(default_factory=list)
    moat_skipped: bool = False   # the VO<->evidence ledger could not run — surface at HITL, never silent


def g1_lint(t: ReelTreatment, *, profile: Optional[dict] = None,
            n_shots: int = 6) -> G1Report:
    """The §1 lint, in CODE. Any issue -> the treatment never reaches spend."""
    issues: list[str] = []
    if len(t.character_sheet.distinctive_features) != 2:
        issues.append("character_sheet needs exactly 2 distinctive_features")
    if len(t.locations) > 4:
        issues.append(f"{len(t.locations)} locations (max 4)")
    if len(t.shots) != n_shots:
        issues.append(f"{len(t.shots)} shots (expected {n_shots})")
    acts = [s.act for s in t.shots]
    if sorted(set(acts)) != [1, 2, 3]:
        issues.append(f"acts present {sorted(set(acts))} (need all of 1,2,3)")
    if acts != sorted(acts):
        issues.append("acts are not in order (the three-act arc must progress)")
    smile_hits = [s.id for s in t.shots if _SMILE_RE.search(s.action + " " + s.emotion)]
    if any(sid != t.shots[-1].id for sid in smile_hits) or len(smile_hits) > 1:
        issues.append(f"smile-type beat outside the single final payoff shot: {smile_hits}")
    for s in t.shots:
        for fld in ("action", "emotion", "story_function", "camera", "motif_placement"):
            if not getattr(s, fld).strip():
                issues.append(f"shot {s.id}: empty {fld}")
        if s is not t.shots[-1] and not s.match_cut_out.strip():
            issues.append(f"shot {s.id}: empty match_cut_out")
        if s.location not in t.locations:
            issues.append(f"shot {s.id}: location {s.location!r} not in locations")
        base = s.screen_rule.split(":", 1)[0].strip()
        if not (base in _ALLOWED_SCREEN or s.screen_rule.startswith("REAL_CONTENT:")):
            issues.append(f"shot {s.id}: screen_rule {s.screen_rule!r} not allowed")
        if not 3 <= s.duration_s <= 8:
            issues.append(f"shot {s.id}: duration {s.duration_s}s outside 3-8s")
    total_s = sum(s.duration_s for s in t.shots)             # H1: shots must sum to the target runtime
    if abs(total_s - _TARGET_DURATION_S) > _DURATION_TOL_S:
        issues.append(f"shot durations sum to {total_s}s "
                      f"(target {_TARGET_DURATION_S}±{_DURATION_TOL_S}s)")
    ids = [s.id for s in t.shots]                            # L1: seeds/clips are named by id
    if len(set(ids)) != len(ids):
        issues.append(f"duplicate shot ids {ids} — seed/clip filenames would collide")
    words = len(t.vo_script_ar.split())
    if not 55 <= words <= 75:
        issues.append(f"VO is {words} words (must be 55-75)")

    # VO <-> EVIDENCE trace via the Ledger (D-R3.4 — the moat, not an LLM opinion).
    # C2: the moat must FAIL VISIBLE. If it cannot run, that is surfaced (logged + moat_skipped +
    # a G1 issue) so HITL #1 sees an ungrounded VO as a risk, never as an invisible clean pass.
    unsourced: list[str] = []
    moat_skipped = False
    if profile is not None:
        try:
            from grounding import EvidenceLedger
            ledger = EvidenceLedger.from_profile(profile)
            unsourced = [v.claim.text for v in ledger.audit_text(t.vo_script_ar)
                         if not v.sourced]
            if unsourced:
                issues.append(f"VO carries {len(unsourced)} unsourced hard claim(s): "
                              f"{unsourced[:3]}")
        except Exception as exc:  # noqa: BLE001
            moat_skipped = True
            logger.warning("VO<->evidence ledger SKIPPED (moat off) — %s: %s",
                           type(exc).__name__, str(exc)[:160])
            issues.append(f"VO<->evidence moat could not run ({type(exc).__name__}) — "
                          f"treat the VO as UNVERIFIED at HITL, not clean")
    return G1Report(ok=not issues, issues=issues, unsourced_vo_claims=unsourced,
                    moat_skipped=moat_skipped)
