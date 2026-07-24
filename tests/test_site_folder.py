# -*- coding: utf-8 -*-
"""A folder per site — the owner's rule: "اعمل لأي موقع فولدر فيه كل النتايج".

`outputs/` had grown into one flat pile of `<slug>_*.json` for every brand at once, so handing a
client "their results" meant hand-picking files by prefix. Everything for a brand now also lives
under `outputs/<slug>/`, with an index that says what each file IS.

Read paths stay backward compatible: the flat file is still found when only it exists, so brands
analysed before this change keep working and nothing has to be migrated. Hermetic."""
from __future__ import annotations

import json
from pathlib import Path

from dashboard.site_folder import collect_site_folder, site_dir


def _seed(root: Path, slug: str = "brand") -> None:
    (root / "posters").mkdir(parents=True, exist_ok=True)
    (root / "reels").mkdir(parents=True, exist_ok=True)
    (root / f"{slug}_profile.json").write_text('{"name": "x"}', encoding="utf-8")
    (root / f"{slug}_result.json").write_text('{"competitors": []}', encoding="utf-8")
    (root / f"{slug}_trends.json").write_text('{"items": []}', encoding="utf-8")
    (root / "posters" / f"{slug}_poster.png").write_bytes(b"png")
    (root / f"other_brand_profile.json").write_text("{}", encoding="utf-8")   # must NOT be taken


def test_collects_only_this_brands_files(tmp_path: Path):
    _seed(tmp_path)
    folder = collect_site_folder("brand", out_dir=str(tmp_path))
    names = {p.name for p in Path(folder).rglob("*") if p.is_file()}
    assert "brand_profile.json" in names and "brand_result.json" in names
    assert "brand_poster.png" in names
    assert not any(n.startswith("other_brand") for n in names)


def test_writes_an_index_that_explains_each_file(tmp_path: Path):
    _seed(tmp_path)
    folder = Path(collect_site_folder("brand", out_dir=str(tmp_path)))
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    assert index["slug"] == "brand" and index["generated_at"]
    by_name = {f["file"]: f for f in index["files"]}
    assert "الملف الشخصي" in by_name["brand_profile.json"]["what"]
    assert by_name["brand_poster.png"]["what"]                 # every file is described
    # a README a human can open without knowing the schema
    assert (folder / "README.md").is_file()


def test_a_missing_brand_produces_no_empty_folder(tmp_path: Path):
    assert collect_site_folder("ghost", out_dir=str(tmp_path)) is None
    assert not (tmp_path / "ghost").exists()


def test_rerunning_refreshes_rather_than_duplicating(tmp_path: Path):
    _seed(tmp_path)
    collect_site_folder("brand", out_dir=str(tmp_path))
    (tmp_path / "brand_profile.json").write_text('{"name": "updated"}', encoding="utf-8")
    folder = Path(collect_site_folder("brand", out_dir=str(tmp_path)))
    body = json.loads((folder / "brand_profile.json").read_text(encoding="utf-8"))
    assert body["name"] == "updated"                           # refreshed, not a second copy
    assert len(list(folder.glob("brand_profile*.json"))) == 1


def test_site_dir_is_derived_not_guessed(tmp_path: Path):
    assert site_dir("brand", out_dir=str(tmp_path)) == tmp_path / "brand"
