"""CLI for the business profile builder.

Usage:
    python -m business_profile path/to/manifest.json
    python -m business_profile path/to/manifest.json --no-llm
    python -m business_profile path/to/manifest.json -o profile.json --verbose
"""
from __future__ import annotations
from dotenv import load_dotenv

load_dotenv()

import logging
import os
import sys
from pathlib import Path

# 2026-06 (PR-encoding-fix): force UTF-8 on stdout/stderr before rich
# initializes. Without this, running this CLI as a subprocess with a
# captured pipe on Windows defaults the streams to cp1252, and rich
# crashes with UnicodeEncodeError when printing Arabic taglines, curly
# quotes, or box-drawing chars in the profile-summary table. The
# benchmark runner invokes this module as a subprocess, so the crash
# only surfaced there (a direct console run worked because the console
# was already UTF-8 capable). reconfigure() overrides the stream
# encoding regardless of locale or PYTHONIOENCODING, which is more
# robust than setting the env var alone.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.table import Table

from .build import (
    BuildResult, build_profile, build_profile_no_llm, write_profile,
)
from .llm.caller import OpenAICaller


def _make_caller(provider: str, model: str):
    """Pick the extraction LLM. 'auto' (default) → Gemini on GCP credits when a project
    is configured (gemini-2.5-flash — Gemini 2.0 is NOT provisioned on Vertex), else
    OpenAI gpt-4o-mini. Override with --provider gemini|openai and --model."""
    prov = (provider or "auto").lower()
    if prov == "auto":
        has_google = (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GEMINI_API_KEY")
                      or os.getenv("GOOGLE_API_KEY"))
        prov = "gemini" if has_google else "openai"
    if prov == "gemini":
        from .llm.caller import GeminiCaller
        return GeminiCaller(model=model or "gemini-2.5-flash")
    return OpenAICaller(model=model or "gpt-4o-mini")

app = typer.Typer(add_completion=False,
                   help="Build a business_profile.json from a scrape manifest.")
console = Console()


