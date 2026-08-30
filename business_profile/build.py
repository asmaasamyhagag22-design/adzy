"""Full Business Profile pipeline orchestrator.

Steps:
1. Load manifest from disk (or accept a parsed manifest).
2. Run rule-based extractors -> partial BusinessProfile.
3. Build evidence pack from manifest + rules profile.
4. Run grouped LLM extraction (4 calls).
5. Validate every LLM-cited block_id and quote.
6. Merge validated payload into the rules profile.
7. Return final BusinessProfile (and the diagnostics if asked).

Two callers:
- `build_profile(manifest_path, caller)` — full pipeline, returns profile
- `build_profile_no_llm(manifest_path)` — rules only, for offline runs

Typical usage:

    from business_profile.build import build_profile
    from business_profile.llm import default_caller

    profile = build_profile(
        "scrapes/spclinic_net_.../manifest.json",
        caller=default_caller(strong=False),   # Gemini (the live stack)
    )
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from scraper.schemas import ScrapeManifest

from .llm.caller import Caller
from .llm.embeddings import embed_texts
from .llm.evidence_pack import EvidencePack, build_evidence_pack
from .llm.extractor import LLMExtractionResult, run_llm_extraction
from .llm.rag import RagRetriever, build_full_evidence_pack
from .llm.validator import ValidatedPayload, validate_llm_extraction
from .merger import merge_profile
from .rules.orchestrator import apply_rules
from .schemas import BusinessProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Result wrapper (full pipeline detail for debugging)
# ---------------------------------------------------------------------

@dataclass
class BuildResult:
    """Full pipeline output, including intermediates for debugging."""
    profile: BusinessProfile
    pack: EvidencePack
    llm_result: Optional[LLMExtractionResult]
    payload: Optional[ValidatedPayload]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _load_manifest(source: Union[str, Path, ScrapeManifest]) -> tuple[ScrapeManifest, str, str]:
    """Accept either a manifest path or a parsed manifest. Returns
    (manifest, path_string, manifest_hash)."""
    if isinstance(source, ScrapeManifest):
        return source, "<in-memory>", ""
    path = Path(source)
    raw = path.read_text(encoding="utf-8")
    manifest = ScrapeManifest.model_validate_json(raw)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return manifest, str(path), digest


def _diagnostics_from_manifest(manifest: ScrapeManifest):
    """Convert scraper-side ScrapeDiagnostics → business_profile-side
    ScrapeDiagnostics (the input shape for compute_readiness).

    Two different classes intentionally — the scraper-side carries
    additional fields (sitemap_urls_found) that the readiness layer
    doesn't need to know about. Returns None when the manifest carries
    no diagnostics (e.g. legacy manifests written before PR-1).
    """
    src = getattr(manifest, "scrape_diagnostics", None)
    if src is None:
        return None
    # Lazy import — avoids circular import risk
    from .schemas import ScrapeDiagnostics as ProfileScrapeDiagnostics
    return ProfileScrapeDiagnostics(
        pages_discovered=src.pages_discovered,
        pages_scraped=src.pages_scraped,
        pages_with_text_blocks=src.pages_with_text_blocks,
        pages_failed=src.pages_failed,
        sitemap_used=src.sitemap_used,
        js_rendering_used=src.js_rendering_used,
    )


def _attach_catalog(profile, manifest) -> None:
    """The store's real, priced items from the leaf pages the crawl already fetched.

    Deterministic and free — see `scraper.catalog`. Runs on BOTH build paths (rules-only and
    LLM) because it needs no model: a rules-only run of a shop should still know what the shop
    sells. Never raises; a store whose pages do not parse simply gets an empty catalog."""
    try:
        from scraper.catalog import build_catalog

        from .schemas import CatalogItem
        items = build_catalog(manifest)
        profile.catalog = [CatalogItem(**it) for it in items]
    except Exception:  # noqa: BLE001 — an enrichment must never fail a profile build
        logger.exception("catalog extraction failed")


# ---------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------

def build_profile_no_llm(
    source: Union[str, Path, ScrapeManifest],
) -> BusinessProfile:
    """Run only the rule-based extractors. No network calls."""
    manifest, path, digest = _load_manifest(source)
    profile = apply_rules(manifest, manifest_path=path)
    if digest:
        profile.extraction_meta.manifest_hash = digest
    # v0.2-b1: rules-only runs intentionally skip the LLM, so offerings
    # are always empty. Set the note so the frontend can render an
    # honest message (vs. a generic "LLM failed").
    if len(profile.offerings) == 0:
        profile.offerings_extraction_note = "llm_skipped"
    # PR-1: even on the rules-only path, attach readiness so the API
    # consumer sees a consistent shape. Diagnostics are passed through
    # so single-page-evidence detection works here too.
    from .readiness import compute_readiness
    profile.readiness = compute_readiness(
        profile,
        scrape_diagnostics=_diagnostics_from_manifest(manifest),
    )
    _attach_catalog(profile, manifest)
    return profile


def build_profile(
    source: Union[str, Path, ScrapeManifest],
    caller: Caller,
    *,
    return_details: bool = False,
    use_rag: bool = False,
    embed_fn=embed_texts,
) -> Union[BusinessProfile, BuildResult]:
    """Run the full pipeline. Returns the final BusinessProfile.

    If return_details=True, returns a BuildResult that exposes the
    intermediate pack, LLM result, and validated payload — useful for
    debugging and for the CLI's verbose mode.

    use_rag (Pillar 2, OPT-IN, default OFF): when True, each extraction group
    reads the top-K blocks SEMANTICALLY relevant to its intent (retrieved from
    the FULL, uncapped pack) instead of a blind 200-block score-ranked slice —
    so the LLM sees the relevant long tail the count cap used to drop. Embeddings
    run on Gemini text-embedding-004 via `embed_fn`; if they're unavailable the
    retriever transparently degrades to the standard pack (no regression). Default
    OFF keeps every existing unit test hermetic (a MockCaller build never embeds).
    embed_fn is injectable so the RAG path is testable without the network.
    """
    manifest, path, digest = _load_manifest(source)

    # U7 telemetry (D-7): run_id minted here (or adopted from TELEMETRY_RUN_ID so the
    # benchmark runner / studio can stitch subprocess stages into one run record). Each
    # pipeline stage below appends one crash-safe JSONL line to runs/<run_id>/telemetry.jsonl.
    from telemetry import RunTelemetry
    tele = RunTelemetry()

    # 1. Rules
    with tele.stage("rules", input_obj=digest or str(path)) as t:
        rules_profile = apply_rules(manifest, manifest_path=path)
        if digest:
            rules_profile.extraction_meta.manifest_hash = digest
        t["output_obj"] = rules_profile.extraction_meta.manifest_hash or "rules"

    # 2. Pack
    with tele.stage("evidence_pack") as t:
        pack = build_evidence_pack(manifest, rules_profile)
        t["output_obj"] = pack.block_ids
        t["note"] = f"{pack.block_count} blocks"
    logger.info(
        "Evidence pack: %d blocks (from %d in manifest, %d after filter)",
        pack.block_count, pack.blocks_total_in_manifest, pack.blocks_after_filter,
    )

    # 2b. Pillar 2 RAG (opt-in). Build the retriever over the FULL (uncapped)
    # pack; reuse the already-built `pack` as its graceful fallback. The
    # validator must then see the full pack's block_ids (a superset of `pack`)
    # so a legitimately-retrieved citation isn't mistaken for a hallucination.
    retriever: Optional[RagRetriever] = None
    validation_pack = pack
    if use_rag:
        full_pack = build_full_evidence_pack(manifest, rules_profile)
        retriever = RagRetriever(full_pack, pack, embed_fn=embed_fn)
        validation_pack = full_pack
        logger.info(
            "RAG enabled: full pack = %d blocks (vs %d in the capped pack)",
            full_pack.block_count, pack.block_count,
        )

    # 3. LLM extract
    # Pass the rules-derived category (if any) so the offerings prompt
    # can swap in per-category guidance. None → generic guidance.
    rules_category_value = (
        rules_profile.category.value.value
        if rules_profile.category.value is not None
        else None
    )
    with tele.stage("llm_extraction", input_obj=pack.block_ids) as t:
        llm_result = run_llm_extraction(
            pack, caller, rules_category=rules_category_value, retriever=retriever,
        )
        t.update(model=llm_result.model, cost_usd=llm_result.total_cost_usd,
                 tokens_in=llm_result.total_input_tokens,
                 tokens_out=llm_result.total_output_tokens,
                 note=f"{llm_result.total_calls}/4 calls ok")
    logger.info(
        "LLM extraction: %d/4 calls ok, $%.4f, %d input + %d output tokens",
        llm_result.total_calls, llm_result.total_cost_usd,
        llm_result.total_input_tokens, llm_result.total_output_tokens,
    )

    # 4. Validate (against the full pack when RAG is on — superset of `pack`)
    with tele.stage("validate") as t:
        payload = validate_llm_extraction(llm_result, validation_pack)
        t.update(decision=f"rejections={payload.diagnostics.total_rejections}",
                 note=f"dropped_fields={len(payload.diagnostics.fields_dropped)}")
    logger.info(
        "Validation: %d rejections, %d fields dropped, %d items dropped",
        payload.diagnostics.total_rejections,
        len(payload.diagnostics.fields_dropped),
        payload.diagnostics.total_items_dropped,
    )

    # 5. Merge
    with tele.stage("merge") as t:
        final_profile = merge_profile(
            rules_profile, payload, llm_result, expected_llm=True,
            scrape_diagnostics=_diagnostics_from_manifest(manifest),
        )
        if digest:
            final_profile.extraction_meta.manifest_hash = digest
        t["output_obj"] = final_profile.extraction_meta.manifest_hash or "profile"
        t["decision"] = ("ready" if getattr(final_profile.readiness, "ready_for_strategy", False)
                         else "not_ready")

    _attach_catalog(final_profile, manifest)

    if return_details:
        return BuildResult(
            profile=final_profile, pack=validation_pack,
            llm_result=llm_result, payload=payload,
        )
    return final_profile


def write_profile(
    profile: BusinessProfile, output_path: Union[str, Path],
) -> Path:
    """Write the profile as pretty JSON. Returns the path."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        profile.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )
    return p