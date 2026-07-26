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
from pydantic import BaseModel

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


def _cli_result_for(domain_key: str) -> tuple[dict, str]:
    """The newest CLI full-run result that belongs to this brand, as (result, filename).

    A CLI run names its output after whatever slug the operator typed, so matching on the filename
    alone is unreliable — Al Fouad's run landed in `alfouad_full_result.json` while the brand key
    is `alfouadpharmacies_com`. The `subject_url` INSIDE each result is the authority; the filename
    is only a tiebreak. ({}, "") when nothing matches — never a guess.
    """
    import json
    import re
    from pathlib import Path

    def _key(url: str) -> str:
        host = re.sub(r"^https?://", "", str(url or "")).split("/")[0].lower()
        return re.sub(r"[^a-z0-9]+", "_", host.removeprefix("www.")).strip("_")

    best: tuple[float, dict, str] = (-1.0, {}, "")
    out = Path("outputs")
    if not out.is_dir():
        return {}, ""
    for p in out.glob("*_result.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — an unreadable neighbour must not break the lookup
            continue
        if not isinstance(data, dict) or _key(data.get("subject_url")) != domain_key:
            continue
        mtime = p.stat().st_mtime
        if mtime > best[0]:
            best = (mtime, data, p.name)
    return best[1], best[2]


@router.post("/brands/{domain_key}/adopt-analysis")
def adopt_analysis(domain_key: str, user: dict = Depends(require_user)) -> dict:
    """Show the analysis this brand ALREADY has, instead of paying to compute it a second time.

    Measured on the owner's own brand: a full CLI run produced a competitive SWOT (11 strengths,
    6 opportunities, 1 threat, 2 site-readiness items) plus 6 market trends and 2 competitors, and
    the Studio displayed `has_swot: false` beside it — because no supported path existed from a
    `<slug>_result.json` into the brand store. Her question was "how did the SWOT not come out?";
    it had come out, in the other world.

    Free and instant: it reads a file. `POST /api/analysis/swot` still exists for a fresh compute.
    """
    store = BrandStore()
    brand = store.get(domain_key, for_user=user["id"])
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")

    result, filename = _cli_result_for(domain_key)
    if not result:
        return {"adopted": False, "reason": "no CLI analysis on disk for this brand",
                "source": ""}

    from ..webapp_analysis import adopt_full_result
    patch = adopt_full_result(brand, result)
    store.update(domain_key, patch, for_user=user["id"])
    return {"adopted": True, "source": filename,
            "swot": bool(patch.get("has_swot")),
            "trends": len(((patch.get("swot") or {}).get("trends")) or []),
            "competitors": len(patch.get("competitors") or []),
            "keys": sorted(result.keys())}


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


# ---------------------------------------------------------------------
# Competitors the owner names herself
#
# Automatic discovery is still being improved, and with no peers there is no SWOT at all —
# "strengths and weaknesses are only such in comparison to the competition" (Pickton & Wright
# 1998). The rebuilt SWOT says so and points here, so these routes are the unblock, not a
# convenience. They live on the brands router deliberately: adding a NAME spends nothing, and
# putting them behind the paid-route guard would gate data entry on a spend switch.
# ---------------------------------------------------------------------

class CompetitorIn(BaseModel):
    domain_key: str
    name: str
    website: str = ""


def _brand_ref(domain_key: str, user_id: str) -> str:
    """The registry key for this brand — derived from the SAME profile the SWOT will use, so a
    competitor added here is found by the analysis later."""
    brand = BrandStore().get(domain_key, for_user=user_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    from competitor.registry import brand_ref_from_profile
    return brand_ref_from_profile(brand.get("_profile") or brand)


@router.get("/brands/{domain_key}/competitors")
def list_brand_competitors(domain_key: str, user: dict = Depends(require_user)) -> dict:
    from competitor.registry import list_competitors
    return {"competitors": list_competitors(_brand_ref(domain_key, user["id"]))}


@router.post("/brands/{domain_key}/competitors")
def add_brand_competitor(domain_key: str, body: CompetitorIn,
                         user: dict = Depends(require_user)) -> dict:
    from competitor.registry import add_user_competitor
    entry = add_user_competitor(_brand_ref(domain_key, user["id"]),
                                name=body.name, website=body.website)
    if entry is None:
        # The two ways this fails are both the caller's input: an empty name, or a URL that is not
        # a public http(s) address (same SSRF gate as every other fetch target).
        raise HTTPException(
            status_code=400,
            detail="Needs a name, and any website must be a public http(s) address.")
    return {"ok": True, "competitor": entry}


@router.delete("/brands/{domain_key}/competitors/{key}")
def delete_brand_competitor(domain_key: str, key: str,
                            user: dict = Depends(require_user)) -> dict:
    from competitor.registry import remove_user_competitor
    removed = remove_user_competitor(_brand_ref(domain_key, user["id"]), key)
    return {"ok": True, "removed": removed}
