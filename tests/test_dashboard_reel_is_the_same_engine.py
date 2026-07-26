# -*- coding: utf-8 -*-
"""ONE reel engine — the dashboard renders what the CLI renders.

MEASURED 2026-07-26, while running the owner's full Al Fouad job:
  * `POST /api/reel/from-profile` called `reel.generate.build_brand_generated_reel`, whose own
    imports are `VeoProvider` + `VertexImagenProvider` + `reel.art_director`. The CLI's default
    path is `reel.creative.render_creative_reel` (director -> gated seeds -> Kling i2v ->
    vocalized VO -> motion contract -> final screening). TWO engines, and the dashboard held the
    one the owner retired: Veo at $0.40/sec, which she replaced with Kling for cost, plus none of
    the v3 fixes she paid for.
  * The Studio's own tab strip labels Video/Campaign "Soon" and neither button has a handler —
    so the working endpoint was unreachable from the product at all («فين الريل؟»).

The rule this file pins: the route must go through the SAME creative entry the CLI uses, and the
Studio must be able to reach it. Hermetic — the engine is stubbed; nothing renders or spends.
"""
from __future__ import annotations

import inspect
from pathlib import Path


def _code_only(module) -> str:
    """The module's CODE with comments AND docstrings removed.

    Recurring trap in this repo (4th time): a test greps for a removed symbol, and the prose
    explaining WHY it was removed satisfies the grep. Comments were already stripped; the
    module docstring naming the retired engine still tripped it."""
    import ast

    src = inspect.getsource(module)
    tree = ast.parse(src)
    doc_lines = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        # `body` is a LIST on Module/ClassDef/FunctionDef, but a single node on If/While/For —
        # only the list-bearing ones can hold a docstring.
        if not isinstance(body, list) or not body or not isinstance(body[0], ast.Expr):
            continue
        val = body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            doc_lines.update(range(val.lineno, (val.end_lineno or val.lineno) + 1))
    return "\n".join(l.split("#")[0] for i, l in enumerate(src.splitlines(), 1)
                     if i not in doc_lines)


def test_the_route_calls_the_creative_director_engine():
    import api.routes.reel as rr
    src = _code_only(rr)
    assert "render_creative_reel" in src, (
        "the dashboard still renders through its own engine instead of the CLI's director")
    assert "build_brand_generated_reel" not in src, (
        "the retired Veo/Imagen engine is still wired into the dashboard route")


def test_the_route_does_not_reach_for_veo():
    """Veo is $0.40/sec and was replaced by Kling (owner, 2026-07-25). The dashboard must pick
    its provider the same way the CLI does — `default_video_provider`, which prefers Kling."""
    import api.routes.reel as rr
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(rr).splitlines())
    assert "VeoProvider" not in src, "the dashboard route can still instantiate Veo directly"
    assert "default_video_provider" in src, "the route does not use the shared provider choice"


def test_the_route_renders_through_the_shared_entry(monkeypatch, tmp_path):
    """Behavioural, not just structural: calling the route must invoke render_creative_reel."""
    import api.routes.reel as rr
    import reel.creative as rc

    calls = {}

    class _Res:
        def __init__(self, p):
            self.reel_path = str(p)
            self.width, self.height, self.duration_s = 1080, 1920, 12.0
            self.has_audio, self.provider = True, "kling"

    class _Creative:
        concept = "one story"
        hook = "hook line"
        scenes = []

    def fake_render(profile, brief, photos, **kw):
        calls["kw"] = kw
        out = Path(kw["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00mp4")
        return _Res(out), _Creative()

    monkeypatch.setattr(rc, "render_creative_reel", fake_render)
    monkeypatch.setattr(rr, "_probe_duration", lambda p: 12.0, raising=False)
    monkeypatch.chdir(tmp_path)

    from reel.schemas import ReelFromProfileRequest
    req = ReelFromProfileRequest(profile={"name": {"value": "Al Fouad"},
                                          "source_url": {"value": "https://alfouad.test/"}},
                                 n_scenes=5, language="ar")
    resp = rr.create_reel_from_profile(req)
    assert calls, "render_creative_reel was never called"
    assert resp.video_base64, "no video came back"
    assert resp.mode in ("creative", "generated"), resp.mode


def test_the_response_carries_the_social_caption():
    """«فوق الريل يبقى مكتوب كلام زي السوشيال» — the poster path already returns one; the reel
    response must too, or the owner has to leave the studio to write it."""
    from reel.schemas import ReelFromProfileResponse
    assert "social_caption" in ReelFromProfileResponse.model_fields, (
        "the reel response has no caption field")


def test_the_studio_can_actually_reach_the_reel():
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    body = "\n".join(l.split("<!--")[0] for l in html.splitlines())
    assert "reel/from-profile" in body or "generateReel" in body, (
        "no way to generate a reel from the Studio — the endpoint is unreachable in the product")
    i = body.find(">Video<")
    assert i > 0, "the Video tab vanished"
    assert "Soon" not in body[i:i + 120], (
        "the Video tab still says Soon while the endpoint is live")


def test_the_studio_does_not_advertise_a_model_it_does_not_use():
    """«كلام مش بستخدمه شيله» — the model card said "Local z-image Turbo"; nothing in this
    project runs z-image (the stack is Gemini/nano-banana + Kling)."""
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    body = "\n".join(l.split("<!--")[0] for l in html.splitlines())
    assert "z-image" not in body.lower(), "the Studio still advertises the z-image model"


def test_a_wrapped_source_url_does_not_crash_the_brief():
    """ROOT of the dashboard reel dropping to Ken Burns (measured 2026-07-26):
    `_extract_program_or_source_url` returned `profile["source_url"]` RAW, but this project's
    profile schema wraps every field as {"value": ..., "source": ...}. PosterBrief.cta_url is a
    str, so a wrapped URL raised ValidationError and killed the whole brief — and with it the
    directed reel — for any profile in its own standard shape."""
    from poster.from_profile import _extract_program_or_source_url
    wrapped = {"source_url": {"value": "https://alfouad.test/"}}
    assert _extract_program_or_source_url(wrapped) == "https://alfouad.test/"
    assert _extract_program_or_source_url({"final_url": {"value": "https://x.test/"}}) == \
        "https://x.test/"
    assert _extract_program_or_source_url({"source_url": "https://plain.test/"}) == \
        "https://plain.test/"
    assert _extract_program_or_source_url({"offerings": [{"page_url": {"value": "https://o.test/p"}}]}) \
        == "https://o.test/p"
    assert _extract_program_or_source_url({}) is None


def test_the_brief_builds_from_a_minimal_wrapped_profile():
    from reel.from_profile import build_reel_brief
    brief = build_reel_brief({"name": {"value": "Al Fouad"},
                              "source_url": {"value": "https://alfouad.test/"}})
    assert brief.business_name == "Al Fouad"
