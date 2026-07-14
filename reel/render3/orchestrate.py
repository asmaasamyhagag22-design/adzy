"""§0 — the Render #3 pipeline orchestrator. Every gate spends before Veo does.

Two entrypoints around the HARD STOP:

  prepare_render3()   Director -> G1 -> character sheet -> HITL #1 package.
                      ZERO seed/Veo spend. The owner approves CONCEPT + PROTAGONIST
                      CARD before anything else runs (HITL law).
  continue_render3()  (after owner approval) seeds w/ refs -> G2 BLOCKING -> Veo
                      per shot from VERIFIED seeds -> assemble -> VO in post ->
                      HITL #2 (present; publish is the owner's).

Code-level invariant (§0, verbatim intent): no Veo request is constructed unless the
G2 verdict for the FULL current seed set is PASS — enforced by run_g2_loop raising
RuntimeError AND re-checked here explicitly.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from .director import ReelTreatment, direct_reel, g1_lint
from .g2 import run_g2_loop
from .nano import generate_with_refs
from .prompts import character_sheet_prompt, locked_hashes, seed_prompt, veo_prompt

logger = logging.getLogger(__name__)

# Cues in an action/camera that a shot must render LEGIBLE in-frame text (badge titles, labels,
# signage) — such shots need the text-capable pro image model, like REAL_CONTENT screens do.
_LEGIBLE_TEXT_CUES = ("legible", "clearly see", "readable", "reads ", "the title")


def _needs_pro_lane(shot) -> bool:
    if shot.screen_rule.startswith("REAL_CONTENT:"):
        return True
    blob = (shot.action + " " + shot.camera).lower()
    return any(cue in blob for cue in _LEGIBLE_TEXT_CUES)


def _resolve_treatment(prep_or_treatment) -> ReelTreatment:
    if isinstance(prep_or_treatment, ReelTreatment):
        return prep_or_treatment
    t = prep_or_treatment.get("treatment")
    if isinstance(t, ReelTreatment):
        return t
    return ReelTreatment.model_validate_json(
        Path(prep_or_treatment["treatment_path"]).read_text(encoding="utf-8"))


def approval_token(prep_or_treatment) -> str:
    """The HITL #1 sign-off token: sha256 of the EXACT treatment the owner approves.

    continue_render3 refuses to seed/animate unless the caller passes this back — so image/Veo
    spend is (a) EXPLICIT: a silent or automated code path that omits it is refused, not run;
    and (b) CONTENT-BOUND: approving concept A never authorizes rendering a mutated concept B,
    because any edit to the treatment changes the token. This turns the HITL law from a comment
    convention into a code checkpoint."""
    t = _resolve_treatment(prep_or_treatment)
    return hashlib.sha256(t.model_dump_json().encode("utf-8")).hexdigest()


def prepare_render3(profile: dict, *, caller: Any, out_dir: "str | Path",
                    n_shots: int = 6, max_g1_retries: int = 3, concept_lock: str = "",
                    generate_card: bool = True, log=print) -> dict:
    """Stage 1 (pre-approval): Director -> G1 -> character sheet. Returns the HITL #1
    package {treatment, treatment_path, sheet_path, hashes} or {'error': ...}. G1
    failures feed CUMULATIVE issues back to the director for up to `max_g1_retries`
    corrective retries (cheap text calls — the HITL law governs image/video spend),
    then fail loud.

    `concept_lock` pins the story to an owner-approved candidate (§5 HITL) — the treatment
    realizes THAT concept and the G1 loop cleans it, never re-inventing a fresh one."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t = direct_reel(profile, caller=caller, n_shots=n_shots, concept_lock=concept_lock)
    if t is None:
        return {"error": "director produced no valid treatment (after retry)"}
    rep = g1_lint(t, profile=profile, n_shots=n_shots)
    seen: list[str] = []
    for attempt in range(max_g1_retries):
        if rep.ok:
            break
        seen = list(dict.fromkeys(seen + rep.issues))       # cumulative, deduped
        log(f"[g1] FAIL (attempt {attempt + 1}): {rep.issues} -> corrective retry")
        t2 = direct_reel(profile, caller=caller, n_shots=n_shots, concept_lock=concept_lock,
                         fix_note="; ".join(seen[:8]))
        if t2 is not None:
            t, rep = t2, g1_lint(t2, profile=profile, n_shots=n_shots)
    if not rep.ok:
        return {"error": f"G1 lint failed after {max_g1_retries + 1} attempts: {rep.issues}"}
    hashes = locked_hashes(t)
    tp = out / "treatment.json"
    tp.write_text(t.model_dump_json(indent=2), encoding="utf-8")
    (out / "locked_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    if not generate_card:
        # §5: after an owner concept-pick, present the CLEAN full treatment (text-only) for
        # approval BEFORE any character card is spent. Card follows on approval.
        log(f"[render3] clean treatment ready for concept approval (text-only): {tp}")
        return {"treatment": t, "treatment_path": str(tp), "hashes": hashes,
                "sheet_path": None, "card_pending": True,
                "approval_token": approval_token(t)}

    sheet_path = out / "character_sheet.png"
    generate_with_refs(character_sheet_prompt(t.character_sheet), sheet_path, refs=None,
                       log=log)
    log(f"[render3] HITL #1 ready — approve the CONCEPT + PROTAGONIST CARD before any "
        f"further spend: {tp} + {sheet_path}")
    return {"treatment": t, "treatment_path": str(tp), "sheet_path": str(sheet_path),
            "hashes": hashes, "approval_token": approval_token(t)}


def render_veo_from_seeds(t: ReelTreatment, seeds: list, *, out_dir: "str | Path",
                          veo_provider: Any = None, log=print) -> list:
    """Veo i2v per shot from ALREADY-G2-VERIFIED seeds (the §0 invariant is enforced upstream,
    where G2 gates the seed set). Returns the per-shot clip paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if veo_provider is None:
        from reel.video_provider import VeoProvider
        veo_provider = VeoProvider()
    clips: list[str] = []
    for i, shot in enumerate(t.shots):
        clip = out / f"clip_{shot.id:02d}.mp4"
        veo_provider.generate(veo_prompt(t, shot), out_path=clip,
                              duration_s=float(shot.duration_s),
                              width=1080, height=1920,
                              reference_image=seeds[i])
        log(f"[render3] shot {shot.id}/{len(t.shots)} animated from VERIFIED seed")
        clips.append(str(clip))
    return clips


def continue_render3(prep: dict, *, caller: Any, out_dir: "str | Path",
                     approval: str = "",
                     location_photos: Optional[dict[str, str]] = None,
                     screenshots: Optional[dict[int, str]] = None,
                     veo_provider: Any = None, max_g2_retries: int = 3,
                     stop_after_g2: bool = False,
                     log=print) -> dict:
    """Stage 2 (POST-approval only — HITL #1 is the owner's). Seeds -> G2 (blocking) ->
    Veo from verified seeds -> per-shot clips. Returns {clips, seeds, g2} — assembly/VO
    ride the existing reel toolchain downstream.

    `approval` MUST equal the HITL #1 token (`prep['approval_token']`, == approval_token(t))
    or this refuses to spend — the HITL law enforced in code, bound to THIS exact treatment
    (H2). Pass it only after the owner signs off on the concept + protagonist card.

    `stop_after_g2=True` returns the G2-VERIFIED seed set (+ verdict) WITHOUT spending on
    Veo — so the caller can show the verified contact sheet before the (expensive) Veo pass,
    then resume with render_veo_from_seeds. The gate is still the code checkpoint; this only
    adds visibility, it never lets Veo fire on an unverified set."""
    t: ReelTreatment = _resolve_treatment(prep)
    expected = approval_token(t)
    if approval != expected:                                  # H2: fail-closed HITL gate
        raise PermissionError(
            "continue_render3 refused: seed/Veo spend requires the owner's HITL #1 approval "
            f"of THIS treatment (expected token {expected[:12]}…, got "
            f"{(approval[:12] + '…') if approval else '<none>'}). Pass "
            "approval=prep['approval_token'] only after sign-off — a mismatch means the "
            "treatment changed since approval, or approval was never given.")
    hashes = prep["hashes"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ref_sheet = Path(prep["sheet_path"]).read_bytes()

    def _refs_for(shot) -> tuple[list, bool, Optional[int]]:
        refs = [(ref_sheet, "image/png")]
        loc_attached = False
        shot_idx: Optional[int] = None
        loc = (location_photos or {}).get(shot.location)
        if loc and Path(loc).is_file():
            refs.append((Path(loc).read_bytes(), "image/jpeg"))
            loc_attached = True
        scr = (screenshots or {}).get(shot.id)
        if scr and Path(scr).is_file():
            refs.append((Path(scr).read_bytes(), "image/png"))
            shot_idx = len(refs)             # 1-based image index in the prompt
        return refs, loc_attached, shot_idx

    def _gen_seed(i: int) -> str:
        shot = t.shots[i]
        refs, loc_attached, scr_idx = _refs_for(shot)
        p = seed_prompt(t, shot, n_shots=len(t.shots),
                        location_photo_attached=loc_attached,
                        screenshot_index=scr_idx, hashes=hashes)
        # Pro lane wherever LEGIBLE in-frame text must render (a REAL_CONTENT screen, or an
        # action/camera that calls for readable text — e.g. a keycard title) and no real
        # screenshot is composited. The pro model is the one that renders text cleanly (§6).
        pro = _needs_pro_lane(shot) and scr_idx is None
        dst = out / f"seed_{shot.id:02d}.png"
        return str(generate_with_refs(p, dst, refs=refs, pro=pro, log=log))

    seeds = [_gen_seed(i) for i in range(len(t.shots))]
    verdict = run_g2_loop(seeds, ref_sheet, regen=_gen_seed, caller=caller,
                          max_retries=max_g2_retries, log=log)
    # §0 code-level invariant — belt AND braces (run_g2_loop already raises on failure).
    if verdict.verdict != "PASS":
        raise RuntimeError(f"G2 FAIL — Veo spend refused: {verdict.model_dump()}")

    if stop_after_g2:
        log("[render3] G2 PASS — stopping before Veo (verified seeds ready for inspection)")
        return {"clips": [], "seeds": seeds, "g2": verdict.model_dump(),
                "treatment": t, "veo_pending": True}

    clips = render_veo_from_seeds(t, seeds, out_dir=out, veo_provider=veo_provider, log=log)
    return {"clips": clips, "seeds": seeds, "g2": verdict.model_dump(),
            "treatment": t}
