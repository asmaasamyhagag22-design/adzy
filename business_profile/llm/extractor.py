"""LLM extractor: runs grouped extraction calls and aggregates results.

This module is the LLM layer's main entry point. It:
1. Builds prompts per group using the evidence pack.
2. Calls the caller for each group (4 calls total).
3. Returns raw LLM responses + aggregate usage info.

It does NOT validate evidence or merge into the BusinessProfile.
Those happen in step 5 (validator) and step 6 (merger). Keeping
this module pure orchestration means we can swap callers (real vs
mock) and the rest of the pipeline doesn't care.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .caller import Caller, Usage
from .evidence_pack import EvidencePack
from .prompts import (
    SYSTEM_PROMPT,
    build_identity_prompt,
    build_offerings_prompt,
    build_positioning_prompt,
    build_trust_prompt,
    offerings_cap_for,
)
from .rag import DEFAULT_K, RagRetriever, default_query_for
from .responses import (
    IdentityResponse,
    OfferingsResponse,
    PositioningResponse,
    TrustResponse,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Aggregate result
# ---------------------------------------------------------------------

@dataclass
class GroupResult:
    """One group's outcome — either a parsed response or an error."""
    group: str
    response: Optional[object] = None
    usage: Optional[Usage] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.response is not None and self.error is None


@dataclass
class LLMExtractionResult:
    """All four groups' results, plus aggregate usage."""
    identity: GroupResult
    offerings: GroupResult
    positioning: GroupResult
    trust: GroupResult

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float | None = 0.0
    total_calls: int = 0
    total_failed: int = 0
    model: str = ""
    duration_ms: int = 0

    @property
    def all_groups(self) -> list[GroupResult]:
        return [self.identity, self.offerings, self.positioning, self.trust]


# ---------------------------------------------------------------------
# Group runner
# ---------------------------------------------------------------------

_GROUPS = (
    ("identity", IdentityResponse, build_identity_prompt),
    ("offerings", OfferingsResponse, build_offerings_prompt),
    ("positioning", PositioningResponse, build_positioning_prompt),
    ("trust", TrustResponse, build_trust_prompt),
)


def run_llm_extraction(
    pack: EvidencePack,
    caller: Caller,
    skip_if_empty_pack: bool = True,
    rules_category: Optional[str] = None,
    retriever: Optional[RagRetriever] = None,
) -> LLMExtractionResult:
    """Run all four grouped LLM calls and aggregate the results.

    rules_category: optional. The rules-derived category value (e.g.
    "restaurant", "education") used to inject per-category guidance
    into the offerings prompt. When None, the generic guidance runs.
    The other three group prompts ignore this argument.

    retriever: optional Pillar-2 RAG retriever. When provided, each group's
    prompt is built from the top-K blocks most relevant to THAT group's intent
    (`retriever.retrieve(default_query_for(group), K)`) instead of the shared
    global `pack`. When None, behavior is exactly as before (global pack). The
    retriever degrades to the global pack itself when embeddings are unavailable,
    so passing one never regresses. NOTE: when a retriever is used the caller
    must validate against the retriever's FULL pack (a superset of `pack`), else
    a legitimately-retrieved citation reads as a hallucination.

    If the pack is empty, returns an all-empty result without making
    any API calls (saves cost on broken scrapes).
    """
    import time
    started = time.monotonic()

    # If there's nothing to read, skip LLM entirely
    if skip_if_empty_pack and pack.block_count == 0:
        logger.warning("Evidence pack is empty; skipping LLM extraction")
        empty = lambda name: GroupResult(group=name, error="empty_pack")
        return LLMExtractionResult(
            identity=empty("identity"),
            offerings=empty("offerings"),
            positioning=empty("positioning"),
            trust=empty("trust"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    results: dict[str, GroupResult] = {}
    total_in = 0
    total_out = 0
    total_cost: float | None = 0.0
    total_calls = 0
    total_failed = 0
    model = ""

    for group_name, response_model, prompt_builder in _GROUPS:
        # Pillar 2 RAG: when a retriever is present, each group reads the top-K
        # blocks most relevant to ITS intent; otherwise the shared global pack
        # (today's behavior). An unknown group query -> keep the global pack.
        group_pack = pack
        if retriever is not None:
            query = default_query_for(group_name)
            if query is not None:
                group_pack = retriever.retrieve(query, DEFAULT_K)
        # Offerings prompt is category-aware (v0.2-b1): swappable guidance AND a
        # category-aware item cap (measured — multi-service giants get 30, ecommerce
        # stays 12 to avoid SKU drift). Others take pack only.
        if group_name == "offerings":
            user_prompt = prompt_builder(
                group_pack, rules_category=rules_category,
                max_offerings=offerings_cap_for(rules_category),
            )
        else:
            user_prompt = prompt_builder(group_pack)
        try:
            response, usage = caller(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=response_model,
                group_name=group_name,
            )
            results[group_name] = GroupResult(
                group=group_name, response=response, usage=usage,
            )
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            if total_cost is not None:
                total_cost = None if usage.cost_usd is None else total_cost + usage.cost_usd
            total_calls += 1
            model = usage.model or model
        except Exception as e:
            logger.error("LLM group '%s' failed: %s", group_name, e)
            results[group_name] = GroupResult(
                group=group_name, error=f"{type(e).__name__}: {e}",
            )
            total_failed += 1

    return LLMExtractionResult(
        identity=results["identity"],
        offerings=results["offerings"],
        positioning=results["positioning"],
        trust=results["trust"],
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost_usd=total_cost if not total_failed else None,
        total_calls=total_calls,
        total_failed=total_failed,
        model=model,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
