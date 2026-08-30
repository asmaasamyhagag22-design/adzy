# -*- coding: utf-8 -*-
"""Three faces, one choice, and the chosen one is what travels.

The owner's spec: "المالك يحدد شكل الشخص… يطلع 3 اشخاص يختار منه واحد، يعمل عليه البوستر والريل.
طول ما هو محتار — يعمل حاجة جينرال." Paid one at a time, on her ruling: the first candidate on
create, the second and third only when she asks.

WHAT USED TO HAPPEN, and why every assertion here is about something that was silently untrue:

  * `validate_portrait` flipped a single unanchored bool. Approving candidate 1 and then adding
    another authorised a poster for whichever portrait was newest — the gate checked "is something
    approved?" when the question was "is THIS face approved?".
  * The only thing that reached the poster was a STRING (`config.influencer_persona`). The face's
    bytes reached nothing, so "the same person" was a description, not an identity.
  * Every portrait was written to one fixed filename, so candidate 2 destroyed candidate 1.

Hermetic: the image generator is stubbed, so nothing here calls a model, touches the network, or
spends. `is_preview` and `degraded_reason` are asserted because a placeholder that presents itself
as a finished render is the failure this project keeps catching."""
from __future__ import annotations

import pytest

from api import webapp_influencer as inf
from api.brand_store import BrandStore

_PROFILE = {"name": {"value": "براند"}, "source_url": {"value": "https://x.com.eg/"},
            "languages": ["ar"], "audience_type": {"value": "B2C"}}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)
    yield


@pytest.fixture
def brand():
    return {"domain_key": "x_com", "organization_name": "براند", "_profile": _PROFILE,
            "brand_colors": ["#204060"]}


def _add(brand, patch):
    """Apply a returned patch the way the job pipeline does."""
    ai = dict(brand.get("ai_influencer") or {})
    for key, value in (patch.get("ai_influencer") or {}).items():
        if isinstance(value, dict) and isinstance(ai.get(key), dict):
            merged = dict(ai[key])
            merged.update(value)
            ai[key] = merged
        else:
            ai[key] = value
    brand["ai_influencer"] = ai
    return brand


# --- candidates accumulate -----------------------------------------------------------------------

def test_the_first_candidate_is_produced_from_the_owners_description(brand):
    patch = inf.generate_candidate(brand, {"gender": "woman", "age": "30s"})
    cands = patch["ai_influencer"]["candidates"]
    assert len(cands) == 1
    only = next(iter(cands.values()))
    assert only["url"].startswith("/api/generated/")
    assert "woman" in only["persona"] and "30s" in only["persona"]


def test_an_undecided_owner_still_gets_a_person(brand):
    """"طول ما هو محتار" — derived from the brand, never left blank."""
    only = next(iter(inf.generate_candidate(brand, {})["ai_influencer"]["candidates"].values()))
    assert only["persona"].strip()
    assert "Egypt" in only["persona"], "the general persona ignored the brand's measured market"


def test_a_second_candidate_does_not_replace_the_first(brand):
    _add(brand, inf.generate_candidate(brand, {"gender": "woman"}))
    _add(brand, inf.generate_candidate(brand, {"gender": "man"}))
    cands = brand["ai_influencer"]["candidates"]
    assert len(cands) == 2, f"a candidate was lost: {list(cands)}"
    urls = {c["url"] for c in cands.values()}
    assert len(urls) == 2, "both candidates point at the same file — one overwrote the other"


def test_a_fourth_candidate_is_refused_with_a_reason(brand):
    for i in range(3):
        _add(brand, inf.generate_candidate(brand, {"gender": "woman", "age": f"{20 + i}s"}))
    with pytest.raises(inf.TooManyCandidates):
        inf.generate_candidate(brand, {"gender": "man"})


# --- selection anchors to a specific face ---------------------------------------------------------

def test_selecting_a_candidate_records_which_one(brand):
    _add(brand, inf.generate_candidate(brand, {"gender": "woman"}))
    _add(brand, inf.generate_candidate(brand, {"gender": "man"}))
    second = list(brand["ai_influencer"]["candidates"])[1]
    _add(brand, inf.select_candidate(brand, second))
    ai = brand["ai_influencer"]
    assert ai["selected_id"] == second
    assert ai["portrait_url"] == ai["candidates"][second]["url"], "the legacy field lies"
    assert ai["has_portrait"] is True and ai["validated"] is True


def test_selecting_an_unknown_candidate_is_refused(brand):
    _add(brand, inf.generate_candidate(brand, {}))
    with pytest.raises(inf.UnknownCandidate):
        inf.select_candidate(brand, "no-such-id")


def test_adding_a_candidate_after_selecting_does_not_move_the_selection(brand):
    """The unanchored-bool defect: approving one face must not authorise a later one."""
    _add(brand, inf.generate_candidate(brand, {"gender": "woman"}))
    chosen = list(brand["ai_influencer"]["candidates"])[0]
    _add(brand, inf.select_candidate(brand, chosen))
    _add(brand, inf.generate_candidate(brand, {"gender": "man"}))
    ai = brand["ai_influencer"]
    assert ai["selected_id"] == chosen
    assert ai["portrait_url"] == ai["candidates"][chosen]["url"]


def test_the_selected_face_is_readable_as_bytes(brand):
    """What actually travels to the poster — bytes, not a sentence about a face."""
    _add(brand, inf.generate_candidate(brand, {"gender": "woman"}))
    chosen = list(brand["ai_influencer"]["candidates"])[0]
    _add(brand, inf.select_candidate(brand, chosen))
    data, mime = inf.selected_face_bytes(brand)
    assert data and len(data) > 100
    assert mime == "image/png"


def test_with_nothing_selected_there_are_no_face_bytes(brand):
    assert inf.selected_face_bytes(brand) == (None, "")


# --- honesty ---------------------------------------------------------------------------------------

def test_a_free_placeholder_says_so(brand):
    only = next(iter(inf.generate_candidate(brand, {})["ai_influencer"]["candidates"].values()))
    assert only["is_preview"] is True


def test_with_generation_enabled_a_failure_names_its_reason(brand, monkeypatch):
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")

    def boom(*a, **k):
        raise RuntimeError("lane exhausted")
    monkeypatch.setattr(inf, "_render_portrait", boom)
    only = next(iter(inf.generate_candidate(brand, {})["ai_influencer"]["candidates"].values()))
    assert only["is_preview"] is True
    assert "lane exhausted" in (only.get("degraded_reason") or ""), only
