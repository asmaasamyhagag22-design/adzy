# -*- coding: utf-8 -*-
"""One motion contract, inherited by every video provider.

BRIEF (owner, 2026-07-27, rule 1): every clip prompt must demand continuous handheld movement and
must forbid the still frame.

MEASURED BEFORE THIS EXISTED: `grep -rn "negative_prompt|motion_strength" reel/` returned NOTHING.
Every clip was generated at the provider's defaults with nothing anywhere forbidding a locked-off
camera, which is exactly how an i2v render of a still becomes a slow push on a photograph: a
slideshow.

WHY A MODULE AND NOT A STRING IN A PROVIDER CLASS. The provider is swappable by design
(`default_video_provider` resolves Veo, AIML or a stub). A contract written inside one class is a
contract the next provider ships without — the failure mode this project has hit repeatedly.

AND IT HAPPENED TO THIS FILE (found 2026-07-28). The paragraph above was written as a warning and
was then ignored: the contract was applied in exactly ONE place, inside a provider since removed, and
Veo received neither clause. Every Veo render was therefore generated with nothing forbidding the
still frame — the defect this module exists to prevent, shipping under a module that says it
prevents it. Both halves now reach Veo in `video_provider._videos_config` (negative) and
`VeoProvider.generate` (positive), and `tests/test_reel_motion_and_pacing.py` asserts it on the
request Veo actually receives rather than on the constants.
"""
from __future__ import annotations

MOTION_POSITIVE = (
    "handheld tracking shot, continuous natural human movement, dynamic camera motion, "
    "fluid physical interaction, subtle camera shake, motion blur on movement"
)

# WHAT TO APPEND WHEN THE DIRECTOR ALREADY CHOSE THE CAMERA.
#
# MEASURED 2026-08-06 on a real scene prompt. The director wrote "Medium shot, hand-held. A woman
# in her 30s ... reaches across a pharmacy counter", and `apply_motion_contract` appended
# "handheld tracking shot, dynamic camera motion, subtle camera shake" — the same instruction
# twice. Harmless there, and NOT harmless the moment the director asks for the opposite: a locked
# macro on a label, or a slow dolly-in, gets a demand for a shaking tracking camera stapled to the
# end of its own prompt. Google's guide is explicit that Veo "interprets structure literally" and
# weights what comes first, so a contradicting tail does not cancel — it competes.
#
# The contract's REAL job is to forbid the still frame (see the module docstring: an i2v render of
# a still becoming a slow push on a photograph). That job is done by the NEGATIVE prompt, which
# rides every request regardless. So when a camera is already specified, the positive clause keeps
# only the part that is about LIFE IN THE FRAME and drops the part that is about the camera.
MOTION_POSITIVE_SUBJECT_ONLY = (
    "continuous natural human movement, fluid physical interaction, motion blur on movement"
)

# Camera language a director may have already written. Matched on the prompt's own words rather
# than a flag, because the prompt is what Veo reads — and because the director is TOLD to name a
# shot size and an explicit move (`creative_director._system_prompt`, the AGENCY FORMULA).
_CAMERA_WORDS = (
    "wide shot", "wide establishing", "establishing shot", "medium shot", "close-up", "close up",
    "macro", "extreme close", "tracking shot", "dolly", "push in", "push-in", "pull back",
    "whip-pan", "whip pan", "pan ", "tilt ", "orbit", "crane", "handheld", "hand-held",
    "locked-off", "locked off", "static shot", "over-the-shoulder", "pov ", "aerial",
)

# THE TEXT BAN, IN ONE PLACE, PHRASED THE WAY THE VENDOR SAYS LANDS.
#
# Google's Veo guide is explicit: "describe what you wish to exclude" AFFIRMATIVELY — their own
# example is "a desolate landscape with no buildings or roads" over "no man-made structures". This
# clause used to be five stacked negations living as a local in `compositor.render_reel`, stated a
# SECOND time by `creative._motion_directive`, and hardcoded a fourth time inside four tests that
# split prompts on its exact opening words. One rule, five copies, and changing it broke the tests
# that were watching it.
#
# It is also the most expensive measured failure in the project: three Al Fouad shots all failed
# `no_junk_generated_text`, all retried, all failed the same way — $12.80 for one outcome.
FRAME_IS_UNPRINTED = (
    " Every surface in frame is blank and unprinted: plain walls, unlabelled packaging, clean "
    "glass, bare screens. Signage and printed matter stay outside the frame or fall far out of "
    "focus."
)

MOTION_NEGATIVE = (
    "static camera, tripod shot, locked-off frame, slideshow, still photo, photograph, "
    "ken burns zoom, slow pan over a still image, frozen subject, motionless, lifeless, rigid, "
    "mannequin, freeze frame"
)

# NO ADHERENCE SCALAR. The brief's 0.75-0.8 "motion strength" band was implemented as the old
# provider's
# `cfg_scale`; Veo's `GenerateVideosConfig` has no equivalent field (checked against the SDK, not
# assumed) — its knobs are the prompt and the negative prompt, which is what this module now
# carries. A constant nothing reads is worse than no constant: it reads as a control that exists.

# A single marker, so applying the contract twice is a no-op. Two copies of the modifier list start
# competing with the scene description instead of qualifying it.
_MARK = "handheld tracking shot"


def names_a_camera(prompt: str) -> bool:
    """True when the prompt already specifies a shot size or a camera move."""
    low = str(prompt or "").lower()
    return any(w in low for w in _CAMERA_WORDS)


def apply_motion_contract(prompt: str) -> str:
    """Scene description first, motion modifiers appended once — and never over the director.

    The order matters: a model reads the opening of a prompt as the subject. Leading with camera
    language produces a beautiful shot of nothing in particular.

    When the prompt ALREADY names a shot size or a move, only the subject-motion clause is
    appended. The still-frame ban lives in the negative prompt either way, so nothing is lost —
    what goes is a second, generic camera instruction competing with the specific one the director
    chose.
    """
    text = str(prompt or "").strip()
    if not text:
        return MOTION_POSITIVE
    if _MARK in text.lower() or MOTION_POSITIVE_SUBJECT_ONLY in text:
        return text
    clause = MOTION_POSITIVE_SUBJECT_ONLY if names_a_camera(text) else MOTION_POSITIVE
    sep = " " if text.endswith((".", "!", "?", ",")) else ". "
    return f"{text}{sep}{clause}."
