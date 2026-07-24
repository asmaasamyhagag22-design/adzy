"""H8 gate: a timeout-SALVAGED homepage that carries real content must count as a
homepage for readiness — the pipeline must not discard a content-rich scrape.

Regression origin (measured 2026-07-05): `compute_readiness` required a homepage with
`not p.failures`. The timeout-partial-content salvage (built to KEEP a homepage whose
useful DOM loaded before a late asset timed out) attaches a benign TIMEOUT failure, so a
salvaged homepage failed the test -> ready_for_extraction=False -> the whole pipeline
aborted. MEASURED: nahdi (226 homepage text blocks) and te.eg (87) were marked not-ready
and discarded; buffaloburger (0 blocks, empty salvage) correctly stayed not-ready.

A hard-failed homepage (bot wall / empty DOM / network) never becomes a HOMEPAGE
PageRecord (it returns early upstream), so the ONLY failure that can sit on a HOMEPAGE
record is the salvage TIMEOUT — requiring content here is the correct, safe guard.

Hermetic: pure function, no network/LLM.
"""
from types import SimpleNamespace

from scraper.schemas import PageType
from scraper.readiness import _homepage_is_usable


def _page(page_type, *, blocks=(), cleaned=None):
    # Blocks carry real .text now (P-62 measures total visible text, not block COUNT); bare
    # values are wrapped so the fixtures stay terse.
    tb = [b if hasattr(b, "text") else SimpleNamespace(text=str(b)) for b in blocks]
    return SimpleNamespace(page_type=page_type, text_blocks=tb, cleaned_main_text=cleaned)


def test_salvaged_homepage_with_text_blocks_is_usable():
    # nahdi/te.eg shape: HOMEPAGE, real text blocks (well over MIN_RENDERED_CHARS), only a
    # benign salvage failure.
    assert _homepage_is_usable(_page(PageType.HOMEPAGE, blocks=[
        "Andalusia Hospitals — trusted care since 1983",
        "Book an appointment with our specialists today.",
    ]))


def test_homepage_with_only_a_tiny_nav_block_is_not_usable():
    # P-62: a single boilerplate nav word ('Home') is no longer enough — total visible text must
    # clear MIN_RENDERED_CHARS. (An empty shell is already blocked earlier at the fetcher, P-61.)
    assert not _homepage_is_usable(_page(PageType.HOMEPAGE, blocks=["Home"]))


def test_salvaged_homepage_with_cleaned_text_only_is_usable():
    assert _homepage_is_usable(_page(PageType.HOMEPAGE, cleaned="Real homepage prose."))


def test_empty_salvaged_homepage_is_not_usable():
    # buffaloburger shape: HOMEPAGE but 0 blocks / no cleaned text -> stays not-ready.
    assert not _homepage_is_usable(_page(PageType.HOMEPAGE))


def test_non_homepage_page_is_not_a_homepage_signal():
    assert not _homepage_is_usable(_page(PageType.CONTACT, blocks=[1, 2]))
