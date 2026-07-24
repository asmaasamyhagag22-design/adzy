"""LIGHT competitor dimensions for the sync web path (no Playwright, no crawl).

The web SWOT route can't run the full scraper per peer (minutes each), so web/SERP
peers carried NO scraped dimensions -> every gap stayed "n/a" -> a B2B/ECOMMERCE
SWOT could never show Opportunities/Threats in the web app. This helper fetches ONE
homepage over plain HTTP (browser UA, bounded read, SSRF-guarded) and derives the
CHEAP dimensions the matrix can honestly compare:

    social_count / cta_count   — from the real anchor inventory (same extractor +
                                 dedup keys as the full scraper)
    whatsapp                   — wa.me / api.whatsapp link present
    online_booking             — a booking-verb CTA anchor present

Everything the lite fetch can NOT honestly tell (offerings/trust — LLM-stage;
shows_reviews; bilingual) stays None = UNKNOWN (rule 4 — never inferred). A JS-only
site that serves an empty shell (no anchors in the server HTML) yields NO dims — its
social/CTA/WhatsApp are JS-injected, so reporting "0 social / no WhatsApp" would be
evidence-of-ABSENCE dressed as knowledge; we return None (UNKNOWN) instead. Fetch/parse
failures also yield None (the peer's cells stay UNKNOWN, never fabricated).

Returns a plain dims dict — `matrix.extract_scraped_dimensions` passes it through.
Never raises.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Callable, Optional

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_MAX_BYTES = 1_500_000
_TIMEOUT_S = 10


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """CS-48: `is_safe_public_url` only vetted the FIRST URL; urlopen then followed a 302 to any
    host, so a public page 302-ing to http://169.254.169.254/ (cloud metadata) or 127.0.0.1 was an
    SSRF sink. Re-validate every redirect target and refuse an unsafe one — the exact hole P-60
    closed in the main scraper but never enumerated on this sink."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from scraper.url_utils import is_safe_public_url
        if not is_safe_public_url(newurl):
            raise urllib.error.HTTPError(newurl, code,
                                         "refused redirect to a non-public host (SSRF guard)",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(url: str) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept-Language": "en,ar;q=0.8"})
    # build_opener with the safe redirect handler so 302s are re-validated (SSRF guard).
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(req, timeout=_TIMEOUT_S) as resp:
        raw = resp.read(_MAX_BYTES)
    return raw.decode("utf-8", errors="replace")


def lite_peer_dims(url: str, *, fetch: Optional[Callable[[str], Optional[str]]] = None
                   ) -> Optional[dict]:
    """Homepage -> partial dims dict (or None). SSRF-guarded; never raises."""
    try:
        from scraper.url_utils import is_safe_public_url
        if not url or not is_safe_public_url(url):
            return None
        html = (fetch or _default_fetch)(url)
        if not html or not html.strip():
            return None

        from scraper.extractors.links import extract_links_from_html
        from scraper.schemas import LinkCategory
        records = extract_links_from_html(html, url, url)
        if not records:
            # No anchors in the server HTML — a client-rendered (JS-only) shell or an empty
            # page. Its social/CTA/WhatsApp load via JS, so we CANNOT honestly assess them:
            # return UNKNOWN rather than report false zeros ("0 social / no WhatsApp") as fact.
            return None

        social = {r.href.rstrip("/") for r in records
                  if r.category == LinkCategory.SOCIAL}
        ctas = {(r.href.rstrip("/"), (r.anchor_text or "").strip()) for r in records
                if r.category == LinkCategory.CTA}
        blob = " ".join(f"{h} {a}".lower() for h, a in ctas)
        whatsapp = any("wa.me" in r.href or "api.whatsapp.com" in r.href
                       for r in records) or "whatsapp" in blob
        booking = any(w in blob for w in
                      ("book", "reserve", "appointment", "احجز", "حجز", "موعد"))

        return {
            "online_booking": booking,
            "whatsapp": whatsapp,
            "shows_reviews": None,      # not detectable from a lite fetch — UNKNOWN
            "cta_count": len(ctas),
            "offerings_count": None,    # LLM-stage only — UNKNOWN
            "bilingual": None,          # needs real language detection — UNKNOWN
            "trust_count": None,        # LLM-stage only — UNKNOWN
            "social_count": len(social),
        }
    except Exception:  # noqa: BLE001 — a peer fetch failure must never break the SWOT
        return None
