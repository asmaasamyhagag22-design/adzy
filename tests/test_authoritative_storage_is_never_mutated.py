# -*- coding: utf-8 -*-
"""The live catalog is evidence. A test run must not change it.

`outputs/webapp` holds the only copy of the data the PostgreSQL cutover is being designed from:
two accounts, eight brand documents, a 93 MB gallery whose 25 images exist nowhere else, and 216
generated files. It is also the default storage root for every writer in the application, because
`webapp_dir()` falls back to it whenever `BASEERA_WEBAPP_DIR` is unset.

That combination is how a test run left a real `b_com_influencer_portrait.png` in the live
`generated/` directory. Not a crash, not a failure — a silent edit to the evidence.

`tests/conftest.py` now redirects every test to its own root by default. This file is the proof
that the redirection actually holds, and it is cheap on purpose: the expensive whole-93 MB hash
runs once here, not on every unit test.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_WEBAPP = _ROOT / "outputs" / "webapp"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def authoritative_manifest(*, cheap: bool = False) -> dict:
    """Hash every authoritative byte. `cheap` skips the 93 MB gallery body."""
    out: dict = {"brands": {}, "generated": {}}
    users = _WEBAPP / "users.json"
    out["users.json"] = _sha(users) if users.is_file() else None
    history = _WEBAPP / "history.json"
    if history.is_file():
        out["history.json"] = (f"size:{history.stat().st_size}" if cheap else _sha(history))
    for p in sorted((_WEBAPP / "brands").glob("*.json")):
        out["brands"][p.name] = _sha(p)
    for p in sorted((_WEBAPP / "generated").iterdir()):
        if p.is_file():
            out["generated"][p.name] = p.stat().st_size if cheap else _sha(p)
    return out


def digest(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:32]


@pytest.fixture(scope="module")
def baseline() -> dict:
    return authoritative_manifest()


def test_the_live_catalog_is_present_and_hashable(baseline):
    """If this file is measuring nothing, every other assertion here is worthless."""
    assert baseline["users.json"], "users.json is missing"
    assert baseline["history.json"], "history.json is missing"
    assert len(baseline["brands"]) >= 7, "the brand catalog is smaller than the migration evidence"
    assert len(baseline["generated"]) >= 200, "the generated archive shrank"


def test_the_isolation_fixture_redirects_the_storage_root():
    """The mechanism itself: inside a test, `webapp_dir()` must not be the live directory."""
    from api.user_auth import webapp_dir

    resolved = webapp_dir().resolve()
    assert resolved != _WEBAPP.resolve(), (
        f"webapp_dir() still resolves to the live catalog ({resolved}) — the autouse isolation "
        f"fixture in tests/conftest.py is not in effect")


def test_a_storage_write_inside_a_test_lands_in_the_temporary_root(baseline):
    """Actually WRITE, the way a real test does, and prove the live catalog did not move."""
    from api.brand_store import BrandStore
    from api.user_auth import webapp_dir

    store = BrandStore()
    store.save({"domain_key": "isolation_probe_com", "organization_name": "Probe"},
               owner_id="probe-user")
    written = webapp_dir() / "brands" / "isolation_probe_com.json"
    assert written.is_file(), "the probe brand was not written where the fixture pointed"
    assert not (_WEBAPP / "brands" / "isolation_probe_com.json").exists(), (
        "the probe brand reached the LIVE catalog")

    assert digest(authoritative_manifest()) == digest(baseline), (
        "writing a brand inside a test changed the authoritative manifest")


def test_a_generated_image_write_inside_a_test_lands_in_the_temporary_root(baseline):
    """The exact path that leaked `b_com_influencer_portrait.png` into the live archive."""
    from api.webapp_images import save_generated_png
    from api.user_auth import webapp_dir

    url = save_generated_png(b"\x89PNG\r\n\x1a\nprobe", "isolation_probe_asset")
    assert url.startswith("/api/generated/")
    assert (webapp_dir() / "generated" / "isolation_probe_asset.png").is_file()
    assert not (_WEBAPP / "generated" / "isolation_probe_asset.png").exists(), (
        "a generated PNG reached the LIVE archive")

    after = authoritative_manifest()
    assert len(after["generated"]) == len(baseline["generated"]), (
        f"the live generated archive changed size: "
        f"{len(baseline['generated'])} -> {len(after['generated'])}")
    assert digest(after) == digest(baseline)


def test_the_gallery_is_not_appended_to_by_a_test(baseline):
    from api.brand_store import record_history

    record_history({"image_b64": "UE5H", "filename": "probe.png", "brand_name": "Probe",
                    "domain_key": "isolation_probe_com", "content_type": "post", "style": ""},
                   owner_id="probe-user")
    assert digest(authoritative_manifest()) == digest(baseline), (
        "record_history appended to the live 93 MB gallery")


# =====================================================================
# the end-to-end gate: run other DB suites in a subprocess, then re-hash
# =====================================================================

@pytest.mark.slow
def test_running_the_db_suites_does_not_touch_the_live_catalog(baseline):
    """The claim that matters, measured across a real pytest run rather than argued.

    Uses a SUBPROCESS so the suites run with their own fixtures and their own process state, and
    the manifest is taken from this process before and after.
    """
    targets = [
        "tests/test_db_foundation.py",
        "tests/test_db_migration_parity.py",
        "tests/test_c2_ownership_claim_contract.py",
        "tests/test_webapp_images.py",
        "tests/test_influencer_candidates.py",
        "tests/test_demo_blockers.py",
    ]
    existing = [t for t in targets if (_ROOT / t).is_file()]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:randomly", *existing],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, proc.stdout[-3000:]

    after = authoritative_manifest()
    if digest(after) != digest(baseline):
        changed = [k for k in after["generated"]
                   if after["generated"].get(k) != baseline["generated"].get(k)]
        added = sorted(set(after["generated"]) - set(baseline["generated"]))
        brands = [k for k in after["brands"] if after["brands"].get(k) != baseline["brands"].get(k)]
        pytest.fail(
            "a test run mutated the authoritative catalog.\n"
            f"  generated added:   {added}\n"
            f"  generated changed: {changed[:10]}\n"
            f"  brands changed:    {brands}\n"
            f"  users.json changed: {after['users.json'] != baseline['users.json']}\n"
            f"  history.json changed: {after['history.json'] != baseline['history.json']}")
