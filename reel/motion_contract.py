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


def apply_motion_contract(prompt: str) -> str:
    """Scene description first, motion modifiers appended once.

    The order matters: a model reads the opening of a prompt as the subject. Leading with camera
    language produces a beautiful shot of nothing in particular.
    """
    text = str(prompt or "").strip()
    if not text:
        return MOTION_POSITIVE
    if _MARK in text.lower():
        return text
    sep = " " if text.endswith((".", "!", "?", ",")) else ". "
    return f"{text}{sep}{MOTION_POSITIVE}."
