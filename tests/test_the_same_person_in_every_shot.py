# -*- coding: utf-8 -*-
"""The protagonist is one person for the whole film — Google's own mechanism, finally used.

MEASURED on the shipped NTI reel: the woman in shot 1 (curly hair, beige sweater) is not the woman
in shot 4 (ponytail, white shirt). A viewer reads that as two different ads.

BE HONEST ABOUT THE CAUSE, because it was not the one I first named. Three of NTI's four scenes
fell back to REAL stock photos as their seeds —

    [seedgen] scene 1: generated seed unavailable -> REAL photo (legacy blur-contain fallback)
    [seedgen] scene 2: generated seed unavailable -> REAL photo
    [seedgen] scene 3: generated seed unavailable -> REAL photo

— because the seed gate rejected every generated still on TEXT (21 retries across two renders,
every reason a garbled screen or label). Three different stock women were seeded ON PURPOSE. That
is fixed at the seed prompt, not here.

What THIS closes is the second line: even with consistent seeds, Veo re-invents a person from the
prompt on every clip, and nothing was telling it otherwise. Google documents exactly one mechanism
— "Provide reference images of a scene, character, object, or style to maintain a consistent
aesthetic across multiple shots" — and the SDK exposes it as `GenerateVideosConfig.reference_images`
with two kinds, ASSET and STYLE. We use ASSET; STYLE is a look reference, not an identity.

The brand's SELECTED presenter was already being resolved for the seed generator and stopping
there. It now reaches the video call too.
"""
from __future__ import annotations

import inspect

import pytest
from google.genai import types

import reel.compositor as comp
import reel.creative as creative
from reel.video_provider import VeoProvider, _reference_images, _videos_config

JPEG = (b"\xff\xd8\xff\xe0fake", "image/jpeg")


# =====================================================================
# the payload
# =====================================================================

def test_a_reference_is_built_as_an_asset_not_a_style():
    """ASSET means 'this exact person/object'. STYLE would ask Veo to copy the LOOK of the photo,
    which is a different request and would flatten the film's own grade."""
    refs = _reference_images(types, [JPEG])
    assert len(refs) == 1
    assert refs[0].reference_type == types.VideoGenerationReferenceType.ASSET


@pytest.mark.parametrize("refs", [None, [], [(None, "image/jpeg")], [(b"", "image/jpeg")]])
def test_nothing_usable_means_no_payload_at_all(refs):
    """`None` is what the config must see when there is no presenter — an empty list would still
    be sent and could change how the model is called."""
    assert _reference_images(types, refs) is None


def test_one_bad_reference_does_not_cost_the_good_ones():
    assert len(_reference_images(types, [(None, "x"), JPEG]) or []) == 1


# =====================================================================
# the config, and its degrades
# =====================================================================

def test_the_config_carries_the_references():
    c = _videos_config(types, 4, reference_images=_reference_images(types, [JPEG]))
    assert len(getattr(c, "reference_images", None) or []) == 1
    assert getattr(c, "resolution", None) == "1080p", "the quality fields must survive alongside"


def test_without_references_the_config_is_what_it_always_was():
    c = _videos_config(types, 4)
    assert getattr(c, "reference_images", None) is None
    assert getattr(c, "resolution", None) == "1080p"


def test_the_references_ride_the_richest_config_only():
    """An SDK build that does not know the field must lose the REFERENCES, not the render — so
    they appear in the first fallback rung and nowhere below it."""
    src = inspect.getsource(_videos_config)
    assert "_refs = {" in src and "**_refs" in src
    assert src.count("**_refs") == 1, "the references are repeated into a fallback rung"


# =====================================================================
# the request
# =====================================================================

def test_the_prompt_points_at_the_references():
    """Google's guide is explicit that the prompt must refer to them — 'Using the provided images
    for the detective…'. A reference nobody mentions is one the model may ignore."""
    src = inspect.getsource(VeoProvider.generate)
    assert "Using the provided reference image" in src
    assert "same face, hair and wardrobe" in src


def test_a_server_refusal_drops_the_references_and_keeps_the_clip():
    """`_videos_config`'s try/except catches a pydantic constructor error — it cannot see a 400.
    Image-to-video and ingredients-to-video may be mutually exclusive on a given model, and a
    refused request generates nothing, so the retry costs a round-trip and no money."""
    src = inspect.getsource(VeoProvider.generate)
    assert "_submit_with_ref_fallback" in src
    assert "re-submitting without them" in src
    assert "refs=None" in src, "the retry does not actually drop the references"


def test_the_submit_used_is_the_one_with_the_fallback():
    src = inspect.getsource(VeoProvider.generate)
    assert "operation = _submit_with_ref_fallback(image_obj)" in src


# =====================================================================
# wired end to end
# =====================================================================

def test_the_compositor_hands_the_presenter_to_every_shot():
    """One protagonist is a RUN-level fact, so it is set once per scene loop rather than threaded
    through each scene's kwargs."""
    src = inspect.getsource(comp.render_reel)
    assert "identity_refs" in inspect.signature(comp.render_reel).parameters
    assert "provider.identity_refs" in src


def test_the_selected_presenter_reaches_the_video_call():
    """`_face` was resolved for the seed generator and stopped there — the stills had the brand's
    chosen face and the clips invented their own."""
    src = inspect.getsource(creative.render_creative_reel)
    assert "identity_refs=" in src
    at = src.find("identity_refs=")
    assert "_face" in src[at:at + 160], "something other than the selected presenter is being sent"


def test_no_presenter_is_an_honest_none():
    """A brand that never picked a face must send nothing, not an empty list dressed as a choice."""
    src = inspect.getsource(creative.render_creative_reel)
    at = src.find("identity_refs=")
    assert "else None" in src[at:at + 160]
