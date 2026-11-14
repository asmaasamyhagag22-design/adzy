"""P-01 + P-74: the Gemini cost meter must count reasoning ('thinking') tokens and read the real
finish reason.

P-01: thinking tokens are billed at the OUTPUT rate but are NOT part of candidates_token_count, so
`cost_usd` must include them (they were dropped -> every 2.5-model estimate understated ~3.5-6x).
P-74: finish_reason was hardcoded "stop", so a MAX_TOKENS truncation / SAFETY block looked clean.

Hermetic: a fake google-genai client is injected (no SDK, no network).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from business_profile.llm.caller import GeminiCaller, _cost_for


class _Resp(BaseModel):
    answer: str


class _FinishReason(Enum):
    MAX_TOKENS = 2


class _FakeUsage:
    prompt_token_count = 40000
    candidates_token_count = 6138
    thoughts_token_count = 30000       # the reasoning tokens the old meter ignored


class _FakeCandidate:
    finish_reason = _FinishReason.MAX_TOKENS


class _FakeResponse:
    parsed = _Resp(answer="hi")
    usage_metadata = _FakeUsage()
    candidates = [_FakeCandidate()]
    text = '{"answer": "hi"}'


class _FakeModels:
    def generate_content(self, **_kw):
        return _FakeResponse()


class _FakeClient:
    models = _FakeModels()


def _caller():
    c = GeminiCaller(model="gemini-2.5-pro")
    c._client = _FakeClient()          # inject; bypass real SDK/ADC
    return c


def test_cost_includes_thinking_tokens():
    _parsed, usage = _caller()("sys", "user", _Resp, group_name="t")
    # visible output stays candidates; reasoning surfaced separately
    assert usage.output_tokens == 6138
    assert usage.thoughts_tokens == 30000
    # cost is charged on input + (output + thoughts), not output alone
    expected = _cost_for("gemini-2.5-pro", 40000, 6138 + 30000)
    assert abs(usage.cost_usd - expected) < 1e-9
    # and that is materially higher than the old (thoughts-dropped) figure
    old = _cost_for("gemini-2.5-pro", 40000, 6138)
    assert usage.cost_usd > old * 3     # ~6x more output tokens billed


def test_real_finish_reason_is_read_not_hardcoded_stop():
    _parsed, usage = _caller()("sys", "user", _Resp, group_name="t")
    assert usage.finish_reason == "MAX_TOKENS"     # was always "stop" before P-74


def test_incomplete_provider_usage_retains_known_counts_without_guessing_cost(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(_FakeResponse, "usage_metadata", SimpleNamespace(prompt_token_count=25))
    parsed, usage = _caller()("sys", "user", _Resp, group_name="partial")
    assert parsed.answer == "hi"
    assert usage.input_tokens == 25
    assert usage.usage_available is False and usage.cost_usd is None


def test_completion_after_ambiguous_retry_keeps_total_unknown(monkeypatch):
    attempts = []

    def generate(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise TimeoutError("response lost")
        return _FakeResponse()

    caller = _caller()
    caller.max_retries = 1
    monkeypatch.setattr(caller._client.models, "generate_content", generate)
    monkeypatch.setattr("business_profile.llm.caller.time.sleep", lambda _: None)
    parsed, usage = caller("sys", "user", _Resp, group_name="ambiguous")
    assert len(attempts) == 2 and parsed.answer == "hi"
    assert usage.input_tokens == 40000 and usage.usage_available is True
    assert usage.cost_usd is None
