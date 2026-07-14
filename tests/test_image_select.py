"""Vision photo-curation for the reel (reel.image_select) — FIX-D1 of the 2026-07-11 campaign.

The old implementation hard-required OPENAI_API_KEY (empty since the all-Gemini migration) and
silently kept EVERYTHING — the mechanism behind the screwdriver/banner/logo-wall reel. It now
runs on the shared Gemini caller and, with no caller (or a failed call), fails CLOSED through
the deterministic collage screen instead of keeping junk. Hermetic: injected fetch + caller.
"""
from __future__ import annotations

import io

from reel.image_select import PhotoSelection, PhotoVerdict, select_brand_photos


def _photo_bytes(color=(90, 60, 40), size=(640, 480)) -> bytes:
    from PIL import Image
    img = Image.new("RGB", size, color)
    for x in range(0, size[0], 7):            # texture so it never reads as blank/collage
        for y in range(0, size[1], 11):
            img.putpixel((x, y), ((x * 3) % 255, (y * 5) % 255, 120))
    buf = io.BytesIO(); img.save(buf, format="JPEG")
    return buf.getvalue()


def _collage_bytes() -> bytes:
    # a white ground with a grid of dark logo blobs separated by full-width gutters
    from PIL import Image
    img = Image.new("L", (600, 600), 250)
    for row in range(4):
        for col in range(3):
            for x in range(col * 200 + 30, col * 200 + 150):
                for y in range(row * 150 + 25, row * 150 + 95):
                    img.putpixel((x, y), 40)
    buf = io.BytesIO(); img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


def test_no_caller_fails_closed_not_open(monkeypatch):
    # no vision caller -> the collage is DROPPED (old behavior kept everything).
    # default_caller is stubbed to None so the test stays hermetic even when another
    # test has loaded the real .env into the process env.
    import business_profile.llm as _llm
    monkeypatch.setattr(_llm, "default_caller", lambda strong=True: None)
    data = {"https://x.com/photo1.jpg": _photo_bytes(),
            "https://x.com/logowall.jpg": _collage_bytes(),
            "https://x.com/photo2.jpg": _photo_bytes((30, 80, 60))}
    out = select_brand_photos(list(data), business_name="X", caller=None,
                              fetch=lambda u: data.get(u))
    assert "https://x.com/logowall.jpg" not in out
    assert "https://x.com/photo1.jpg" in out and "https://x.com/photo2.jpg" in out


def test_caller_verdicts_keep_real_photos_best_first():
    urls = ["https://x.com/banner.jpg", "https://x.com/hero.jpg", "https://x.com/ok.jpg"]
    data = {u: _photo_bytes() for u in urls}

    def caller(system, user, response_model, group_name="", images=()):
        assert response_model is PhotoSelection and len(images) == 3
        return PhotoSelection(verdicts=[
            PhotoVerdict(index=0, kind="text_banner_or_poster", subject="training banner",
                         on_brand=True, quality=3),
            PhotoVerdict(index=1, kind="real_photo", subject="students in lab",
                         on_brand=True, quality=5),
            PhotoVerdict(index=2, kind="real_photo", subject="campus exterior",
                         on_brand=True, quality=4),
        ]), None

    out = select_brand_photos(urls, business_name="X", caller=caller,
                              fetch=lambda u: data.get(u))
    assert out == ["https://x.com/hero.jpg", "https://x.com/ok.jpg"]   # banner dropped, best first

    # return_verdicts pairs each kept URL with what the vision model said it depicts (the label was
    # previously discarded) — best-first, aligned to the URL order. Default call is unchanged (URLs).
    verds = select_brand_photos(urls, business_name="X", caller=caller,
                                fetch=lambda u: data.get(u), return_verdicts=True)
    assert verds == [("https://x.com/hero.jpg", "students in lab"),
                     ("https://x.com/ok.jpg", "campus exterior")]


def test_caller_failure_falls_back_closed():
    urls = ["https://x.com/photo.jpg", "https://x.com/logowall.jpg"]
    data = {"https://x.com/photo.jpg": _photo_bytes(),
            "https://x.com/logowall.jpg": _collage_bytes()}

    def broken(*a, **k):
        raise RuntimeError("boom")

    out = select_brand_photos(urls, business_name="X", caller=broken,
                              fetch=lambda u: data.get(u))
    assert out == ["https://x.com/photo.jpg"]


def test_tracking_pixels_filtered_before_any_fetch():
    seen = []
    urls = ["https://www.facebook.com/tr?id=123", "https://x.com/real.jpg",
            "https://www.google-analytics.com/collect"]
    select_brand_photos(urls, business_name="X", caller=None,
                        fetch=lambda u: seen.append(u) or _photo_bytes())
    assert all("facebook" not in u and "analytics" not in u for u in seen)


def test_single_or_empty_input_passthrough():
    assert select_brand_photos([], business_name="X") == []
    assert select_brand_photos(["https://x.com/only.jpg"], business_name="X") == \
        ["https://x.com/only.jpg"]