@app.command()
def main(
    manifest: str = typer.Argument(..., help="Path to manifest.json"),
    output: str = typer.Option(
        None, "--output", "-o",
        help="Output path. Default: <manifest_dir>/business_profile.json",
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm",
        help="Skip the LLM step; use rules-only extractors.",
    ),
    provider: str = typer.Option(
        "auto", "--provider", help="LLM provider: auto | gemini | openai.",
    ),
    model: str = typer.Option(
        "", "--model", help="Model id override (default per provider: "
        "gemini-2.5-flash / gpt-4o-mini).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Build a business_profile.json from a manifest."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    manifest_path = Path(manifest)
    if not manifest_path.exists():
        console.print(f"[red]Manifest not found:[/red] {manifest_path}")
        raise typer.Exit(2)

    out_path = Path(output) if output \
        else manifest_path.parent / "business_profile.json"

    if no_llm:
        console.print(f"[cyan]Building (rules only)[/cyan] {manifest_path}")
        profile = build_profile_no_llm(manifest_path)
        llm_summary = "skipped"
        pack_summary = "n/a"
        diag_summary = "n/a"
    else:
        # CS-92: LLM extraction makes live billed calls. Refuse under a no-spend context
        # (pytest / BASEERA_NO_SPEND); --no-llm is the free rules-only path.
        from spend_tripwire import refuse_if_no_spend
        refuse_if_no_spend("live LLM extraction calls (identity/offerings/positioning/trust)",
                           alternative="run with --no-llm for the rules-only profile")
        caller = _make_caller(provider, model)
        console.print(f"[cyan]Building (rules + {type(caller).__name__})[/cyan] {manifest_path}")
        result: BuildResult = build_profile(
            manifest_path, caller=caller, return_details=True, use_rag=True,
        )
        profile = result.profile
        llm_summary = (
            f"{result.llm_result.total_calls}/4 calls, "
            f"${result.llm_result.total_cost_usd:.4f}, "
            f"in={result.llm_result.total_input_tokens} "
            f"out={result.llm_result.total_output_tokens}"
        )
        pack_summary = (
            f"{result.pack.block_count} blocks "
            f"(from {result.pack.blocks_total_in_manifest} in manifest)"
        )
        diag_summary = (
            f"rejections={result.payload.diagnostics.total_rejections}, "
            f"fields_dropped={len(result.payload.diagnostics.fields_dropped)}, "
            f"items_dropped={result.payload.diagnostics.total_items_dropped}"
        )

    write_profile(profile, out_path)

    # Rasterize an inline-<svg> brand logo (e.g. Vodafone's <use> sprite) from the
    # scrape's saved homepage HTML into the profile, so the poster/reel render the REAL
    # logo instead of a wordmark. Best-effort + gated (a logo that would render blank is
    # rejected -> the wordmark stands); never blocks the build.
    try:
        from scraper.inline_svg_logo import enrich_profile_logo, resolve_raw_homepage
        raw_home = resolve_raw_homepage(manifest_path.parent / "raw")
        if raw_home is not None and enrich_profile_logo(out_path, raw_home):
            console.print("[green]Rasterized inline-svg brand logo into the profile.[/green]")
    except Exception as exc:  # never let logo enrichment break the build
        logging.getLogger(__name__).debug("inline-svg logo enrichment skipped: %s", exc)

    # Summary table
    q = profile.quality
    table = Table(title="Profile Summary", show_header=True, header_style="bold magenta")
    table.add_column("Field"); table.add_column("Value")
    table.add_row("Output", str(out_path))
    table.add_row("Source URL", profile.source_url)
    table.add_row("Name",
                  f"{profile.name.value or '-'} ({profile.name.source_type.value})")
    table.add_row("Category",
                  f"{profile.category.value.value if profile.category.value else '-'} "
                  f"({profile.category.source_type.value})")
    table.add_row("Tagline",
                  (profile.tagline.value or "-")[:80])
    table.add_row("Offerings", str(len(profile.offerings)))
    table.add_row("Audience type",
                  profile.audience_type.value.value if profile.audience_type.value else "-")
    table.add_row("Tone of voice",
                  profile.tone_of_voice.value.value if profile.tone_of_voice.value else "-")
    table.add_row("Value props", str(len(profile.value_propositions)))
    table.add_row("Trust signals", str(len(profile.trust_signals)))
    table.add_row("Service areas", str(len(profile.service_areas)))
    table.add_row("Contact channels",
                  f"phones={len(profile.contact_channels.phones)} "
                  f"emails={len(profile.contact_channels.emails)} "
                  f"wa={len(profile.contact_channels.whatsapp_numbers)} "
                  f"form={profile.contact_channels.has_contact_form}")
    table.add_row("Visual",
                  f"palette={len(profile.visual.palette_hex)} "
                  f"logo={profile.visual.logo_url is not None} "
                  f"primary={profile.visual.primary_color or '-'}")
    table.add_row("Languages", ", ".join(profile.languages) or "-")
    table.add_row("---", "---")
    table.add_row("Fields extracted", str(q.fields_extracted))
    table.add_row("Fields inferred", str(q.fields_inferred))
    table.add_row("Fields missing", str(q.fields_missing))
    table.add_row("Major missing", ", ".join(q.major_missing) or "-")
    table.add_row("Ready for strategy",
                  "[green]YES[/green]" if q.ready_for_strategy else "[red]NO[/red]")
    table.add_row("---", "---")
    table.add_row("Pack", pack_summary)
    table.add_row("LLM", llm_summary)
    table.add_row("Validator", diag_summary)
    console.print(table)

    if profile.quality.ready_for_strategy:
        raise typer.Exit(0)
    raise typer.Exit(2)


if __name__ == "__main__":
    app()