# -*- coding: utf-8 -*-
"""Three measured poster defects the owner asked to fix together.

ALL THREE COME FROM THE SAME TWO RUNS (2026-07-25, Abu Auf and Al Fouad, logs in runs/).

1. THE ONE-SHOT ENGINE IS PREFERRED FOR ARABIC AND CANNOT WRITE ARABIC.

       prefer_oneshot = engine == "oneshot" or (bool(brief.logo_url) and arabic)

   An Arabic brand with a logo goes to the engine that draws its own type — and Abu Auf measured
   `fidelity=0.0` on all three attempts before falling back. The comment above that line explains
   why it was chosen (the classic STYLE background used to bake a garbled Arabic logo) and cites a
   pilot at "Arabic fidelity 5/5"; today's measurement is 0/3. Three wasted image calls (~$0.36)
   and minutes of latency before the path that works even starts.

   The logo problem it was solving is handled elsewhere now — the real mark is composited
   deterministically. Explicit `engine="oneshot"` still tries it, so the capability is not removed,
   only un-preferred for the script it cannot render.

2. A QA RETRY REGENERATES THE IMAGE AND NEVER THE DESIGN. `spec` is computed once, before the
   loop, and every attempt renders with that same spec. Al Fouad:

       attempt 0: "clearly fake, AI-generated products with garbled text"
       attempt 1: "Amateur typography with heavy drop shadows and a weak visual hierarchy"
       attempt 2: "inconsistent and misspelled brand logo ('HARMACIES')"

   The critic named TYPOGRAPHY, and the typography was byte-identical on the next attempt because
   only the background was redrawn. A retry that cannot change what was criticised is not a retry.

3. A POSTER THAT FAILED QA SHIPS WITH NOTHING SAID. `no attempt passed; returning best (score=3)`
   — and `api/webapp_images` carries only `trust_violation`, so a 3/10 render the critic rejected
   reaches the client looking like any other. Same failure class as everything else this session:
   the verdict exists, and stops before the screen.

Hermetic: no image model, no network.
"""
from __future__ import annotations

import inspect

import poster.pipeline as pp


# --- 1) Arabic no longer prefers the engine that cannot write it --------------------------------

def test_an_arabic_brand_with_a_logo_does_not_prefer_oneshot():
    """Checked against the CODE. The comment recording why the old rule was removed quotes it, and
    a test that cannot tell a decision from its record forbids writing the record down — the same
    slip this suite has now made three times, once per comment syntax."""
    src = inspect.getsource(pp)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "bool(brief.logo_url) and arabic" not in code, (
        "Arabic still routes to the one-shot engine, which measured fidelity 0.0 three times")


def test_the_oneshot_engine_is_still_reachable_on_request():
    """Un-preferred, not removed — the capability stays for anyone who asks for it explicitly."""
    src = inspect.getsource(pp)
    assert 'engine == "oneshot"' in src


def test_the_reason_is_written_next_to_the_decision():
    src = inspect.getsource(pp)
    assert "fidelity" in src.lower()


# --- 2) a criticised design is changed, not re-rendered identically ------------------------------

def test_the_pipeline_can_revise_the_design_between_attempts():
    assert hasattr(pp, "_revise_spec_for"), (
        "no way to change the design after the critic names a design problem")


def test_a_typography_complaint_changes_the_headline_treatment():
    from poster.template import PosterBrief, default_design_spec
    brief = PosterBrief(business_name="Example", headline="عنوان", cta_text="اطلب")
    spec = default_design_spec(brief)
    spec = spec.model_copy(update={"headline_treatment": "lockup"})

    class _V:
        overall_pass = False
        reason = "Amateur typography with heavy drop shadows and a weak visual hierarchy."
    revised = pp._revise_spec_for(spec, _V(), attempt=0)
    assert revised.headline_treatment != "lockup", (
        "the critic named the typography and the typography did not change")


def test_a_complaint_about_the_IMAGE_leaves_the_design_alone():
    """"Fake AI products" is an image problem. Reshuffling the layout for it would be noise."""
    from poster.template import PosterBrief, default_design_spec
    brief = PosterBrief(business_name="Example", headline="عنوان", cta_text="اطلب")
    spec = default_design_spec(brief)

    class _V:
        overall_pass = False
        reason = "The poster features clearly fake, AI-generated products."
    revised = pp._revise_spec_for(spec, _V(), attempt=0)
    assert revised.headline_treatment == spec.headline_treatment
    assert revised.layout == spec.layout


def test_a_hierarchy_complaint_also_moves_the_layout():
    from poster.template import PosterBrief, default_design_spec
    brief = PosterBrief(business_name="Example", headline="عنوان", cta_text="اطلب")
    spec = default_design_spec(brief)

    class _V:
        overall_pass = False
        reason = "Weak visual hierarchy; the eye does not know where to land."
    revised = pp._revise_spec_for(spec, _V(), attempt=0)
    assert (revised.layout != spec.layout
            or revised.headline_treatment != spec.headline_treatment)


def test_revision_never_raises_on_a_verdict_it_cannot_read():
    from poster.template import PosterBrief, default_design_spec
    brief = PosterBrief(business_name="Example", headline="عنوان", cta_text="اطلب")
    spec = default_design_spec(brief)
    assert pp._revise_spec_for(spec, None, attempt=0) is not None
    assert pp._revise_spec_for(spec, object(), attempt=1) is not None


# --- 3) shipping a failed render says so ---------------------------------------------------------

def test_a_failed_qa_verdict_reaches_the_client(monkeypatch):
    """`returning best (score=3)` must not look like a finished poster."""
    import api.webapp_images as wi

    class _QA:
        overall_pass = False
        checked = True
        score = 3
        reason = "Amateur typography with heavy drop shadows."

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = _QA()

    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    monkeypatch.setattr(wi, "_render_real",
                        lambda b, **k: wi.RealRender(png=b"\x89PNG" + b"0" * 40, result=_Res()))
    out = wi.generate_image({"domain_key": "x", "_profile": {"name": "X"}}, headline="h")
    assert out.get("qa_failed") is True, "a rejected render shipped indistinguishable from a good one"
    assert "typography" in (out.get("qa_note") or "").lower()


def test_a_passing_render_carries_no_scary_flag(monkeypatch):
    import api.webapp_images as wi

    class _QA:
        overall_pass = True
        checked = True
        score = 9
        reason = "Clean, professional, on-brand."

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = _QA()

    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    monkeypatch.setattr(wi, "_render_real",
                        lambda b, **k: wi.RealRender(png=b"\x89PNG" + b"0" * 40, result=_Res()))
    out = wi.generate_image({"domain_key": "x", "_profile": {"name": "X"}}, headline="h")
    assert not out.get("qa_failed")


def test_an_unchecked_render_is_not_reported_as_failed(monkeypatch):
    """NOT_CHECKED is a third state and already visible as None. It is not a failure."""
    import api.webapp_images as wi

    class _QA:
        overall_pass = None
        checked = False
        score = 0
        reason = ""

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = _QA()

    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    monkeypatch.setattr(wi, "_render_real",
                        lambda b, **k: wi.RealRender(png=b"\x89PNG" + b"0" * 40, result=_Res()))
    out = wi.generate_image({"domain_key": "x", "_profile": {"name": "X"}}, headline="h")
    assert not out.get("qa_failed")


def test_the_frontend_shows_the_rejected_verdict():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "qa_failed" in html, "the critic's rejection has no renderer"
