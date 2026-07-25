# -*- coding: utf-8 -*-
"""Two generated images must not land on one filename.

MEASURED: `webapp_influencer` saved every portrait as `{domain_key}_influencer_portrait` and every
poster as `{domain_key}_influencer_poster` (fixed names), and `save_generated_png` writes straight
to that path. So generating a second portrait OVERWRITES the first — which kills the owner's whole
requirement ("3 people, pick one", keep the rejected ones) BEFORE any model is even called. The UI
hid it: `index.html` appends `?t=Date.now()` to the src, so the picture visibly changes and nothing
suggests the previous one is gone.

There is a second reason the name must not be predictable. `/api/generated/{name}` is public with
no auth — deliberately, because it only serves already-generated PNGs (locked by
tests/test_webapp_spend_gate.py). With a name built from the domain key alone, anyone who can guess
a brand's slug can fetch that brand's faces and posters. The repo's own precedent for this is
`poster/pipeline.py`, which names renders `poster_{uuid4().hex[:8]}`.

Hermetic — Pillow only, no model, no network."""
from __future__ import annotations

import re

import pytest

from api.webapp_images import read_generated_png, save_generated_png, unique_asset_name


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    yield


def test_two_assets_for_one_brand_do_not_collide():
    a = unique_asset_name("x_com", "influencer_portrait")
    b = unique_asset_name("x_com", "influencer_portrait")
    assert a != b, "the second portrait would overwrite the first"


def test_both_files_survive_on_disk():
    """The requirement in one assertion: the owner can still see candidate 1 after candidate 2."""
    first = save_generated_png(b"FIRST-PNG-BYTES", unique_asset_name("x_com", "portrait"))
    second = save_generated_png(b"SECOND-PNG-BYTES", unique_asset_name("x_com", "portrait"))
    assert first != second
    assert read_generated_png(first.rsplit("/", 1)[-1]) == b"FIRST-PNG-BYTES"
    assert read_generated_png(second.rsplit("/", 1)[-1]) == b"SECOND-PNG-BYTES"


def test_the_name_is_not_guessable_from_the_brand_alone():
    """`/api/generated/{name}` is public by design — a predictable name exposes every brand's
    faces to anyone who can guess its slug."""
    name = unique_asset_name("nanocareegypt_com", "influencer_portrait")
    assert name != "nanocareegypt_com_influencer_portrait"
    suffix = name.replace("nanocareegypt_com", "").replace("influencer_portrait", "")
    assert len(re.sub(r"[^a-z0-9]", "", suffix)) >= 8, f"too little entropy: {name}"


def test_the_name_still_says_which_brand_and_what_it_is():
    """Unguessable must not mean unreadable — the owner debugs from these filenames."""
    name = unique_asset_name("x_com", "influencer_portrait")
    assert "x_com" in name and "influencer_portrait" in name


def test_the_name_is_filesystem_safe():
    name = unique_asset_name("aç/../b com", "por trait")
    assert re.fullmatch(r"[A-Za-z0-9._-]+", name), name
    assert ".." not in name and "/" not in name
