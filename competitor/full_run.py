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
        failures = getattr(manifest, "failures", []) or []
        fails = "; ".join(str(f) for f in failures[:2])
        # HONEST distinction: a 429 means the site is RATE-LIMITING us (temporary — it works
        # again after a short cooldown), NOT that it blocks crawlers or is down. Telling the
        # user "may block scraping / be unreachable" on a 429 is misleading (the site is fine).
        if rate_limited_failure(failures):
            print("!! rate-limited (HTTP 429) — the site asked us to slow down; it is NOT blocking "
                  "us and is not down. Wait about a minute and analyze it again"
                  + (f" ({fails})" if fails else "") + ".", file=sys.stderr)
        else:
            print("!! could not read this site — 0 pages scraped"
                  + (f" ({fails})" if fails else "")
                  + ". It may block crawlers, be down, or have an invalid certificate.",
                  file=sys.stderr)
        return 3

    say("[2/5] building BusinessProfile (OpenAI extraction)...")
    caller = _make_caller()
    profile = _as_profile(build_profile(manifest, caller, use_rag=True))
    if not hasattr(profile, "category"):
        print("!! build_profile() didn't return something with a .category field.", file=sys.stderr)
        return 1
    say("      subject category = %s" % _category_str(profile))

    say("[3/5] discovering competitors (adaptive router)...")
    client = PlacesClient()
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
        themes = ReviewThemeExtractor()(result.competitors)
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
    try:
        from trends import keywords_from_profile, top_trends_bounded
        kws = keywords_from_profile(profile_dict or {})
        if kws:                              # HARD 12s deadline — a hung source never blocks analyze
            trends = top_trends_bounded(kws, timeout_s=12.0, require_match=True, top_k=6)
    except Exception:
        trends = []

    swot = synthesize_swot(matrix, themes=themes,
                           unique_insights=unique_insight_texts(profile),
                           profile=profile_dict, trends=trends)

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
            media_plan = build_media_plan(profile, manifest=manifest, run_id=_rid)
            say("[media-plan] " + ("grounded plan produced" if media_plan else
                                   "no objective could be grounded (honest-degrade)"))
        except Exception as exc:  # noqa: BLE001
            say(f"[media-plan] skipped ({type(exc).__name__})")

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
        "competitor_count": n,
        "scrapable_benchmarks": scrapable,
        "discovery_notes": list(getattr(result, "notes", []) or []),
        "profile": (
            profile.model_dump(mode="json")
            if hasattr(profile, "model_dump") else str(profile)
        ),
        "competitors": [dataclasses.asdict(c) for c in result.competitors],
        "registry_diff": registry_diff,   # stable-baseline diff for the dashboard (None on first run)
        # Market Pulse (Round-2 A): the trends were computed then folded ONLY into SWOT — persist
        # them so the dashboard can surface the live signals as their own section.
        "trends": [dataclasses.asdict(t) if dataclasses.is_dataclass(t) else t for t in (trends or [])],
        "swot": swot_json,
        "tows": dataclasses.asdict(tows),
        "media_plan": (media_plan.model_dump(mode="json") if media_plan is not None else None),
    }

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