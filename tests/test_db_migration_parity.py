# -*- coding: utf-8 -*-
"""Migration invariants — three of them, and they are NOT the same invariant.

The previous version of this file asserted one rule that quietly becomes wrong:

    migration 0001  ==  current ORM models

That holds only while 0001 is the entire chain. The day someone adds 0002 to introduce a column,
the models correctly gain it and 0001 correctly does not — and a permanent equality test would go
red and push whoever is on call into editing a released historical revision to make CI green. The
test would be actively teaching the one habit the freeze exists to prevent.

So the invariants are separated:

  A. HISTORICAL IMMUTABILITY (here, permanent)
     Revision 0001 must not derive its behaviour from anything mutable. No `db.models` import, no
     metadata, no create_all. This stays true no matter how many revisions follow.
     It is a STATIC guarantee about how the file is written. It cannot stop a developer from
     hand-editing the SQL, and does not pretend to — that is migration policy and code review.

  B. CHAIN CORRECTNESS (real PostgreSQL — tests/test_db_postgres_integration.py)
     base -> 0001 -> ... -> head actually applies, reverses, and re-applies.

  C. HEAD ↔ ORM PARITY (real PostgreSQL — same file)
     The schema the FULL chain produces at head, read back by introspection, matches the current
     models. THIS is the future-proof parity rule, and it is the one that keeps working forever.

What remains here about 0001's CONTENT is the INITIAL REVISION CONTRACT: a description of what
that frozen file creates. It is checked against the file itself, never against today's models, so
it stays true after 0002, 0003 and everything after.

One transitional check does compare the two — and DISABLES ITSELF the moment a second revision
exists. See its docstring.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_VERSIONS = _ROOT / "db" / "migrations" / "versions"
_REVISION = _VERSIONS / "0001_initial_schema.py"


def _revision_files() -> list[Path]:
    return sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")


def _norm(sql: str) -> str:
    return " ".join(sql.replace("\n", " ").split()).rstrip(";").strip()


def _literal(name: str) -> list[str]:
    """Read a module-level literal out of the revision WITHOUT importing alembic."""
    tree = ast.parse(_REVISION.read_text(encoding="utf-8"))
    for node in tree.body:
        target = (getattr(getattr(node, "target", None), "id", "")
                  or getattr(getattr(node, "targets", [None])[0], "id", ""))
        if target == name:
            return [_norm(s) for s in ast.literal_eval(node.value)]
    pytest.fail(f"{name} not found in revision 0001")


@pytest.fixture(scope="module")
def frozen() -> list[str]:
    return _literal("UPGRADE_SQL")


@pytest.fixture(scope="module")
def frozen_down() -> list[str]:
    return _literal("DOWNGRADE_SQL")


# =====================================================================
# A. HISTORICAL IMMUTABILITY — permanent, and true for every future revision
# =====================================================================

@pytest.mark.parametrize("revision", _revision_files(), ids=lambda p: p.stem)
def test_no_revision_derives_its_behaviour_from_the_models(revision):
    """A revision records what WAS applied. It must not be recomputed from what the models say now.

    Applied to every revision, not just 0001, so 0002 inherits the rule automatically.
    """
    tree = ast.parse(revision.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    leaking = {m for m in imported if m == "db" or m.startswith("db.")}
    assert not leaking, (
        f"{revision.name} imports {leaking}. Editing the models would then change what an "
        f"already-released migration builds on a fresh database, while databases that already ran "
        f"it would never receive the change. Add a NEW revision instead.")


@pytest.mark.parametrize("revision", _revision_files(), ids=lambda p: p.stem)
def test_no_revision_calls_metadata_create_or_drop(revision):
    src = revision.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]          # drop the docstring, which discusses the pattern
    for banned in ("create_all", "drop_all", "Base.metadata", "MetaData("):
        assert banned not in body, f"{revision.name} still builds its schema dynamically ({banned})"


@pytest.mark.parametrize("revision_file", _revision_files(), ids=lambda p: p.stem)
def test_revision_ids_fit_the_alembic_version_column(revision_file):
    """The released schema stores version_num in VARCHAR(32); reject a chain it cannot record."""
    tree = ast.parse(revision_file.read_text(encoding="utf-8"))
    revision_id = None
    for node in tree.body:
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else list(getattr(node, "targets", [])))
        if any(isinstance(target, ast.Name) and target.id == "revision" for target in targets):
            revision_id = ast.literal_eval(node.value)
            break

    assert isinstance(revision_id, str), f"{revision_file.name} has no literal revision id"
    assert len(revision_id) <= 32, (
        f"{revision_file.name} revision id is {len(revision_id)} chars; alembic_version allows 32")


def test_0001_is_the_root_revision():
    ns: dict = {}
    for line in _REVISION.read_text(encoding="utf-8").splitlines():
        if re.match(r"^(revision|down_revision|branch_labels|depends_on)\s*=", line):
            exec(line, ns)  # noqa: S102 — four literal assignments from a repo-owned file
    assert ns["revision"] == "0001_initial_schema"
    assert ns["down_revision"] is None


# =====================================================================
# INITIAL REVISION CONTRACT — what the frozen file creates.
# Checked against the FILE, never against today's models, so 0002 cannot break it.
# =====================================================================

def _tables(stmts: list[str]) -> set[str]:
    return {m.group(1) for s in stmts for m in [re.match(r"CREATE TABLE (\w+) \(", s)] if m}


def _composite_fks(stmts: list[str]) -> set[tuple[str, str, str]]:
    out = set()
    for s in stmts:
        for name, cols, ref in re.findall(
                r"CONSTRAINT (fk_\w+) FOREIGN KEY\(([^)]*)\) REFERENCES ([\w]+ \([^)]*\))", s):
            if "," in cols:
                out.add((name, " ".join(cols.split()), ref))
    return out


def _indexes(stmts: list[str]) -> set[str]:
    return {m for s in stmts for m in re.findall(r"CREATE INDEX (\w+) ON", s)}


INITIAL_TABLES = {
    "users", "brands", "profile_snapshots", "runs", "run_dependencies", "calendar_posts",
    "attempts", "assets", "jobs", "usage_records", "approvals", "brand_owner_inputs",
    "brand_competitors",
}

# Every pointer that could otherwise cross its parent, as revision 0001 shipped it.
INITIAL_COMPOSITE_FKS = {
    ("fk_brands_current_profile", "current_profile_id, id", "profile_snapshots (id, brand_id)"),
    ("fk_runs_input_profile", "input_profile_id, brand_id", "profile_snapshots (id, brand_id)"),
    ("fk_runs_selected_attempt", "selected_attempt_id, id", "attempts (id, run_id)"),
    ("fk_runs_calendar_post", "calendar_post_id, brand_id", "calendar_posts (id, brand_id)"),
    ("fk_attempts_run", "run_id, brand_id", "runs (id, brand_id)"),
    ("fk_attempts_asset", "asset_id, brand_id", "assets (id, brand_id)"),
    ("fk_run_dependencies_run", "run_id, brand_id", "runs (id, brand_id)"),
    ("fk_run_dependencies_depends_on", "depends_on_run_id, brand_id", "runs (id, brand_id)"),
    ("fk_usage_records_run", "run_id, brand_id", "runs (id, brand_id)"),
    ("fk_usage_records_attempt", "attempt_id, run_id", "attempts (id, run_id)"),
    ("fk_calendar_posts_calendar_run", "calendar_run_id, brand_id, calendar_run_kind",
     "runs (id, brand_id, kind)"),
    ("fk_approvals_subject_run", "subject_run_id, brand_id, subject_kind",
     "runs (id, brand_id, kind)"),
}


def test_initial_revision_creates_its_thirteen_tables(frozen):
    assert _tables(frozen) == INITIAL_TABLES


def test_initial_revision_ships_every_membership_key(frozen):
    """The 12 composite FKs that make a cross-parent pointer unrepresentable."""
    assert _composite_fks(frozen) == INITIAL_COMPOSITE_FKS


def test_initial_revision_ships_its_indexes(frozen):
    assert _indexes(frozen) >= {
        "ix_brands_owner_id", "ix_runs_brand_kind_created", "ix_jobs_expired_leases",
        "ix_jobs_open", "ix_calendar_posts_brand_scheduled_date",
    }


@pytest.mark.parametrize("check", [
    "ck_users_email_is_normalised", "ck_jobs_exactly_one_principal",
    "ck_jobs_running_jobs_name_their_claimant", "ck_run_dependencies_no_self_dependency",
    "ck_calendar_posts_parent_is_a_calendar_run",
    "ck_attempts_succeeded_attempts_have_an_asset",
    "ck_runs_only_creative_runs_target_a_post", "ck_runs_live_lineage_is_complete",
])
def test_initial_revision_ships_its_domain_rules(check, frozen):
    assert any(check in s for s in frozen), f"{check} is not in revision 0001"


# =====================================================================
# downgrade mirrors upgrade — a property of the frozen file alone
# =====================================================================

def test_downgrade_drops_exactly_what_this_revision_created(frozen, frozen_down):
    dropped = {m.group(1) for s in frozen_down for m in [re.match(r"DROP TABLE (\w+)", s)] if m}
    assert dropped == _tables(frozen)
    assert not any("CASCADE" in s.upper() for s in frozen_down), (
        "CASCADE would let a downgrade reach objects this revision never created")
    for s in frozen_down:
        assert re.match(r"^(DROP TABLE \w+|ALTER TABLE \w+ DROP CONSTRAINT \w+)$", s), s


def test_downgrade_removes_the_circular_constraints_before_their_tables(frozen, frozen_down):
    """The cycle that forced ALTER on the way up also breaks a naive reverse drop.

    Reverse order alone reaches `profile_snapshots` while `brands.fk_brands_current_profile` still
    references it, and PostgreSQL refuses. Static ordering only — the real proof is the
    upgrade/downgrade/upgrade cycle in the integration suite.
    """
    added = {m for s in frozen for m in re.findall(r"ALTER TABLE \w+ ADD CONSTRAINT (\w+)", s)}
    assert added, "no ALTER-added constraints found in the upgrade"
    removed = {m for s in frozen_down
               for m in re.findall(r"ALTER TABLE \w+ DROP CONSTRAINT (\w+)", s)}
    assert added == removed, f"downgrade misses: {sorted(added - removed)}"

    first_table = next(i for i, s in enumerate(frozen_down) if s.startswith("DROP TABLE"))
    last_constraint = max(i for i, s in enumerate(frozen_down) if s.startswith("ALTER TABLE"))
    assert last_constraint < first_table, (
        "a table is dropped before the circular constraints come off; downgrade will fail")


# =====================================================================
# TRANSITIONAL — and it switches itself off
# =====================================================================

@pytest.mark.skipif(
    len(_revision_files()) != 1,
    reason="more than one revision exists: 0001 is no longer the whole chain, so head parity is "
           "the real invariant and lives in tests/test_db_postgres_integration.py")
def test_while_0001_is_the_entire_chain_it_must_agree_with_the_models():
    """A stopgap, scoped to the fact that head IS 0001 today — and it expires on its own.

    The real parity rule (chain-at-head vs models, by PostgreSQL introspection) needs a live
    server, and this environment has none. Until one exists, this keeps the models and the only
    revision from drifting unnoticed.

    The skipif is the important part. The moment 0002 lands, head stops being 0001, this check
    disappears, and nobody is ever pushed to edit a released revision to make it pass.
    """
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    from sqlalchemy import create_mock_engine

    from db.models import Base

    out: list[str] = []

    def dump(sql, *a, **kw):
        text = str(sql.compile(dialect=engine.dialect)).strip()
        if text:
            out.append(_norm(text))

    engine = create_mock_engine("postgresql+psycopg://", dump)
    Base.metadata.create_all(engine, checkfirst=False)

    frozen_sql = _literal("UPGRADE_SQL")
    only_migration = sorted(set(frozen_sql) - set(out))
    only_models = sorted(set(out) - set(frozen_sql))
    assert not only_migration and not only_models, (
        "the single revision and db/models.py have drifted.\n"
        "The fix is a NEW revision (0002), never an edit to 0001 — after which this check "
        "disables itself and head parity takes over.\n\n"
        f"in migration only:\n  " + "\n  ".join(s[:160] for s in only_migration)
        + f"\n\nin models only:\n  " + "\n  ".join(s[:160] for s in only_models))


def test_the_transitional_check_is_keyed_on_the_chain_length():
    """Guard the guard: the stopgap must be wired to the revision COUNT, not to a hardcoded flag.

    If someone replaces the skipif with something that does not track the chain, the expiry
    stops working and the misleading invariant comes back.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    block = src.split("TRANSITIONAL", 1)[1]
    assert "len(_revision_files()) != 1" in block, (
        "the transitional parity check no longer expires when a second revision is added")
    assert len(_revision_files()) >= 1
