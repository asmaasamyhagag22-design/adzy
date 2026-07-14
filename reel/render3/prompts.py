"""§2-§4, §6-§8 — the locked blocks and every prompt builder, byte-for-byte.

The pack's design: identity anchor FIRST -> scene in the middle -> consistency lock
repeated LAST. STYLE_BLOCK and CHARACTER_BLOCK are pure functions of the treatment; the
sha256 lock (`locked_hashes` + `assert_locked`) guarantees no code path mutates a block
between the seed calls — the byte-for-byte invariant is checked, not hoped for.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .director import CharacterSheet, ColorScript, ReelTreatment, Shot

# §8 — the single source of truth, reused verbatim in every generation call.
GLOBAL_NEGATIVE = (
    "different person; altered facial features; changed hijab color or pattern; "
    "second protagonist; extra main character; futuristic glowing interface; "
    "holographic UI; sci-fi graphics; gibberish text on screens; text overlay; "
    "caption; subtitle; logo; watermark; Gulf abaya styling; generic Western "
    "office stock look"
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------
# §3 — locked blocks
# ---------------------------------------------------------------------------------

def style_block(cs: ColorScript, act: int) -> str:
    """STYLE_BLOCK — only the act palette line changes."""
    pal = {1: cs.act1, 2: cs.act2, 3: cs.act3}[act]
    k = cs.constants
    return (
        f"STYLE (do not deviate): photorealistic cinematic still, vertical 9:16, 35mm "
        f"film look, {k.lens} lens, {k.dof}, {k.lighting}, {k.grain}. Palette for this act: "
        f"{pal.hex[0]}, {pal.hex[1]} — {pal.grade}. No text overlays, no captions, no "
        f"logos, no watermark."
    )


def _gender_words(ch: CharacterSheet) -> tuple[str, str]:
    """(noun, possessive) for the character's presenting gender. Default (empty) -> woman,
    preserving the original female-only behavior for treatments predating the field."""
    is_man = (ch.presenting_gender or "").strip().lower().startswith("m")
    return ("man", "his") if is_man else ("woman", "her")


def character_block(ch: CharacterSheet, act: int) -> str:
    """CHARACTER_BLOCK — constant except the act outfit slot (act 1-2 -> outfit_act1,
    act 3 -> outfit_act3). Identity anchor + the lock sentence that stops re-sampling."""
    outfit = ch.outfit_act3 if act == 3 else ch.outfit_act1
    noun, poss = _gender_words(ch)
    hijab_or_hair = f"hijab: {ch.hijab}" if ch.hijab else "hair"
    wearing = f"wearing {ch.hijab} and {outfit}" if ch.hijab else f"wearing {outfit}"
    lock_feature = f"{poss} hijab color or pattern" if ch.hijab else f"{poss} hair"
    return (
        f"PROTAGONIST — the SAME person as in the attached reference sheet; identity "
        f"must match it exactly (face, {hijab_or_hair}, build): {ch.name}, {ch.age}, Egyptian "
        f"{noun}, {ch.skin_tone}, {ch.face}; distinctive: {ch.distinctive_features[0]}, "
        f"{ch.distinctive_features[1]}; eyes {ch.eyes}; {wearing}; "
        f"{ch.constant_accessory} visible.\n"
        f"Do not change {poss} face, {lock_feature}, or {poss} identity in any way."
    )


def locked_hashes(t: ReelTreatment) -> dict[str, str]:
    """The sha256 of every locked block — computed ONCE per treatment; every seed call
    asserts against these (assert_locked) so drift is impossible by construction."""
    out: dict[str, str] = {}
    for act in (1, 2, 3):
        out[f"style_act{act}"] = sha256(style_block(t.color_script, act))
        out[f"character_act{act}"] = sha256(character_block(t.character_sheet, act))
    return out


def assert_locked(block: str, expected_hash: str, name: str) -> None:
    """The §3 code-level invariant: `assert sha256(block_in_prompt) == LOCKED_HASH`."""
    got = sha256(block)
    if got != expected_hash:
        raise AssertionError(f"locked block {name!r} drifted: {got[:12]} != {expected_hash[:12]}")


# ---------------------------------------------------------------------------------
# §2 — character sheet prompt (1 Nano Banana call)
# ---------------------------------------------------------------------------------

def character_sheet_prompt(ch: CharacterSheet) -> str:
    noun, poss = _gender_words(ch)
    subj = "He" if noun == "man" else "She"
    hijab_line = (f"{subj} wears a {ch.hijab}." if ch.hijab
                  else f"{poss.capitalize()} hair is styled simply.")
    return (
        "Generate ONE image: a character reference sheet on a plain light-grey studio "
        "background, showing the SAME person in three views side by side — "
        "(1) front portrait, chest-up; (2) three-quarter left portrait; "
        "(3) full body standing.\n\n"
        f"{ch.name}, a {ch.age}-year-old Egyptian {noun}, {ch.skin_tone} skin, {ch.face} face; "
        f"distinctive: {ch.distinctive_features[0]}, {ch.distinctive_features[1]}; "
        f"eyes {ch.eyes}. {hijab_line} "
        f"Wearing {ch.outfit_act1}. Constant accessory: {ch.constant_accessory}.\n\n"
        "Neutral expression, soft even studio lighting, no props, no text, no "
        "watermark. Photorealistic. This sheet is the identity reference for all "
        "subsequent scenes."
    )


# ---------------------------------------------------------------------------------
# §6 — screen rules
# ---------------------------------------------------------------------------------

def expand_screen_rule(rule: str) -> str:
    r = (rule or "NONE").strip()
    # An optional ":detail" suffix on the safe tokens is descriptive flavor —
    # keep it, but the safety clause always rides along (never weakened by it).
    base, _, detail = r.partition(":")
    base, detail = base.strip(), detail.strip()
    if base == "NONE":
        return "No screens visible in frame."
    if base == "OUT_OF_FOCUS":
        return ((detail.rstrip(".") + ". " if detail else "")
                + "The screen is far out of focus (bokeh); a soft glow only, no readable "
                  "or implied content.")
    if base == "ANGLED_AWAY":
        return ((detail.rstrip(".") + ". " if detail else "")
                + "The screen faces away from camera; its content is not visible.")
    if r.startswith("REAL_CONTENT:"):
        desc = r.split(":", 1)[1].strip()
        return (f"The screen displays exactly this mundane, real content: {desc}. "
                f"Rendered plainly and physically plausibly — never glowing, never abstract.")
    return "No screens visible in frame."


def screen_rule_with_screenshot(image_index: int) -> str:
    """§6 strongest form — a real screenshot is attached and composited."""
    return (f"The screen displays EXACTLY the attached screenshot (image {image_index}), "
            f"slightly angled, with natural reflection.")


# ---------------------------------------------------------------------------------
# §4 — per-shot seed prompt (refs attached by the caller)
# ---------------------------------------------------------------------------------

def seed_prompt(t: ReelTreatment, shot: Shot, *, n_shots: int,
                location_photo_attached: bool = False,
                screenshot_index: Optional[int] = None,
                hashes: Optional[dict[str, str]] = None) -> str:
    """The §4 seed prompt: identity anchor first -> scene -> negatives last. When `hashes`
    is given, the locked blocks are hash-asserted (the §3 invariant) before use."""
    cb = character_block(t.character_sheet, shot.act)
    sb = style_block(t.color_script, shot.act)
    if hashes:
        assert_locked(cb, hashes[f"character_act{shot.act}"], f"character_act{shot.act}")
        assert_locked(sb, hashes[f"style_act{shot.act}"], f"style_act{shot.act}")
    screen = (screen_rule_with_screenshot(screenshot_index)
              if screenshot_index is not None else expand_screen_rule(shot.screen_rule))
    _noun, poss = _gender_words(t.character_sheet)          # gender-aware (C1 fix): never hardcode her
    place = ("Place the protagonist inside the attached real location (image 2); preserve the "
             "location's architecture and signage.\n" if location_photo_attached else "")
    return (
        "Using attached image 1 as the identity reference, render the SAME person in a "
        "new scene.\n\n"
        f"{cb}\n\n"
        f"{sb}\n\n"
        f"SCENE {shot.id}/{n_shots} — {shot.location}: {shot.action}. "
        f"Emotion on {poss} face: {shot.emotion}.\n"
        f"Camera: {shot.camera}. Motif: {shot.motif_placement}.\n"
        f"Screen: {screen}\n"
        f"{place}"
        f"\nNEGATIVE: {GLOBAL_NEGATIVE}."
    )


# ---------------------------------------------------------------------------------
# §7 — Veo prompt from a VERIFIED seed
# ---------------------------------------------------------------------------------

def veo_prompt(t: ReelTreatment, shot: Shot) -> str:
    ch = t.character_sheet
    noun, poss = _gender_words(ch)                          # gender-aware (C1 fix)
    hijab_bit = f", {poss} {ch.hijab} hijab" if ch.hijab else ""
    end_bit = f" End the shot on: {shot.match_cut_out}." if shot.match_cut_out.strip() else ""
    return (
        f"Animate this exact frame. Identity and world lock: the {noun}'s face{hijab_bit}, "
        f"{poss} outfit, and the setting must remain exactly as shown; no new person becomes "
        f"the subject; no scene change.\n\n"
        f"Action: {shot.action}. {poss.capitalize()} emotion: {shot.emotion}, visible in "
        f"{poss} face. Camera: {shot.camera}.{end_bit}\n\n"
        f"Audio: ambient room tone only — no dialogue, no music, no narration. "
        f"No on-screen text, captions, or logos."
    )
