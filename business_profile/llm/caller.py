"""LLM caller abstraction.

A `Caller` is a callable that takes (system_prompt, user_prompt,
response_schema) and returns (parsed_response, usage_info).

Two implementations:
- OpenAICaller: real API client using client.beta.chat.completions.parse()
- MockCaller: deterministic stub for tests

The interface is deliberately narrow so extractors can be unit-tested
without hitting the network or burning tokens.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=BaseModel)


# ---------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------

@dataclass
class Usage:
    """Token counts and cost from one LLM call.

    `output_tokens` is the VISIBLE completion (candidates); `thoughts_tokens` is the reasoning
    ('thinking') tokens a Gemini 2.5 model spends internally (P-01). Thinking tokens are billed at
    the output rate but are NOT part of candidates_token_count, so `cost_usd` must count both —
    otherwise every cost estimate for a thinking model is understated ~3.5–6x."""
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    model: str = ""
    finish_reason: Optional[str] = None


# Pricing per 1M tokens (input, output). Update if pricing changes.
# Keep this table in sync with whatever models you actually call —
# an unknown model returns a cost of 0.0 AND logs a warning (see below),
# so a silent $0.00 across the whole run is the signal that this table
# is stale, not that the calls were free.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.150, 0.600),       # input, output
    "gpt-4o-mini-2024-07-18": (0.150, 0.600),
    "gpt-4o": (2.500, 10.000),
    # Gemini (approx list price /1M tokens; on Vertex these draw from GCP credits).
    "gemini-2.5-pro": (1.250, 10.000),
    "gemini-2.5-flash": (0.300, 2.500),
    "gemini-2.0-flash": (0.150, 0.600),
    "gemini-1.5-pro": (1.250, 5.000),
    "gemini-1.5-flash": (0.075, 0.300),
    # Mock model used in tests — explicitly zero so tests don't warn.
    "mock-model": (0.0, 0.0),
}

# Track models we've already warned about, so the log isn't spammed with
# one warning per call. One warning per unknown model per process is enough.
_WARNED_UNKNOWN_MODELS: set[str] = set()


def _cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a call. Matches model by prefix to handle date
    suffixes (e.g. 'gpt-4o-mini-2024-07-18' matches 'gpt-4o-mini').

    If the model isn't in the pricing table, returns 0.0 but logs a
    WARNING the first time that model is seen. A silent $0.00 cost
    across an entire run almost always means the pricing table is stale
    (e.g. after switching to a new model), NOT that the calls were free.
    This was a real foot-gun: switching models made every cost report,
    SSE payload, and benchmark cost field read $0 with no indication.
    """
    for key, (in_price, out_price) in _PRICING.items():
        if model.startswith(key):
            return (input_tokens / 1_000_000) * in_price + \
                   (output_tokens / 1_000_000) * out_price

    # Unknown model — warn once, then return 0.0 as a clearly-uncosted value.
    if model not in _WARNED_UNKNOWN_MODELS:
        _WARNED_UNKNOWN_MODELS.add(model)
        logger.warning(
            "No pricing entry for model %r — cost will report $0.00 for all "
            "calls with this model. Add it to _PRICING in caller.py to get "
            "accurate cost tracking. (input=%d output=%d tokens this call)",
            model, input_tokens, output_tokens,
        )
    return 0.0


# ---------------------------------------------------------------------
# Caller protocol
# ---------------------------------------------------------------------

class Caller(Protocol):
    """Callable that runs a single LLM extraction call.

    Returns (parsed_response, usage). Raises on unrecoverable error.
    """
    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[R],
        group_name: str = "",
        images: Optional[list] = None,
    ) -> tuple[R, Usage]:
        ...


# ---------------------------------------------------------------------
# OpenAI implementation
# ---------------------------------------------------------------------

