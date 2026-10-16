"""Which representation did the SERVER choose when we expressed no language preference?

F-06 closed the server-side half of language-acquisition bias by sending no `Accept-Language`.
The client-side half stayed open: Playwright's context `locale` also drives `navigator.language`,
and a locale-adaptive site reads that and routes itself. MEASURED 2026-08-12 against a local
JS-adaptive origin — with `Accept-Language` absent, `locale=en-US` still landed the crawl on
`/en`, and `ar-EG`/`de-DE` landed it on `/ar`/`/de`. The crawler was still choosing.

So the browser locale must FOLLOW a representation the server already picked, never choose one.
This module makes that first, preference-free observation, over the F-01 guarded transport.

It deliberately does NOT decide the brand's marketing language. It reports what was served and
how strong that evidence is; an unreadable or language-neutral landing stays UNKNOWN rather than
being resolved to whatever is convenient.
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from .config import USER_AGENT
from .url_utils import open_public_url

# A deterministic locale for rendering when evidence cannot name one. It is a RENDERING FALLBACK
# and never content evidence — see `RepresentationProbe.language_is_evidence`.
RENDER_FALLBACK_LOCALE = "en-US"

_HTML_LANG_RE = re.compile(r"<html\b[^>]*\blang\s*=\s*[\"']([^\"'>\s]+)", re.I)
_ALT_TAG_RE = re.compile(r"<link\b(?=[^>]*\brel\s*=\s*[\"']?alternate)[^>]*>", re.I)
_HREFLANG_RE = re.compile(r"\bhreflang\s*=\s*[\"']([^\"'>\s]+)", re.I)
_HREF_RE = re.compile(r"\bhref\s*=\s*[\"']([^\"'>\s]+)", re.I)
_MAX_BYTES = 1_500_000


@dataclass
class RepresentationProbe:
    """One preference-free observation of what a site serves by default."""

    requested_url: str
    final_url: str = ""
    html_lang: Optional[str] = None
    content_language: Optional[str] = None       # the `Content-Language` response header
    vary_accept_language: bool = False           # server admits it negotiates on language
    # hreflang tag -> absolute URL. The REGION SURVIVES (`en-US` and `en-GB` stay distinct keys);
    # only the case is normalised to lower, because BCP-47 tags are case-insensitive and two
    # spellings of one tag must not become two variants. Order is not preference: this is a
    # mapping, and callers must not read the first key as "the" language.
    hreflang: dict[str, str] = field(default_factory=dict)
    # `x-default` is a ROUTING relation to a URL, not a language. Kept separately for that reason.
    x_default_url: Optional[str] = None
    error: Optional[str] = None

    @property
    def observed_language(self) -> str:
        """Primary subtag of the served representation, or '' when nothing states it.

        `<html lang>` is the representation describing itself and is preferred. `Content-Language`
        is accepted only when the markup says nothing — it is frequently a stale server default.
        """
        for value in (self.html_lang, self.content_language):
            primary = str(value or "").strip().lower().split(",")[0].split("-")[0].strip()
            if primary.isalpha() and 2 <= len(primary) <= 3:
                return primary
        return ""

    @property
    def language_is_evidence(self) -> bool:
        """True only when the SERVER's own default representation named its language."""
        return bool(self.observed_language)

    @property
    def is_language_selector(self) -> bool:
        """A landing that names no language while advertising several is a chooser, not content.

        Calling such a page English (or Arabic) because of `x-default` would invent the answer it
        is explicitly declining to give.
        """
        return not self.observed_language and len(self.hreflang) > 1


def render_locale_for(probe: Optional[RepresentationProbe]) -> tuple[str, str]:
    """``(locale, provenance)`` for the browser context. Provenance is 'evidence' or 'fallback'.

    'fallback' is a technical rendering choice with no evidential weight. Downstream must not read
    it as the brand's language — that conflation is the whole defect this module exists to stop.
    """
    if probe is not None and probe.language_is_evidence:
        tag = str(probe.html_lang or probe.content_language or "").strip()
        # Keep the region when the representation stated one (`ar-EG` is better than `ar`).
        tag = tag.split(",")[0].strip()
        return (tag or probe.observed_language), "evidence"
    return RENDER_FALLBACK_LOCALE, "fallback"


def probe_neutral_representation(url: str, *, timeout: float = 15.0,
                                 opener=None) -> RepresentationProbe:
    """GET ``url`` with NO language preference and report what came back. Never raises.

    `opener` is a seam for tests; production uses the F-01 guarded transport, so the destination
    and every redirect hop are re-checked exactly as elsewhere.
    """
    probe = RepresentationProbe(requested_url=str(url or ""))
    try:
        # No Accept-Language: the server applies its own default and we observe that choice.
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        fetch = opener or open_public_url
        with fetch(request, timeout=timeout) as response:
            probe.final_url = getattr(response, "url", "") or str(url)
            headers = getattr(response, "headers", {}) or {}
            probe.content_language = headers.get("Content-Language")
            probe.vary_accept_language = "accept-language" in str(
                headers.get("Vary") or "").lower()
            body = response.read(_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001 — a failed probe degrades to the rendering fallback
        probe.error = f"{type(exc).__name__}: {exc}"[:200]
        return probe

    html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    match = _HTML_LANG_RE.search(html)
    if match:
        probe.html_lang = match.group(1).strip()
    base = probe.final_url or probe.requested_url
    for tag in _ALT_TAG_RE.findall(html):
        lang = _HREFLANG_RE.search(tag)
        href = _HREF_RE.search(tag)
        if not lang or not href:
            continue
        code = lang.group(1).strip().lower()
        absolute = urljoin(base, href.group(1).strip())
        if code == "x-default":
            probe.x_default_url = absolute       # a URL relation, never a language
        else:
            probe.hreflang.setdefault(code, absolute)
    return probe
