"""URL normalization and helpers.

Goal: two URLs that point to the same logical page produce the same
normalized form, so the crawler doesn't visit /about, /about/,
/about?utm_source=x and /about#team as four different pages.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin

from .config import TRACKING_PARAMS

# Public-suffix-aware registrable-domain resolution. `tld` ships an offline
# bundled PSL (no network). If it's somehow unavailable, same_registrable_host
# degrades to the legacy www-stripped hostname comparison (see below).
try:
    from tld import get_fld
    _TLD_OK = True
except Exception:  # pragma: no cover - dependency fallback for light envs
    _TLD_OK = False


_ABSOLUTE_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def is_malformed_concatenated_url(url: str) -> bool:
    """Detect inputs like https://a.com/https://b.com/.

    Pydantic's HttpUrl can treat the second absolute URL as part of the path,
    but for this product it is almost always accidental pasted/concatenated
    input. We reject it instead of silently scraping the wrong site.
    """
    raw = (url or "").strip()
    matches = list(_ABSOLUTE_URL_RE.finditer(raw))
    return len(matches) > 1


def validate_input_url(url: str) -> str:
    url = (url or "").strip()
    if is_malformed_concatenated_url(url):
        raise ValueError(
            "Malformed URL: multiple absolute URLs detected. Provide one clean business URL only."
        )
    return url


def ensure_scheme(url: str) -> str:
    """Add https:// if no scheme is provided. Pure/robust — NEVER raises: this is called by
    `normalize_url` on EVERY scraped link href during crawl/dedup, so a single malformed link
    (a real site can emit `https://a.com/https://b.com`) must not crash the whole scrape.
    Malformed USER INPUT is rejected separately by `validate_input_url` at the entry boundary
    (the `scrape()` entry + the API request schema), not here."""
    url = (url or "").strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def is_safe_public_url(url: str) -> bool:
    """SSRF guard: True only for an http(s) URL whose host resolves ENTIRELY to
    public IPs.

    Blocks non-http(s) schemes and any host that resolves to a loopback /
    private / link-local (incl. the cloud-metadata endpoint 169.254.169.254) /
    reserved / multicast / unspecified address. Use before the server makes an
    outbound request to a user-supplied URL.

    Limitation (documented, not over-claimed): this resolves DNS at check time,
    so it does not fully close the TOCTOU / DNS-rebinding window — that needs
    pinning the resolved IP at fetch time. It is a strong baseline that stops the
    common SSRF targets (localhost, internal ranges, cloud metadata).
    """
    try:
        parts = urlparse(url if "://" in (url or "") else "https://" + (url or ""))
    except Exception:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (
            addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified
        ):
            return False
    return True


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply :func:`is_safe_public_url` to every urllib redirect destination.

    ``urllib`` validates redirect syntax, resolves relative ``Location`` values, and
    calls ``redirect_request`` before opening the next connection.  Keeping the policy
    here preserves those standard semantics while refusing a destination that the
    initial request was never entitled to trust.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_safe_public_url(newurl):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "refused redirect to a non-public host (request-safety guard)",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_url(request, *, timeout: float, context=None):
    """Open one public HTTP(S) request and re-check every automatic redirect hop.

    ``request`` may be a URL string or ``urllib.request.Request``.  The initial
    destination is checked here as well as at higher-level entry points, making the
    transport safe when a caller forgets its own preflight check.  ``context`` is the
    optional TLS context used by image fetchers with custom certificate handling.
    """
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request or "")
    if not is_safe_public_url(url):
        raise urllib.error.URLError("refused non-public URL (request-safety guard)")
    handlers = [SafeRedirectHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=timeout)


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    Steps:
    - lowercase the scheme and host
    - strip fragment
    - drop tracking query params (utm_*, fbclid, gclid, ...)
    - sort remaining query params for deterministic order
    - remove default ports (:80 / :443)
    - collapse a trailing slash on the root path; preserve elsewhere
    """
    url = ensure_scheme(url)
    parts = urlparse(url)

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    # Strip default ports
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    # Filter query params
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    kept.sort()
    query = urlencode(kept)

    # Path: ensure leading slash; only modify trailing slash on root
    path = parts.path or "/"

    return urlunparse((scheme, netloc, path, parts.params, query, ""))


