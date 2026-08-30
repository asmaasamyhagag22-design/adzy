"""Step 1 CLI: a BusinessProfile JSON -> a viewable poster PNG.

    python -m poster <business_profile.json> --out poster.png
    python -m poster <business_profile.json> --out poster.png --no-image

Pipeline: load .env (Vertex project/location) -> build the brief from the profile
-> generate a TEXT-FREE Imagen background -> render the final poster via headless
Chromium (Playwright). No Pillow. --no-image swaps Imagen for a palette gradient
so you can iterate on layout without spending on image generation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass


def _fail_reason(exc: Exception) -> str:
    """ONE readable line for the studio log tail (owner's screenshot 2026-07-12 showed a raw
    google.genai traceback fragment — useless to a non-engineer). Names the quota case."""
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None) or ""
    s = str(exc)
    if str(code) == "429" or "RESOURCE_EXHAUSTED" in s or "429" in s[:80]:
        return ("Google image quota is busy (429) — usually a reel rendering on the same "
                "project at the same time. Press Regenerate in a minute or two.")
    if str(code) in ("500", "503") or "UNAVAILABLE" in s:
        return "Google's image service hiccuped (server error). Press Regenerate."
    return f"{type(exc).__name__}{f' {code}' if code else ''}: {s[:200]}"


def main() -> int:
    # Windows consoles default to cp1252 and crash on Arabic in print(); force UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    _load_env()

    ap = argparse.ArgumentParser(
        prog="poster", description="Generate a poster from a BusinessProfile JSON."
    )
    ap.add_argument("profile", help="path to a business_profile.json")
    ap.add_argument("--out", default="outputs/posters/poster.png", help="output PNG path")
    ap.add_argument("--prompt-file", default=None,
                    help="HITL: a user-APPROVED generation prompt (utf-8 text file); replaces "
                         "the assembled one-shot brief verbatim — render gates still apply.")
    ap.add_argument(
        "--engine", choices=("classic", "oneshot"), default="classic",
        help="poster engine: 'classic' (Imagen background + crisp HTML overlay) or 'oneshot' "
             "(the whole creative composed by the image model; reserves a clean corner and "
             "composites the REAL logo — best for a brand whose LOGO carries Arabic script, "
             "which the classic STYLE background can bake garbled).",
    )
    ap.add_argument(
        "--no-image", action="store_true",
        help="skip Imagen; use a brand-palette gradient background (fast/free).",
    )
    ap.add_argument(
        "--static-concept", action="store_true",
        help="skip the LLM art-director; use the static creative concept prompt.",
    )
    ap.add_argument(
        "--research", action="store_true",
        help="(now ON by default when keys exist) deep-search the brand for FRESH, sourced "
             "ad copy instead of the verbatim profile headline.",
    )
    ap.add_argument(
        "--no-research", action="store_true",
        help="disable the deep-search brand research (use the verbatim profile headline).",
    )
    ap.add_argument(
        "--trend", action="store_true",
        help="ride a CURRENT trend relevant to the brand: the research copy may tie one "
             "angle to a live trend (from the free trend sources).",
    )
    ap.add_argument(
        "--variation", type=int, default=None,
        help="with --research: pick angle N (deterministic). Omit for a fresh angle each run.",
    )
    ap.add_argument(
        "--brandbook", action="store_true",
        help="understand the brand once (multimodal): a vision model picks the brand's "
             "OWN real photo as the background instead of an invented Imagen scene.",
    )
    ap.add_argument(
        "--brand-dna", default=None,
        help="path to a BrandCreativeDNA json (from `python -m brand.creative_dna`): the "
             "image is rendered in the brand's OWN learned visual language.",
    )
    ap.add_argument("--headline", default=None,
                    help="override the headline (e.g. a planned content-calendar hook).")
    ap.add_argument("--product", default=None,
                    help="URL of ONE product page — the poster is about THAT item. Resolves its "
                         "real name and packshot from the catalogue, or fetches the page when the "
                         "link is not in it (a store's Arabic and English URLs never match).")
    ap.add_argument("--product-name", default=None,
                    help="FEATURE one product: make it the hero offering so the concept centres on "
                         "it (the user picked it from the scraped catalogue).")
    ap.add_argument("--product-image", default=None,
                    help="the picked product's real image — composited as the PRIMARY product prop "
                         "(oneshot engine) so the poster SHOWS that exact product with its real label.")
    ap.add_argument("--from-plan", default=None,
                    help="a content_plan.json (from `python -m strategy`) to drive this poster.")
    ap.add_argument("--item", type=int, default=0,
                    help="with --from-plan: which calendar item index to render (default 0).")
    args = ap.parse_args()

    profile_path = Path(args.profile)
    if not profile_path.is_file():
        print(f"!! profile not found: {profile_path}", file=sys.stderr)
        return 2

    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    # CS-92: a poster render generates a background image via a PAID model (Gemini/Imagen) and gates
    # it — it inherently spends. Refuse under a no-spend context (pytest / BASEERA_NO_SPEND) so it
    # can never bill by accident (the class of bug that caused the grade_u1 spend incident).
    from spend_tripwire import refuse_if_no_spend
    refuse_if_no_spend("generates a poster background image via a paid image model and renders it",
                       alternative="inject a caller / run the hermetic tests instead")

    # FEATURE a user-picked product: make it the HERO offering so the concept/art-director centres
    # the poster on it instead of a generic brand creative (engineer's suggestion #1). Grounded —
    # it's a real product the user chose from the scraped catalogue.
    # ONE LINK IS ENOUGH. The reel lane takes a product URL and resolves the name and the packshot
    # from it; the poster lane demanded both by hand (`--product-name` AND `--product-image`), so
    # whoever ran it had to go read the page themselves — which is exactly how a headline ends up
    # being written by the operator instead of derived from the brand. Same resolver as the reel,
    # so one pasted link cannot produce two different ideas of what is being advertised.
    if args.product and not (args.product_name and args.product_image):
        try:
            from api.webapp_images import hero_product_facts
            _f = hero_product_facts(profile, args.product)
            if _f.get("name") and not args.product_name:
                args.product_name = _f["name"]
            if (_f.get("images") or []) and not args.product_image:
                args.product_image = _f["images"][0]
            if _f:
                print(f"[product] resolved from the link: {args.product_name}"
                      + (f"  ({_f.get('price')})" if _f.get("price") else ""))
            else:
                print("!! the product link resolved to nothing — the poster falls back to the "
                      "brand's own hero offering", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — a dead link must not cost the poster
            print(f"!! the product link could not be read ({type(exc).__name__}) — "
                  "falling back to the brand's own hero offering", file=sys.stderr)

    if args.product_name:
        _p = profile.get("profile", profile) if isinstance(profile.get("profile"), dict) else profile
        _offs = _p.get("offerings") or []
        _p["offerings"] = [{"name": args.product_name}] + [o for o in _offs
                                                           if (o.get("name") if isinstance(o, dict) else o) != args.product_name]
        print(f"[product] featuring '{args.product_name}' as the hero offering")

    from poster.from_profile import build_poster_brief
    from poster.template import render_poster_html
    from poster.render_playwright import render_html_to_png

    # Loop closure: a content-calendar item (from `python -m strategy`) can drive the
    # poster — its hook (else topic) becomes the headline. --headline overrides directly.
    headline_override = args.headline
    if args.from_plan and not headline_override:
        try:
            plan = json.loads(Path(args.from_plan).read_text(encoding="utf-8"))
            items = plan.get("items") or []
            if items:
                it = items[max(0, min(args.item, len(items) - 1))]
                headline_override = (it.get("hook") or it.get("topic") or "").strip() or None
                print(f"[plan] item {args.item}: {it.get('date')} {it.get('platform')} "
                      f"{it.get('content_type')} — headline={headline_override!r}")
        except Exception as exc:
            print(f"      (could not read --from-plan: {type(exc).__name__})", file=sys.stderr)

    # ONE Gemini caller powers the whole pipeline (concept + design + image + vision QA).
    caller = None
    try:
        from business_profile.llm import default_caller
        caller = default_caller(strong=True)        # Gemini 2.5 Pro
    except Exception:
        caller = None

    # Optional EXPLICIT copy override: --research deep-searches for a fresh sourced headline
    # (else the Creative Concept layer writes the copy). An explicit --headline / --from-plan
    # hook always wins.
    # Trends (--trend): fetch ONCE, up front. Previously trends were read ONLY inside the
    # `--research and not headline_override` block, so `--trend` was a silent no-op whenever
    # research was off or a headline was planned — exactly the campaign dispatch path (H7).
    # Now the trend titles feed BOTH the research angle path AND the concept copy, so a
    # dispatched poster genuinely rides a trend (its subheadline/angle); the grounding gate
    # still protects every claim, and the planned headline is not overridden.
    trend_context = ""
    if args.trend and caller is not None:
        try:
            from trends import top_trends, keywords_from_profile
            # D-8 (CS-62): search the BRAND'S market, not Google's US/English default. `profile`
            # is right here; resolve gl/hl from its own evidence. ("","") when silent -> omitted.
            from poster.locale import search_locale
            _gl, _hl = search_locale(profile)
            hot = top_trends(keywords_from_profile(profile), top_k=5, require_match=True,
                             gl=_gl, hl=_hl)
            trend_context = "\n".join(f"- {t.title}" for t in hot if getattr(t, "title", ""))
            if trend_context:
                print(f"[trend] {len(hot)} on-topic trend(s) will inform the concept copy")
            else:
                print("[trend] no on-topic trend matched the brand — skipping", file=sys.stderr)
        except Exception as exc:
            print(f"[trend] skipped ({type(exc).__name__})", file=sys.stderr)
            trend_context = ""

    if caller is not None and args.research and not headline_override:
        from poster.brand_research import research_brand, pick_angle
        from poster.art_director import _persona_lines
        print("[research] deep search for fresh, sourced brand facts...")
        research = research_brand(
            (profile.get("name") or {}).get("value") if isinstance(profile.get("name"), dict)
            else profile.get("name") or "",
            homepage_url=str(profile.get("url") or profile.get("website") or ""),
            persona=_persona_lines(profile), trend_context=trend_context, caller=caller,
        )
        # Evidence-Ledger gate: verify each angle against the brand's real evidence + the
        # research facts (a claim backed only by a junk snippet is dropped).
        ledger = None
        try:
            from grounding import EvidenceLedger
            ledger = EvidenceLedger.from_profile(profile, research=research.model_dump())
        except Exception:
            ledger = None
        angle, _sub, prov = pick_angle(research, "", variation=args.variation, ledger=ledger)
        if angle:
            headline_override = angle
            print(f"      angle: {angle!r}  (grounded_in: {prov})")

    from poster.variation import build_variation
    variation = build_variation(args.variation)
    print(f"      variation: {variation['mood']} / {variation['lighting']} / {variation['energy']}"
          + (f" (seed={args.variation})" if args.variation is not None else " (fresh)"))

    # Explicit --brand-dna path overrides the pipeline's build-or-load.
    brand_dna = None
    if args.brand_dna:
        try:
            from brand.creative_dna import load_creative_dna
            brand_dna = load_creative_dna(args.brand_dna)
            print(f"      brand DNA loaded: used_vision={brand_dna.used_vision}")
        except Exception as exc:
            print(f"      (could not load --brand-dna: {type(exc).__name__})", file=sys.stderr)

    # The ONE pipeline shared with the web app.
    from poster.pipeline import generate_poster
    prompt_override = None
    if getattr(args, "prompt_file", None):
        try:
            prompt_override = Path(args.prompt_file).read_text(encoding="utf-8")
            print(f"      HITL prompt override loaded ({len(prompt_override)} chars)")
        except Exception as exc:
            print(f"      (could not read --prompt-file: {type(exc).__name__})", file=sys.stderr)
    try:
        res = generate_poster(
            profile, caller=caller, variation=variation, brand_dna=brand_dna,
            no_image=args.no_image, headline_override=headline_override,
            trend_context=trend_context or None, engine=args.engine,
            out_dir=str(Path(args.out).parent or "."),
            product_image=args.product_image,
            product_name=args.product_name,
            prompt_override=prompt_override,
            canonical_out=str(Path(args.out)),
        )
    except Exception as exc:  # noqa: BLE001 — the studio shows the log TAIL: make it readable
        print(f"[X] poster failed: {_fail_reason(exc)}", file=sys.stderr)
        return 3

    # Place the result at the requested --out path.
    out_path = Path(args.out)
    if str(Path(res.poster_path)) != str(out_path):
        import shutil
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(res.poster_path, out_path)
    print(f"DONE -> {out_path}  | QA pass={res.qa.overall_pass} ({res.qa.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
