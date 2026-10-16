# -*- coding: utf-8 -*-
"""The influencer card must not tell a user to enable something they already enabled.

`webapp_influencer` renders a Pillow placeholder and stamps it "AI influencer preview · enable
image generation for the final render". That sentence is unconditional: with
BASEERA_ENABLE_PAID_IMAGES=1 already set, the user is told to go and do the thing they did. And the
real reason is different from what the caption implies — there is no influencer image pipeline in
the repo at all, so the flag would change nothing.

This is the SAME defect the sibling module already fixed and documented (webapp_images.py: "The
watermark used to be the only signal, and it told a user who had already set
BASEERA_ENABLE_PAID_IMAGES=1 to go and enable image generation"). This file keeps the two paths
honest in the same way, and adds the machine-readable fields the frontend can act on — the caption
alone is an image, so nothing downstream could ever branch on it."""
from __future__ import annotations

import pytest

from api import webapp_influencer as inf


@pytest.fixture(autouse=True)
def _isolated_generated_output(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))


@pytest.fixture
def brand():
    return {"domain_key": "b_com", "organization_name": "براند",
            "_profile": {"languages": ["ar"]}}


def test_the_patch_declares_itself_a_preview(brand):
    patch = inf.generate_portrait(brand, {"gender": "female"})
    ai = patch["ai_influencer"]
    assert ai.get("is_preview") is True, "nothing downstream can tell this is not a real render"


def test_the_reason_names_the_missing_pipeline_not_the_flag(brand, monkeypatch):
    """With paid images ON, the honest reason is 'there is no such renderer', not 'switch it on'."""
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    ai = inf.generate_portrait(brand, {"gender": "female"})["ai_influencer"]
    reason = str(ai.get("degraded_reason") or "")
    assert reason, "a user with paid images enabled is told nothing about why this is a placeholder"
    assert "enable" not in reason.lower(), f"still asks them to enable it: {reason!r}"


def test_with_paid_images_off_the_caption_may_still_point_at_the_switch(brand, monkeypatch):
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)
    note = inf._note_for(brand)
    assert note.strip()


def test_the_caption_changes_with_the_flag(brand, monkeypatch):
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)
    off = inf._note_for(brand)
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    on = inf._note_for(brand)
    assert off != on, "the same sentence is stamped whether or not generation is enabled"


def test_the_caption_is_in_the_brands_language(brand, monkeypatch):
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    note = inf._note_for(brand)
    assert any("؀" <= ch <= "ۿ" for ch in note), f"an Arabic brand got: {note!r}"


def test_the_poster_step_carries_the_same_disclosure(brand):
    brand = {**brand, "ai_influencer": {"has_portrait": True, "validated": True, "config": {}}}
    ai = inf.generate_influencer_poster(brand, product_hint="x",
                                        scene_description="y")["ai_influencer"]
    assert ai.get("is_preview") is True