def dedup_key(url: str) -> str:
    """A scheme- and trailing-slash-INSENSITIVE key for treating URLs that point at the
    SAME logical page as ONE crawl-frontier entry.

    Distinct from ``normalize_url``: that function's output is the URL we actually FETCH,
    so it must stay faithful to the scheme (forcing https there would break an http-only
    site). This key is for DEDUP ONLY — it folds ``http://x/a``, ``https://x/a`` and
    ``https://x/a/`` onto one value so the frontier does not spend two budget slots
    fetching the same page (MEASURED: orange.eg fetched http+https and trailing-slash
    variants of /contact-us). The caller keeps the first-seen faithful ``normalize_url``
    form as the fetch target. Root ``/`` is preserved (never stripped to empty)."""
    n = normalize_url(url)
    p = urlparse(n)
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunparse(("https", p.netloc, path, p.params, p.query, ""))


def _registrable_domain(url: str) -> Optional[str]:
    """The eTLD+1 (registrable domain) for a URL via the public-suffix list:
    'web.vodafone.com.eg' -> 'vodafone.com.eg'. Returns None when the host has no
    public-suffix match (IP literal, localhost, intranet / unknown TLD)."""
    if not _TLD_OK:
        return None
    try:
        return get_fld(ensure_scheme(url), fix_protocol=True, fail_silently=True)
    except Exception:
        return None


def same_registrable_host(a: str, b: str) -> bool:
    """Return True if two URLs belong to the same site (same registrable domain).

    Uses the public-suffix list (tld) so that subdomains of one site compare
    equal — `www.`, `web.`, `eshop.`, `my.` of vodafone.com.eg all share the
    registrable domain `vodafone.com.eg`. This matters for real enterprise sites
    that redirect the apex/www to a subdomain (e.g. www.vodafone.com.eg ->
    web.vodafone.com.eg) and serve their navigation across subdomains: the old
    www-only-stripping equality classified every such internal link as EXTERNAL
    and collapsed the crawl to a single page (MEASURED: Vodafone EG, internal=0).

    Multi-label suffixes (.com.eg, .co.uk) are handled correctly, and sites that
    merely SHARE a public suffix are NOT merged (a.blogspot.com != b.blogspot.com,
    vodafone.com.eg != vodafone.co.uk). Falls back to the legacy www-stripped
    hostname equality when a host has no public-suffix match (IP literal /
    localhost / intranet / unknown TLD) so those hosts keep prior behavior.
    """
    fa = _registrable_domain(a)
    fb = _registrable_domain(b)
    if fa and fb:
        return fa == fb
    # Fallback: hosts the PSL can't classify (IP / localhost / intranet).
    ha = urlparse(ensure_scheme(a)).netloc.lower().removeprefix("www.")
    hb = urlparse(ensure_scheme(b)).netloc.lower().removeprefix("www.")
    return ha == hb and ha != ""


def get_host(url: str) -> str:
    return urlparse(ensure_scheme(url)).netloc.lower()


# A leading path segment that is a LOCALE (en, ar, us, en-eg, ar_eg, ...) — a locale
# homepage like /en or /en-eg is the brand homepage, NOT a deep content page.
_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}([-_][a-z]{2})?$", re.IGNORECASE)
_HOME_SEGMENTS = {"home", "index", "default", "main"}
_HOME_FILE_RE = re.compile(r"^(index|default|home)\.[a-z0-9]+$", re.IGNORECASE)


def site_root_if_deep(url: str) -> Optional[str]:
    """If `url` is a DEEP CONTENT page, return its site root (``scheme://host/``); else
    None.

    Used so the crawler can anchor brand IDENTITY (name / tagline / description / logo)
    to the real homepage when the seed URL was a deep page — MEASURED disaster: ITI was
    scraped at ``/diplomaStructure/.../tracks/...`` so its tagline became the track name
    ("DATA SCIENCE") and the offerings were all one track.

    Returns None (do NOT re-anchor) when the URL is already a homepage:
      * the bare root ``/``
      * a LOCALE homepage — ``/en``, ``/us``, ``/en-eg``, ``/en/home`` (these ARE the
        homepage; re-anchoring them to the bare root can regress a locale-gated site).
    A path with any real content segment left after stripping a locale prefix and a
    trailing home/index marker is treated as deep. (MEASURED across 83 saved scrapes:
    cleanly separates the locale homes orange/en, adidas/us, defacto/en-eg,
    vodafone/en/home from the deep pages iti/tracks, sofitel/restaurant, elkbabgi/menu,
    elmenus/<city>, buffalo/branches/all/home.)
    """
    try:
        p = urlparse(ensure_scheme(url))
    except Exception:
        return None
    if not p.netloc:
        return None
    segs = [s for s in (p.path or "").split("/") if s]
    if segs and _LOCALE_SEGMENT_RE.match(segs[0]):
        segs = segs[1:]                                  # strip a leading locale prefix
    while segs and (segs[-1].lower() in _HOME_SEGMENTS or _HOME_FILE_RE.match(segs[-1])):
        segs = segs[:-1]                                 # strip trailing home/index
    if not segs:
        return None                                      # already a homepage
    return f"{p.scheme}://{p.netloc}/"


