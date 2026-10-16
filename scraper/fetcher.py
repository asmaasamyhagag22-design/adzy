"""Page fetching via Playwright.

Responsibilities:
- Launch a Chromium browser context with our user agent and viewport.
- For a given URL, navigate, scroll to trigger lazy loading, capture
  HTML, two screenshots (full + viewport), and bot-protection signals.
- Return a `FetchResult` dataclass; the caller decides what to do
  with it (extract, classify, etc.).

This module knows nothing about the manifest or downstream stages.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PWTimeout
from playwright.sync_api import Error as PWError

from .config import (
    ACCEPT_LANGUAGE,
    BROWSER_LOCALE,
    BOT_PROTECTION_HTML_MARKERS,
    BOT_PROTECTION_TEXT_SIGNALS,
    MIN_RENDERED_CHARS,
    NAV_TIMEOUT_MS,
    PAGE_TIMEOUT_MS,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    USER_AGENT,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
)
from .errors import ErrorCode
from .time_utils import utc_now
from datetime import datetime


@dataclass
class FetchResult:
    url: str
    final_url: str
    http_status: Optional[int]
    fetched_at: datetime
    duration_ms: int
    html: str = ""
    rendered_text: str = ""
    # Human-readable content recovered from same-site JSON APIs during the render (SPA content
    # that never appears in the HTML). The crawler turns this into citable text_blocks.
    api_text: str = ""
    full_screenshot_bytes: bytes = b""
    viewport_screenshot_bytes: bytes = b""
    content_hash: Optional[str] = None
    screenshot_hash: Optional[str] = None
    error_code: Optional[ErrorCode] = None
    error_message: Optional[str] = None
    bytes_downloaded: int = 0
    # True when navigation timed out but Playwright still exposed useful DOM.
    # The crawler should process the page and record the timeout as a warning
    # on PageRecord.failures instead of dropping the page completely.
    partial_content: bool = False
    partial_reason: Optional[str] = None
    # True when the (non-critical) full-page screenshot failed. The page still succeeds —
    # visual identity degrades gracefully to logo pixels + header/footer colors — so this
    # is surfaced as a manifest note, NOT an error_code that would discard the page.
    screenshot_failed: bool = False
    # Server-requested wait (seconds) parsed from a Retry-After header on 429/503.
    retry_after_s: Optional[float] = None
    # Marker for the caller — used by extractors that need a live Page
    # for computed-CSS reads. We attach the Page only when requested.
    _page: Optional[Page] = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.error_code is None


def _finalize_hashes_and_bytes(result: FetchResult) -> None:
    if result.html:
        result.content_hash = hashlib.sha256(result.html.encode("utf-8", errors="replace")).hexdigest()
    if result.full_screenshot_bytes:
        result.screenshot_hash = hashlib.sha256(result.full_screenshot_bytes).hexdigest()
    result.bytes_downloaded = (
        len(result.html.encode("utf-8", errors="replace"))
        + len(result.full_screenshot_bytes)
        + len(result.viewport_screenshot_bytes)
    )


def _salvage_partial_dom(page: Page, result: FetchResult, reason: str) -> bool:
    """Best-effort DOM salvage after navigation timeout.

    Some real sites time out on network/lazy assets while the useful DOM is
    already present. Returning True here means the crawler can still extract
    text, links, menu content, and visual hints from the partially loaded page.
    """
    try:
        result.final_url = page.url or result.final_url
    except Exception:
        pass

    try:
        result.html = page.content() or ""
    except Exception:
        result.html = result.html or ""

    try:
        result.rendered_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        result.rendered_text = result.rendered_text or ""

    # CONTENT-BASED salvage (P-61): the old check kept anything with non-empty HTML, so an empty
    # shell (`<html><body></body></html>`) that timed out was "salvaged" as usable. Require real
    # VISIBLE TEXT — the thing extraction and readiness actually consume — not just a stripped tag.
    if len(result.rendered_text.strip()) < MIN_RENDERED_CHARS:
        return False

    try:
        result.full_screenshot_bytes = page.screenshot(full_page=True, type="png")
    except Exception:
        pass

    try:
        result.viewport_screenshot_bytes = page.screenshot(full_page=False, type="png")
    except Exception:
        pass

    result.partial_content = True
    result.partial_reason = f"timeout_partial_content_salvaged: {reason[:220]}"
    _finalize_hashes_and_bytes(result)
    return True


def make_browser_context(browser: Browser, *, locale: Optional[str] = None) -> BrowserContext:
    """Create a context with our standard fingerprint.

    ignore_https_errors=True: many REAL small-business sites (especially MENA) have an expired or
    misconfigured TLS cert — Chromium refuses those by default (net::ERR_CERT_AUTHORITY_INVALID),
    so the whole scrape returned 0 pages in ~20s (MEASURED: marasimltd.com). We only READ public
    marketing content (no credentials submitted), so a cert-authority error is not a real risk and
    must not block the crawl."""
    # One CONSISTENT identity, derived from the engine actually running (scraper/browser_identity).
    # MEASURED 2026-07-21: the hardcoded UA claimed Chrome/131 while the engine was 147, Sec-CH-UA
    # was empty, and navigator.languages said en-US while our header advertised Arabic too. Those
    # contradictions are the cheapest thing for an ordinary site to block on — and they were ours.
    from .browser_identity import browser_headers, user_agent_for
    version = getattr(browser, "version", "") or ""
    return browser.new_context(
        user_agent=user_agent_for(version),
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        java_script_enabled=True,
        ignore_https_errors=True,
        # page.route cannot see requests intercepted by a Service Worker.  Blocking
        # workers keeps every network destination at the request-safety boundary below.
        service_workers="block",
        # FOLLOWS the representation the server already chose; never chooses one.
        #
        # `locale` drives `navigator.language`, and a locale-adaptive site reads that and routes
        # itself — MEASURED: with Accept-Language absent, `en-US` still landed a JS-adaptive origin
        # on /en, `ar-EG` on /ar. So the caller passes the locale of the representation a
        # preference-free HTTP probe already received (`scraper.representation`). It is never
        # inherited from the host machine, which would make the crawl vary by laptop; when no
        # evidence names a language the caller passes the deterministic RENDERING FALLBACK, which
        # carries no evidential weight.
        locale=(locale or BROWSER_LOCALE),
        extra_http_headers=browser_headers(version),
    )


def _capture_screenshots(page: Page, result: FetchResult, above_fold_only: bool = False) -> None:
    """Capture the full-page + viewport screenshots for a FULL homepage fetch.

    A screenshot failure is NON-FATAL: the page's html/text/links/offerings are already
    captured and ARE the core of the profile, and visual identity degrades gracefully to
    logo pixels + header/footer colors when no screenshot exists. Setting an error_code
    here made result.ok False and DISCARDED the whole fully-scraped page (MEASURED:
    azzafahmy.com -> 0 pages on a SCREENSHOT_FAILED, losing all its text/offerings). We flag
    the degradation (surfaced as a manifest note) instead of failing the page.

    `above_fold_only` (two-phase mode): below-fold product-image BYTES were blocked, so a full-page
    screenshot would render broken images below the fold and skew the raw palette. We use the
    VIEWPORT (above-fold: logo + header + hero, all loaded) as the palette source instead — which
    is more brand-representative anyway. The screenshot is only consumed by the raw pixel palette."""
    if above_fold_only:
        try:
            vp = page.screenshot(full_page=False, type="png")
            result.full_screenshot_bytes = vp          # palette reads full_screenshot_bytes
            result.viewport_screenshot_bytes = vp
        except PWError as e:
            result.screenshot_failed = True
            result.error_message = f"viewport screenshot failed (non-fatal): {str(e)[:180]}"
        return
    try:
        result.full_screenshot_bytes = page.screenshot(full_page=True, type="png")
    except PWError as e:
        result.screenshot_failed = True
        result.error_message = f"full_page screenshot failed (non-fatal): {str(e)[:180]}"
    try:
        result.viewport_screenshot_bytes = page.screenshot(full_page=False, type="png")
    except PWError:
        # Viewport screenshot is non-critical; skip silently.
        pass


def looks_bot_protected(*, html: str = "", title: str = "", status: int = 200,
                        text: Optional[str] = None) -> bool:
    """Public, testable form of the interstitial check — see `_detect_bot_protection`.

    Exists because the distinction it encodes cost a whole site: a protection vendor's script on a
    fully served page is not a block (pepsi.com, 200, 35KB, real title), while the same marker on
    an empty untitled page is.
    """
    import re as _re
    body = text if text is not None else _re.sub(r"<[^>]+>", " ", html or "")
    return _detect_bot_protection(body, title, http_status=status, html=html) is not None


def _detect_bot_protection(text: str, title: str, *, http_status: Optional[int] = None,
                           html: str = "", headers: Optional[dict] = None) -> Optional[ErrorCode]:
    """Detect a WAF/anti-bot interstitial. LANGUAGE-INDEPENDENT (P-63): the old check matched only
    English display phrases, so a non-English Cloudflare/WAF challenge served with HTTP 200 was
    saved as if it were the brand's homepage. We now also inspect (a) HTML/challenge-JS fingerprints
    and WAF response headers — present regardless of the challenge's language — and (b) a
    block-status (403/429/503) returned with almost no visible text."""
    haystack = (text[:5000] + " " + (title or "")).lower()
    if "captcha" in haystack:
        return ErrorCode.CAPTCHA_DETECTED
    for sig in BOT_PROTECTION_TEXT_SIGNALS:
        if sig.lower() in haystack:
            return ErrorCode.BOT_PROTECTION

    # (a) language-independent HTML / header fingerprints — but a vendor's script is NOT a block.
    # MEASURED (pepsi.com, 2026-07-21): the site answered 200 with a full 35KB page titled
    # "Pepsi Official Site", and we discarded the whole site because the HTML contained
    # `_incapsula_` — Imperva's own script, which ships on every page it protects INCLUDING the
    # ones served happily to real users. Presence-of-vendor is not refusal. The marker only counts
    # when the page also LOOKS like an interstitial: no title and almost no rendered text (the same
    # standard branch (b) below already applies to block statuses).
    html_low = (html or "").lower()
    from .config import BOT_CHALLENGE_MARKERS, BOT_VENDOR_MARKERS
    if any(m in html_low for m in BOT_CHALLENGE_MARKERS):
        return ErrorCode.BOT_PROTECTION          # a challenge orchestrator only loads to block us
    if any(m in html_low for m in BOT_VENDOR_MARKERS):
        # The vendor is present, which says nothing about whether WE were refused. Only an actual
        # interstitial does: no title, or almost nothing rendered.
        if not (title or "").strip() or len((text or "").strip()) < MIN_RENDERED_CHARS:
            return ErrorCode.BOT_PROTECTION
    hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    if "challenge" in hdrs.get("cf-mitigated", ""):
        return ErrorCode.BOT_PROTECTION

    # (b) a block status returned with almost no visible text — a block in ANY language.
    #
    # 429 IS NOT IN THIS LIST, AND THAT IS THE POINT. A 429 means "you are going too fast", which
    # is a statement about timing, not about permission. Reporting it as BOT_PROTECTION printed
    # "مش هيتفتح بإعادة المحاولة" — it will not open on a retry — to an owner whose site opens
    # fine on the next attempt. Worse, it short-circuited the patience this file already builds
    # for exactly this case: `_is_rate_limited` + the caller's `rate_limit_retries` budget exist
    # to wait a 429 out, and they only run while the result is classified transient.
    #
    # MEASURED (alfouadpharmacies.com, 2026-07-25): HTTP 429 + 169 bytes, reported as a permanent
    # block on a site that returns 1.3 MB and HTTP 200 over an ordinary connection.
    #
    # A 429 that carries a real challenge marker is still caught above, by (a) — narrowing this
    # inference does not blind it.
    if http_status in (403, 503) and len((text or "").strip()) < MIN_RENDERED_CHARS:
        return ErrorCode.BOT_PROTECTION
    return None


def _scroll_to_load(page: Page) -> None:
    """Scroll through the page to trigger lazy-loaded content, DWELLING at the
    bottom so async footer widgets finish before we capture the DOM.

    MEASURED (elkbabgi.com, a Strikingly site): the footer social links are
    injected by JS only after the footer has been in view for ~1s. The old
    fast scroll (120ms/step, then immediately back to top) captured the DOM
    before the widget rendered -> social/logo silently dropped. We now scroll to
    a stable bottom, dwell, and wait for network to settle before capturing.
    Universal (helps any lazy-loading site), not site-specific.
    """
    try:
        page.evaluate(
            """async () => {
                const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                let last = -1;
                for (let i = 0; i < 40; i++) {
                    window.scrollBy(0, Math.max(400, window.innerHeight * 0.9));
                    await sleep(250);
                    const reached = window.scrollY + window.innerHeight;
                    const h = document.body.scrollHeight;
                    if (reached >= h - 5) {
                        if (h === last) break;     // stable bottom reached
                        last = h;                  // grew (lazy content) -> keep going
                    }
                }
                await sleep(1400);                 // dwell for lazy footer widgets
                window.scrollTo(0, 0);
                await sleep(250);
            }"""
        )
        # Late XHR-driven widgets (e.g. a social-icons footer) need a final beat.
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PWTimeout:
            pass
    except PWError:
        # Best-effort; not fatal
        pass


# Transient failures worth a retry: a network blip, an HTTP/2 reset, or a
# navigation timeout often clears on a second try. Permanent outcomes
# (bot-block, CAPTCHA, hard 4xx, empty DOM) are NOT retried — retrying them
# just wastes the scrape budget and can look like hammering.
_TRANSIENT_ERRORS = {
    ErrorCode.NETWORK_ERROR,
    ErrorCode.RENDER_ERROR,
    ErrorCode.TIMEOUT,
}

# HTTP statuses that mean "try again shortly", not "no": 429 is literally
# Too-Many-Requests (MEASURED: bobana-eg.com 429'd one burst and served the
# same URL fine moments later — the old code failed the whole run in 19s),
# and 5xx are server hiccups. Same set net.py already retries for JSON APIs.
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}
# A 429 asks us to SLOW DOWN — back off far longer than a network blip, and
# honor the server's own Retry-After when it sends one (capped: a scrape run
# can't stall for minutes on one page).
_RATE_LIMIT_BACKOFF_S = 6.0
_RETRY_AFTER_CAP_S = 20.0


def _is_transient(result: "FetchResult") -> bool:
    if result.error_code in _TRANSIENT_ERRORS:
        return True
    return (result.error_code is ErrorCode.HTTP_ERROR
            and (result.http_status or 0) in _TRANSIENT_HTTP)


def _retry_delay_s(result: "FetchResult", attempt: int) -> float:
    """attempt is 1-based. Rate-limit statuses wait longest; Retry-After wins (capped)."""
    if result.retry_after_s is not None:
        return min(result.retry_after_s, _RETRY_AFTER_CAP_S)
    if result.http_status == 429:
        return _RATE_LIMIT_BACKOFF_S * attempt
    return RETRY_BACKOFF_SECONDS * attempt


# Heavy, non-essential resource types blocked on LIGHT sub-page fetches: sub-pages are crawled
# for TEXT + LINKS (+ DOM-based image URLs, which are attributes, not loaded pixels), so loading
# images/media/fonts only burns time. Scripts / XHR / CSS / documents still load, so JS-injected
# content (products, links, lazy text) is preserved. Universal (helps any heavy page).
_HEAVY_RESOURCE_TYPES = frozenset({"image", "media", "font"})

# Analytics / ads / tracking / telemetry hosts — pure noise for our profile (they carry NO brand
# content and are never screenshotted), yet they fire many beacons per page. Blocking them on
# EVERY fetch (homepage + subpages) cuts our request footprint, which both speeds the crawl AND
# lowers the chance of tripping a site's per-IP rate limit — the measured cause of the HTTP 429
# that killed whole scrapes (topshoes.store fired ~20 such requests on the homepage alone). These
# substrings match ONLY third-party trackers; they never match first-party content, product
# images, or a site's own CDN (cdn.shopify.com, imgix, etc. are deliberately NOT here).
_TRACKING_HOST_SIGNS = (
    "googletagmanager.com", "google-analytics.com", "analytics.google.com",
    "googleadservices.com", "doubleclick.net", "merchant-center-analytics",
    "adservice.google", "googlesyndication.com",
    "analytics.tiktok", "tiktokw.us", "connect.facebook.net", "facebook.com/tr",
    "monorail-edge.shopifysvc", "otlp-http", "converted.in",
    "hotjar.com", "clarity.ms", "snap.licdn.com", "bat.bing.com", "criteo.",
    "pinterest.com/ct", "ct.pinterest.com", "sc-static.net", "cdn.segment.com",
)


def _is_tracking_request(url: str) -> bool:
    """True for a third-party analytics/ads/telemetry beacon (safe to drop — no brand content)."""
    u = (url or "").lower()
    return any(sign in u for sign in _TRACKING_HOST_SIGNS)


# Two-phase carve-out (owner ruling): Phase A blocks PRODUCT-image bytes but MUST still load the
# 1-2 LOGO/brand images — the logo picker needs real getBoundingClientRect dims (a blocked <img>
# collapses to ~0x0 and the +12 suitable_logo_shape bonus + floor-rescue are lost). These URL signs
# whitelist brand-chrome images so they load even after the below-fold block engages.
_LOGO_URL_SIGNS = ("logo", "brand", "favicon", "apple-touch", "apple_touch", "/icon", "wordmark",
                   "identity", "/header", "site-icon", "sprite")


def _is_logo_like(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in _LOGO_URL_SIGNS)


def _block_heavy_route(route) -> None:
    """LIGHT-mode router: drop heavy resources AND trackers; let content/scripts/XHR through."""
    try:
        req = route.request
        if req.resource_type in _HEAVY_RESOURCE_TYPES or _is_tracking_request(req.url):
            route.abort()
        else:
            _continue_with_language_policy(route)
    except Exception:
        try:
            _continue_with_language_policy(route)
        except Exception:
            pass


def _block_tracking_route(route) -> None:
    """FULL-mode router (homepage): drop ONLY trackers — first-party images/CSS/fonts still load
    so the screenshot and product-image URLs are unaffected."""
    try:
        if _is_tracking_request(route.request.url):
            route.abort()
        else:
            _continue_with_language_policy(route)
    except Exception:
        try:
            _continue_with_language_policy(route)
        except Exception:
            pass


def _continue_with_language_policy(route) -> None:
    """Let one already-allowed request proceed, carrying THIS crawl's language policy.

    Every `route.continue_()` in the browser path goes through here so the policy cannot be
    neutral on the first request and quietly biased on the rest.

    Default (`ACCEPT_LANGUAGE is None`): the header is REMOVED, so the server applies its own
    default representation and the crawl observes that choice instead of steering it. Chromium
    always emits an `Accept-Language` derived from the context locale, so removing the header is
    an explicit act — leaving it alone would ship the locale as a preference.

    This changes only the headers of a request the request-safety boundary has ALREADY allowed;
    it never converts an abort into a continue.
    """
    try:
        if ACCEPT_LANGUAGE is None:
            headers = {k: v for k, v in route.request.headers.items()
                       if k.lower() != "accept-language"}
        else:
            headers = {**route.request.headers, "accept-language": ACCEPT_LANGUAGE}
        route.continue_(headers=headers)
    except Exception:  # noqa: BLE001 — a header rewrite must never lose the request itself
        try:
            route.continue_()
        except Exception:
            pass


_BROWSER_LOCAL_SCHEMES = frozenset({"about", "blob", "data"})


def _browser_request_is_allowed(url: str) -> bool:
    """True for a public HTTP(S) destination or a non-network browser-local URL."""
    from urllib.parse import urlparse

    try:
        scheme = urlparse(str(url or "")).scheme.lower()
    except Exception:
        return False
    if scheme in _BROWSER_LOCAL_SCHEMES:
        return True
    if scheme not in ("http", "https"):
        return False
    from .url_utils import is_safe_public_url
    return is_safe_public_url(str(url))


def _is_rate_limited(result: "FetchResult") -> bool:
    return (result.error_code is ErrorCode.HTTP_ERROR
            and (result.http_status or 0) == 429)


def fetch_page(context: BrowserContext, url: str, keep_page: bool = False,
               light: bool = False, rate_limit_retries: int = 0,
               two_phase: bool = False) -> FetchResult:
    """Fetch one page, retrying ONLY transient transport failures.

    A successful or permanently-failed result returns immediately. A transient
    failure (DNS/HTTP2/timeout) is retried up to RETRY_ATTEMPTS times with linear
    backoff. A failed attempt always closes its own page (see _fetch_page_once's
    finally), so retries never leak a page; only a successful keep_page result
    holds the live Page open for the caller.

    `rate_limit_retries` grants a LARGER, 429-only patience budget for a make-or-break
    fetch — the homepage. A homepage 429 loses the ENTIRE scrape (0 pages -> "could not
    read this site"), yet a rate-limit cooldown clears in tens of seconds. So the caller
    can ask the homepage to wait out a 429 (6s,12s,18s,24s...) while every OTHER failure
    (and every subpage) still fails fast on the normal RETRY_ATTEMPTS budget — no site is
    ever hammered, only politely waited on.
    """
    result = _fetch_page_once(context, url, keep_page=keep_page, light=light, two_phase=two_phase)
    attempt = 0
    while not result.ok and _is_transient(result):
        budget = max(RETRY_ATTEMPTS, rate_limit_retries) if _is_rate_limited(result) \
            else RETRY_ATTEMPTS
        if attempt >= budget:
            break
        attempt += 1
        time.sleep(_retry_delay_s(result, attempt))
        result = _fetch_page_once(context, url, keep_page=keep_page, light=light, two_phase=two_phase)
    if attempt and result.error_message:
        result.error_message = f"[retry {attempt}] {result.error_message}"
    return result


def two_phase_route_decision(*, url: str, resource_type: str, block_below_fold: bool) -> str:
    """The two-phase routing DECISION, extracted pure (CS-54/70: the test used to re-implement
    this logic instead of executing it). Returns 'abort' | 'continue'. The closure inside
    _fetch_page_once delegates here — one truth, directly testable."""
    if _is_tracking_request(url):
        return "abort"
    if block_below_fold and resource_type == "image" and not _is_logo_like(url):
        return "abort"
    return "continue"


def _fetch_page_once(context: BrowserContext, url: str, keep_page: bool = False,
                     light: bool = False, two_phase: bool = False) -> FetchResult:
    """Fetch one page (single attempt). Caller is responsible for the context
    lifecycle.

    `light=True` (sub-page mode): block heavy resources (images/media/fonts) and skip the
    full-page screenshot — sub-pages need text + links, not pixels. Big render-time saving on
    image-heavy e-commerce; scripts/XHR/CSS still load so JS content is preserved.

    `two_phase=True` (FULL homepage, the owner's footprint ruling): let ABOVE-fold images load
    (logo/header/hero -> real logo dims + a clean palette), then after the initial load block
    BELOW-fold product-image BYTES during the scroll (their URLs are still collected from the DOM).
    Logo-like URLs stay whitelisted throughout. Cuts the ~106-product-image bulk with zero content
    loss and no logo-detection regression. `--thorough` disables it (caller passes two_phase=False).

    If keep_page=True, the returned FetchResult carries the live Page
    so extractors can run page.evaluate(). Caller MUST close the page
    after use (`result._page.close()`).
    """
    started = time.monotonic()
    result = FetchResult(
        url=url,
        final_url=url,
        http_status=None,
        fetched_at=utc_now(),
        duration_ms=0,
    )

    # SSRF guard (P-60): this is the server-side fetch sink for a user/link-derived URL. Every
    # fetch — the homepage AND every crawl-frontier subpage routes through here — so guarding the
    # fetcher makes SSRF a property of the SCRAPER, not of one caller (api/routes/run.py only
    # validated the entry string). Reject before opening a page for anything that resolves to a
    # non-public IP (loopback / RFC1918 / link-local cloud-metadata 169.254.169.254 / reserved).
    from .url_utils import is_safe_public_url
    if not is_safe_public_url(url):
        result.error_code = ErrorCode.BLOCKED_URL
        result.error_message = f"blocked non-public URL: {url[:200]}"
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    # Routing: LIGHT drops heavy resources + trackers. FULL homepage drops trackers; in two-phase
    # mode it ALSO blocks below-fold image bytes once `_img_gate["block"]` is flipped after the
    # above-fold load (logo-like URLs stay allowed for correct logo dims).
    _img_gate = {"block": False}
    _blocked_request = {"url": ""}

    def _two_phase_route(route) -> None:
        try:
            req = route.request
            verdict = two_phase_route_decision(url=req.url, resource_type=req.resource_type,
                                               block_below_fold=_img_gate["block"])
            if verdict == "abort":
                route.abort(); return
            _continue_with_language_policy(route)
        except Exception:  # noqa: BLE001
            try:
                _continue_with_language_policy(route)
            except Exception:
                pass

    content_handler = (_block_heavy_route if light
                       else (_two_phase_route if two_phase else _block_tracking_route))

    def _request_safety_route(route) -> None:
        try:
            destination = str(route.request.url or "")
        except Exception:  # an unclassifiable destination is not safe to continue
            destination = ""
        if not _browser_request_is_allowed(destination):
            _blocked_request["url"] = destination or "<unavailable>"
            try:
                route.abort()
            except Exception:
                pass
            return
        content_handler(route)

    try:
        page.route("**/*", _request_safety_route)
    except Exception as exc:
        # Continuing without the interceptor would silently restore the exact bypass
        # this boundary exists to close.  Fail visibly before navigation instead.
        result.error_code = ErrorCode.BLOCKED_URL
        result.error_message = f"request-safety interceptor unavailable: {type(exc).__name__}"
        result.duration_ms = int((time.monotonic() - started) * 1000)
        try:
            page.close()
        except Exception:
            pass
        return result

    # API-driven SPA capture: some sites (Angular/React) expose almost no <a href> links and load
    # their real content from their OWN JSON APIs (ITI: iti.gov.eg shell + pgateway.iti.gov.eg
    # program catalogue). Capture same-registrable-domain JSON responses during the render so that
    # content isn't invisible to a link-follower. Bounded; best-effort; never fails the fetch.
    _xhr_bodies: list[tuple[str, str]] = []
    _XHR_MAX = 30

    def _capture_json(resp) -> None:
        try:
            if len(_xhr_bodies) >= _XHR_MAX:
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return
            from .url_utils import same_registrable_host
            if not same_registrable_host(resp.url, url):
                return
            body = resp.text()
            if body and len(body) <= 400_000:
                _xhr_bodies.append((resp.url, body))
        except Exception:  # noqa: BLE001 — a response body may be unavailable/redirect; skip it
            pass

    try:
        page.on("response", _capture_json)
    except Exception:  # noqa: BLE001
        pass

    try:
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None:
            result.http_status = response.status
            if response.status in (429, 503):
                try:
                    ra = (response.headers or {}).get("retry-after")
                    if ra and ra.strip().isdigit():
                        result.retry_after_s = float(ra.strip())
                except Exception:  # noqa: BLE001 — headers may be unavailable; backoff covers it
                    pass
        result.final_url = page.url

        # SSRF guard, post-redirect (P-60): a public host can 302 to an internal target
        # (e.g. 169.254.169.254). page.goto follows redirects, so re-validate the FINAL url and
        # discard the captured content before anything is saved to the manifest.
        if result.final_url and result.final_url != url and not is_safe_public_url(result.final_url):
            result.error_code = ErrorCode.BLOCKED_URL
            result.error_message = f"redirect to non-public URL blocked: {result.final_url[:200]}"
            result.html = ""
            result.rendered_text = ""
            return result

        # Wait briefly for additional content; networkidle can hang forever
        # on sites with long-poll connections, so we cap it.
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeout:
            pass

        # Two-phase: the above-fold load is done (logo/header/hero have real bytes+dims). From here,
        # block below-fold product-image bytes — the scroll still fires their lazy src (URLs kept),
        # only the pixels are skipped.
        if two_phase:
            _img_gate["block"] = True

        # Trigger lazy content
        _scroll_to_load(page)

        # Grab HTML + text + title
        result.html = page.content()
        result.rendered_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        title = page.title() or ""

        # Fold in any API-driven SPA content captured during the render (see _capture_json above),
        # so the SAME text extraction that reads a normal page now also sees the SPA's JSON content
        # (programme names, contacts, partners) that never appears in the HTML.
        if _xhr_bodies:
            try:
                from .xhr_capture import extract_json_text
                extra = extract_json_text(_xhr_bodies)
                if extra:
                    result.api_text = extra                       # -> citable text_blocks (crawler)
                    result.rendered_text = (result.rendered_text.rstrip() + "\n" + extra).strip()
            except Exception:  # noqa: BLE001
                pass

        # Bot protection check (P-63: pass status + html + WAF headers for language-independent
        # detection, not just the English display text).
        try:
            _resp_headers = response.headers if response is not None else None
        except Exception:  # noqa: BLE001 — headers may be unavailable on some responses
            _resp_headers = None
        bot_code = _detect_bot_protection(result.rendered_text, title,
                                          http_status=result.http_status,
                                          html=result.html, headers=_resp_headers)
        if bot_code is not None:
            result.error_code = bot_code
            result.error_message = f"Bot protection detected (title='{title[:80]}')"

        # Empty DOM check (after possible bot detection). CONTENT-BASED (P-61): the old guard was
        # `not text AND "<body" not in html` — but almost every document has a <body> tag, so it
        # only caught malformed HTML, never the common "body present, zero rendered content" case
        # (a failed SPA / JS wall / blank shell). marasim shipped 0-char scrapes as pages_succeeded
        # that way. Now flag on the visible-text length directly, regardless of the <body> tag.
        elif len(result.rendered_text.strip()) < MIN_RENDERED_CHARS:
            result.error_code = ErrorCode.EMPTY_RENDERED_DOM
            result.error_message = (
                f"Rendered DOM has too little visible text "
                f"({len(result.rendered_text.strip())} < {MIN_RENDERED_CHARS} chars)"
            )

        elif not light:
            # Screenshots — only when content is real, and only for FULL fetches. The homepage
            # needs them for visual identity; LIGHT sub-pages skip them (pixels aren't used, and
            # the full-page screenshot is the slowest step + can hang on font loads). In two-phase
            # mode the below-fold images are blocked, so use the clean above-fold viewport.
            _capture_screenshots(page, result, above_fold_only=two_phase)

        # HTTP status flag (don't fail hard on 4xx — record but continue)
        if result.http_status and result.http_status >= 400 and result.error_code is None:
            result.error_code = ErrorCode.HTTP_ERROR
            result.error_message = f"HTTP {result.http_status}"

        _finalize_hashes_and_bytes(result)

    except PWTimeout as e:
        msg = str(e)[:300]
        if _salvage_partial_dom(page, result, msg):
            # Treat as processable content, but preserve the warning details.
            result.error_code = None
            result.error_message = result.partial_reason
        else:
            result.error_code = ErrorCode.TIMEOUT
            result.error_message = msg
    except PWError as e:
        msg = str(e).lower()
        if _blocked_request["url"]:
            result.error_code = ErrorCode.BLOCKED_URL
            result.error_message = (
                "blocked non-public browser request: " + _blocked_request["url"][:200]
            )
        elif "net::err_name_not_resolved" in msg or "dns" in msg:
            result.error_code = ErrorCode.NETWORK_ERROR
        elif "ssl" in msg or "cert" in msg:
            result.error_code = ErrorCode.NETWORK_ERROR
        else:
            result.error_code = ErrorCode.RENDER_ERROR
        result.error_message = str(e)[:300]
    except Exception as e:
        result.error_code = ErrorCode.UNKNOWN_ERROR
        result.error_message = f"{type(e).__name__}: {e}"[:300]
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if keep_page and result.ok:
            result._page = page
        else:
            try:
                page.close()
            except Exception:
                pass

    return result
