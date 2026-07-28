# -*- coding: utf-8 -*-
"""Two facts the classic poster path had, and never passed to its own art critic.

MEASURED (Nano Care, 2026-07-28). A poster whose product is the brand's OWN photograph, pasted
pixel-exact, and whose logo is the brand's OWN file, inlined by the template, came back:

    "The poster features a fabricated product, uses a distorted logo, and the overall colour
     palette is off-brand."

`invented_product` and `logo_ok` are both HARD GATES in `_recompute_pass`, so that verdict fails
the render outright. Neither question should have been asked:

  * `expect_real_products` — with a real product photo actually in the pixels, "is this
    fabricated?" has a known answer. Left as None the critic is TOLD to look for a fabricated
    hero product (the fail-closed default from the topshoes fix), and on an unusual overhead
    packshot it says yes.
  * `logo_composited` — a model cannot garble a wordmark it never drew. A stylised logo reading
    as "distorted" is the brand's design; this was measured once already on Volume Qatar.

The ONESHOT path has passed both since the topshoes fix. The CLASSIC path — which renders most
posters — passed neither. Produced, then never delivered one step later: the same failure class
this file's neighbours were written for.
"""
from __future__ import annotations

import inspect


def _qa_calls() -> list:
    """Every `poster_vision_qa(...)` invocation in the pipeline, as source windows.

    A window rather than a balanced-paren match on purpose: the arguments nest two deep
    (`bool(getattr(brief, "logo_url", ""))`), and a regex that silently truncates there would
    make this whole file pass for the wrong reason."""
    import poster.pipeline as P
    lines = inspect.getsource(P).splitlines()
    out = []
    for i, line in enumerate(lines):
        if "poster_vision_qa(" in line and "def " not in line and "import" not in line:
            out.append("\n".join(lines[i:i + 12]))
    return out


def _classic_qa_call() -> str:
    """The invocation inside the classic render loop — the one whose verdict is then passed
    through `_excuse_expected_brand`."""
    for call in _qa_calls():
        if "_excuse_expected_brand" in call:
            return call
    raise AssertionError("the classic QA block has moved")


def test_the_classic_path_says_whether_the_product_is_real():
    call = _classic_qa_call()
    assert "expect_real_products=" in call, (
        "the critic is still asked to hunt for a fabricated product on a poster carrying the "
        "brand's own photograph")


def test_the_classic_path_says_the_logo_was_not_drawn():
    call = _classic_qa_call()
    assert "logo_composited=" in call, (
        "the critic still judges the letterforms of a logo the pipeline pasted from a file")


def test_a_composited_product_answers_the_question_yes():
    """The value must follow what reached the PIXELS, not merely that a URL was supplied — a
    composite that ended in mode 'none' really does leave any hero product invented."""
    call = _classic_qa_call()
    assert "_mode_composited" in call or "_real_product_in_frame" in call, (
        "the flag is derived from the input rather than from what was actually composited")


def test_a_hard_gate_still_fires_when_no_product_was_supplied():
    """The topshoes protection must survive: no real photo -> an invented hero product FAILS."""
    from poster.vision_qa import _recompute_pass
    assert _recompute_pass(logo_ok=True, script_wellformed=True, invented_product=True,
                           third_party_mark=False, score=9, model_pass=True) is False


def test_a_composited_logo_exempts_only_the_letterforms():
    """Telling the critic the logo was pasted must not disable the other hard gates."""
    from poster.vision_qa import _recompute_pass
    assert _recompute_pass(logo_ok=False, script_wellformed=True, invented_product=False,
                           third_party_mark=False, score=9, model_pass=True,
                           logo_composited=True) is True
    assert _recompute_pass(logo_ok=True, script_wellformed=False, invented_product=False,
                           third_party_mark=False, score=9, model_pass=True,
                           logo_composited=True) is False
    assert _recompute_pass(logo_ok=True, script_wellformed=True, invented_product=True,
                           third_party_mark=False, score=9, model_pass=True,
                           logo_composited=True) is False


def test_the_invented_product_instruction_is_dropped_only_when_we_know(monkeypatch):
    """End to end on the prompt the critic actually receives."""
    from poster.vision_qa import poster_vision_qa
    seen = {}

    def _caller(system, user, model, group_name=None, images=None):
        seen["system"] = system
        return model(has_latin_text=False, cta_clipped=False, on_brand_color=True,
                     candid_violation=False, overall_pass=True, reason="ok", score=9), {}

    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    poster_vision_qa(png, caller=_caller, expect_real_products=True)
    assert "invented_product = true if" not in seen["system"]

    poster_vision_qa(png, caller=_caller, expect_real_products=None)
    assert "invented_product = true if" in seen["system"], (
        "unknown must keep asking — fail-closed is why the topshoes sneaker was caught")


def test_both_render_paths_now_agree():
    """The oneshot path has always passed these; the two must not drift apart again."""
    calls = _qa_calls()
    assert len(calls) >= 2, f"expected both render paths to call the critic, found {len(calls)}"
    for c in calls:
        assert "expect_real_products=" in c, c[:300]
        assert "logo_composited=" in c, c[:300]
