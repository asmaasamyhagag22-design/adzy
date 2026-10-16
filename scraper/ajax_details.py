"""Discover 'reveal-on-click' detail URLs that JavaScript builds from a data-attribute.

The owner hit this on NTI (National Telecommunication Institute): a course catalogue where each
course is `div.courseLinks a[data-target=NNNN]` and a click runs
    $('#popover-cont').load("pages/modules/" + link.getAttribute('data-target') + ".html")
so the REAL detail page (Overview / Prerequisites / Hours) lives at
`pages/modules/<data-target>.html` — a URL that JS assembles and that NEVER appears in the HTML.
The crawler follows hrefs, so it can't reach these "url-less modal" details at all
("شال الزايدات واسكراب nti نفسها").

`extract_ajax_detail_urls(html, base_url, js_texts)` learns the `.load(PREFIX + el.attr + SUFFIX)`
template from the page's JS and applies it to every element carrying that attribute, turning the
url-less modals into real, fetchable detail URLs. Universal for the very common jQuery `.load()`
reveal pattern; pure + hermetic; never raises."""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# jQuery/DOM patterns that build a URL as  "<prefix>" + <element attribute> + "<suffix>"  and
# hand it to .load()/.get()/.ajax(). Captures the prefix, the attribute name (via getAttribute /
# .attr / .data / dataset.x), and the optional suffix literal.
_LOAD_RE = re.compile(
    r"""\.(?:load|get|ajax|html)\(\s*['"]([^'"]*?)['"]\s*\+\s*[^;]*?"""
    r"""(?:getAttribute\(\s*['"]([\w:-]+)['"]\s*\)"""
    r"""|(?:\.attr|\.data)\(\s*['"]([\w:-]+)['"]\s*\)"""
    r"""|dataset\.([A-Za-z0-9_]+))"""
    r"""\s*(?:\+\s*['"]([^'"]*?)['"])?""",
    re.IGNORECASE | re.DOTALL,
)


def _dataset_to_attr(name: str) -> str:
    """dataset.courseId -> data-course-id ; dataset.target -> data-target."""
    kebab = re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), name)
    return "data-" + kebab


def _templates(js: str) -> list[tuple[str, str, str]]:
    """(prefix, attribute_name, suffix) for every reveal template found in the JS."""
    out: list[tuple[str, str, str]] = []
    for m in _LOAD_RE.finditer(js or ""):
        prefix = m.group(1) or ""
        attr = m.group(2) or m.group(3) or (_dataset_to_attr(m.group(4)) if m.group(4) else "")
        if not attr:
            continue
        suffix = (m.group(5) or "").split("?", 1)[0]        # drop a JS-side cache-buster query
        out.append((prefix, attr.lower(), suffix))
    return out


def extract_ajax_detail_urls(
    html: str, base_url: str, js_texts: Optional[Iterable[str]] = None, *, max_urls: int = 300,
) -> list[str]:
    """Resolve JS-built 'load-on-click' detail URLs from a page.

    `js_texts` = the page's JavaScript (inline <script> bodies + fetched external scripts) — that's
    where the `.load(prefix + attr + suffix)` template lives. Returns de-duplicated absolute URLs,
    in document order. [] when there is no such template or no matching element."""
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        return []
    joined_js = "\n".join(t for t in (js_texts or []) if t)
    templates = _templates(joined_js)
    if not templates:
        return []
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001
        return []

    seen: set[str] = set()
    out: list[str] = []
    for prefix, attr, suffix in templates:
        for el in soup.find_all(attrs={attr: True}):
            val = (el.get(attr) or "").strip()
            # skip the site's own "no module" sentinels (NTI uses data-target="0")
            if not val or val in ("0", "#", "javascript:void(0)"):
                continue
            detail = urljoin(base_url, prefix + val + suffix)
            if detail not in seen:
                seen.add(detail)
                out.append(detail)
                if len(out) >= max_urls:
                    return out
    return out


def _same_origin(url: str, base: str) -> bool:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:  # noqa: BLE001
        return False


def _default_script_fetch(url: str) -> Optional[str]:
    """Guarded, size-capped GET of a same-origin script body."""
    try:
        from scraper.url_utils import is_safe_public_url, open_public_url
        if not is_safe_public_url(url):
            return None
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with open_public_url(req, timeout=15) as r:
            return r.read(1_000_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def discover_ajax_details(html: str, base_url: str, *, fetch=None, max_scripts: int = 8) -> list[str]:
    """End-to-end discovery: gather the page's inline + SAME-ORIGIN external JS (that's where the
    `.load(...)` template lives), then resolve the JS-built detail URLs. `fetch(url)->str|None`
    fetches a script body (injectable for tests; defaults to a guarded GET). Only same-origin
    scripts are fetched (the template is first-party; skip jQuery/CDN). Never raises."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001
        return []
    js_texts: list[str] = []
    externals: list[str] = []
    for s in soup.find_all("script"):
        src = s.get("src")
        if src:
            u = urljoin(base_url, src)
            if _same_origin(u, base_url):
                externals.append(u)
        else:
            t = s.string or s.get_text() or ""
            if t.strip():
                js_texts.append(t)
    if externals:
        f = fetch or _default_script_fetch
        for u in externals[:max_scripts]:
            try:
                body = f(u)
            except Exception:  # noqa: BLE001
                body = None
            if body:
                js_texts.append(body)
    return extract_ajax_detail_urls(html, base_url, js_texts)
