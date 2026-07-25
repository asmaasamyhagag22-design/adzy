"""Brand catalog + history routes (gated by require_user at mount time).

  GET    /api/brands            -> {brands: [...]}   (cards for the home grid)
  GET    /api/brands/{key}      -> the full brand object the Studio renders off
  DELETE /api/brands/{key}      -> remove a brand
  GET    /api/history?limit=100 -> {history: [...]}  (generated-image gallery)

Every route is SCOPED to the calling account. It used to say "brands are a shared catalog — auth
gates access, it does not scope it", and that was measured on 2026-07-25 to mean: a brand-new
account that had scraped nothing listed all three brands, read one's full `_profile`, and got
`200 {"ok": true}` from DELETE. The gate answered "is this a valid session?" while the endpoint had
to answer "does this caller own this brand?".

Brands stored before ownership existed have no recorded owner. They stay readable and are flagged
`unclaimed` rather than assigned to a guess; deleting one is refused until a human claims it with
`python -m api.claim_brands`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import brand_store as bs
from ..brand_store import BrandStore, read_history
from .auth import require_user

router = APIRouter()


@router.get("/brands")
def list_brands(user: dict = Depends(require_user)) -> dict:
    return {"brands": BrandStore().list(for_user=user["id"])}


@router.get("/brands/{domain_key}")
def get_brand(domain_key: str, user: dict = Depends(require_user)) -> dict:
    brand = BrandStore().get(domain_key, for_user=user["id"])
    if brand is None:
        # Deliberately indistinguishable from "absent": whether another account holds this brand is
        # itself information the caller is not entitled to.
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


@router.delete("/brands/{domain_key}")
def delete_brand(domain_key: str, user: dict = Depends(require_user)) -> dict:
    """DELETE stays idempotent: absent, and belonging-to-someone-else, both answer 200.

    An earlier draft of this route raised 404 for "not found". That was reverted after the claim
    was refuted: a 200 for an already-absent resource is deliberate idempotent DELETE semantics —
    the same file 404s correctly in `get_brand`, so the author knew how. Keeping 200 also closes a
    hole the ownership change would otherwise have opened: if "not yours" answered 404 while
    "absent" answered 200, the status code itself would tell an attacker which brands exist in
    other accounts. Nothing is deleted in either case; only the caller's own data can be destroyed.
    """
    outcome = BrandStore().delete(domain_key, for_user=user["id"])
    if outcome == bs.UNCLAIMED:
        # Not a leak: unclaimed brands are already visible to this caller, so naming the state
        # tells them nothing new — and it is the one outcome they can act on.
        raise HTTPException(
            status_code=409,
            detail=("This brand was created before accounts existed, so it has no recorded owner. "
                    "Claim it first: python -m api.claim_brands --email <your email>"))
    return {"ok": True, "deleted": outcome == bs.DELETED}


@router.get("/history")
def history(limit: int = 100, user: dict = Depends(require_user)) -> dict:
    return {"history": read_history(limit, for_user=user["id"])}