# ccTLD -> ISO-3166 region. GENERIC TLDs (.com/.net/.org/.io/...) are deliberately
# ABSENT: they carry NO country signal, so a .com site must never be assigned a
# region from its TLD (that is exactly what produced the fabricated +20 phone on the
# Saudi .com site nahdionline.com — see region_from_site).
_CCTLD_REGION = {
    "eg": "EG", "sa": "SA", "ae": "AE", "kw": "KW", "qa": "QA", "bh": "BH",
    "om": "OM", "jo": "JO", "lb": "LB", "ps": "PS", "iq": "IQ", "sy": "SY",
    "ye": "YE", "ma": "MA", "tn": "TN", "dz": "DZ", "ly": "LY", "sd": "SD",
    "uk": "GB", "gb": "GB", "ie": "IE", "de": "DE", "fr": "FR", "it": "IT",
    "es": "ES", "pt": "PT", "nl": "NL", "be": "BE", "ch": "CH", "at": "AT",
    "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "pl": "PL", "gr": "GR",
    "ru": "RU", "ua": "UA", "tr": "TR", "in": "IN", "pk": "PK", "bd": "BD",
    "cn": "CN", "jp": "JP", "kr": "KR", "id": "ID", "my": "MY", "sg": "SG",
    "th": "TH", "vn": "VN", "ph": "PH", "us": "US", "ca": "CA", "mx": "MX",
    "br": "BR", "ar": "AR", "cl": "CL", "co": "CO", "au": "AU", "nz": "NZ",
    "za": "ZA", "ng": "NG", "ke": "KE",
}

# A leading locale path segment of the LANGUAGE-REGION form: 'ar-sa', 'en_gb'.
# The SECOND subtag is the country (region). A language-only segment ('/ar', '/en')
# has no region and is intentionally NOT matched here.
_LOCALE_REGION_RE = re.compile(r"^[a-z]{2}[-_]([a-z]{2})$", re.IGNORECASE)


def region_from_site(url: Optional[str]) -> Optional[str]:
    """Best-effort ISO-3166 region (alpha-2) for a site, from RELIABLE universal signals.

    Order, strongest first:
      1. URL locale path segment of the language-region form (``/ar-sa`` -> ``SA``,
         ``/en-gb`` -> ``GB``) — the market signal process.md already trusts (§5.D).
      2. ccTLD of the registrable domain (``.eg`` -> ``EG``, ``vodafone.com.eg`` ->
         ``EG``). Generic TLDs (.com/.net/...) carry no country signal -> skipped.

    Returns ``None`` when neither reliable signal is present. The ``html lang`` region
    subtag is DELIBERATELY NOT used: ``lang="en-US"`` is a common template default that
    mislabels non-US sites (measured: the Egyptian sites daturial/laserhairremovalcairo/
    thehairaddict all carry en-US boilerplate). The point of this signal is to know when a
    site is NOT in the home market so a bare national phone number isn't parsed under the
    wrong country (nahdi: Saudi 920024673 parsed as EG -> a valid-looking WRONG
    +20920024673). Universal — no vertical or single-country assumption baked in here.
    """
    # 1. URL locale path (e.g. /ar-sa). Only the language-region form yields a region.
    try:
        segs = [s for s in (urlparse(ensure_scheme(url or "")).path or "").split("/") if s]
    except Exception:
        segs = []
    if segs:
        m = _LOCALE_REGION_RE.match(segs[0])
        if m:
            return m.group(1).upper()
    # 2. ccTLD of the registrable domain (handles multi-label suffixes like .com.eg).
    reg = _registrable_domain(url or "")
    if reg:
        last = reg.rsplit(".", 1)[-1].lower()
        if last in _CCTLD_REGION:
            return _CCTLD_REGION[last]
    return None


def get_domain_slug(url: str) -> str:
    """Make a filesystem-safe slug from a host: 'www.example.com' -> 'example_com'."""
    host = get_host(url).removeprefix("www.")
    return host.replace(".", "_").replace(":", "_")


def resolve(base_url: str, href: str) -> str:
    """Resolve a possibly-relative href against base_url."""
    return urljoin(base_url, href)


def is_http_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)
