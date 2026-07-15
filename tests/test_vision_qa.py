"""Vision QA gate (build brief Step H). Hermetic: MockCaller only."""
from __future__ import annotations

from poster.vision_qa import _QAResponse, poster_vision_qa
from business_profile.llm.caller import MockCaller


def test_qa_no_caller_is_not_checked_not_a_pass():
    # TRI-STATE (P6): "not verified" must not be spellable as "passed".
    v = poster_vision_qa(b"\x89PNG\r\n\x1a\n" + b"0" * 32, caller=None)
    assert v.checked is False and v.overall_pass is None
    assert not v.overall_pass                                # falsy -> callers fail closed


def test_qa_parses_a_fail_verdict(tmp_path):
    p = tmp_path / "poster.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    resp = _QAResponse(
        has_latin_text=True, cta_clipped=False, on_brand_color=True,
        candid_violation=False, overall_pass=False, reason="Latin in the headline",
    )
    v = poster_vision_qa(str(p), caller=MockCaller({"poster_vision_qa": resp}), arabic=True)
    assert v.checked is True
    assert v.has_latin_text is True and v.overall_pass is False
    assert "latin" in v.reason.lower()


def test_qa_parses_a_pass_verdict(tmp_path):
    p = tmp_path / "poster.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    resp = _QAResponse(
        has_latin_text=False, cta_clipped=False, on_brand_color=True,
        candid_violation=False, overall_pass=True, reason="clean",
    )
    v = poster_vision_qa(str(p), caller=MockCaller({"poster_vision_qa": resp}))
    assert v.checked is True and v.overall_pass is True


def test_malformed_arabic_fails_even_with_no_latin(tmp_path):
    # Batch 2 fix: a poster with NO Latin but broken/disconnected Arabic (script_wellformed=False)
    # must FAIL, even if the model over-optimistically returned overall_pass=True. The character-
    # level OCR gate can't see visual ligature breakage; this vision criterion does.
    p = tmp_path / "poster.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    resp = _QAResponse(
        has_latin_text=False, cta_clipped=False, on_brand_color=True, candid_violation=False,
        script_wellformed=False, overall_pass=True, reason="text looks fine to me",
    )
    v = poster_vision_qa(str(p), caller=MockCaller({"poster_vision_qa": resp}), arabic=True)
    assert v.checked is True
    assert v.script_wellformed is False
    assert v.overall_pass is False       # code-side hard gate overrides the model's optimism


def test_wellformed_script_still_passes(tmp_path):
    p = tmp_path / "poster.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    resp = _QAResponse(
        has_latin_text=False, cta_clipped=False, on_brand_color=True, candid_violation=False,
        script_wellformed=True, overall_pass=True, reason="clean",
    )
    v = poster_vision_qa(str(p), caller=MockCaller({"poster_vision_qa": resp}), arabic=True)
    assert v.overall_pass is True and v.script_wellformed is True
