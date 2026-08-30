"""CLI: `python -m scraper <url> [--output-dir DIR]`."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Load .env at the CLI entry point so optional features that read keys (e.g. the
# Deep Search fallback's SERPER_API_KEY) work from `python -m scraper`. The API
# loads .env on its own; library code never loads it (keeps helpers side-effect free).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

import typer
from rich.console import Console
from rich.table import Table

from .crawler import scrape

app = typer.Typer(add_completion=False, help="Scrape a business website into a structured manifest.")
console = Console()


@app.command()
def main(
    url: str = typer.Argument(..., help="Business website URL (https:// optional)"),
    # "" = whatever `scraper.config.scrapes_root()` resolves (BASEERA_SCRAPES_DIR, else "scrapes").
    # A literal here would freeze the default before `.env` is read and quietly split the corpus.
    output_dir: str = typer.Option("", "--output-dir", "-o", help="Root directory for scrape outputs"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
):
    """Scrape one URL."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    console.print(f"[bold cyan]Scraping[/bold cyan] {url}")
    manifest, out_path = scrape(url, output_dir)

    # Summary table
    table = Table(title="Scrape Summary", show_header=True, header_style="bold magenta")
    table.add_column("Field")
    table.add_column("Value")

    table.add_row("Output dir", str(out_path))
    table.add_row("Final URL", manifest.scrape_meta.final_url or "(none)")
    table.add_row("Pages attempted", str(manifest.scrape_meta.pages_attempted))
    table.add_row("Pages succeeded", str(manifest.scrape_meta.pages_succeeded))
    table.add_row("Duration (ms)", str(manifest.scrape_meta.duration_ms))
    table.add_row("Budget exceeded", str(manifest.scrape_meta.budget_exceeded))
    table.add_row("Robots allowed", str(manifest.robots.allowed))
    table.add_row("Languages",
                  ", ".join(f"{e.code}({e.proportion:.0%})" for e in manifest.languages) or "(none)")
    table.add_row("Phones", str(len(manifest.contact.phones)))
    table.add_row("Emails", str(len(manifest.contact.emails)))
    table.add_row("WhatsApp", str(len(manifest.contact.whatsapp)))
    table.add_row("Social links", str(len(manifest.links.social)))
    table.add_row("CTA candidates", str(len(manifest.links.cta_candidates)))
    table.add_row("Forms", str(sum(len(p.forms) for p in manifest.pages)))
    table.add_row("Text blocks", str(sum(len(p.text_blocks) for p in manifest.pages)))
    table.add_row("Color palette", str(len(manifest.visual.color_palette)))
    table.add_row("Logo found", str(manifest.visual.logo is not None))
    table.add_row("Failures", str(len(manifest.failures)))
    table.add_row("Ready for extraction", str(manifest.readiness.ready_for_extraction))
    if manifest.readiness.major_missing:
        table.add_row("Missing", ", ".join(manifest.readiness.major_missing))

    console.print(table)

    if manifest.failures:
        console.print("\n[bold red]Failures:[/bold red]")
        for f in manifest.failures:
            console.print(f"  - {f.code.value}: {f.message} ({f.page_url or '-'})")

    console.print(f"\n[green]Manifest written:[/green] {out_path / 'manifest.json'}")

    # Exit code reflects readiness
    sys.exit(0 if manifest.readiness.ready_for_extraction else 2)


if __name__ == "__main__":
    app()
