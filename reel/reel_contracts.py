"""Reel creative contracts — the hard craft bar for the reel director.

Same pattern as `poster/contracts.py`: module-level named `[BRACKET-TAG]` string constants,
imperative rules, "facts gated / form free". Each names a failure class a real creative director
would reject, so the director prevents it BEFORE the plan reaches the owner (the coherence gate in
`reel/coherence.py` then re-checks each in code — the model can't self-pass). Universal: every rule
is business-agnostic; nothing here is keyed to a vertical.
"""
from __future__ import annotations

PRODUCT_VISIBLE_CONTRACT = (
    "[PRODUCT ON SCREEN]\n"
    "SHOW the product/service, don't gesture at it. At least ONE scene is a hero-product moment "
    "(mark hero_product=true): the real product / app / service held, used, or on display, filling "
    "the frame, recognisable on the first watch. Abstract shapes, 'glowing UI', blurred dashboards, "
    "and abstract charts are BANNED as the subject of any scene — they hide the product. Video models "
    "cannot render legible product UI, so when the product IS a screen/app/dashboard, keep the "
    "generated screen abstract AND set product_overlay=\"real_screen\" so the REAL screen is "
    "composited in post. Every other scene keeps the product present or clearly implied."
)

ARRESTING_HOOK_CONTRACT = (
    "[HOOK — WORKS MUTED]\n"
    "Scene 0 must stop the scroll in 2 seconds with the SOUND OFF — it reads on the visual alone: a "
    "striking action, a product macro, or a visible before/after, plus an on_screen_text "
    "(caption_role=\"name\") naming the brand + what it is so a muted viewer gets it instantly. BANNED "
    "opener class (cliché, voice-dependent): a stressed/tired/worried person at a laptop or desk, a "
    "talking head that only works with narration, or a logo on a blank wall. If the idea only works "
    "because of the voice-over, the hook has FAILED."
)

ONE_CTA_CONTRACT = (
    "[ONE CTA]\n"
    "Exactly ONE call to action. The final scene's caption (caption_role=\"cta\") and its voiceover "
    "state the SAME single concrete next step in the same words, and reel.cta carries that same line. "
    "No earlier scene issues a different or competing CTA. A caption that says one action while the "
    "voice says another is a failure. Make it a concrete action, not a slogan."
)

UNIFIED_VOICE_CONTRACT = (
    "[ONE VOICE]\n"
    "Write every spoken line in ONE locked register/dialect for this audience — set reel.dialect and "
    "never drift: if a colloquial dialect, stay colloquial in every line (never jump to formal "
    "mid-reel); if formal, stay formal; if English, keep one register. It is ONE continuous person "
    "speaking a single thought from hook to CTA, not glued taglines. Vary delivery, not dialect."
)

SOUND_DESIGN_CONTRACT = (
    "[SOUND ARC]\n"
    "Author the sound, don't leave it to chance. Set reel.sound_design as a mood PROGRESSION across "
    "the reel (e.g. 'tense minimal -> warm build -> confident resolve'). Give each scene a sound_mood "
    "and, on key story beats, an sfx cue — a whoosh on a hard motion, an impact on the reveal, a "
    "chime on the pay-off. NO scene is dead air: every scene carries voice, music, or an sfx. A scene "
    "may be spoken-silent ONLY when music/sfx clearly carry it."
)

CAPTION_STRATEGY_CONTRACT = (
    "[CAPTIONS — REINFORCE OR ADD]\n"
    "Captions serve the muted viewer and must earn their place. A caption either (a) reinforces the "
    "point for sound-off in DIFFERENT words than the voice, or (b) adds a second layer (a stat, a "
    "name, a proof word) the voice doesn't say. NEVER print the voiceover verbatim as the caption. "
    "Set caption_role per captioned scene: name | benefit | proof | cta | layer. Each caption <=5 "
    "words, readable."
)

BRAND_SPECIFIC_CONTRACT = (
    "[SAY THE ONE THING ONLY THIS BRAND CAN SAY]\n"
    "Use at least one claim TRUE OF THIS BRAND ALONE — from the BRAND-UNIQUE facts in the identity "
    "(differentiators, unique insights, real proof). Put it in the copy (voice or caption) and record "
    "it in reel.brand_unique_claim. A reel that could be any competitor has failed. The claim's facts "
    "must be grounded in the identity — invent nothing."
)

FEASIBILITY_CONTRACT = (
    "[ONE SHOT, ONE MOTION]\n"
    "Each scene is ONE continuous camera move and ONE action a video model can actually render — "
    "never 'quick cuts', 'cut to', a montage, or several actions in one scene (split them into "
    "separate scenes). Keep the protagonist ONE fixed look (same person, wardrobe, hair) across the "
    "reel. If a beat needs a clean morph/transform or precise on-screen graphics, don't ask the video "
    "model for it — mark it (product_overlay / a motion note) for post/motion-graphics."
)

# Joined block composed into the director system prompt, right after CRAFT_CONTRACT.
REEL_CRAFT_CONTRACTS = "\n\n".join((
    PRODUCT_VISIBLE_CONTRACT, ARRESTING_HOOK_CONTRACT, ONE_CTA_CONTRACT, UNIFIED_VOICE_CONTRACT,
    SOUND_DESIGN_CONTRACT, CAPTION_STRATEGY_CONTRACT, BRAND_SPECIFIC_CONTRACT, FEASIBILITY_CONTRACT,
))

# Every [BRACKET-TAG] header, for tests that assert composition into the prompt.
CONTRACT_TAGS = (
    "[PRODUCT ON SCREEN]", "[HOOK — WORKS MUTED]", "[ONE CTA]", "[ONE VOICE]", "[SOUND ARC]",
    "[CAPTIONS — REINFORCE OR ADD]", "[SAY THE ONE THING ONLY THIS BRAND CAN SAY]",
    "[ONE SHOT, ONE MOTION]",
)
