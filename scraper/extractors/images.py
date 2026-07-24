"""Images of interest.

Roles recorded:
- LOGO: <img> inside <header>, or alt containing 'logo'
- HERO: the first large above-fold <img> not classified as logo
- OG_IMAGE: from <meta property="og:image">
- CONTENT: real on-page PHOTOS (food / interior / product / team) — the brand's
  actual imagery, used to GROUND generation (the reel/poster) in the real place
  instead of an invented scene.

CONTENT collection is deliberately broad about WHERE it looks: modern sites (e.g.
Strikingly, Squarespace, Wix) serve their photos as CSS backgrounds
(`style="background-image:url(...)"`, `data-bg="..."`) and lazy `data-src`, NOT
plain `<img src>`. The old extractor read only `<img src>`, so it captured the logo
and threw every photo away (MEASURED on elkbabgi: 1 of 27 images kept). It is still
conservative about WHAT it keeps: logos/icons/svgs and header/nav/footer chrome are
excluded so a CONTENT image is a real photograph, not brand furniture.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from ..schemas import ImageOfInterest, ImageRole

# Lazy-load attributes that carry the real image URL before JS swaps it in.
_LAZY_IMG_ATTRS = ("data-src", "data-original", "data-lazy-src", "data-lazy")
# Element attributes used for lazy CSS-background images.
_BG_ATTRS = ("data-bg", "data-background", "data-background-image", "data-bg-url")
# url(...) inside an inline style="background-image:url(...)".
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)", re.IGNORECASE)
# Substrings that mark an image as brand chrome (logo/icon), not a content photo.
_NON_PHOTO_MARKERS = (
    "logo", "favicon", "icon", "sprite", "badge", "watermark", "placeholder",
    "loader", "spinner", "avatar-default", "blank.gif",
)
# Analytics/marketing TRACKING-PIXEL hosts — an <img> to these is a 1x1 beacon, not
# a photo (e.g. facebook.com/tr, google-analytics). Excluded so they never reach
# the content set (they also break downstream vision fetches).
_TRACKER_HOSTS = (
    "facebook.com/tr", "facebook.com/plugins", "google-analytics.com",
    "googletagmanager.com", "doubleclick.net", "analytics.", "/pixel",
    "/tr?", "px.ads.", "bat.bing.com", "hotjar.", "clarity.ms",
)
_PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
# Chrome containers whose images are navigation/branding, not content.
_CHROME_ANCESTORS = ("header", "nav", "footer")
_MAX_CONTENT_PER_PAGE = 10


def _as_int(x) -> Optional[int]:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _norm_url(raw: Optional[str], page_url: str) -> Optional[str]:
    """Resolve //, relative, and absolute image URLs to an http(s) absolute URL."""
    if not raw:
        return None
    u = raw.strip().strip("'\"")
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/") or not u.lower().startswith(("http://", "https://")):
        u = urljoin(page_url, u)
    return u if u.lower().startswith(("http://", "https://")) else None


def _looks_like_photo(url: str) -> bool:
    """An extension/keyword heuristic: a content photo is a raster image whose URL
    carries no logo/icon marker. SVG/GIF are excluded (vector/animation = chrome)."""
    path = urlparse(url).path.lower()
    low = url.lower()
    if any(t in low for t in _TRACKER_HOSTS):     # tracking pixel / beacon, not a photo
        return False
    if any(m in low for m in _NON_PHOTO_MARKERS):
        return False
    if path.endswith((".svg", ".gif")):
        return False
    # Accept known raster photo extensions; also accept extensionless CDN URLs
    # (many image CDNs hide the extension) so we don't drop real photos.
    if path.endswith(_PHOTO_EXTS):
        return True
    return "." not in path.rsplit("/", 1)[-1]  # no file extension at all -> CDN photo


def _in_chrome(el) -> bool:
    """True if the element lives inside a header/nav/footer (branding, not content)."""
    for parent in el.parents:
        name = getattr(parent, "name", None)
        if name in _CHROME_ANCESTORS:
            return True
        role = (parent.get("role") if hasattr(parent, "get") else None) or ""
        if role in ("banner", "navigation", "contentinfo"):
            return True
    return False


# Query params that describe a TRANSFORM (size/quality/format), not image IDENTITY.
_IMG_TRANSFORM_PARAMS = {
    "w", "h", "q", "width", "height", "quality", "dpr", "fit", "format", "fm", "auto",
    "crop", "resize", "size", "s", "sharp", "blur", "bg", "lossless", "cs",
}
_IMG_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "svg", "bmp", "tiff"}
# Query params an image PROXY uses to name the REAL image (Next.js /_next/image?url=…, imgix, etc.)
_IMG_SOURCE_PARAMS = ("url", "src", "image", "imageurl", "file", "fileurl", "path", "i")


