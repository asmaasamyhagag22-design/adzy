# -*- coding: utf-8 -*-
"""The chosen face must arrive at the model as an IMAGE — and not disguised as a product.

This is the assertion the whole feature rests on. "نانو بانانا مميزة جداً انها تحافظ ع التفاصيل" is
true of this codebase already — `poster/oneshot.py` attaches real product photos and the model keeps
their labels. What was missing was a way for a FACE to use that same channel.

WHY IT CANNOT SIMPLY RIDE ALONG IN `product_images`, all three measured in poster/pipeline.py:
  * `_props_rules` describes every attachment to the model as "REAL PRODUCT PROPS", with the first
    one as the hero product. A face would be composited onto a counter as merchandise.
  * `expect_real_products=bool(product_imgs)` makes the art critic start demanding a real product,
    so a correct influencer poster gets failed by QA.
  * every attachment is sent through `read_rendered_text` — a paid vision call, per face, whose
    output then joins `allowed_lines` and weakens the junk-text gate.
  * and `product_images` is truncated to `[:2]`, so a third attachment vanishes silently.

So identity travels on its own parameter, placed BEFORE the prompt — the ordering
`reel/render3/nano.py` uses, where `refs[0]` is the identity reference. Hermetic: a fake client
captures what would have been sent, so no model is called and nothing is spent."""
from __future__ import annotations

from pathlib import Path

import pytest

from poster.oneshot import generate_oneshot_poster


class _FakeResponse:
    """Shaped like the real one: one candidate part carrying image bytes."""

    def __init__(self):
        png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        # `mime_type` is read by the real reader — the first draft of this fake omitted it and the
        # tests failed on the fixture, not on the code under test.
        blob = type("D", (), {"data": png, "mime_type": "image/png"})()
        part = type("P", (), {"inline_data": blob})()
        content = type("C", (), {"parts": [part]})()
        self.candidates = [type("Cand", (), {"content": content})()]


class _FakeModels:
    def __init__(self, sink):
        self._sink = sink

    def generate_content(self, *, model, contents, config):
        self._sink["model"] = model
        self._sink["contents"] = list(contents)
        return _FakeResponse()


class _FakeClient:
    def __init__(self, sink):
        self.models = _FakeModels(sink)


@pytest.fixture
def sent(tmp_path, monkeypatch):
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "1000")
    sink: dict = {}
    generate_oneshot_poster("a poster prompt", tmp_path / "out.png",
                            identity_ref=(b"FACE-BYTES", "image/png"),
                            client=_FakeClient(sink))
    return sink


def _parts(sink):
    return sink["contents"]


def test_the_face_is_actually_sent_to_the_model(sent):
    blobs = [p for p in _parts(sent) if not isinstance(p, str)]
    assert blobs, "no image part reached the model at all"


def test_the_face_comes_before_the_prompt(sent):
    """`reel/render3/nano.py` puts refs first with refs[0] as the identity — the one ordering in
    this repo that was measured to hold a face across renders."""
    parts = _parts(sent)
    first_text = next(i for i, p in enumerate(parts) if isinstance(p, str))
    assert first_text > 0, f"the prompt came first: {[type(p).__name__ for p in parts]}"


def test_the_face_is_not_counted_as_a_product_image(tmp_path, monkeypatch):
    """The `[:2]` product cap must not be able to swallow the identity."""
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "1000")
    sink: dict = {}
    generate_oneshot_poster("p", tmp_path / "o.png",
                            identity_ref=(b"FACE", "image/png"),
                            product_images=[(b"A", "image/jpeg"), (b"B", "image/jpeg"),
                                            (b"C", "image/jpeg")],
                            client=_FakeClient(sink))
    blobs = [p for p in sink["contents"] if not isinstance(p, str)]
    assert len(blobs) == 3, f"identity + 2 capped products expected, got {len(blobs)}"


def test_without_an_identity_the_call_is_exactly_as_it_was(tmp_path, monkeypatch):
    """The ordinary poster path must not change shape — the prompt still leads."""
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "1000")
    sink: dict = {}
    generate_oneshot_poster("p", tmp_path / "o.png", client=_FakeClient(sink))
    assert isinstance(sink["contents"][0], str)


def test_the_prompt_tells_the_model_the_attachment_is_a_person_not_a_prop():
    """Sending the bytes is half of it: `_props_rules` would otherwise call it merchandise.

    (Signature taken from the real `build_oneshot_prompt`, which takes a `copy` dict — the first
    draft of this test invented flat keyword arguments it does not have.)"""
    from poster.oneshot import build_oneshot_prompt
    prompt = build_oneshot_prompt({"headline": "عنوان", "cta": "اشترِ"},
                                  brand_name="براند", n_identity_refs=1)
    low = prompt.lower()
    assert "person" in low or "presenter" in low
    assert "same person" in low or "identity" in low


def test_a_poster_with_no_identity_never_mentions_a_presenter():
    from poster.oneshot import build_oneshot_prompt
    prompt = build_oneshot_prompt({"headline": "عنوان", "cta": "اشترِ"}, brand_name="براند")
    assert "same person" not in prompt.lower()
