"""The catalog has to survive the DASHBOARD pipeline, not only the CLI one.

MEASURED 2026-08-02 on a fresh Al Fouad scrape started from the dashboard: the crawl reached 42
pages, 40 of them product pages, every one with its `<h1>` intact, and `build_catalog` on that very
manifest returns 24 priced items WITH images. The stored brand said `catalog: []`.

Nothing failed and nothing was logged. `business_profile.build` attaches the catalog on both of ITS
paths; `api/jobs/runner.py` is a THIRD pipeline — `apply_rules` -> LLM -> `merge_profile` -> store —
and it never called that step. The CLI knew what the shop sells and the dashboard did not: a step
wired into one caller and forgotten in the other, which is the defect class this repo repeats.

The fix is at ONE point, and these tests are what say so: `merge_profile` begins with
`deepcopy(rules_profile)`, so attaching before the LLM covers the merged path AND the skip_llm path
without a second call site to keep in step.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import api.jobs.runner as runner


def test_the_runner_attaches_the_catalog():
    src = inspect.getsource(runner.run_pipeline_job)
    assert "_attach_catalog(rules_profile, manifest)" in src, (
        "the dashboard pipeline builds a profile without a catalog"
    )


def test_it_is_attached_before_the_llm_branch_so_one_call_covers_both():
    """If it were attached after the `skip_llm` early-return, a rules-only run — which is what a
    brand gets whenever the LLM is unavailable — would still store an empty catalog."""
    src = inspect.getsource(runner.run_pipeline_job)
    assert src.index("_attach_catalog(rules_profile, manifest)") < src.index("if skip_llm:")


def test_the_merge_preserves_whatever_the_rules_profile_carried():
    """The single call site is only correct because of this. If `merge_profile` ever stopped
    deep-copying, the catalog would vanish again on the LLM path only — the hardest kind to spot."""
    from business_profile.merger import merge_profile
    assert "deepcopy(rules_profile)" in inspect.getsource(merge_profile)


def test_attaching_never_raises_on_a_manifest_it_cannot_read():
    """A catalog is an enrichment. A shop whose pages do not parse gets an empty one; it must not
    cost the brand its whole profile."""
    from business_profile.build import _attach_catalog
    prof = SimpleNamespace(catalog=["untouched"])
    _attach_catalog(prof, SimpleNamespace())          # no pages, no images
    assert prof.catalog == []
    _attach_catalog(prof, None)                       # not a manifest at all
    assert prof.catalog == []
