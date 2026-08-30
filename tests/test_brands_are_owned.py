# -*- coding: utf-8 -*-
"""A brand belongs to the account that created it — reading or deleting someone else's is refused.

MEASURED LIVE (2026-07-25, localhost:8000). Two fresh accounts were created; account B scraped
nothing at all. `GET /api/brands` as B returned all three brands (nanocareegypt_com, pepsi_com,
volumeqatar_com); `GET /api/brands/nanocareegypt_com` as B returned the FULL object including the
raw `_profile`; and `DELETE /api/brands/<anything>` returned `200 {"ok": true}` whether or not a
brand existed. The stored brand JSONs carry no owner field at all, so the gate could only ever
answer "is this a valid session?" while the question the endpoint had to answer was "does this
caller own this brand?" — the project's #1 failure class, sitting on client scrape data.

THE LEGACY PROBLEM, AND WHY IT IS DISCLOSED RATHER THAN GUESSED. The brands already on disk were
created before ownership existed, so who owns them is not recorded anywhere. Assigning them to some
account now would be inventing evidence — the one thing this codebase is built not to do. So they
are surfaced as UNCLAIMED: still readable (they are the owner's own working catalog on her own
machine), never deletable by anyone, and marked in the response so the state is visible instead of
silent. `python -m api.claim_brands --email …` resolves them deliberately, by a human.

Hermetic — a tmp store, no server, no network."""
from __future__ import annotations

import pytest

from api.brand_store import BrandStore

_A = "user-aaa"
_B = "user-bbb"


@pytest.fixture()
def store(tmp_path):
    return BrandStore(base=tmp_path / "brands")


def _brand(key: str) -> dict:
    return {"domain_key": key, "organization_name": key.upper(),
            "_profile": {"name": {"value": "secret"}}}


# --- ownership on new brands -----------------------------------------------------------------

def test_a_saved_brand_records_who_created_it(store):
    saved = store.save(_brand("a_com"), owner_id=_A)
    assert saved["owner_id"] == _A


def test_the_list_shows_me_my_brands_and_not_another_users(store):
    store.save(_brand("mine_com"), owner_id=_A)
    store.save(_brand("theirs_com"), owner_id=_B)
    keys = {b["domain_key"] for b in store.list(for_user=_A)}
    assert keys == {"mine_com"}, f"another account's brands leaked into the list: {keys}"


def test_reading_another_users_brand_is_refused(store):
    store.save(_brand("theirs_com"), owner_id=_B)
    assert store.get("theirs_com", for_user=_A) is None, "read the other account's full profile"
    assert store.get("theirs_com", for_user=_B) is not None, "the owner lost access to their own"


def test_deleting_another_users_brand_is_refused_and_the_file_survives(store):
    store.save(_brand("theirs_com"), owner_id=_B)
    assert store.delete("theirs_com", for_user=_A) == "forbidden"
    assert store.get("theirs_com", for_user=_B) is not None, "the brand was destroyed anyway"


def test_the_owner_can_delete_their_own(store):
    store.save(_brand("mine_com"), owner_id=_A)
    assert store.delete("mine_com", for_user=_A) == "deleted"
    assert store.get("mine_com", for_user=_A) is None


def test_deleting_something_that_never_existed_says_so(store):
    """The old route answered 200 {ok:true} for a key that was never there."""
    assert store.delete("ghost_com", for_user=_A) == "not_found"


def test_updating_another_users_brand_is_refused(store):
    """Enrichment writes must be scoped too, or B rewrites A's SWOT through a job."""
    store.save(_brand("theirs_com"), owner_id=_B)
    assert store.update("theirs_com", {"swot": "mine now"}, for_user=_A) is None
    assert "swot" not in store.get("theirs_com", for_user=_B)


# --- legacy brands: disclosed, never guessed ---------------------------------------------------

def test_a_legacy_brand_is_readable_but_flagged_unclaimed(store):
    store.save(_brand("legacy_com"))                      # no owner_id — pre-dates ownership
    listed = store.list(for_user=_A)
    assert [b["domain_key"] for b in listed] == ["legacy_com"]
    assert listed[0]["unclaimed"] is True, "an unowned brand is presented as if it were owned"


def test_a_legacy_brand_cannot_be_deleted_by_anyone(store):
    store.save(_brand("legacy_com"))
    assert store.delete("legacy_com", for_user=_A) == "unclaimed"
    assert store.get("legacy_com", for_user=_A) is not None


def test_claiming_assigns_every_unowned_brand_and_leaves_owned_ones_alone(store):
    store.save(_brand("legacy_com"))
    store.save(_brand("theirs_com"), owner_id=_B)
    assert store.claim_unowned(_A) == 1
    assert store.get("legacy_com", for_user=_A)["owner_id"] == _A
    assert store.get("theirs_com", for_user=_B)["owner_id"] == _B, "claim stole an owned brand"


def test_after_claiming_the_brand_behaves_like_any_owned_one(store):
    store.save(_brand("legacy_com"))
    store.claim_unowned(_A)
    assert store.get("legacy_com", for_user=_B) is None
    assert store.delete("legacy_com", for_user=_A) == "deleted"


# --- the unscoped API stays available for the internal callers that legitimately need it --------

def test_omitting_the_user_keeps_the_unscoped_behaviour_for_internal_callers(store):
    """Background jobs resolve a brand they were already authorised for; they pass no user."""
    store.save(_brand("theirs_com"), owner_id=_B)
    assert store.get("theirs_com") is not None
    assert len(store.list()) == 1
