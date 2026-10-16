"""Vision QA gate — a REAL self-critique that rejects and triggers regeneration.

Per the build brief (Step H): after rendering the FINAL poster, a vision model inspects the
actual rendered image and returns a structured verdict. On fail the pipeline regenerates
(bounded retries); if it still fails it returns a CLEAR ERROR, never a broken poster.

Checks (on the rendered poster, not just the background):
  * has_latin_text   — Latin letters in the HEADLINE / CHIPS / CTA (the brand LOGO is exempt)
  * cta_clipped      — the CTA / any element cut off at the frame edge
  * on_brand_color   — the dominant colour matches the brand (e.g. WE = purple), not random
  * candid_violation — candid/documentary look or flat natural lighting when the brand DNA
                       forbids it
  * overall_pass + reason

Side-effect free (multimodal caller injected). Without an authoritative caller the result is
QA_UNVERIFIED, never PASS or acceptance-eligible. Never raises.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field


POSTER_QA_POLICY_ID = "poster_visual_qa"
POSTER_QA_POLICY_VERSION = 1
POSTER_QA_SOURCE = "poster.vision_qa.poster_vision_qa"

QA_PASS = "QA_PASS"
QA_FAIL = "QA_FAIL"
QA_UNVERIFIED = "QA_UNVERIFIED"
ACCEPTED = "ACCEPTED"


class PosterAttemptIdentity(BaseModel):
    """The exact generated candidate: execution attempt + candidate + immutable pixels."""

    attempt_id: str
    candidate_id: str
    content_sha256: str


class PosterQAPolicy(BaseModel):
    """The known required visual-QA policy for one candidate."""

    policy_id: str = POSTER_QA_POLICY_ID
    version: int = POSTER_QA_POLICY_VERSION
    mandatory_checks: list[str] = Field(default_factory=list)
    non_applicable_checks: dict[str, str] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class PosterAcceptanceDecision(BaseModel):
    """The only decision allowed to turn generated pixels into an accepted Poster."""

    attempt_identity: PosterAttemptIdentity
    policy_id: str
    policy_version: int
    qa_status: str
    lifecycle_status: str
    generated: bool = True
    qa_executed: bool
    mandatory_checks_expected: list[str] = Field(default_factory=list)
    mandatory_checks_completed: list[str] = Field(default_factory=list)
    mandatory_checks_missing: list[str] = Field(default_factory=list)
    failed_mandatory_checks: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    acceptance_eligible: bool
    accepted: bool
    publishable: bool
    deliverable: bool
    qa_passed: bool


def _poster_bytes(poster: "str | Path | bytes") -> bytes:
    return poster if isinstance(poster, bytes) else Path(poster).read_bytes()


def poster_attempt_identity(
    poster: "str | Path | bytes",
    *,
    attempt_id: str,
    candidate_id: str,
) -> PosterAttemptIdentity:
    """Bind QA to the exact bytes that were inspected, never merely to a filename."""
    data = _poster_bytes(poster)
    return PosterAttemptIdentity(
        attempt_id=str(attempt_id or "").strip(),
        candidate_id=str(candidate_id or "").strip(),
        content_sha256=hashlib.sha256(data).hexdigest(),
    )


def required_poster_qa_policy(
    *,
    arabic: bool,
    logo_composited: bool,
    expect_real_products: Optional[bool],
    product_named: bool = False,
) -> PosterQAPolicy:
    """Return the one supported policy, including explicit and reasoned non-applicability."""
    checks = [
        "critic_pass", "has_latin_text", "cta_clipped", "on_brand_color",
        "candid_violation", "single_focal", "strong_typography", "cta_prominent",
        "script_wellformed", "invented_product", "third_party_mark",
        "text_lines", "garbled_text", "score",
    ]
    non_applicable: dict[str, str] = {}
    if logo_composited:
        non_applicable["logo_ok"] = (
            "the brand's own logo bytes were composited deterministically; visual QA judges "
            "the finished mark through the remaining visibility and composition checks")
    else:
        checks.append("logo_ok")
    if expect_real_products is False:
        non_applicable["placement_logical"] = "no real product is required in this Poster"
        non_applicable["product_count"] = "no real product is required in this Poster"
    else:
        checks.extend(["placement_logical", "product_count"])
    return PosterQAPolicy(
        mandatory_checks=checks,
        non_applicable_checks=non_applicable,
        context={
            "arabic": bool(arabic),
            "logo_composited": bool(logo_composited),
            "expect_real_products": expect_real_products,
            "product_named": bool(product_named),
        },
    )


class PosterQAVerdict(BaseModel):
    has_latin_text: bool = False
    cta_clipped: bool = False
    on_brand_color: bool = True
    candid_violation: bool = False
    logo_ok: bool = True            # brand mark crisp/complete (hard gate)
    single_focal: bool = True       # one clear focal point, not competing ones
    strong_typography: bool = True  # an intentional display lockup, not a default font
    cta_prominent: bool = True      # a confident button, not a footnote
    script_wellformed: bool = True  # copy well-formed for its script (Arabic ligatures/dots) — hard gate
    # PRODUCT AUTHENTICITY (owner-caught: topshoes' poster showed an INVENTED sneaker with a
    # swoosh — a false product claim invisible to every text gate). Additive, hard gates:
    invented_product: bool = False  # a fabricated hero product presented as the brand's merchandise
    third_party_mark: bool = False  # any other company's logo/trademark rendered in the scene
    # PLACEMENT (owner-caught 2026-07-20: the medicine box landed anywhere, any way, floating and
    # mis-scaled). Permissive default so old verdicts/models don't retro-fail every poster.
    placement_logical: bool = True   # the product sits somewhere physically real for its category
    # EVERY STRING THE CRITIC COULD READ. Without this the stray-text gate downstream sees an
    # empty list and reports "could not verify" forever — a dead gate that looks alive, which is
    # the failure class this repo keeps paying for.
    text_lines: list = Field(default_factory=list)
    # MALFORMED TEXT, judged on the PIXELS (2026-07-26). String-distance on a transcription was
    # measured to fail both ways — real label words absent from the product name look "invented",
    # and «FIC. TRUSTWORSHY» looks plausible. The critic can see the difference; code cannot.
    garbled_text: bool = False
    # HOW MANY DISTINCT PRODUCTS are in frame. A named-product ad showing three is the "chaotic
    # composition of multiple floating products" the owner rejected; 0 means nothing was counted.
    product_count: int = 0
    # THE LINEAGE OF ANY BRAND RULE THIS VERDICT CITED. Populated only when `on_brand_color`
    # came back false: it names the constraint terms the model asserted as REQUIRED, the
    # artifact/field/hash that authorised each one, and — when the brand supplied no such rule —
    # the typed defect BRAND_CONSTRAINT_NOT_GROUNDED. An ungrounded citation is recorded here
    # and does NOT set `on_brand_color` false, so it cannot block or trigger a paid re-render.
    brand_constraint_lineage: dict = Field(default_factory=dict)
    score: int = 7                  # art-director score 1..10
    # TRI-STATE (P6, 2026-07-15): True = PASS, False = FAIL, None = NOT_CHECKED.
    # It used to default to True, so a single Vertex 429 on the QA call silently converted the art
    # critic from strict to absent while the verdict still said "passed" — "cannot verify" spelled
    # as "verified". None is falsy, so `if qa.overall_pass` fails CLOSED.
    overall_pass: Optional[bool] = None
    # WHAT THE CRITIC ITSELF DECIDED, kept beside the recomputed answer. `overall_pass` is the
    # code-side conjunction, so once it is written the model's own aggregate is unrecoverable —
    # and a later reader that needs it (the expected-brand excuse) had no choice but to guess.
    # It guessed `True`, which turned an explicit veto into a PASS. Tri-state: None = the model
    # never rendered an aggregate (no caller, unreadable image, failed call).
    critic_pass: Optional[bool] = None
    reason: str = ""
    checked: bool = False           # True only when a vision model actually inspected it
    # Acceptance provenance. Empty on historical verdicts: unknown policy/source is deliberately
    # UNVERIFIED rather than retroactively upgraded.
    qa_source: str = ""
    policy_id: str = ""
    policy_version: Optional[int] = None
    policy_context: dict[str, Any] = Field(default_factory=dict)
    mandatory_checks_expected: list[str] = Field(default_factory=list)
    mandatory_checks_completed: list[str] = Field(default_factory=list)
    non_applicable_checks: dict[str, str] = Field(default_factory=dict)
    attempt_identity: Optional[PosterAttemptIdentity] = None


def _failed_check_names(qa: PosterQAVerdict, policy: PosterQAPolicy) -> list[str]:
    """Evaluate known mandatory dimensions; absence is handled separately as UNVERIFIED."""
    context = policy.context
    product_named = bool(context.get("product_named"))
    predicates = {
        "critic_pass": qa.critic_pass is True,
        "has_latin_text": qa.has_latin_text is False,
        "cta_clipped": qa.cta_clipped is False,
        "on_brand_color": qa.on_brand_color is True,
        "candid_violation": qa.candid_violation is False,
        "logo_ok": qa.logo_ok is True,
        "single_focal": qa.single_focal is True,
        "strong_typography": qa.strong_typography is True,
        "cta_prominent": qa.cta_prominent is True,
        "script_wellformed": qa.script_wellformed is True,
        "invented_product": qa.invented_product is False,
        "third_party_mark": qa.third_party_mark is False,
        "placement_logical": qa.placement_logical is True,
        "text_lines": bool(qa.text_lines),
        "garbled_text": qa.garbled_text is False,
        "product_count": (int(qa.product_count or 0) == 1 if product_named
                          else int(qa.product_count or 0) > 0),
        "score": 7 <= int(qa.score or 0) <= 10,
    }
    return [name for name in policy.mandatory_checks if not predicates.get(name, False)]


def decide_poster_acceptance(
    qa: Optional[PosterQAVerdict],
    *,
    attempt_identity: PosterAttemptIdentity,
    required_policy: PosterQAPolicy,
    current_attempt_identity: Optional[PosterAttemptIdentity] = None,
) -> PosterAcceptanceDecision:
    """Pure canonical decision: authoritative complete explicit PASS for these exact pixels only."""
    current = current_attempt_identity or attempt_identity
    reason_codes: list[str] = []
    expected = list(required_policy.mandatory_checks)
    completed: list[str] = []
    missing: list[str] = list(expected)
    failed: list[str] = []
    qa_executed = False

    if qa is None:
        reason_codes.append("QA_RESULT_MISSING")
    else:
        qa_executed = bool(qa.checked and qa.qa_source == POSTER_QA_SOURCE)
        if not qa.checked:
            reason_codes.append("QA_NOT_EXECUTED")
        if qa.qa_source != POSTER_QA_SOURCE:
            reason_codes.append("QA_SOURCE_NOT_AUTHORITATIVE")
        if (qa.policy_id != POSTER_QA_POLICY_ID
                or qa.policy_version != POSTER_QA_POLICY_VERSION):
            reason_codes.append("QA_POLICY_UNKNOWN")
        elif (qa.policy_id != required_policy.policy_id
              or qa.policy_version != required_policy.version
              or dict(qa.policy_context or {}) != dict(required_policy.context)
              or list(qa.mandatory_checks_expected or []) != expected
              or dict(qa.non_applicable_checks or {})
              != dict(required_policy.non_applicable_checks)):
            reason_codes.append("QA_POLICY_MISMATCH")

        bound = qa.attempt_identity
        if bound is None:
            reason_codes.append("QA_ATTEMPT_IDENTITY_MISSING")
        else:
            if (bound.attempt_id != attempt_identity.attempt_id
                    or bound.candidate_id != attempt_identity.candidate_id):
                reason_codes.append("QA_ATTEMPT_ID_MISMATCH")
            if (bound.content_sha256 != attempt_identity.content_sha256
                    or bound.content_sha256 != current.content_sha256):
                reason_codes.append("QA_CONTENT_MISMATCH")
        if (attempt_identity.attempt_id != current.attempt_id
                or attempt_identity.candidate_id != current.candidate_id):
            reason_codes.append("QA_ATTEMPT_ID_MISMATCH")

        completed_set = set(qa.mandatory_checks_completed or [])
        completed = [name for name in expected if name in completed_set]
        missing = [name for name in expected if name not in completed_set]
        # A present-but-empty transcription and invalid score are missing usable evidence, not a
        # measured creative failure.
        if "text_lines" in completed and not qa.text_lines:
            completed.remove("text_lines")
            missing.append("text_lines")
        if "score" in completed and not (1 <= int(qa.score or 0) <= 10):
            completed.remove("score")
            missing.append("score")
        if "critic_pass" in completed and qa.critic_pass is None:
            completed.remove("critic_pass")
            missing.append("critic_pass")
        missing = [name for name in expected if name in set(missing)]
        if missing:
            reason_codes.append("QA_MANDATORY_CHECKS_MISSING")
        if qa.overall_pass is None:
            reason_codes.append("QA_OVERALL_UNVERIFIED")

        structural = {
            "QA_NOT_EXECUTED", "QA_SOURCE_NOT_AUTHORITATIVE", "QA_POLICY_UNKNOWN",
            "QA_POLICY_MISMATCH", "QA_ATTEMPT_IDENTITY_MISSING",
            "QA_ATTEMPT_ID_MISMATCH", "QA_CONTENT_MISMATCH",
            "QA_MANDATORY_CHECKS_MISSING", "QA_OVERALL_UNVERIFIED",
        }
        if not structural.intersection(reason_codes):
            failed = _failed_check_names(qa, required_policy)
            if qa.overall_pass is False:
                reason_codes.append("QA_OVERALL_FAIL")
            if failed:
                reason_codes.append("QA_MANDATORY_CHECK_FAILED")

    unverified_codes = {
        "QA_RESULT_MISSING", "QA_NOT_EXECUTED", "QA_SOURCE_NOT_AUTHORITATIVE",
        "QA_POLICY_UNKNOWN", "QA_POLICY_MISMATCH", "QA_ATTEMPT_IDENTITY_MISSING",
        "QA_ATTEMPT_ID_MISMATCH", "QA_CONTENT_MISMATCH",
        "QA_MANDATORY_CHECKS_MISSING", "QA_OVERALL_UNVERIFIED",
    }
    if unverified_codes.intersection(reason_codes):
        status = QA_UNVERIFIED
    elif qa is not None and qa.overall_pass is True and not failed:
        status = QA_PASS
        reason_codes.append("QA_EXPLICIT_PASS")
    else:
        status = QA_FAIL
    accepted = status == QA_PASS
    explanation = (
        "Authoritative visual QA explicitly passed every mandatory measurement for the exact "
        "immutable Poster attempt." if accepted else
        ("No authoritative visual-QA result exists for this exact Poster attempt."
         if qa is None else
         (str(getattr(qa, "reason", "") or "").strip()
          or "Poster visual QA did not establish an explicit complete PASS."))
    )
    return PosterAcceptanceDecision(
        attempt_identity=attempt_identity,
        policy_id=required_policy.policy_id,
        policy_version=required_policy.version,
        qa_status=status,
        lifecycle_status=ACCEPTED if accepted else status,
        qa_executed=qa_executed,
        mandatory_checks_expected=expected,
        mandatory_checks_completed=completed,
        mandatory_checks_missing=missing,
        failed_mandatory_checks=failed,
        reason_codes=reason_codes,
        explanation=explanation,
        acceptance_eligible=accepted,
        accepted=accepted,
        publishable=accepted,
        deliverable=accepted,
        qa_passed=accepted,
    )


def _policy_from_verdict(qa: Optional[PosterQAVerdict]) -> PosterQAPolicy:
    context = dict(getattr(qa, "policy_context", None) or {})
    return required_poster_qa_policy(
        arabic=bool(context.get("arabic", False)),
        logo_composited=bool(context.get("logo_composited", False)),
        expect_real_products=context.get("expect_real_products", False),
        product_named=bool(context.get("product_named", False)),
    )


def poster_acceptance_from_output(
    output: Mapping[str, Any],
    *,
    payload: Optional[bytes] = None,
    expected_attempt_id: str = "",
) -> PosterAcceptanceDecision:
    """Re-derive truth from raw QA + current bytes; ignore booleans/statuses supplied by callers."""
    raw = dict(output or {})
    if payload is None:
        try:
            payload = base64.b64decode(str(raw.get("image_b64") or ""), validate=True)
        except Exception:
            payload = b""
    qa_raw = raw.get("visual_qa")
    try:
        qa = (qa_raw if isinstance(qa_raw, PosterQAVerdict)
              else PosterQAVerdict.model_validate(qa_raw)) if qa_raw else None
    except Exception:
        qa = None
    policy = _policy_from_verdict(qa)
    bound = getattr(qa, "attempt_identity", None)
    attempt_id = str(expected_attempt_id or raw.get("generation_attempt_id")
                     or getattr(bound, "attempt_id", "") or "unbound")
    candidate_id = str(getattr(bound, "candidate_id", "") or raw.get("filename")
                       or "generated-poster")
    current = poster_attempt_identity(
        payload or b"", attempt_id=attempt_id, candidate_id=candidate_id)
    return decide_poster_acceptance(
        qa, attempt_identity=current, required_policy=policy,
        current_attempt_identity=current)


class _QAResponse(BaseModel):
    has_latin_text: bool
    cta_clipped: bool
    on_brand_color: bool
    candid_violation: bool
    overall_pass: bool
    reason: str
    # Art-director rubric (defaults keep older callers/tests valid; the model fills them).
    logo_ok: bool = True
    single_focal: bool = True
    strong_typography: bool = True
    cta_prominent: bool = True
    script_wellformed: bool = True
    invented_product: bool = False   # additive (permissive default keeps older tests valid)
    third_party_mark: bool = False
    placement_logical: bool = True   # additive (permissive default keeps older tests valid)
    text_lines: list[str] = []       # every readable string in the image, verbatim
    garbled_text: bool = False       # any malformed / nonsensical / misspelled string in frame
    product_count: int = 0           # distinct products visible
    score: int = 7


def _recompute_pass(*, logo_ok: bool, script_wellformed: bool, invented_product: bool,
                    third_party_mark: bool, score: int, model_pass: bool,
                    has_latin_text: bool = False, cta_clipped: bool = False,
                    on_brand_color: bool = True, candid_violation: bool = False,
                    arabic: bool = True, logo_composited: bool = False) -> bool:
    """PD-1: the verdict is recomputed in CODE, never trusted from the model. `logo_composited`
    drops `logo_ok` from the conjunction for the same reason the prompt does. Latin is a hard
    failure only for Arabic authored copy; an English poster is supposed to contain Latin."""
    ok = (bool(model_pass)
          and not (bool(arabic) and bool(has_latin_text))
          and not bool(cta_clipped)
          and bool(on_brand_color)
          and not bool(candid_violation)
          and bool(script_wellformed)
          and not bool(invented_product)
          and not bool(third_party_mark)
          and int(score or 0) >= 7)
    return ok and (True if logo_composited else bool(logo_ok))


def _recompute_verdict_pass(verdict: Any, *, arabic: bool,
                            logo_composited: bool = False,
                            model_pass: Optional[bool] = None) -> bool:
    """Apply the one canonical conjunction to a structured verdict after any wrapper update."""
    aggregate = getattr(verdict, "overall_pass", None) if model_pass is None else model_pass
    return _recompute_pass(
        has_latin_text=bool(getattr(verdict, "has_latin_text", False)),
        cta_clipped=bool(getattr(verdict, "cta_clipped", False)),
        on_brand_color=bool(getattr(verdict, "on_brand_color", True)),
        candid_violation=bool(getattr(verdict, "candid_violation", False)),
        logo_ok=bool(getattr(verdict, "logo_ok", True)),
        script_wellformed=bool(getattr(verdict, "script_wellformed", True)),
        invented_product=bool(getattr(verdict, "invented_product", False)),
        third_party_mark=bool(getattr(verdict, "third_party_mark", False)),
        score=int(getattr(verdict, "score", 0) or 0),
        model_pass=bool(aggregate),
        arabic=arabic,
        logo_composited=logo_composited,
    )


def _logo_criterion(composited: bool) -> str:
    """What `logo_ok` means. When the REAL asset was composited deterministically, the model-garbles-
    the-logo failure this gate was built for cannot happen — and judging letterforms then rejects the
    brand's own design (MEASURED on Volume Qatar: an intentionally interlocking wordmark read as
    "completely garbled" three renders in a row). It becomes a VISIBILITY check instead."""
    if not composited:
        return ("- logo_ok = true ONLY if the brand logo/lockup is crisp, complete and correctly "
                "spaced with NO garbled/overlapping/duplicated glyphs or stray characters "
                "(HARD GATE).\n")
    return ("- logo_ok = true if the brand mark is VISIBLE and legible against what sits behind it "
            "(enough contrast, not cropped, not covered by other elements). It was composited "
            "pixel-exactly from the brand's OWN file, so judge ONLY its visibility and placement — "
            "NEVER its letterforms: a stylised wordmark with overlapping or interlocking letters is "
            "the brand's design, not damage.\n")


def _pass_criteria(arabic: bool, *, logo_composited: bool = False) -> str:
    """The `overall_pass` conjunction — language-aware.

    MEASURED (Volume Qatar, 2026-07-21): a clean ENGLISH poster (verbatim fidelity 1.0, critic
    score 8/10) was rejected with "The poster uses Latin script, which violates the pass criteria."
    "No Latin in the copy" is correct for an ARABIC brand and wrong for every other one — the
    per-field instruction already knew that; this line did not. A D-8 language-axis leak: a true
    rule applied to a question it does not belong to.
    """
    logo_part = "" if logo_composited else "logo_ok AND "
    if arabic:
        return (f"- overall_pass = true ONLY if {logo_part}script_wellformed AND no Latin in the "
                "copy AND no clipping AND on_brand_color AND no candid violation AND no "
                "invented_product AND no third_party_mark AND score >= 7.\n")
    return (f"- overall_pass = true ONLY if {logo_part}script_wellformed AND no clipping AND "
            "on_brand_color AND no candid violation AND no invented_product AND no "
            "third_party_mark AND score >= 7. This brand's own language is NOT Arabic, so Latin "
            "copy is CORRECT and must never count against the poster.\n")


def poster_vision_qa(
    poster_png: "str | Path | bytes",
    *,
    caller: Any = None,
    brand_dna: Any = None,
    arabic: bool = True,
    expect_real_products: Optional[bool] = None,
    logo_composited: bool = False,
    product_named: bool = False,
    expected_attempt_identity: Optional[PosterAttemptIdentity] = None,
    required_policy: Optional[PosterQAPolicy] = None,
    profile: Optional[dict] = None,
    #: The SAME ResolvedVisualDirection generation consumed. Supplying it is what makes a
    #: blocking brand finding checkable against the constraints the generator was given.
    visual_direction: Any = None,
) -> PosterQAVerdict:
    """Inspect the FINAL rendered Poster and bind the result to its exact immutable bytes."""
    try:
        data = _poster_bytes(poster_png)
    except Exception:
        return PosterQAVerdict(reason="could not read poster image", checked=False)
    policy = required_policy or required_poster_qa_policy(
        arabic=arabic,
        logo_composited=logo_composited,
        expect_real_products=expect_real_products,
        product_named=product_named,
    )
    identity = expected_attempt_identity or poster_attempt_identity(
        data,
        attempt_id=f"standalone:{hashlib.sha256(data).hexdigest()[:16]}",
        candidate_id=f"poster:{hashlib.sha256(data).hexdigest()[:16]}",
    )

    def _unverified(reason: str) -> PosterQAVerdict:
        return PosterQAVerdict(
            reason=reason,
            checked=False,
            qa_source=POSTER_QA_SOURCE,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            policy_context=dict(policy.context),
            mandatory_checks_expected=list(policy.mandatory_checks),
            mandatory_checks_completed=[],
            non_applicable_checks=dict(policy.non_applicable_checks),
            attempt_identity=identity,
        )

    if caller is None:
        return _unverified("no vision caller: QA skipped")

    brand_color = ""
    if brand_dna is not None:
        brand_color = (getattr(brand_dna, "color_usage", "") or "").strip()
    lang_line = (
        "The brand is ARABIC: the headline, chips and CTA must be Arabic. has_latin_text = true "
        "if ANY of the HEADLINE / CHIPS / CTA contain Latin letters. IGNORE the brand logo "
        "(it may contain Latin — that is fine)."
        if arabic else
        "has_latin_text = false unless there is clearly broken/garbled text."
    )
    color_line = (f"The brand's colour language is: {brand_color}. on_brand_color = true only if "
                  "the poster's DOMINANT colour matches that." if brand_color else
                  "on_brand_color = true unless the colours look random/off for a brand poster.")

    system = (
        "You are a WORLD-CLASS advertising ART DIRECTOR reviewing a FINAL rendered poster. "
        "Your bar: 'Would a top brand actually run this?' Be strict — return a structured verdict.\n"
        f"- {lang_line}\n"
        f"{_logo_criterion(logo_composited)}"
        "- cta_clipped = true if the CTA, or any text/chip/logo, is cut off at / outside the frame.\n"
        "- cta_prominent = true if the CTA reads as a confident, high-contrast button (not a "
        "tiny low-contrast footnote).\n"
        "- single_focal = true if there is ONE clear focal point with a clean eye-path to the "
        "headline (false if focal points compete).\n"
        "- strong_typography = true if the headline reads as an intentional display LOCKUP "
        "(deliberate weight/size hierarchy), not a flat default font.\n"
        "- script_wellformed = true ONLY if every rendered copy line is WELL-FORMED for its "
        "script — this is stricter than 'no Latin'. For Arabic: letters must be correctly "
        "SHAPED and CONNECTED (no isolated/disjointed glyphs, no broken ligatures), with correct "
        "dots (ت=2, ث=3) and ة/ه as intended. A poster with NO Latin but broken/garbled/"
        "disconnected Arabic is script_wellformed = FALSE (HARD GATE).\n"
        f"- {color_line}\n"
        "- candid_violation = true if the imagery looks candid/documentary or flat natural "
        "lighting (amateur/stock) rather than a polished brand campaign.\n"
        # PRODUCT AUTHENTICITY (additive, 2026-07-12 — the topshoes invented-sneaker class).
        # FAIL-CLOSED (P6 #9): ask unless we KNOW real product photos were supplied. This was
        # `if expect_real_products is False`, but poster/pipeline.py calls QA without the argument
        # => None => the instruction was OMITTED => invented_product kept its permissive default
        # => the hard gate could never fire, while the conjunction below still named it and
        # overall_pass=True certified the poster clean. So the owner-caught fabricated hero sneaker
        # shipped on every classic poster. None (unknown) must ask; only True may skip.
        + ("- invented_product = true if the poster features a SPECIFIC hero product presented "
           "as this brand's merchandise even though NO real product photo was provided — a "
           "fabricated shoe/bottle/device/dish shown as theirs (HARD GATE: a fake product is a "
           "false claim). Generic out-of-focus background objects are fine.\n"
           if expect_real_products is not True else "")
        # PLACEMENT (2026-07-20): the prompt now carries placement physics; the critic must be
        # able to SEE the failure, otherwise a floating box still ships. Phrased per CATEGORY, not
        # "on a surface" — shoes belong on the ground, a car on a road, a sofa in a room.
        + "- garbled_text = true if ANY text in the frame is malformed: misspelled, nonsensical, "
        "half-formed, or letters that only imitate writing. Judge the GLYPHS you can see. A "
        "correctly-spelled real brand or product name is NOT garbled, however unfamiliar.\n"
        + "- product_count: how many DISTINCT products (different items, not repeats of one) are "
        "visible in the frame. Count 0 if none.\n"
        + "- text_lines: EVERY readable string in the image, VERBATIM, one entry each — the "
        "headline, the subheadline, the button, any chip, any word on packaging, any sign, any "
        "fine print, in whatever script it appears. Transcribe exactly what is drawn, including "
        "misspelled or nonsensical strings; do NOT correct, translate, tidy or omit anything. "
        "This list is compared in code against the copy that was actually authored.\n"
        + "- placement_logical = true if any product shown sits somewhere physically real for a "
        "product OF ITS KIND: supported by something that would truly carry it (counter, shelf, "
        "ground, road, room, hand, worn), at a believable SIZE next to the objects around it, with "
        "a contact shadow and lighting that match the scene. FALSE if it floats/hovers unsupported, "
        "is absurdly large or small, casts no shadow where it touches, or is placed somewhere "
        "nonsensical for that category.\n"
        + "- third_party_mark = true if ANY other company's logo or trademark appears anywhere "
        "in the scene — a swoosh, an apple mark, any recognizable brand symbol on objects or "
        "clothing (HARD GATE).\n"
        "- score = 1..10 overall creative quality as a premium brand poster.\n"
        + _pass_criteria(arabic, logo_composited=logo_composited)
        + "- reason = one short sentence naming the biggest problem (or 'clean')."
    )
    user = "Review this rendered poster as an art director and return the verdict."

    try:
        resp, _u = caller(system, user, _QAResponse, group_name="poster_vision_qa",
                          images=[(data, "image/png")])
    except Exception as exc:  # noqa: BLE001
        return _unverified(f"QA call failed ({type(exc).__name__})")

    fields_set = set(getattr(resp, "model_fields_set", set()) or set())
    response_field = {"critic_pass": "overall_pass"}
    completed = [
        check for check in policy.mandatory_checks
        if response_field.get(check, check) in fields_set
    ]
    # A BRAND-FIDELITY BLOCK MUST NAME A CONSTRAINT THE BRAND ACTUALLY SUPPLIED.
    #
    # MEASURED (run `poster_ebeae26f`, marasimltd.com, 2026-08-31): this check blocked three
    # consecutive renders for violating a "dusty blue" brand colour guideline. No Marasim
    # authority contains one — the DNA handed to this function prescribes "a vibrant, fiery
    # orange for accents" and its `dont_list` forbids cool colours outright. `color_line` gives
    # the model free prose and asks for a boolean, so nothing required the rule it cited to
    # exist; it fused "dusty" (in the prose) with "blue" (present only inside a prohibition that
    # was never sent) and inverted the brand. Three paid background renders were spent on it.
    #
    # Detecting a violation of a SUPPLIED constraint stays blocking. Citing a constraint with no
    # lineage is a defect in the verifier, so it is reported and de-fanged rather than obeyed.
    _on_brand_color = bool(resp.on_brand_color)
    _constraint_lineage: dict = {}
    if not _on_brand_color:
        try:
            from poster.brand_constraints import (build_constraint_set,
                                                  constraint_set_from_direction,
                                                  check_cited_constraint)
            # PARITY (§8): when generation resolved a visual direction, the verifier judges
            # against THAT object and carries its hash — not a second interpretation of the DNA.
            if visual_direction is not None:
                _cs = constraint_set_from_direction(visual_direction)
                _direction_hash = getattr(visual_direction, "resolution_hash", "")
            else:
                _cs = build_constraint_set(brand_dna=brand_dna, profile=profile)
                _direction_hash = ""
            _constraint_lineage = check_cited_constraint(resp.reason or "", _cs)
            _constraint_lineage["direction_hash"] = _direction_hash
            if not _constraint_lineage.get("grounded", True):
                _on_brand_color = True          # not a brand failure: an ungrounded citation
        except Exception:  # noqa: BLE001 — a lineage check must never take down the QA pass
            _constraint_lineage = {}

    verdict = PosterQAVerdict(
        has_latin_text=bool(resp.has_latin_text),
        cta_clipped=bool(resp.cta_clipped),
        on_brand_color=_on_brand_color,
        brand_constraint_lineage=_constraint_lineage,
        candid_violation=bool(resp.candid_violation),
        logo_ok=bool(resp.logo_ok),
        single_focal=bool(resp.single_focal),
        strong_typography=bool(resp.strong_typography),
        cta_prominent=bool(resp.cta_prominent),
        script_wellformed=bool(resp.script_wellformed),
        invented_product=bool(resp.invented_product),
        third_party_mark=bool(resp.third_party_mark),
        placement_logical=bool(resp.placement_logical),
        score=int(resp.score or 0),
        # Preserve the model aggregate long enough for the canonical code-side conjunction below.
        overall_pass=bool(resp.overall_pass),
        critic_pass=bool(resp.overall_pass),
        reason=(resp.reason or "").strip(),
        text_lines=[str(t).strip() for t in (getattr(resp, "text_lines", None) or [])
                    if str(t).strip()],
        garbled_text=bool(getattr(resp, "garbled_text", False)),
        product_count=int(getattr(resp, "product_count", 0) or 0),
        checked=True,
        qa_source=POSTER_QA_SOURCE,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_context=dict(policy.context),
        mandatory_checks_expected=list(policy.mandatory_checks),
        mandatory_checks_completed=completed,
        non_applicable_checks=dict(policy.non_applicable_checks),
        attempt_identity=identity,
    )
    return verdict.model_copy(update={
        "overall_pass": _recompute_verdict_pass(
            verdict, arabic=arabic, logo_composited=logo_composited,
            model_pass=bool(resp.overall_pass)),
    })



def product_count_ok(count: int, *, product_named: bool) -> Optional[bool]:
    """Is this many products right for this ad?

    OWNER (2026-07-26), on an ad for ONE pasted product that came back full of other brands'
    boxes: «إيه المنتجات؟ أنا بعمل لاروش». A named-product ad shows ONE product. A brand-wide ad
    may legitimately show a range. Nothing counted -> unverified, never a pass."""
    n = int(count or 0)
    if n <= 0:
        return None
    if not product_named:
        return True
    return n == 1


def third_party_is_expected(reason: str, *, product_name: str = "") -> bool:
    """Is the "third-party mark" the very product the owner asked us to advertise?

    ROOT (Al Fouad, 2026-07-26). The owner pasted a La Roche-Posay product link; the poster
    correctly showed that product; the QA hard-gate then failed it — "the ad is for a pharmacy
    but is dominated by a third-party product brand" — and the result shipped stamped
    untrustworthy. The rule is right for an INVENTED swoosh on a model's shirt and wrong for a
    retailer advertising the stock it sells. A true statement answering a different question.

    So: when a product was explicitly requested, a mark whose name appears in that product's
    name is EXPECTED, not a violation. Everything else stays a violation, and with no requested
    product nothing is ever excused."""
    name = str(product_name or "").strip().lower()
    text = str(reason or "").lower()
    if not name or not text:
        return False
    # Compare on the product's distinctive words, not the whole string: the verdict says
    # "La Roche-Posay", the product is "La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+".
    stop = {"the", "and", "for", "with", "spf", "ml", "gm", "pack", "size", "new", "kit"}
    words = [w for w in re.split(r"[^\w؀-ۿ]+", name) if len(w) > 2 and w not in stop]
    if not words:
        return False
    hits = sum(1 for w in words if w in text)
    # Two matching words (or one, for a single-word brand) is the brand being named.
    return hits >= min(2, len(words))
