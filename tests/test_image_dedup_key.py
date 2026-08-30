"""P-66: image de-dup must identify the underlying IMAGE, not its transform.

The old key was the path filename only. For an image PROXY (/_next/image?url=…, Wix, Cloudinary,
imgix) every photo shares a generic path ("image") and differs only in the query, so the old key
collapsed an entire product catalogue into ONE image (measured: Next.js sites). The new key reads
the referenced source from the query, while STILL collapsing genuine transform variants of the
same photo (so we don't regress the CDN-transform de-dup the key was built for).
"""
from __future__ import annotations

from scraper.extractors.images import _basename_key


def test_proxy_urls_for_distinct_products_do_not_collapse():
    ring = "https://x.com/_next/image?url=%2Fproducts%2Fring.jpg&w=640&q=75"
    neck = "https://x.com/_next/image?url=%2Fproducts%2Fnecklace.jpg&w=640&q=75"
    assert _basename_key(ring) != _basename_key(neck)


def test_proxy_transform_variants_of_one_photo_still_collapse():
    small = "https://x.com/_next/image?url=%2Fproducts%2Fring.jpg&w=640&q=75"
    large = "https://x.com/_next/image?url=%2Fproducts%2Fring.jpg&w=1200&q=90"
    assert _basename_key(small) == _basename_key(large)


def test_path_based_cdn_transform_variants_still_collapse():
    # The original intent: same photo, transform in the PATH -> one key.
    a = "https://cdn.y.com/h_630,w_1200/13987186/962275_444653.jpeg"
    b = "https://cdn.y.com/h_1500,w_2000/13987186/962275_444653.jpeg"
    assert _basename_key(a) == _basename_key(b)


def test_plain_distinct_filenames_stay_distinct():
    assert _basename_key("https://x.com/img/a.jpg") != _basename_key("https://x.com/img/b.jpg")


def test_same_plain_photo_with_only_transform_query_collapses():
    a = "https://x.com/img/hero.jpg?w=640&q=75"
    b = "https://x.com/img/hero.jpg?w=1280&q=90"
    assert _basename_key(a) == _basename_key(b)
