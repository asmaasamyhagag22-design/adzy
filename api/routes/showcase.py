"""GET /api/showcase — the landing page's hero gallery (unauthenticated).

`frontend/landing.html` fetches this with no auth header and reads `{images: [...]}` — a list of
image URL strings. We serve whatever is in `frontend/assets/showcase/` (stable, same-origin URLs).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
_SHOWCASE = _FRONTEND / "assets" / "showcase"
_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}


@router.get("/showcase")
def showcase() -> dict:
    images: list[str] = []
    if _SHOWCASE.is_dir():
        for p in sorted(_SHOWCASE.iterdir()):
            if p.is_file() and p.suffix.lower() in _EXTS:
                images.append(f"/assets/showcase/{p.name}")
    return {"images": images}
