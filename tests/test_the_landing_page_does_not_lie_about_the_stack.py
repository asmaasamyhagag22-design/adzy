# -*- coding: utf-8 -*-
r"""The front door of a demo must not describe a different product.

MEASURED 2026-07-26, on `/` — the first screen anyone sees:

    landing.html:487  "Powered by a local LLM and the z-image turbo diffusion model"
    landing.html:605  "Made with local AI - Ollama · z-image turbo"
    landing.html:684  "Campaign posters rendered by z-image turbo on local GPU."

None of it is true. Text runs on Gemini, images on the Vertex image models, video on Kling
(`reel/video_provider.py:750`), voice on ElevenLabs. There is no Ollama, no z-image turbo and no
local GPU anywhere in the repo — `grep -ri "ollama\|z-image" --include=*.py` returns nothing.

This is the same failure class as the rest of the audit work, pointed outward: a true-sounding
statement that answers a question nobody asked of a system nobody built. A prospective client
reading it and then asking about local deployment gets a very bad answer live.

The test does not police marketing tone. It only forbids naming a technology the code does not use.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Technologies the UI has claimed and the repo has never contained. Add to this list, never remove
# from it: the point is that a claim must be re-earned by the code, not inherited from a template.
FICTIONAL = ("ollama", "z-image", "local gpu", "local llm", "stable diffusion", "midjourney")


@pytest.mark.parametrize("page", ["landing.html", "index.html"])
def test_no_page_names_a_technology_the_project_does_not_use(page):
    text = (_ROOT / "frontend" / page).read_text(encoding="utf-8", errors="replace").lower()
    named = [t for t in FICTIONAL if t in text]
    assert not named, (
        f"{page} advertises {named} — nothing in the repo implements any of them; "
        "a demo viewer who asks about it gets an answer that contradicts the product")


def test_the_claim_is_absent_from_the_code_too():
    """Guard the premise: if one of these ever becomes real, this test must be updated
    deliberately rather than silently satisfied by a stale UI string."""
    hits = []
    for p in _ROOT.rglob("*.py"):
        parts = set(p.parts)
        # `tests` is excluded on purpose: this file and the engine-guard test both NAME the
        # fictional technologies in order to forbid them. The guard is about product code.
        if parts & {".git", "node_modules", "scrapes", "outputs", ".claude", "__pycache__",
                    "tests"}:
            continue
        low = p.read_text(encoding="utf-8", errors="replace").lower()
        hits += [f"{p.name}:{t}" for t in ("ollama", "z-image") if t in low]
    assert not hits, f"the code now references {hits} — revisit the landing copy deliberately"