def _basename_key(url: str) -> str:
    """Dedup key that identifies the underlying IMAGE, not its transform (P-66).

    Two legitimate URL shapes must be told apart:
      1. CDN transform in the PATH, real filename at the end
         (…/h_630,w_1200/13987186/962275_444653.jpeg) — the filename IS the identity, so
         collapsing by filename correctly merges the h_1500 variant of the SAME photo.
      2. Image PROXY with the real image in the QUERY
         (/_next/image?url=/products/foo.jpg&w=640) — here the path filename is a generic
         "image", so filename-only keying collapsed EVERY product photo into one (measured on
         Next.js/Wix/Cloudinary sites). Key on the referenced source instead, minus transforms."""
    p = urlparse(url)
    fname = p.path.rsplit("/", 1)[-1].lower()
    qs = parse_qsl(p.query)
    # (2) proxy pattern: a query param references the real image -> key on ITS basename.
    for k, v in qs:
        if k.lower() in _IMG_SOURCE_PARAMS and v:
            return v.rsplit("/", 1)[-1].lower() or v.lower()
    # (1) the path filename carries a real image extension -> it is the identity; drop the query
    #     (transforms) so variants of one photo merge.
    ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
    if ext in _IMG_EXTS:
        return fname
    # (3) generic/proxy path with no image extension and no source param -> keep the NON-transform
    #     query so distinct images that differ only in the query don't collapse to one.
    kept = sorted((k.lower(), v) for k, v in qs if k.lower() not in _IMG_TRANSFORM_PARAMS)
    return (fname + "?" + urlencode(kept)).lower() if kept else fname


def _collect_content_images(
    soup: BeautifulSoup, page_url: str, exclude_keys: set[str]
) -> list[ImageOfInterest]:
    """Gather real on-page photos from <img> (incl. lazy attrs), inline CSS
    background-image, and data-bg attributes. Logos/icons/chrome excluded."""
    out: list[ImageOfInterest] = []
    seen: set[str] = set(exclude_keys)

    def consider(raw: Optional[str], el, alt: Optional[str] = None) -> None:
        if len(out) >= _MAX_CONTENT_PER_PAGE:
            return
        url = _norm_url(raw, page_url)
        if not url or not _looks_like_photo(url):
            return
        if (alt and "logo" in alt.lower()) or _in_chrome(el):
            return
        key = _basename_key(url)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(ImageOfInterest(
            role=ImageRole.CONTENT, src=url, alt=alt, page_url=page_url,
        ))

    # 1) <img> — src + lazy attrs + first srcset entry.
    for img in soup.find_all("img"):
        alt = img.get("alt")
        consider(img.get("src"), img, alt)
        for attr in _LAZY_IMG_ATTRS:
            consider(img.get(attr), img, alt)
        ss = img.get("srcset") or img.get("data-srcset")
        if ss:
            consider(ss.split(",")[0].strip().split(" ")[0], img, alt)

    # 2) lazy CSS-background attributes (data-bg / data-background / ...).
    for attr in _BG_ATTRS:
        for el in soup.find_all(attrs={attr: True}):
            consider(el.get(attr), el)

    # 3) inline style="...background-image:url(...)".
    for el in soup.find_all(style=True):
        style = el.get("style") or ""
        if "background" in style.lower():
            for m in _CSS_URL_RE.findall(style):
                consider(m, el)

    return out


def extract_images_of_interest(
    html: str,
    page_url: str,
    og_image_url: Optional[str] = None,
    viewport_height: int = 800,
) -> list[ImageOfInterest]:
    out: list[ImageOfInterest] = []
    seen_srcs: set[str] = set()

    if not html:
        if og_image_url and og_image_url not in seen_srcs:
            out.append(ImageOfInterest(
                role=ImageRole.OG_IMAGE, src=og_image_url, page_url=page_url
            ))
        return out

    soup = BeautifulSoup(html, "lxml")

    # ----- Logo: first <img> inside <header>, else alt-contains-'logo' -----
    logo_el = None
    header = soup.find("header")
    if header:
        logo_el = header.find("img")
    if logo_el is None:
        for img in soup.find_all("img"):
            alt = (img.get("alt") or "").lower()
            if "logo" in alt:
                logo_el = img
                break

    if logo_el is not None and logo_el.get("src"):
        src = urljoin(page_url, logo_el["src"])
        seen_srcs.add(src)
        out.append(ImageOfInterest(
            role=ImageRole.LOGO,
            src=src,
            alt=logo_el.get("alt"),
            page_url=page_url,
            width=_as_int(logo_el.get("width")),
            height=_as_int(logo_el.get("height")),
            above_fold=True,  # logos are essentially always above the fold
        ))

    # ----- Hero: first large <img> in <main>/<body>, skipping logo -----
    candidates = soup.select("main img, section img, body img")
    hero_el = None
    for img in candidates:
        if logo_el is not None and img is logo_el:
            continue
        if not img.get("src"):
            continue
        w = _as_int(img.get("width"))
        h = _as_int(img.get("height"))
        # Prefer images explicitly sized large, or with no size info
        if w is None or w >= 300:
            hero_el = img
            break
    if hero_el is not None:
        src = urljoin(page_url, hero_el["src"])
        if src not in seen_srcs:
            seen_srcs.add(src)
            out.append(ImageOfInterest(
                role=ImageRole.HERO,
                src=src,
                alt=hero_el.get("alt"),
                page_url=page_url,
                width=_as_int(hero_el.get("width")),
                height=_as_int(hero_el.get("height")),
                above_fold=True,
            ))

    # ----- OG image (from metadata) -----
    if og_image_url:
        og_abs = urljoin(page_url, og_image_url)
        if og_abs not in seen_srcs:
            seen_srcs.add(og_abs)
            out.append(ImageOfInterest(
                role=ImageRole.OG_IMAGE,
                src=og_abs,
                page_url=page_url,
            ))

    # ----- CONTENT photos (the real on-page imagery) -----
    # Exclude anything already captured as logo/hero/og (by filename) so the
    # brand chrome isn't re-listed as content.
    exclude = {_basename_key(i.src) for i in out}
    out.extend(_collect_content_images(soup, page_url, exclude))

    return out