class OpenAICaller:
    """Real OpenAI caller using structured outputs (`beta.chat.completions.parse`).

    Lazily imports the SDK so that tests/mocks don't require `openai`
    to be installed.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        timeout_s: float = 60.0,
        max_retries: int = 1,
        temperature: float = 0.0,
    ):
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.temperature = temperature
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None  # lazy

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise RuntimeError(
                    "The `openai` package is required. Install with: pip install openai"
                ) from e
            self._client = OpenAI(api_key=self._api_key, timeout=self.timeout_s)
        return self._client

    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[R],
        group_name: str = "",
        images: Optional[list] = None,
    ) -> tuple[R, Usage]:
        # `images` is accepted for Caller-protocol parity. OpenAI 4o is vision-capable
        # too, but it's the fallback path here, so we don't wire vision through it yet.
        client = self._get_client()
        started = time.monotonic()
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                # `beta.chat.completions.parse` is OpenAI's strict structured outputs.
                completion = client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=response_model,
                    temperature=self.temperature,
                )
                msg = completion.choices[0].message
                if msg.refusal:
                    raise RuntimeError(f"Model refused: {msg.refusal}")
                parsed = msg.parsed
                if parsed is None:
                    raise RuntimeError("Model returned no parsed object")

                u = completion.usage
                usage = Usage(
                    input_tokens=u.prompt_tokens if u else 0,
                    output_tokens=u.completion_tokens if u else 0,
                    cost_usd=_cost_for(
                        self.model,
                        u.prompt_tokens if u else 0,
                        u.completion_tokens if u else 0,
                    ),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    model=self.model,
                    finish_reason=completion.choices[0].finish_reason,
                )
                logger.info(
                    "LLM call ok group=%s model=%s in=%d out=%d cost=$%.4f ms=%d",
                    group_name, self.model, usage.input_tokens, usage.output_tokens,
                    usage.cost_usd, usage.duration_ms,
                )
                return parsed, usage
            except Exception as e:
                last_err = e
                logger.warning(
                    "LLM call failed (attempt %d/%d) group=%s err=%s",
                    attempt + 1, self.max_retries + 1, group_name, e,
                )
                if attempt < self.max_retries:
                    time.sleep(1.0 + attempt)  # simple backoff

        assert last_err is not None
        raise last_err


# ---------------------------------------------------------------------
# Gemini implementation (google-genai; Vertex mode by default)
# ---------------------------------------------------------------------

class GeminiCaller:
    """Gemini caller via `google-genai`, same Caller protocol as OpenAICaller.

    Uses VERTEX mode by default (no API key needed) — it authenticates with the
    machine's GCP ADC + project, i.e. it draws on GCP credits, which is why it's the
    cheap path when you have a large Vertex/GCP balance vs a tiny OpenAI balance.
    Structured outputs via `response_schema` (a Pydantic model) -> `response.parsed`.
    Lazily imports the SDK and builds the client so tests/mocks need neither.
    """

    def __init__(
        self,
        model: str = "gemini-2.5-pro",
        *,
        use_vertex: bool = True,
        project: Optional[str] = None,
        location: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 60.0,
        temperature: float = 0.0,
        max_retries: int = 1,
    ):
        self.model = model
        # An explicit API key (AI Studio) flips us out of Vertex mode.
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.use_vertex = use_vertex and not self._api_key
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.max_retries = max_retries
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as e:
                raise RuntimeError(
                    "The `google-genai` package is required for GeminiCaller. "
                    "Install with: pip install google-genai"
                ) from e
            if self.use_vertex:
                self._client = genai.Client(
                    vertexai=True, project=self.project, location=self.location
                )
            else:
                self._client = genai.Client(api_key=self._api_key)
        return self._client

    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[R],
        group_name: str = "",
        images: Optional[list] = None,
    ) -> tuple[R, Usage]:
        from google.genai import types

        client = self._get_client()
        started = time.monotonic()
        last_err: Optional[Exception] = None

        # Multimodal: each image is (bytes, mime_type). Gemini 2.5 is natively vision-
        # capable, so it can SEE the brand's real screenshots/photos and reason about
        # the visual identity — far richer than text-only hex/font hints.
        if images:
            contents: list = [
                types.Part.from_bytes(data=img, mime_type=mime)
                for (img, mime) in images
            ]
            contents.append(user_prompt)
        else:
            contents = user_prompt

        for attempt in range(self.max_retries + 1):
            try:
                resp = client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=self.temperature,
                        response_mime_type="application/json",
                        response_schema=response_model,
                    ),
                )
                parsed = getattr(resp, "parsed", None)
                if parsed is None:
                    # Some SDK versions don't auto-parse; validate the JSON text.
                    parsed = response_model.model_validate_json(resp.text or "")
                elif not isinstance(parsed, response_model):
                    # SDK returned a dict — coerce into the Pydantic model.
                    parsed = response_model.model_validate(parsed)

                um = getattr(resp, "usage_metadata", None)
                in_tok = getattr(um, "prompt_token_count", 0) or 0
                out_tok = getattr(um, "candidates_token_count", 0) or 0
                # P-01: thinking/reasoning tokens are billed at the OUTPUT rate but are NOT in
                # candidates_token_count. Count them in cost (both output-rate) or every 2.5-model
                # estimate is understated ~3.5–6x. Surface them separately for telemetry honesty.
                thoughts_tok = getattr(um, "thoughts_token_count", 0) or 0
                # P-74: read the REAL finish reason (MAX_TOKENS / SAFETY / RECITATION), not a
                # hardcoded "stop" — a truncated or safety-blocked completion used to look clean.
                try:
                    _cand0 = (getattr(resp, "candidates", None) or [None])[0]
                    _fr = getattr(_cand0, "finish_reason", None)
                    finish_reason = getattr(_fr, "name", None) or (str(_fr) if _fr is not None else "stop")
                except Exception:  # noqa: BLE001
                    finish_reason = "stop"
                usage = Usage(
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    thoughts_tokens=thoughts_tok,
                    cost_usd=_cost_for(self.model, in_tok, out_tok + thoughts_tok),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    model=self.model,
                    finish_reason=finish_reason,
                )
                logger.info(
                    "Gemini call ok group=%s model=%s in=%d out=%d think=%d cost=$%.4f ms=%d fr=%s",
                    group_name, self.model, in_tok, out_tok, thoughts_tok, usage.cost_usd,
                    usage.duration_ms, finish_reason,
                )
                return parsed, usage
            except Exception as e:
                last_err = e
                logger.warning(
                    "Gemini call failed (attempt %d/%d) group=%s err=%s",
                    attempt + 1, self.max_retries + 1, group_name, e,
                )
                if attempt < self.max_retries:
                    time.sleep(1.0 + attempt)

        assert last_err is not None
        raise last_err


def default_caller(*, strong: bool = True) -> Optional["Caller"]:
    """Return a GEMINI Caller — Gemini 2.5 is the ONLY LLM provider (owner directive:
    no OpenAI; GCP credits dwarf the OpenAI balance).

    `strong=True` -> Gemini 2.5 **Pro** (complex reasoning: design, research, copy);
    `strong=False` -> Gemini 2.5 **Flash** (the cheap, token-heavy default, e.g. profile
    extraction). Override the model with the GEMINI_MODEL env var. Uses Vertex
    (GOOGLE_CLOUD_PROJECT + ADC) or a Gemini API key. Returns None when google-genai or a
    Gemini credential is missing (callers then use their deterministic fallback). Never raises.
    """
    import importlib.util

    try:
        has_genai = bool(importlib.util.find_spec("google.genai"))
        has_creds = bool(
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if has_genai and has_creds:
            model = os.environ.get("GEMINI_MODEL") or (
                "gemini-2.5-pro" if strong else "gemini-2.5-flash"
            )
            return GeminiCaller(model=model)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------
# Mock implementation for tests
# ---------------------------------------------------------------------

@dataclass
class MockCall:
    """Record of one mock invocation."""
    group_name: str
    system_prompt: str
    user_prompt: str
    response_model: type
    response: BaseModel


class MockCaller:
    """Deterministic caller that returns pre-staged responses by group name.

    Usage:
        mock = MockCaller({"identity": staged_identity_response, ...})
        result, usage = mock(sys, user, IdentityResponse, group_name="identity")
        # mock.calls now has the recorded MockCall
    """

    def __init__(
        self,
        responses_by_group: dict[str, BaseModel],
        input_tokens: int = 1000,
        output_tokens: int = 200,
        model: str = "mock-model",
        raise_on_group: Optional[set[str]] = None,
    ):
        self.responses_by_group = responses_by_group
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model
        self.raise_on_group = raise_on_group or set()
        self.calls: list[MockCall] = []

    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[R],
        group_name: str = "",
        images: Optional[list] = None,
    ) -> tuple[R, Usage]:
        if group_name in self.raise_on_group:
            raise RuntimeError(f"MockCaller: simulated failure for group '{group_name}'")

        resp = self.responses_by_group.get(group_name)
        if resp is None:
            raise RuntimeError(f"MockCaller: no staged response for group '{group_name}'")
        if not isinstance(resp, response_model):
            raise TypeError(
                f"MockCaller: staged response for '{group_name}' is "
                f"{type(resp).__name__}, expected {response_model.__name__}"
            )

        self.calls.append(MockCall(
            group_name=group_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=response_model,
            response=resp,
        ))
        usage = Usage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=_cost_for(self.model, self.input_tokens, self.output_tokens),
            duration_ms=10,
            model=self.model,
            finish_reason="stop",
        )
        return resp, usage  # type: ignore[return-value]