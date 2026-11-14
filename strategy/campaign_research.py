"""Bounded source research for a single named offer."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from http.client import HTTPException
import re
from time import monotonic
from urllib.error import URLError
from urllib.parse import urldefrag, urlsplit

from bs4 import BeautifulSoup

from business_profile.llm.embeddings import top_k
from scraper.extractors.links import extract_links_from_html
from scraper.campaign_http import open_campaign_url


MAX_PAGES = 2
MAX_SECONDS = 6.0
MAX_BYTES = 256_000
OFFER_FIELDS = ("name", "price", "priceCurrency", "availableAtOrFrom",
                "validThrough", "description")
QUESTIONS = ("Where are the named offer's price, branch, terms and validity published?",
             "ما مصدر سعر العرض والفرع والشروط ومدة الصلاحية؟")
QUERY_TERMS = ("offer price pricing terms conditions book booking workshop "
               "عرض سعر اسعار الأسعار شروط حجز التسجيل ورشة")


@dataclass(frozen=True)
class Page:
    url: str
    body: bytes
    source_kind: str = "unspecified"
    captured_at: str | None = None


@dataclass(frozen=True)
class Offer:
    page_url: str
    sha256: str
    captured_at: str
    fields: dict[str, str]
    expires_at: str
    source_kind: str

    def as_dict(self):
        return asdict(self)


class ResearchGap(ValueError):
    pass


def _text(value):
    return " ".join(value.split())


def same_source(url, root):
    try:
        target, origin = urlsplit(url), urlsplit(root)
        return (target.scheme in {"http", "https"} and bool(target.hostname)
                and not target.username and not target.password
                and target.hostname == origin.hostname
                and (target.port or (443 if target.scheme == "https" else 80))
                == (origin.port or (443 if origin.scheme == "https" else 80))
                and target.scheme == origin.scheme)
    except ValueError:
        return False


def load_public_page(url, timeout, *, max_bytes=MAX_BYTES, user_agent=None, socket_timeout=2.0):
    deadline = monotonic() + timeout
    with open_campaign_url(url, deadline=deadline, socket_timeout=socket_timeout, max_bytes=max_bytes,
                           user_agent=user_agent or "ADZY/1.0") as response:
        if response.status in {206, 226} or response.headers.get("Content-Range") is not None:
            raise ResearchGap("SOURCE_PARTIAL_RESPONSE")
        if response.headers.get_content_type() != "text/html":
            raise ResearchGap("HTML_REQUIRED")
        if any(coding.strip().lower() != "identity"
               for coding in response.headers.get_all("Content-Encoding", ())):
            raise ResearchGap("UNSUPPORTED_CONTENT_ENCODING")
        transfer = response.headers.get_all("Transfer-Encoding", ())
        if transfer and [coding.strip().lower() for coding in transfer] != ["chunked"]:
            raise ResearchGap("UNSUPPORTED_TRANSFER_ENCODING")
        lengths = response.headers.get_all("Content-Length", ())
        if lengths and (transfer or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,18}", lengths[0].strip())):
            raise ResearchGap("INVALID_SOURCE_LENGTH")
        if lengths and int(lengths[0]) > max_bytes:
            raise ResearchGap("PAGE_TOO_LARGE")
        body = bytearray()
        try:
            while len(body) <= max_bytes:
                if monotonic() >= deadline:
                    raise ResearchGap("SOURCE_DEADLINE")
                chunk = response.read1(min(8192, max_bytes + 1 - len(body)))
                if not chunk:
                    if response.length:
                        raise ResearchGap("SOURCE_TRUNCATED")
                    return Page(url, bytes(body), "public_http")
                body.extend(chunk)
        except HTTPException as exc:
            raise ResearchGap("SOURCE_TRUNCATED") from exc
        raise ResearchGap("PAGE_TOO_LARGE")


def _expires(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
        raise ResearchGap("EXPLICIT_EXPIRY_OFFSET_REQUIRED")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ResearchGap("EXPLICIT_EXPIRY_OFFSET_REQUIRED") from None


def parse_offer(page: Page, expected_name: str, now: datetime) -> Offer:
    soup = BeautifulSoup(page.body, "lxml")
    body = soup.body
    if body is None:
        raise ResearchGap("OFFER_NOT_FOUND")
    if (soup.select_one("script, style, link[rel~=stylesheet], base, template, iframe, object, embed")
            or body.select_one("img, svg, canvas, video, audio, [hidden], [aria-hidden=true], [style], [itemref]")):
        raise ResearchGap("UNSUPPORTED_OFFER_PAGE_CONTENT")
    scopes = body.select('[itemscope][itemtype="https://schema.org/Offer"]')
    if not scopes:
        raise ResearchGap("OFFER_NOT_FOUND")
    if len(scopes) != 1:
        raise ResearchGap("OFFER_AMBIGUOUS")
    scope = scopes[0]
    props = scope.select("[itemprop]")
    if (len(props) != len(OFFER_FIELDS)
            or set(tag["itemprop"] for tag in props) != set(OFFER_FIELDS)
            or any(tag.parent is not scope for tag in props)):
        raise ResearchGap("OFFER_FIELDS_INCOMPLETE_OR_AMBIGUOUS")
    if scope.select_one("a[href], [content], [datetime], [itemscope]"):
        raise ResearchGap("UNSUPPORTED_OFFER_PAGE_CONTENT")
    fields = {tag["itemprop"]: _text(tag.get_text(" ", strip=True)) for tag in props}
    if _text(body.get_text(" ", strip=True)) != _text(" ".join(fields.values())):
        raise ResearchGap("UNPARSED_OFFER_CONTEXT")
    if not all(fields.values()) or any(len(value) > 160 for value in fields.values()):
        raise ResearchGap("OFFER_FIELD_SIZE_OR_EMPTY")
    if fields["name"].casefold() != _text(expected_name).casefold():
        raise ResearchGap("OFFER_NAME_MISMATCH")
    price = fields["price"]
    if not re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,2})?", price) or Decimal(price) <= 0:
        raise ResearchGap("EXACT_PRICE_REQUIRED")
    if not re.fullmatch(r"[A-Z]{3}", fields["priceCurrency"]):
        raise ResearchGap("EXPLICIT_CURRENCY_REQUIRED")
    expires = _expires(fields["validThrough"])
    if now >= expires:
        raise ResearchGap("OFFER_EXPIRED")
    return Offer(page.url, sha256(page.body).hexdigest(), now.isoformat(), fields, expires.isoformat(), page.source_kind)


def unparsed_excerpt(page):
    body = BeautifulSoup(page.body, "lxml").body
    if body is None:
        return ""
    for tag in body.select('[itemscope][itemtype="https://schema.org/Offer"] [itemprop]'):
        tag.decompose()
    return _text(body.get_text(" ", strip=True))[:240]


def _rank(links, name):
    """Reuse the RAG cosine ranker with local binary term vectors."""
    vocabulary = sorted(set(re.findall(r"\w+", (QUERY_TERMS + " " + name).casefold())))
    vectors = []
    for url, label in links:
        terms = set(re.findall(r"\w+", (urlsplit(url).path + " " + label).casefold()))
        vectors.append([float(term in terms) for term in vocabulary])
    order = top_k([1.0] * len(vocabulary), vectors, len(links))
    return [links[index] for index in order]


def research(root, name, loader, *, now=None, clock=None, method="guided", hint=None,
             max_pages=MAX_PAGES, seconds=MAX_SECONDS):
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = monotonic()
    trace = []
    pending = [(root, "BRAND_ENTRY")]
    while pending and len(trace) < max_pages and monotonic() - started < seconds:
        url, why = pending.pop(0)
        step = {"page_url": url, "reason": why}
        trace.append(step)
        page = None
        try:
            page = loader(url, max(.01, seconds - (monotonic() - started)))
            if not same_source(page.url, root):
                raise ResearchGap("SOURCE_ORIGIN_MISMATCH")
            if monotonic() - started >= seconds:
                break
            offer = parse_offer(page, name, now if now is not None else clock())
        except (OSError, URLError, ResearchGap) as exc:
            step["outcome"] = str(exc) if isinstance(exc, ResearchGap) else "SOURCE_UNAVAILABLE"
            if len(trace) == 1:
                pending = _next_pages(page, root, name, method, hint)
        else:
            step["outcome"] = "SUPPORTED_OFFER"
            return _result("READY", trace, started, offer)
    if trace and "outcome" not in trace[-1]:
        trace[-1]["outcome"] = "DEADLINE"
    exhausted = bool(pending) or monotonic() - started >= seconds
    return _result("BUDGET_EXHAUSTED" if exhausted else "NO_SUPPORTED_OFFER", trace, started)


def _next_pages(page, root, name, method, hint):
    links = {}
    if page and same_source(page.url, root):
        html = page.body.decode("utf-8", errors="replace")
        for link in extract_links_from_html(html, page.url, root):
            target = urldefrag(link.href)[0]
            if same_source(target, root) and target not in {root, page.url}:
                links.setdefault(target, link.anchor_text)
    ordered = list(links.items())
    if method == "guided" and ordered:
        ordered = _rank(ordered, name)
    pending = [(link, "GAP_MATCH" if method == "guided" else "PAGE_ORDER") for link, _ in ordered]
    if hint and same_source(hint, root) and hint != root:
        pending = [(hint, "OWNER_SOURCE")] + [row for row in pending if row[0] != hint]
    return pending


def _result(status, trace, started, offer=None):
    return {"status": status, "questions": list(QUESTIONS), "trace": trace,
            "page_calls": len(trace), "elapsed_ms": round(1000 * (monotonic() - started), 3),
            "provider_calls": 0, "offer": offer.as_dict() if offer else None,
            "next_action": None if offer else "ASK_OWNER_FOR_DEDICATED_TERMS_PAGE"}
