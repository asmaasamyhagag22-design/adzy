"""Brand catalog + history routes (gated by require_user at mount time).

  GET    /api/brands            -> {brands: [...]}   (cards for the home grid)
  GET    /api/brands/{key}      -> the full brand object the Studio renders off
  DELETE /api/brands/{key}      -> remove a brand
  GET    /api/history?limit=100 -> {history: [...]}  (generated-image gallery)

Brands are a shared catalog (a demo, not multi-tenant) — auth gates access, it does not scope it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..brand_store import BrandStore, read_history

router = APIRouter()


@router.get("/brands")
def list_brands() -> dict:
    return {"brands": BrandStore().list()}


@router.get("/brands/{domain_key}")
def get_brand(domain_key: str) -> dict:
    brand = BrandStore().get(domain_key)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


@router.delete("/brands/{domain_key}")
def delete_brand(domain_key: str) -> dict:
    BrandStore().delete(domain_key)
    return {"ok": True}


@router.get("/history")
def history(limit: int = 100) -> dict:
    return {"history": read_history(limit)}
