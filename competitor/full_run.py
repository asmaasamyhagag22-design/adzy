"""
full_run.py -- end-to-end: URL -> BusinessProfile -> competitors -> matrix -> SWOT.

Lives in competitor/. Run from the PROJECT ROOT. Both styles work:

    python -m competitor.full_run https://some-clinic.com
    python competitor/full_run.py  https://some-clinic.com
    python competitor/full_run.py  https://some-clinic.com --no-themes

Needs in your .env (this script loads it before any stage runs):
    GOOGLE_MAPS_API_KEY    Places discovery            (you confirmed: working)
    GOOGLE_CLOUD_PROJECT   subject BusinessProfile + review-theme extraction (Gemini 2.5 Pro,
                           Vertex); review themes are optional (--no-themes skips)

Output is forced to UTF-8 so Arabic review text won't crash the Windows console.
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Make the project root importable whether launched as
#   python -m competitor.full_run ...   (root already on sys.path)
# or
#   python competitor/full_run.py ...   (only competitor/ is on sys.path by default)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_env():
    """Load KEY=VALUE pairs from the project-root .env into os.environ (only
    for keys not already set in the shell), so Google/Gemini + any fallback all see
    their keys before any stage runs. Dependency-free."""
    for folder in (_ROOT, *_ROOT.parents, Path.cwd()):
        env_path = folder / ".env"
        if not env_path.is_file():
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, val = line.partition("=")
                    name = name.strip()
                    if name:
                        os.environ.setdefault(name, val.strip().strip('"').strip("'"))
        except OSError:
            continue
        break  # first .env found wins


_load_env()  # must happen before importing/using anything that reads keys

from scraper import scrape
from business_profile.build import build_profile
from business_profile.llm import OpenAICaller, default_caller
from competitor import (
    PlacesClient,
    route_discovery,
    build_matrix,
    ReviewThemeExtractor,
    synthesize_swot,
    format_swot,
)
from competitor.swot import unique_insight_texts


def scrape_fn(url):
    """What build_matrix calls to scrape each benchmark competitor's site. LIGHT crawl — the matrix
    only needs surface signals (whatsapp/cta/social/offerings counts), and a full store crawl PER
    competitor made an e-commerce analysis time out (>25 min)."""
    manifest, _ = scrape(url, light=True)
    return manifest


def scrape_yielded_nothing(manifest) -> bool:
    """True when the crawl fetched ZERO usable pages — the site blocked us, was unreachable, or had
    an invalid cert. Building a profile from an empty manifest yields a hollow, ungrounded result;
    the caller must surface a clear failure instead of proceeding on garbage."""
    return len(getattr(manifest, "pages", []) or []) == 0


def _apify_html(item: dict, text: str) -> str:
    """Rebuild a page with a real <head> from what Apify actually returned.

    MEASURED (pepsi.com, 2026-07-21): the website-content-crawler returns READABILITY-extracted
    markup — `<div id="readability-content">…` with no head, no <title>, no <meta>, no og: tags.
    Every rule-based extractor reads exactly those for the brand name, description and visual
    identity, so a perfectly good 12-page fetch produced `name=None` and the SWOT then reported
    "No clear brand tagline" about PEPSI.

    Nothing is invented here: the title/description come from Apify's own `metadata`, and the H1
    is used only as a fallback when the crawler returned no title. A field we do not have is
    simply omitted — an empty `<title></title>` is worse than none, because it reads as "this
    brand has no title" rather than "we could not see it".
    """
    import html as _h
    import re as _re

    body = str(item.get("html") or f"<p>{_h.escape(text)}</p>")
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    title = str(meta.get("title") or "").strip()
    if not title:
        m = _re.search(r"<h1[^>]*>(.*?)</h1>", body, _re.I | _re.S)
        title = _re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
    desc = str(meta.get("description") or "").strip()
    lang = str(meta.get("languageCode") or "").strip()

    head = []
    if title:
        head.append(f"<title>{_h.escape(title)}</title>")
        head.append(f'<meta property="og:site_name" content="{_h.escape(title)}">')
    if desc:
        head.append(f'<meta name="description" content="{_h.escape(desc)}">')
    lang_attr = f' lang="{_h.escape(lang)}"' if lang else ""
    return f"<html{lang_attr}><head>{''.join(head)}</head><body>{body}</body></html>"


def recover_blocked_scrape(url, manifest, *, fetch=None, scrape_dir=None, log=None):
    """Last resort for a 0-page crawl: fetch through Apify and rebuild a REAL manifest.

    MEASURED (pepsi.com, 2026-07-21): our crawler drives a real headless Chromium and still gets
    HTTP 403 on the homepage, the sitemap AND robots.txt — the refusal is at the CDN edge, so no
    browser tuning reaches it. The owner's decision is to route those through Apify.

    The first version of this assigned Apify's raw dicts to `manifest.pages` and silently failed:
    a `PageRecord` (scraper/schemas.py) holds no text at all, only `html_path` / `clean_text_path`
    pointing at files the profile builder reads later. So the pages are WRITTEN to the same layout
    the crawler produces (`raw/*.html`, `clean/*.txt`) and recorded over it.

    Fires only on a genuinely empty crawl (a working site is never billed), stamps
    `content_source="apify"` so a paid third-party fetch can never read as our own crawl, and
    returns None on any failure so the honest block message stands.
    """
    if getattr(manifest, "pages", None):
        return None                       # the crawl worked — nothing to recover, nothing to bill
    try:
        fetch = fetch or (lambda u: __import__(
            "scraper.apify_fallback", fromlist=["apify_scrape"]).apify_scrape(u))
        res = fetch(url)
    except Exception:  # noqa: BLE001 — the fallback must never replace one failure with a crash
        return None
    items = list(getattr(res, "pages", None) or [])
    if not items:
        return None

    try:
        from datetime import datetime, timezone
        from hashlib import sha256
        from scraper.schemas import PageRecord, PageTier, PageType

        # Write INTO the run's own scrape directory. The first version defaulted to a sibling
        # `scrapes/apify_<ts>/`, which split the evidence away from the manifest that references
        # it — and made a test's fixture text land in the real scrapes/ folder.
        base = Path(scrape_dir) if scrape_dir else Path("scrapes") / (
            "apify_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
        raw, clean = base / "raw", base / "clean"
        raw.mkdir(parents=True, exist_ok=True)
        clean.mkdir(parents=True, exist_ok=True)

        records = []
        for i, it in enumerate(items):
            text = str(it.get("text") or it.get("markdown") or "").strip()
            if not text:
                continue
            html = _apify_html(it, text)
            stem = f"{i:02d}_{'homepage' if i == 0 else 'page'}"
            html_p, text_p = raw / f"{stem}.html", clean / f"{stem}.txt"
            html_p.write_text(html, encoding="utf-8")
            text_p.write_text(text, encoding="utf-8")
            page_url = str(it.get("url") or url)
            records.append(PageRecord(
                url=page_url, final_url=page_url, http_status=200,
                fetched_at=datetime.now(timezone.utc),
                fetch_duration_ms=0,          # fetched by Apify, not timed by us
                page_type=(PageType.HOMEPAGE if i == 0 else PageType.OTHER),
                tier=(PageTier.HIGH if i == 0 else PageTier.MEDIUM),
                viewport_width=1280, viewport_height=900,
                content_hash=sha256(text.encode("utf-8")).hexdigest(),
                html_path=str(html_p), clean_text_path=str(text_p)))
        if not records:
            return None
        manifest.pages = records

        # The brand's identity is read from `manifest.site_metadata`
        # (business_profile/rules/from_metadata.py:165), NOT from the per-page HTML. The crawler
        # fills it; a recovery that only rebuilt pages left it empty, so every metadata rule had
        # nothing to read and Pepsi came out nameless (MEASURED 2026-07-21). Fill it from the SAME
        # Apify metadata, and only where the crawler managed to read nothing itself.
        try:
            first = items[0] if items else {}
            meta = first.get("metadata") if isinstance(first.get("metadata"), dict) else {}
            sm = getattr(manifest, "site_metadata", None)
            if sm is not None:
                if not str(getattr(sm, "title", "") or "").strip():
                    title = str(meta.get("title") or "").strip()
                    if title:
                        sm.title = title
                if not str(getattr(sm, "description", "") or "").strip():
                    desc = str(meta.get("description") or "").strip()
                    if desc:
                        sm.description = desc
        except Exception:  # noqa: BLE001 — metadata is a bonus, never a blocker
            pass

        try:
            setattr(manifest, "content_source", getattr(res, "source", "apify"))
        except Exception:  # noqa: BLE001 — a frozen model must not lose the recovery
            pass
    except Exception:  # noqa: BLE001
        return None

    if log:
        log(f"      recovered {len(records)} page(s) via Apify (the site blocked our crawler)")
    return manifest


def profile_is_hollow(profile) -> bool:
    """True when extraction produced nothing identifiable — no name, no category, no description,
    no offerings.

    MEASURED (pepsi.com, 2026-07-21): a run finished "successfully" on such a profile and the SWOT
    reported "No clear brand tagline established on-site", "Thin value proposition", "Narrow
    offering range surfaced on-site". Every one is FALSE about Pepsi — they are OUR extraction
    failing, re-labelled as an audit of the brand. That is the most dangerous shape a bug can take
    here: it looks like insight.

    `scrape_yielded_nothing` already refuses the 0-page case for the same reason; this closes the
    step after it, where the crawl (or a fallback) delivered pages but nothing could be read.
    """
    if profile is None:
        return True

    def _val(name):
        v = getattr(profile, name, None)
        if isinstance(v, dict):
            v = v.get("value")
        v = getattr(v, "value", v)
        return str(v).strip() if v is not None else ""

    if _val("name") or _val("category") or _val("description"):
        return False
    offerings = getattr(profile, "offerings", None) or []
    return not list(offerings)


def scrape_failure_message(failures) -> str:
    """The one sentence the owner reads when a crawl returns zero pages.

    MEASURED (pepsi.com, 2026-07-21): the old message pasted a Python repr
    (`<ErrorCode.BOT_PROTECTION: ...> occurred_at=datetime.datetime(...)`) and offered three very
    different causes at once — "blocks crawlers, is down, or has an invalid certificate" — even
    though the failure object already says which one it is. A deliberate block, a temporary
    throttle and a dead domain need three different next steps.

    Verified independently: pepsi.com and pepsico.com return HTTP 403 to an ordinary browser
    user-agent, so a block is a real, permanent property of some sites — not our bug.
    """
    text = " ".join(f"{getattr(f, 'code', '')} {getattr(f, 'message', '')}"
                    for f in (failures or [])).upper()
    if "429" in text or "RATE" in text:
        return ("الموقع طلب مننا نبطّأ (429) — هو شغّال ومش بيمنعنا. استني دقيقة وحلّليه تاني.")
    if "BOT" in text or "403" in text or "FORBIDDEN" in text or "CAPTCHA" in text:
        return ("الموقع ده بيمنع الزواحف عمداً (حماية بوت) — مش عطل عندنا ومش هيتفتح بإعادة "
                "المحاولة. جرّبي رابط تاني للبراند: موقع الفرع المحلي، أو الموقع الرسمي للشركة "
                "الأم، أو صفحة المتجر.")
    if "DNS" in text or "RESOLVE" in text or "TIMEOUT" in text or "CONNECT" in text:
        return ("مقدرناش نوصل للموقع أصلاً — اتأكدي إن الرابط مكتوب صح وإن الموقع شغّال.")
    if "CERT" in text or "SSL" in text:
        return ("شهادة أمان الموقع غير صالحة، فالمتصفحات والزواحف بتوقفه — دي حاجة عند صاحب الموقع.")
    return ("مقدرناش نقرا الموقع (صفر صفحات). يمكن بيمنع الزواحف، أو واقع، أو الرابط غلط.")


def rate_limited_failure(failures) -> bool:
    """True when a 0-page crawl failed on an HTTP 429 (rate limit) — a TEMPORARY throttle, not a
    block or an outage. Drives an honest, actionable message ("wait a minute and retry") instead of
    the misleading "site may block crawlers / be unreachable"."""
    return any("429" in str(getattr(f, "message", "")) for f in (failures or []))


def _make_caller():
    """build_profile needs an explicit LLM caller. Use the PRIMARY prod caller (Gemini/Vertex — the
    GCP credits are the budget, and it's what poster/reel/strategy already use), falling back to
    OpenAI only if Gemini can't be built. MEASURED (ITI, 2026-07-07): gpt-4o-mini extracted 0
    offerings from the API-recovered SPA blocks where Gemini extracted 7 — the dashboard was silently
    running the weaker extractor while everything downstream ran on Gemini."""
    try:
        return default_caller(strong=True)
    except Exception:  # noqa: BLE001 — no Gemini/Vertex -> OpenAI fallback
        try:
            return OpenAICaller()
        except TypeError:
            return OpenAICaller(model="gpt-4o-mini")


def _as_profile(built):
    """build_profile may return the BusinessProfile directly OR a BuildResult
    wrapper -- handle both without guessing which."""
    for attr in ("profile", "business_profile"):
        if hasattr(built, attr):
            return getattr(built, attr)
    return built


def _category_str(profile):
    """category may be a plain enum/str or a wrapped field with .value."""
    cat = getattr(profile, "category", None)
    val = getattr(cat, "value", cat)              # unwrap a field-with-value
    return getattr(val, "value", val)             # unwrap an enum


def _voice_files_for(url: str, dirs=None) -> list:
    """Saved review/signal files that belong to THIS subject, matched by normalized-slug
    prefix (alameda_hc matches alameda-hc.com; >=5 chars so demo fixtures never false-hit)."""
    import re as _re
    from pathlib import Path
    from urllib.parse import urlparse
    key = _re.sub(r"[^a-z0-9]", "", _re.sub(r"^www\.", "", (urlparse(url).netloc or "").lower()))
    hits, seen = [], set()
    for d in (dirs or [Path("reviews/fixtures"), Path("runs/review_snapshots"),
                       Path("social_intel/fixtures")]):
        if not Path(d).is_dir():
            continue
        for f in sorted(Path(d).glob("*.json")):
            stem = _re.sub(r"[^a-z0-9]", "", f.stem.lower())
            if len(stem) >= 5 and (key.startswith(stem) or stem.startswith(key)) \
                    and f.stem not in seen:
                seen.add(f.stem)                  # fixtures win over same-name raw snapshots
                hits.append(f)
    return hits


def _own_voice_themes(url: str, profile_dict, say, *, caller=None) -> list:
    """SWOT-ready ReviewThemes from the subject's OWN saved customer voice (Google-Maps
    reviews + Facebook signals). Parse-only; hashing already happened at ingestion."""
    import json as _json
    rows = []
    for f in _voice_files_for(url):
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and "reviews" in data:      # raw Maps snapshot
            from reviews.providers.google_maps import reviews_from_snapshot
            own = bool(data.get("is_own_brand", True))
            rows += [{"id": f"V{len(rows) + i + 1}", "text": r.text, "is_own_brand": own}
                     for i, r in enumerate(reviews_from_snapshot(data))]
        elif isinstance(data, list):                          # sanitized fixture (Maps or FB)
            for r in data:
                if isinstance(r, dict) and str(r.get("text") or "").strip():
                    rows.append({"id": f"V{len(rows) + 1}", "text": str(r["text"]),
                                 "is_own_brand": bool(r.get("is_own_brand", True))})
    if not rows:
        return []
    if caller is None:
        caller = _make_caller()
    cat = None
    if isinstance(profile_dict, dict):
        c = profile_dict.get("category")
        cat = c.get("value") if isinstance(c, dict) else c
    from reviews.absa import extract_aspect_themes, themes_for_swot
    found = extract_aspect_themes(rows, category=str(cat or ""), caller=caller)
    say("      customer voice: %d saved rows -> %d grounded theme(s)" % (len(rows), len(found)))
    return themes_for_swot(found)


def main():
    _load_env()

    ap = argparse.ArgumentParser(description="URL -> profile -> competitors -> SWOT")
    ap.add_argument("url", help="subject business URL (use one with a REAL website)")
    ap.add_argument("--thorough", action="store_true",
                    help="load EVERY homepage image (disables the two-phase below-fold image-byte "
                         "block) — slower/heavier; the rare site that needs every image.")
    ap.add_argument("--no-themes", action="store_true",
                    help="skip the Gemini review-theme extraction step")
    ap.add_argument("--no-media-plan", action="store_true",
                    help="skip the U1 media-plan deduction (objective/destination/KPI/persona/geo)")
    ap.add_argument("--json", action="store_true",
                    help="print the SWOT as JSON to stdout (progress goes to stderr)")
    ap.add_argument("--out", default=None,
                    help="path to write the full result JSON (profile + competitors + "
                         "SWOT). Default: result.json inside the scrape's output folder.")
    args = ap.parse_args()

    # Progress -> stderr when --json, so stdout stays valid, pipeable JSON.
    def say(msg=""):
        print(msg, file=sys.stderr if args.json else sys.stdout)

    # The profile build needs a working LLM caller. Gemini/Vertex is the PRIMARY (default_caller,
    # via GOOGLE_CLOUD_PROJECT), OpenAI is only the fallback — so require EITHER, not OPENAI_API_KEY
    # specifically (a stale gate from before the all-Gemini migration blocked runs on Gemini-only .env).
    if not (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("!! No LLM credentials found. Set GOOGLE_CLOUD_PROJECT (Gemini/Vertex, primary) "
              "or OPENAI_API_KEY (fallback) in your .env and re-run.", file=sys.stderr)
        return 2

    say("[1/5] scraping subject site: %s" % args.url)
    manifest, scrape_dir = scrape(args.url, thorough=args.thorough)

    # A 0-page crawl (blocked / unreachable / bad cert) would otherwise build a hollow profile and a
    # garbage dashboard that LOOKS successful. Fail loudly instead so the studio shows a clear reason.
    if scrape_yielded_nothing(manifest):
        # The site refused OUR crawler. Try Apify before giving up (owner's decision, 2026-07-21);
        # it fetches from its own infrastructure and the manifest records that provenance.
        say("      our crawler got 0 pages — trying the Apify fallback...")
        if recover_blocked_scrape(args.url, manifest, scrape_dir=scrape_dir,
                                  log=say) is not None:
            say("      [OK] recovered via Apify")

    if scrape_yielded_nothing(manifest):
        failures = getattr(manifest, "failures", []) or []
        fails = "; ".join(str(f) for f in failures[:2])
        # HONEST distinction: a 429 means the site is RATE-LIMITING us (temporary — it works
        # again after a short cooldown), NOT that it blocks crawlers or is down. Telling the
        # user "may block scraping / be unreachable" on a 429 is misleading (the site is fine).
        print("!! " + scrape_failure_message(failures), file=sys.stderr)
        if fails:                       # the raw failure stays, for us, on its own line
            print("   [debug] " + fails[:300], file=sys.stderr)
        return 3

    say("[2/5] building BusinessProfile (OpenAI extraction)...")
    caller = _make_caller()
    profile = _as_profile(build_profile(manifest, caller, use_rag=True))
    if not hasattr(profile, "category"):
        print("!! build_profile() didn't return something with a .category field.", file=sys.stderr)
        return 1
    # A crawl can succeed (or a fallback half-succeed) and extraction still yield nothing. Shipping
    # that produces confident-sounding FALSE findings about the brand — measured on pepsi.com.
    if profile_is_hollow(profile):
        print("!! اتقرت صفحات بس مطلعناش منها أي هوية للبراند (اسم/تصنيف/وصف/منتجات) — "
              "غالباً الموقع بيحمّل محتواه بجافاسكريبت أو بيقدّم نسخة فاضية للزواحف. "
              "مش هنكمّل بتحليل مبني على فراغ.", file=sys.stderr)
        return 3

    say("      subject category = %s" % _category_str(profile))

    say("[3/5] discovering competitors (adaptive router)...")
    # P-81 (D-8): search the BRAND'S market, not a hardcoded EG/ar. gl/hl come from the brand's own
    # evidence (same resolver the trends SERP uses below); empty -> Places' own default, never EG.
    try:
        from poster.locale import search_locale
        _gl, _hl = search_locale(profile or {})
    except Exception:  # noqa: BLE001 — a resolver failure must not force a wrong market
        _gl, _hl = "", ""
    client = PlacesClient(region_code=(_gl or "").upper(), language_code=_hl or "")
    from competitor.web_discovery import default_web_engine
    web_engine = default_web_engine()
    if web_engine is not None:
        say("      web discovery: %s (search peers for ECOMMERCE/HYBRID)" % web_engine.name)
    # Adaptive routing: LOCAL->Places, ECOMMERCE->SERP web engine (when a search
    # key is set), HYBRID->both, UNKNOWN->skip. Never raises; an empty result
    # degrades to a standalone SWOT below instead of stopping the run.
    result = route_discovery(profile, places_client=client, manifest=manifest,
                             web_engine=web_engine)
    n = len(result.competitors)
    scrapable = sum(1 for c in result.competitors
                    if getattr(c, "has_scrapable_site", False))
    say("      competitors=%d  scrapable_benchmarks=%d" % (n, scrapable))
    for note in getattr(result, "notes", []) or []:
        say("   note: %s" % note)
    if n == 0:
        say("   -> no competitors; continuing in STANDALONE mode (profile-only SWOT).")

    # COMPETITOR REGISTRY (owner-caught non-determinism, 2026-07-12): the first approved
    # discovery persists as the STABLE baseline; later runs DIFF (present/disappeared/
    # new-pending) instead of silently replacing. SWOT computes against the APPROVED set, not
    # the raw live pool — so "who are my competitors" stops drifting run-to-run. Newcomers are
    # PENDING (owner approves via the dashboard, HITL); never auto-added. Best-effort: any
    # failure degrades to the raw pool (today's behaviour), never blocks the run.
    registry_diff = None
    matrix_competitors = result.competitors
    try:
        from datetime import datetime, timezone
        from competitor.registry import stable_competitors
        _now = datetime.now(timezone.utc).date().isoformat()
        # SWOT computes against the APPROVED baseline, never the raw live pool — and the approved
        # set is authoritative EVEN WHEN EMPTY. This was `if approved:` (P6 #11b): rejecting every
        # peer via HITL yields [], which is falsy, so matrix_competitors silently kept the raw pool
        # and the rival the owner explicitly removed came back in the next SWOT.
        matrix_competitors, registry_diff = stable_competitors(args.url, result.competitors,
                                                               now=_now)
        d = registry_diff
        say("   registry: %d present, %d disappeared, %d new pending owner approval"
            % (len(d["still_present"]), len(d["disappeared"]), len(d["new_pending"])))
    except Exception as exc:  # noqa: BLE001 — registry never blocks a run
        say("   registry skipped (%s)" % type(exc).__name__)

    say("[4/5] building comparison matrix (scrapes %d benchmark site(s))..." % scrapable)
    # The subject's OWN Places listing (rating/volume/price) — without it every
    # Places gap is "n/a" and market-position Threats can never fire. Grounded
    # match (domain / exact name) only; None on no-match, never raises.
    from competitor import find_subject_places
    subject_places = find_subject_places(profile, client)
    if subject_places is not None:
        say("      subject Places listing: %s (rating=%s, reviews=%s)" % (
            subject_places.name, subject_places.rating, subject_places.review_count))
    matrix = build_matrix(
        manifest,                 # reuse the subject's manifest -> no re-scrape of subject
        matrix_competitors,       # the APPROVED registry set (stable), not the raw live pool
        scrape_fn=scrape_fn,
        subject_places=subject_places,
    )

    themes = []
    if args.no_themes:
        say("[5/5] skipping review themes (--no-themes)")
    else:
        say("[5/5] extracting grounded review themes (Gemini 2.5 Pro)...")
        # P-79: themes from the APPROVED registry set, not the raw live pool (which drifts run-to-run).
        themes = ReviewThemeExtractor()(matrix_competitors)
        try:
            say("      kept %d grounded theme(s)" % len(themes))
        except TypeError:
            pass

    # Serialize the profile ONCE — the SWOT now mines it for brand-level strengths (value props +
    # proof, Ledger-gated) and TOWS reuses it downstream.
    profile_dict = (profile.model_dump(mode="json")
                    if hasattr(profile, "model_dump") else (profile if isinstance(profile, dict) else None))

    # OWN-BRAND CUSTOMER VOICE (owner directive 2026-07-12 — the review work must SHOW on the
    # dashboard): saved review snapshots/fixtures of the SUBJECT feed ABSA (verbatim-quote gate,
    # >=2 threshold) and enter the SWOT as evidence-quoted Strengths/Weaknesses (R-5). Parse-only
    # (PD-4): reads what scripts/pull_reviews.py already saved; no saved data -> nothing, silently.
    try:
        voice = _own_voice_themes(args.url, profile_dict, say)
        if voice:
            themes = list(themes) + voice
    except Exception as exc:  # noqa: BLE001 — customer voice never blocks an analyze
        say("      customer voice skipped (%s)" % type(exc).__name__)

    # On-topic market trends -> brand-level Opportunities/Threats (esp. for online brands with no
    # Places peers). Best-effort: any failure (offline / no key) degrades to [] — never blocks.
    trends: list = []
    _trend_kws: list = []      # kept for the trends artifact (outputs/<slug>_trends.json)
    _trend_gl = _trend_hl = ""
    try:
        from trends import keywords_from_profile, top_trends_bounded
        kws = keywords_from_profile(profile_dict or {})
        _trend_kws = list(kws or [])
        # D-8 (2026-07-15): search the BRAND'S market, not Google's default. Without gl/hl the
        # SERP answers for the US in English, and the dashboard then labels the result
        # "إشارات السوق الحيّة · Live market signals" — a real URL answering a question about
        # somebody else's market. ("", "") when the evidence is silent -> no market claim.
        try:
            from poster.locale import search_locale
            gl, hl = search_locale(profile_dict or {})
        except Exception:  # noqa: BLE001 — a resolver failure must not cost us the trends
            gl, hl = "", ""
        _trend_gl, _trend_hl = gl, hl
        if kws:                              # HARD 12s deadline — a hung source never blocks analyze
            trends = top_trends_bounded(kws, timeout_s=12.0, require_match=True, top_k=6,
                                        gl=gl, hl=hl)
    except Exception:
        trends = []

    # ش1 (owner ruling 2026-07-19: FULL-AUTO with caps): when META_ADS_TOKEN exists, run the
    # adaptive batch pulls (20×batch, confidence early-stop, hard cap 200, call ledger) for the
    # discovered competitors — writing fresh snapshots the presence reader below then consumes.
    # Every failure degrades to the old snapshot-only behavior; the run NEVER breaks on ads intel.
    # IG autopull (owner ruling 2026-07-19 "عمّميها"): the brand's OWN public Instagram feeds the
    # evidence-bound marketing intel automatically — Apify Tier-2, spend-guarded, PII-stripped.
    # Every failure degrades to "no IG intel", never breaks the run.
    own_marketing_intel = None
    from urllib.parse import urlparse as _up
    _slug = "".join(c if c.isalnum() else "_"
                    for c in (_up(args.url).netloc or "brand").lower().replace("www.", ""))
    try:
        from trends.artifact import write_trends_file
        _tp = write_trends_file(_slug, trends, keywords=_trend_kws,
                                gl=_trend_gl, hl=_trend_hl)
        if _tp:
            say(f"[trends] {len(trends)} signal(s) -> {_tp}")
    except Exception:  # noqa: BLE001 — an artifact must never break the run
        pass

    try:
        from social_intel.ig_autopull import ig_autopull_enabled, pull_ig_intel
        if ig_autopull_enabled():
            mi = pull_ig_intel(profile_dict, _slug, _make_caller(), log=say)
            if mi is not None and not mi.is_empty():
                own_marketing_intel = mi.model_dump(mode="json")
    except Exception as _exc:  # noqa: BLE001
        say(f"[ig] autopull skipped ({type(_exc).__name__})")
    if own_marketing_intel is None:
        # NEVER wipe earned intel (owner-caught 2026-07-20: a re-analysis erased the live IG
        # extraction): (a) re-extract from the saved snapshot (zero Apify spend), else
        # (b) carry the previous result's marketing_intel forward.
        try:
            from social_intel.ig_autopull import extract_from_saved_snapshot
            mi2 = extract_from_saved_snapshot(_slug, _make_caller(), log=say)
            if mi2 is not None and not mi2.is_empty():
                own_marketing_intel = mi2.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            pass
    if own_marketing_intel is None:
        try:
            import json as _json2
            _prev_p = Path("outputs") / f"{_slug}_result.json"
            if _prev_p.is_file():
                _prev = _json2.loads(_prev_p.read_text(encoding="utf-8"))
                if (_prev.get("marketing_intel") or {}).get("offers"):
                    own_marketing_intel = _prev["marketing_intel"]
                    say("[ig] carried the previous run's marketing intel forward")
        except Exception:  # noqa: BLE001
            pass

    # نضج البراند (owner ruling 2026-07-20): deterministic verdict from existing evidence —
    # unlocks the FUNNEL plan + the awareness cell for nascent brands. Never breaks the run.
    maturity = None
    try:
        from media_plan.maturity import brand_maturity
        _ig_snap = None
        _snap_p = Path("runs/social_snapshots") / f"{_slug}_ig.json"
        if _snap_p.is_file():
            _ig_snap = json.loads(_snap_p.read_text(encoding="utf-8"))
        maturity = brand_maturity(profile_dict, ig_snapshot=_ig_snap)
        say(f"[maturity] {maturity.level}: {maturity.disclosure[:110]}")
    except Exception as _exc:  # noqa: BLE001
        say(f"[maturity] skipped ({type(_exc).__name__})")

    competitor_intel_map: dict = {}
    try:
        from ads_intel.orchestrator import autopull_enabled, run_adaptive_pulls
        if autopull_enabled():
            say("[ads] السحب الذكي التكيفي — live Meta pulls (batched + capped)")
            _bundles = run_adaptive_pulls(matrix_competitors, profile=profile_dict,
                                          caller=_make_caller(), log=say)
            competitor_intel_map = {r: b.intel.model_dump(mode="json")
                                    for r, b in _bundles.items()
                                    if getattr(b, "intel", None) is not None}
    except Exception as _exc:  # noqa: BLE001
        say(f"[ads] adaptive pull skipped ({type(_exc).__name__}) — using saved snapshots only")

    # U2 ad-presence (A8): read any SAVED Meta Ad Library snapshots for the competitors and fold
    # their proven-winner / active ads into the SWOT as Threats. Honest-degrade: no snapshot on
    # disk -> [] -> zero threats added.
    ad_pres: list = []
    try:
        from ads_intel import ad_presence_for_refs, ref_slug
        _refs = [ref_slug(getattr(c, "name", "")) for c in matrix_competitors]
        ad_pres = ad_presence_for_refs([r for r in _refs if r])
    except Exception:  # noqa: BLE001 — ads-intel must never break a run
        ad_pres = []

    swot = synthesize_swot(matrix, themes=themes,
                           unique_insights=unique_insight_texts(profile),
                           profile=profile_dict, trends=trends, ad_presence=ad_pres)

    # TOWS synthesis (strategies + priority actions) from the cited SWOT —
    # deterministic here (no extra LLM cost on the CLI path); never raises.
    from competitor import build_tows
    tows = build_tows(swot, caller=None, profile=profile_dict)

    # U1 MEDIA PLAN (CLI parity with the dashboard path): the deduced objective/destination/KPI/
    # persona/geo. Guarded + honest-degrade (None if no objective can be grounded); never breaks a run.
    media_plan = None
    if not args.no_media_plan:
        say("[5/5] deducing the media plan (U1)")
        try:
            import re as _re
            from media_plan.builder import build_media_plan
            _rid = "cli_" + (_re.sub(r"[^a-z0-9]+", "_", args.url.lower()).strip("_")[:40] or "run")
            media_plan = build_media_plan(profile, manifest=manifest, run_id=_rid, maturity=maturity)
            say("[media-plan] " + ("grounded plan produced" if media_plan else
                                   "no objective could be grounded (honest-degrade)"))
        except Exception as exc:  # noqa: BLE001
            say(f"[media-plan] skipped ({type(exc).__name__})")

    # Media Plan v2 (Phase C): the strategist's layers — narrative + 3 test cells + reality-check
    # guardrails. DETERMINISTIC + no spend (built from the profile/SWOT/media_plan already produced).
    # marketing_intel (offers/USPs/pain-points) stays EMPTY here — it needs an owner-provided IG
    # snapshot + a paid extraction call (PD-10 + spend), so it's fed later; §5/§2/§3 stand alone.
    media_plan_v2 = None
    try:
        from media_plan_v2 import build_media_plan_v2
        _obj = None
        if media_plan is not None:
            _o = getattr(media_plan, "objectives", None) or []
            _obj = getattr(_o[0], "objective", None) if _o else None
        media_plan_v2 = build_media_plan_v2(profile_dict, media_plan=media_plan, swot=swot,
                                            objective=str(_obj) if _obj else None,
                                            ad_presence=ad_pres,
                                            competitor_intel=competitor_intel_map or None,
                                            maturity=maturity)
    except Exception as exc:  # noqa: BLE001 — the v2 layer must never break a run
        say(f"[media-plan-v2] skipped ({type(exc).__name__})")

    # --- consolidated result -> output file ------------------------------------
    # Everything the run produced, grounded: the subject profile, the discovered
    # competitors (with their why_selected provenance), and the cited SWOT. The
    # profile is pydantic (model_dump); competitors + SWOT are dataclasses (asdict).
    import dataclasses
    import json as _json
    from datetime import datetime, timezone

    swot_json = dataclasses.asdict(swot)            # mode + 4 quadrants + notes
    result_doc = {
        "subject_url": args.url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subject_category": _category_str(profile),
        # P-79: the client-facing count + list are the APPROVED registry set (what SWOT/themes use),
        # not the raw discovery pool `n` (kept above only for the discovery log + standalone check).
        "competitor_count": len(matrix_competitors),
        "scrapable_benchmarks": scrapable,
        "discovery_notes": list(getattr(result, "notes", []) or []),
        # The per-candidate hard-filter trail (discovery.py builds it, nothing persisted it) —
        # without it "who was rejected and why" is unrecoverable from the saved run, which is
        # exactly the question asked when a brand ships with one competitor.
        "discovery_audit": list(getattr(result, "audit", []) or []),
        "profile": (
            profile.model_dump(mode="json")
            if hasattr(profile, "model_dump") else str(profile)
        ),
        "competitors": [dataclasses.asdict(c) for c in matrix_competitors],
        "registry_diff": registry_diff,   # stable-baseline diff for the dashboard (None on first run)
        # Market Pulse (Round-2 A): the trends were computed then folded ONLY into SWOT — persist
        # them so the dashboard can surface the live signals as their own section.
        "trends": [dataclasses.asdict(t) if dataclasses.is_dataclass(t) else t for t in (trends or [])],
        "swot": swot_json,
        "tows": dataclasses.asdict(tows),
        "media_plan": (media_plan.model_dump(mode="json") if media_plan is not None else None),
        # Media Plan v2 (Phase C): strategic narrative + test cells + reality check (deterministic).
        "media_plan_v2": (media_plan_v2.model_dump(mode="json") if media_plan_v2 is not None else None),
        # A8: competitors' running-ads intelligence (empty until the owner runs the free Meta pull).
        # `ads_available` drives the dashboard's conditional "not available yet" honesty gap.
        "ad_presence": [ap.model_dump(mode="json") for ap in ad_pres],
        "ads_available": bool(ad_pres),
        # ش1/ش3: the evidence-bound competitor intel map {ref_slug: MarketingIntelligence}
        "competitor_intel": competitor_intel_map,
        # the brand's OWN evidence-bound intel (IG autopull) — p4's offers/pains cards
        "marketing_intel": own_marketing_intel,
        "brand_maturity": (maturity.model_dump(mode="json") if maturity is not None else None),
    }

    # Logo enrichment on the DASHBOARD's path. The `business_profile` CLI enriches the profile
    # FILE, but the dashboard analyses through this module and keeps the profile in the result —
    # so every dashboard brand silently skipped it (inline-svg logos AND the JPEG->vector upgrade).
    try:
        from scraper.inline_svg_logo import enrich_logo_in_profile_dict, resolve_raw_homepage
        _raw = resolve_raw_homepage(Path(scrape_dir) / "raw")
        if _raw is not None and isinstance(result_doc.get("profile"), dict):
            if enrich_logo_in_profile_dict(result_doc["profile"], _raw):
                say("[logo] rasterized the real vector logo into the profile")
    except Exception:  # noqa: BLE001 — enrichment must never break a run
        pass

    out_path = Path(args.out) if args.out else (Path(scrape_dir) / "result.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _json.dumps(result_doc, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if media_plan is not None:                       # a sibling file, same shape the dashboard writes
        (out_path.parent / "media_plan.json").write_text(
            media_plan.model_dump_json(indent=2), encoding="utf-8")
    say("\n[ok] wrote full result (profile + competitors + SWOT) -> %s"
        % out_path.resolve())

    if args.json:
        payload = dict(swot_json)
        payload["competitor_count"] = n
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 72)
        print(format_swot(swot))
        print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())