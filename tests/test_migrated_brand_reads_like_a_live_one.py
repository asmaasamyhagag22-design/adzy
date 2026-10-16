# -*- coding: utf-8 -*-
"""A migrated brand must read through the REAL read model exactly as a live one does.

THE TEST THAT WAS MISSING. Every earlier migration test inspected `Run` ROWS — kind, status,
provenance, counts — and every one of them passed while the product was broken. The rehearsal found
it by folding a migrated brand through `api/read_model.py` and looking at what the SPA would
actually render: `swot` null, `has_swot` false, `marketing_strategy` null, `has_strategy` false —
beside `swot_approved: true`, because approval derives from an Approval row and does not care what
the fold produced. An approval with nothing to approve.

Root cause: `plan.py` passed the SWOT and Strategy INNER blocks as `Run.output` while `trends`,
`calendar`, `influencer_poster` and `poster` wrapped theirs under the document key.
`api/read_model.py` merges `run.output` onto the brand document at the TOP LEVEL, so the wrapped
kinds restored their key and these two scattered their fields across the document.

So the assertions here are deliberately at the PRODUCT boundary, not the row boundary: what does a
client see. A row-shaped test cannot fail for this defect, which is exactly why it did not.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy", reason="SQLAlchemy is required for this proof")
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from db.migration_tools.apply import apply_plan  # noqa: E402
from db.migration_tools.assets import stage_assets  # noqa: E402
from db.migration_tools.plan import _stage_output, build_migration_plan  # noqa: E402
from db.test_database_safety import require_disposable_test_database  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")


# =====================================================================
# the normaliser, on its own — no database needed
# =====================================================================

def test_the_canonical_stage_output_matches_the_live_generator_patch():
    """`Run.output` for a live run IS the generator's patch, verbatim.

    `webapp_jobs._persist` hands the patch straight to `finish_enrichment_job`, which stores it as
    `runs.output`. So the canonical shape is not a matter of taste — it is whatever
    `api/webapp_analysis.generate_swot` / `generate_strategy` return, and this asserts the migration
    produces the same KEYS.
    """
    swot_block = {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}
    assert set(_stage_output("swot", swot_block)) == {"swot", "has_swot"}
    assert _stage_output("swot", swot_block)["has_swot"] is True
    assert _stage_output("swot", swot_block)["swot"] == swot_block

    strategy_block = {"goals": [], "tows_matrix": {}}
    assert set(_stage_output("marketing_strategy", strategy_block)) == {
        "marketing_strategy", "has_strategy"}
    assert _stage_output("marketing_strategy", strategy_block)["has_strategy"] is True


def test_a_stage_output_is_wrapped_exactly_once():
    """§3. An already-wrapped source must not become `{"swot": {"swot": …}}`."""
    inner = {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}
    from_inner = _stage_output("swot", inner)
    from_wrapped = _stage_output("swot", {"swot": inner, "has_swot": True})

    assert from_inner == from_wrapped, "wrapping is not idempotent"
    assert "swot" not in from_inner["swot"], f"double-wrapped: {from_inner}"
    assert "marketing_strategy" not in _stage_output(
        "marketing_strategy", {"marketing_strategy": {"goals": []}})["marketing_strategy"]


def test_a_block_that_merely_contains_a_same_named_field_keeps_its_shape():
    """The unwrap is narrow on purpose: only a dict that IS a wrapper is unwrapped."""
    tricky = {"swot": "a field that happens to be called swot", "strengths": []}
    assert _stage_output("swot", tricky)["swot"] == tricky


def test_absent_optional_extras_are_not_invented():
    """`_swot_raw` rides along when the source has it, and stays absent when it does not."""
    inner = {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}
    assert "_swot_raw" not in _stage_output("swot", inner, extras={"_swot_raw": None})
    assert _stage_output("swot", inner, extras={"_swot_raw": {"x": 1}})["_swot_raw"] == {"x": 1}


def test_every_stage_that_owns_a_document_key_goes_through_one_function():
    """The defect was FOUR call sites answering one question two different ways.

    Syntactic, and deliberately so: a future stage added with its own hand-rolled dict is how this
    comes back.
    """
    import ast

    source = (_ROOT / "db" / "migration_tools" / "plan.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    hand_rolled = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "append"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "candidates"):
            continue
        payload = node.args[0] if node.args else None
        if not isinstance(payload, ast.Tuple) or len(payload.elts) != 3:
            continue
        third = payload.elts[2]
        is_wrapped = isinstance(third, ast.Call) and getattr(
            third.func, "id", "") == "_stage_output"
        if not is_wrapped:
            kind = getattr(payload.elts[0], "value", "?")
            hand_rolled.append(kind)
    assert hand_rolled in ([], ["calendar"], ["influencer_poster"],
                           ["calendar", "influencer_poster"],
                           ["influencer_poster", "calendar"]), (
        f"a stage builds its Run.output by hand instead of through _stage_output: {hand_rolled}. "
        "Multi-key outputs (calendar, influencer_poster) are the documented exceptions.")


# =====================================================================
# the real corpus, folded through the real read model
# =====================================================================

pg = pytest.mark.skipif(not _DSN, reason="BASEERA_TEST_DATABASE_URL is required")


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    """The real legacy corpus, migrated into a fresh disposable database."""
    root = tmp_path_factory.mktemp("readmodel-source")
    shutil.copytree(_ROOT / "outputs" / "webapp", root / "webapp", copy_function=shutil.copy2)
    shutil.copytree(_ROOT / "registry", root / "registry", copy_function=shutil.copy2)

    require_disposable_test_database(_DSN)

    proc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(_ROOT),
                          capture_output=True, text=True, timeout=600,
                          env={**os.environ, "PYTHONPATH": str(_ROOT),
                               "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, proc.stderr[-2000:]

    engine = create_engine(_DSN, future=True)
    # ONE truncate, CASCADE: `runs` and `calendar_posts` reference each other, so no delete order
    # exists that satisfies both. The disposable database is emptied for this module because the
    # point of the proof is a migration into a database the migration itself populated.
    tables = ("approvals, usage_records, job_results, job_events, jobs, run_dependencies, "
              "attempts, assets, calendar_posts, runs, profile_snapshots, brand_competitors, "
              "brand_owner_inputs, gallery_entries, gallery_blobs, brands, users")
    with engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")

    plan, _manifest, _quarantine = build_migration_plan(root)
    assets = tmp_path_factory.mktemp("readmodel-assets")
    stage_assets(plan, source_root=root, target_root=assets)
    apply_plan(plan, source_root=root, asset_root=assets, dsn=_DSN,
               quarantine_ack_digest=plan["quarantine_manifest_digest"])
    return plan, root, engine


def _documents(engine):
    """Every migrated brand, folded through the REAL read model, as its owner would see it."""
    from api.read_model import BrandReadModel
    from db.models import Brand
    from db.services import PersistenceService
    from db.uow import UnitOfWork

    service = PersistenceService(lambda: UnitOfWork(lambda: Session(engine, future=True)))
    model = BrandReadModel(service)
    out = {}
    with Session(engine, future=True) as session:
        brands = list(session.scalars(select(Brand)).all())
    for brand in brands:
        viewer = brand.owner_id
        if viewer is None:
            continue
        out[brand.domain_key] = model.document(brand=brand, user_id=viewer)
    return out


def _source_brands(root: Path) -> dict:
    out = {}
    for path in sorted((root / "webapp" / "brands").glob("*.json")):
        if ".bak." in path.name:
            continue
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return out


@pg
def test_a_migrated_brand_with_a_swot_shows_that_swot(migrated):
    plan, root, engine = migrated
    documents = _documents(engine)
    source = _source_brands(root)
    checked = 0
    for domain, legacy in source.items():
        if not (legacy.get("has_swot") and isinstance(legacy.get("swot"), dict)):
            continue
        document = documents.get(domain)
        if document is None:
            continue          # unclaimed brands have no owner to read as; covered separately
        checked += 1
        assert document.get("swot"), f"{domain}: migrated brand shows no SWOT"
        assert document.get("has_swot") is True, f"{domain}: has_swot is not true"
        assert set(document["swot"]) >= {"strengths", "weaknesses", "opportunities", "threats"}, (
            f"{domain}: the SWOT block lost its structure: {sorted(document['swot'])}")
    assert checked >= 3, f"only {checked} brands exercised this"


@pg
def test_a_migrated_brand_with_a_strategy_shows_that_strategy(migrated):
    plan, root, engine = migrated
    documents = _documents(engine)
    source = _source_brands(root)
    checked = 0
    for domain, legacy in source.items():
        if not (legacy.get("has_strategy") and isinstance(legacy.get("marketing_strategy"), dict)):
            continue
        document = documents.get(domain)
        if document is None:
            continue
        checked += 1
        assert document.get("marketing_strategy"), f"{domain}: migrated brand shows no strategy"
        assert document.get("has_strategy") is True, f"{domain}: has_strategy is not true"
    assert checked >= 1, f"only {checked} brands exercised this"


@pg
def test_an_approved_swot_is_never_approved_and_invisible(migrated):
    """THE INVALID STATE, forbidden outright.

    `swot_approved: true` with no visible SWOT is the shape the defect produced, and it is worse
    than either half alone: the gate says a human approved something the product cannot show.
    """
    plan, root, engine = migrated
    documents = _documents(engine)
    source = _source_brands(root)
    for domain, document in documents.items():
        legacy = source.get(domain) or {}
        if not document.get("swot_approved"):
            continue
        assert legacy.get("has_swot"), (
            f"{domain}: approved in PostgreSQL but the frozen source has no SWOT at all")
        assert document.get("swot"), (
            f"{domain}: swot_approved is true and the SWOT is invisible — the source HAS one")
        assert document.get("has_swot") is True, f"{domain}: approved but has_swot is false"


@pg
def test_the_migrated_document_agrees_with_the_legacy_document(migrated):
    """The product-semantic comparison, field by field, against the frozen legacy source."""
    plan, root, engine = migrated
    documents = _documents(engine)
    source = _source_brands(root)
    differences = []
    for domain, legacy in source.items():
        document = documents.get(domain)
        if document is None:
            continue
        for field in ("has_swot", "has_strategy"):
            if bool(legacy.get(field)) != bool(document.get(field)):
                differences.append(f"{domain}.{field}: legacy={legacy.get(field)!r} "
                                   f"postgres={document.get(field)!r}")
        for field in ("swot", "marketing_strategy"):
            if bool(legacy.get(field)) != bool(document.get(field)):
                differences.append(f"{domain}.{field}: legacy={bool(legacy.get(field))} "
                                   f"postgres={bool(document.get(field))}")
    assert not differences, "migrated brands do not read like their legacy originals:\n" + \
        "\n".join(differences)


@pg
def test_the_migrated_swot_block_is_not_double_wrapped(migrated):
    """Belt to the normaliser's braces, measured on the real corpus rather than a fixture."""
    plan, root, engine = migrated
    documents = _documents(engine)
    for domain, document in documents.items():
        swot = document.get("swot")
        if isinstance(swot, dict):
            assert "swot" not in swot, f"{domain}: swot is double-wrapped"
        strategy = document.get("marketing_strategy")
        if isinstance(strategy, dict):
            assert "marketing_strategy" not in strategy, f"{domain}: strategy is double-wrapped"


@pg
def test_the_quarantine_artifact_cannot_be_read_as_dropped_gallery_images(migrated):
    """§9. The operator reads this at the moment they decide whether to continue."""
    plan, _root, _engine = migrated
    summary = plan.get("gallery_summary")
    assert summary, "the plan carries no gallery summary"
    assert summary["gallery_entries_dropped"] == 0
    assert summary["source_logical_entries"] == len(plan["history"])
    assert summary["brand_scoped_assets_created"] + summary["not_brand_scoped_assets"] == \
        summary["source_logical_entries"]

    history_quarantine = [q for q in plan["quarantine"]
                          if q["source_locator"].startswith("webapp/history.json")]
    for entry in history_quarantine:
        assert entry["reason"] == "HISTORY_NOT_A_BRAND_SCOPED_ASSET", entry["reason"]
        assert "GALLERY entry itself still migrates" in entry["safe_explanation"]
    for row in plan["history"]:
        assert row["gallery_disposition"] == "MIGRATED_BY_GALLERY_IMPORT"
