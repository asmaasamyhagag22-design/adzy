# -*- coding: utf-8 -*-
"""One motion contract, inherited by every video provider.

BRIEF (owner, 2026-07-27, rule 1): every clip prompt must demand continuous handheld movement and
must forbid the still frame.

MEASURED BEFORE THIS EXISTED: `grep -rn "negative_prompt|cfg_scale|motion_strength" reel/` returned
NOTHING. The Kling request body was `{model_name, prompt, duration, aspect_ratio, image}` — no
negative prompt, no adherence control. Every clip was generated at the provider's defaults with
nothing anywhere forbidding a locked-off camera, which is exactly how an i2v render of a still
becomes a slow push on a photograph: a slideshow.

WHY A MODULE AND NOT A STRING IN THE KLING CLASS. The provider is swappable by design
(`default_video_provider` resolves Kling, Veo, AIML or a stub). A contract written inside one class
is a contract the next provider ships without — the failure mode this project has hit repeatedly.
Adding Runway or Luma means importing from here, or the tests fail.
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

# Kling exposes adherence as `cfg_scale` (0..1) rather than a "motion_strength" scalar; the brief's
# 0.75-0.8 band maps onto it directly. Higher = the prompt (and therefore the motion clause) is
# obeyed harder. Above ~0.85 Kling starts over-cooking faces, so the band is capped deliberately.
MOTION_CFG_SCALE = 0.78

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
