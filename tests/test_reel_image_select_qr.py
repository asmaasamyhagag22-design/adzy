# -*- coding: utf-8 -*-
"""P-90 (2026-07-18): with no vision caller, image_select fell back to `_fail_closed`, which only
screened COLLAGES (is_collage_bytes). A QR code / dense B&W matrix passed straight through and the
reel "zoomed on a QR code" (the exact owner bug). Add a deterministic QR/matrix screen to the
no-caller path. Hermetic — synthetic images built in-memory, no network, no vision."""
from __future__ import annotations

import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(img: "Image.Image") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _qr_like(modules: int = 25, scale: int = 10) -> bytes:
    """A deterministic QR-like matrix: 3 corner finder squares + a pseudo-random module grid."""
    m = Image.new("L", (modules, modules), 255)
    px = m.load()
    for r in range(modules):
        for c in range(modules):
            finder = ((r < 7 and c < 7) or (r < 7 and c >= modules - 7)
                      or (r >= modules - 7 and c < 7))
            on = finder and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
            if not finder:
                on = ((r * 7 + c * 13 + r * c) % 3) == 0        # irregular data modules
            px[c, r] = 0 if on else 255
    return _png(m.resize((modules * scale, modules * scale), Image.NEAREST))


def _real_photo_like(w: int = 800, h: int = 600) -> bytes:
    """A smooth diagonal gradient — full midtone range, almost no B/W transitions (a real photo)."""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            v = int((x / w + y / h) * 127)
            px[x, y] = (v, 120, 200 - v // 2)
    return _png(im)


def test_is_qr_code_detects_a_matrix_but_not_a_photo():
    from reel.image_quality import is_qr_code_bytes
    assert is_qr_code_bytes(_qr_like()) is True
    assert is_qr_code_bytes(_real_photo_like()) is False
    assert is_qr_code_bytes(b"") is False


def test_fail_closed_drops_a_qr_code_keeps_the_photo():
    from reel.image_select import _fail_closed
    pairs = [("https://x/qr.png", _qr_like()), ("https://x/photo.jpg", _real_photo_like())]
    kept = _fail_closed(pairs, 12)
    assert kept == ["https://x/photo.jpg"]                     # the QR is screened out


def test_select_brand_photos_no_caller_screens_the_qr(monkeypatch):
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda *a, **k: None)  # force the no-caller path
    from reel.image_select import select_brand_photos
    imgs = {"https://x/qr.png": _qr_like(), "https://x/photo.jpg": _real_photo_like()}
    out = select_brand_photos(list(imgs), business_name="B", caller=None,
                              fetch=lambda u: imgs.get(u))
    assert out == ["https://x/photo.jpg"]
