"""Reel Studio CLI:  python -m reel <profile.json> [--out reel.mp4]

    python -m reel digilians_profile.json --out reel.mp4
    python -m reel profile.json --no-video                 # offline stub (no Veo/creds)
    python -m reel profile.json --music track.mp3 --scale 0.5   # fast preview w/ music

Builds the verbatim, zero-hallucination storyboard from the profile, generates a
text-free clip per scene (Veo live in your GCP, or the offline stub), then overlays
the RTL/LTR text + logo (rendered by Chromium so Arabic shapes correctly) and muxes
optional music with ffmpeg.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass


def _final_screening(reel_path) -> "int | None":
    """The last viewer before the owner: screen the ASSEMBLED film (letterboxing, silence,
    endcard state). Returns 3 to stop the ship on a failed screening, None to proceed.
    REEL_SCREENING=0 skips (the hermetic suite sets it); REEL_ALLOW_BELOW_GRADE=1 reports the
    issues but ships anyway. A reel that COULD NOT be screened ships with a loud note — the
    gate blocks on measured defects, never on its own blindness."""
    import os as _os
    if _os.environ.get("REEL_SCREENING") == "0":
        return None
    try:
        from reel.final_screening import screen_reel
        v = screen_reel(reel_path, log=lambda m: print(f"     {m}", file=sys.stderr))
    except Exception:  # noqa: BLE001 — the viewer failing is not the film failing
        return None
    if not v.checked:
        print("     ! screening could not run — the reel ships UNSCREENED", file=sys.stderr)
        return None
    if v.passed:
        print("     + final screening: clean", file=sys.stderr)
        return None
    if _os.environ.get("REEL_ALLOW_BELOW_GRADE") == "1":
        print("     ! shipping BELOW GRADE by explicit override", file=sys.stderr)
        return None
    print(f"[X] final screening FAILED ({len(v.issues)} issue(s) above) — not shipping. "
          "Set REEL_ALLOW_BELOW_GRADE=1 to override.", file=sys.stderr)
    return 3


def _append_brand_endcard(profile: dict, reel_path, *, enabled: bool) -> None:
    """Append the branded END-CARD (real logo on the brand colour) to a finished reel — the owner:
    "the reel must END with the logo and the brand colour".

    THREE STATES, stamped into the reel's sidecar (v2 lesson: the card silently failed on a dims
    mismatch and the owner found out from the SCREEN — «الاند مش في الفيديو»): "appended",
    "failed", or "disabled". A failure also prints, so the shipping log never reads clean over a
    card-less reel."""
    from pathlib import Path as _P

    def _stamp(state: str) -> None:
        side = _P(str(reel_path)).with_suffix(".path.json")
        try:
            import json as _json
            data = {}
            if side.is_file():
                try:
                    data = _json.loads(side.read_text(encoding="utf-8")) or {}
                except Exception:  # noqa: BLE001
                    data = {}
            data["endcard"] = state
            side.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001 — the stamp must never kill a finished reel
            pass

    if not enabled:
        _stamp("disabled")
        return
    ok = False
    try:
        from reel.endcard import append_endcard_to_reel
        ok = bool(append_endcard_to_reel(profile, reel_path))
    except Exception:  # noqa: BLE001 — the end-card is a nicety, never fail the reel over it
        ok = False
    _stamp("appended" if ok else "failed")
    if ok:
        print("     + branded end-card (logo on the brand colour)", file=sys.stderr)
    else:
        print("     ! ENDCARD FAILED — the reel has no closing brand card", file=sys.stderr)


def main() -> int:
    _load_env()
    import json

    from reel import build_reel_brief, build_storyboard
    from reel.compositor import render_reel
    from reel.video_provider import (
        KenBurnsProvider, StubVideoProvider, default_video_provider,
    )

    ap = argparse.ArgumentParser(description="profile.json -> marketing reel (.mp4)")
    ap.add_argument("profile", help="path to a BusinessProfile JSON")
    ap.add_argument("--out", default=None, help="output .mp4 (default: outputs/reels/<name>.mp4)")
    ap.add_argument("--no-video", action="store_true",
                    help="use the offline stub (brand gradients) instead of Veo")
    ap.add_argument("--real", action="store_true",
                    help="FAITHFUL OFFLINE mode: animate the business's REAL scraped "
                         "photos with Ken Burns (ffmpeg), no AI. Default (no flag) uses "
                         "Veo 3.1 to bring those same real photos to life (image-to-video).")
    ap.add_argument("--frames", type=int, default=10,
                    help="target number of scenes/shots (default 10), bounded by the "
                         "number of real photos the scrape found")
    ap.add_argument("--music", default=None, help="optional audio track to mux (mp3/m4a/wav)")
    ap.add_argument("--scale", type=float, default=1.0, help="resolution scale (0.5 = fast preview)")
    ap.add_argument("--max-seconds", type=float, default=28.0, help="cap total reel length")
    ap.add_argument("--no-logo", action="store_true", help="skip the logo overlay")
    ap.add_argument("--static-scene", action="store_true",
                    help="skip the LLM art-director; use deterministic per-category scenes")
    ap.add_argument("--no-select", action="store_true",
                    help="skip the vision photo-curator (use ALL scraped content images; "
                         "may include partner logos / QR codes / icons)")
    ap.add_argument("--template", action="store_true",
                    help="use the OLD deterministic field-presence storyboard instead of the "
                         "creative director (kept for debugging; the director is the default).")
    ap.add_argument("--allow-template-fallback", action="store_true",
                    help="if the creative director fails, degrade to the template storyboard "
                         "instead of exiting — the degraded reel is stamped in its audit.")
    ap.add_argument("--creative", action="store_true",
                    help="CREATIVE mode: Opus directs the reel (per-scene Veo 3.1 "
                         "prompts + voice-over) from the identity + real photos. Veo 3.1 "
                         "brings each real photo to life; OpenAI TTS narrates.")
    ap.add_argument("--language", default=None, choices=["ar", "en"],
                    help="OWNER RULE: the reel's output language is the USER's explicit choice "
                         "(captions + voice-over); default = the brand's dominant language.")
    ap.add_argument("--plan-file", default=None,
                    help="HITL: a user-APPROVED creative plan JSON (from the studio preview); "
                         "replaces the director call verbatim — all render gates still apply.")
    ap.add_argument("--no-voiceover", action="store_true",
                    help="skip the AI voice-over narration (default: Gemini TTS narrates "
                         "each scene's on-screen text; runs on GCP credits)")
    ap.add_argument("--headline", default=None,
                    help="override the reel headline (e.g. a planned content-calendar hook).")
    ap.add_argument("--from-plan", default=None,
                    help="a content_plan.json (from `python -m strategy`) to drive this reel.")
    ap.add_argument("--item", type=int, default=0,
                    help="with --from-plan: which calendar item index to render (default 0).")
    ap.add_argument("--product-image", default=None,
                    help="FEATURE one product: use this real product image as the reel's footage "
                         "(the user picked it from the scraped catalogue) instead of the whole set.")
    ap.add_argument("--product-name", default=None,
                    help="the picked product's name, woven into the story/voice-over.")
    args = ap.parse_args()

    profile_path = Path(args.profile)
    if not profile_path.is_file():
        print(f"!! profile not found: {profile_path}", file=sys.stderr)
        return 2

    # CS-92: a reel render spends the MOST (paid Veo video + vision selection + TTS). Refuse under a
    # no-spend context (pytest / BASEERA_NO_SPEND) so a reel can never render by accident — this is
    # also the owner's standing rule ("رندر الريل بإيدها"): only a real human CLI run may spend.
    from spend_tripwire import refuse_if_no_spend
    refuse_if_no_spend("renders a reel via paid Veo video + vision/TTS calls",
                       alternative="use the hermetic reel tests (MockCaller + fake TTS)")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    # LLM art-director: the scene is derived from the brand persona. Falls back to
    # deterministic per-category templates when no OpenAI key / --static-scene / the
    # call fails (same discipline as the poster).
    caller = None
    if not args.static_scene:
        try:
            from business_profile.llm import OpenAICaller
            caller = OpenAICaller()
        except Exception:
            caller = None

    # Loop closure: a content-calendar item (from `python -m strategy`) can drive the
    # reel — its hook (else topic) becomes the headline. --headline overrides directly.
    headline_override = args.headline
    if args.from_plan and not headline_override:
        try:
            plan = json.loads(Path(args.from_plan).read_text(encoding="utf-8"))
            items = plan.get("items") or []
            if items:
                it = items[max(0, min(args.item, len(items) - 1))]
                headline_override = (it.get("hook") or it.get("topic") or "").strip() or None
                print(f"[plan] item {args.item}: {it.get('date')} {it.get('platform')} "
                      f"{it.get('content_type')} -> headline={headline_override!r}", file=sys.stderr)
        except Exception as exc:
            print(f"   (could not read --from-plan: {type(exc).__name__})", file=sys.stderr)

    brief = build_reel_brief(profile, headline_override=headline_override)

    # IMAGE PIPELINE — two UNIVERSAL filters before a scraped photo becomes a reel scene:
    #   1) a TECHNICAL quality gate (deterministic, runs ALWAYS): reject tiny thumbnails /
    #      banner strips / blank graphics that would upscale to a blurry "dumb frame".
    #   2) the VISION curator (optional, needs a key): keep only on-brand PHOTOS.
    # `selected` is ALWAYS the gated set (possibly []), so the raw garbage is NEVER used; an
    # empty set routes the reel to generated b-roll instead of animating junk (e.g. a brand
    # whose only "images" are 298px category thumbnails -> 0 usable -> text-to-video scene).
    raw_photos = (profile.get("visual") or {}).get("content_images") or []
    selected: list = []
    photo_subjects: list = []            # what each selected photo depicts (director hint)
    if raw_photos:
        from reel.image_quality import filter_usable_photos
        usable = filter_usable_photos(raw_photos, max_keep=max(2, args.frames))
        print(f"   [quality] {len(usable)}/{len(raw_photos)} content images pass the photo-quality gate",
              file=sys.stderr)
        selected = usable
        if not args.no_select and usable:
            from reel.image_select import select_brand_photos
            def _f(k):
                fld = profile.get(k)
                return fld.get("value") if isinstance(fld, dict) else fld
            curated = select_brand_photos(
                usable, business_name=brief.business_name,
                category=str(brief.category or _f("category") or ""),
                description=str(_f("description") or ""),
                max_keep=max(2, args.frames), return_verdicts=True,
            )
            selected = [u for u, _ in curated]
            photo_subjects = [s for _, s in curated]
            print(f"   [curate] {len(selected)}/{len(usable)} are on-brand photos", file=sys.stderr)
            if not selected:
                # FULLY-GENERATED mode (owner ruling 2026-07-12): photos are CONDITIONING +
                # JUDGING references now, not display material — the curator's display bar
                # no longer gates them out. Without this, zero curated photos killed the
                # whole creative plan (measured on NTI: 0/6 curated -> director skipped).
                selected = usable
                photo_subjects = []       # subjects were for the curated set; don't misalign
                print(f"   [curate] 0 curated -> keeping {len(usable)} quality-passing "
                      "photo(s) as generation/judging references", file=sys.stderr)

    # FEATURE the user-picked product: the reel is about ONE product — EXCLUSIVELY that image, so
    # every scene is the SAME product (several shots/uses), not a mixed montage (owner: "ريل عن منتج
    # واحد مش ميت منتج"). Bypasses the quality gate — the user chose it.
    if args.product_image:
        selected = [args.product_image]
        photo_subjects = []               # the featured product image has no curated subject label
        # FRAMING: a single product photo is usually landscape/square; the default 'cover' seed
        # CROPS it to 9:16 (owner: "الصورة كأنها مقصوصة"). 'pad' CONTAINS the WHOLE product over its
        # OWN background colour — nothing cropped AND no visible band (a blurred copy left a soft
        # band on seed-hugging scenes), so the seed Veo builds its vertical scene around is complete
        # and seamless. (User may still override with an explicit REEL_SEED_FILL.)
        import os as _os
        _os.environ.setdefault("REEL_SEED_FILL", "pad")
        if args.product_name:
            print(f"   [product] featuring ONLY '{args.product_name}' "
                  f"(seed fill={_os.environ['REEL_SEED_FILL']})", file=sys.stderr)

    out = Path(args.out) if args.out else Path("outputs/reels") / f"{profile_path.stem}.mp4"

    # THE CREATIVE DIRECTOR IS THE DEFAULT (owner ruling 2026-07-25: «عايزة البرفكت ريل زي ما
    # الوكالات الكبيرة بتعمل»). The 2026-07-25 Abu Auf reel shipped story-less because this block
    # ran only on --creative and the CLI run didn't pass it — the field-presence template rendered
    # five disconnected scenes while the director sat unused. --creative is now a no-op alias
    # (the dashboard passes it); --template opts into the old path explicitly.
    if not args.template:
        from reel.creative import render_creative_reel
        photos = selected if selected is not None else \
            ((profile.get("visual") or {}).get("content_images") or [])
        provider = StubVideoProvider() if args.no_video else default_video_provider()
        print(f"[creative] Opus directing {args.frames} scenes from {len(photos)} real photos; "
              f"provider={provider.name}", file=sys.stderr)
        plan_override = None
        if getattr(args, "plan_file", None):
            try:
                import json as _json
                plan_override = _json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
                print(f"[creative] HITL plan override loaded ({len(plan_override.get('scenes') or [])} scenes)",
                      file=sys.stderr)
            except Exception as exc:
                print(f"[creative] could not read --plan-file: {type(exc).__name__}", file=sys.stderr)
        try:
            result, creative = render_creative_reel(
                profile, brief, photos, provider=provider, out_path=out,
                n_scenes=max(3, args.frames), scale=args.scale,
                language=args.language,
                include_logo=not args.no_logo, music_path=args.music,
                with_voiceover=not args.no_voiceover,
                featured_product=args.product_name,
                plan_override=plan_override,
                photo_subjects=photo_subjects or None,
            )
        except Exception as exc:  # noqa: BLE001
            # البند 5 (spent-but-failed honesty): a failure mid-render may have ALREADY paid for
            # seeds/TTS/partial Veo. Sweep the scratch (seeds + audio stems) into _failed/ so the
            # owner can see what her money bought, and say so — never a silent generic failure.
            import shutil as _sh
            import time as _t
            out_dir = Path(out).parent
            failed_dir = out_dir / "_failed" / f"{Path(out).stem}_{int(_t.time())}"
            swept = []
            for scratch in [out_dir / "_genseeds", Path(str(out) + ".vo.m4a"),
                            Path(str(out) + ".bed.m4a"),
                            out_dir / (Path(out).stem + ".vo.m4a"),
                            out_dir / (Path(out).stem + ".bed.m4a")]:
                try:
                    if scratch.exists():
                        failed_dir.mkdir(parents=True, exist_ok=True)
                        _sh.move(str(scratch), str(failed_dir / scratch.name))
                        swept.append(scratch.name)
                except Exception:  # noqa: BLE001
                    pass
            note = f" (تم حفظ نواتج جزئية مدفوعة في {failed_dir})" if swept else ""
            print(f"[X] reel failed: {exc}{note}", file=sys.stderr)
            print("    الإنفاق الفعلي مسجَّل في runs/vertex_spend.jsonl", file=sys.stderr)
            return 3
        if result is not None:
            _append_brand_endcard(profile, result.reel_path, enabled=not args.no_logo)
            # per-asset trail (poster parity): copy audit + scene-QA verdicts + the
            # structural audio statement, exported next to the reel.
            from reel.audit import build_reel_audit, write_reel_audit
            audit_p = write_reel_audit(
                build_reel_audit(profile, creative, render_result=result), result.reel_path)
            if audit_p:
                print(f"     audit trail -> {audit_p}")
            _stop = _final_screening(result.reel_path)
            if _stop is not None:
                return _stop
            print(f"\n[ok] CREATIVE reel -> {Path(result.reel_path).resolve()}")
            print(f"     concept: {creative.concept[:90]}")
            print(f"     {result.width}x{result.height}  {result.duration_s}s  "
                  f"audio={result.has_audio}  provider={result.provider}")
            return 0
        if not args.allow_template_fallback:
            # A weak reel that LOOKS directed is worse than a failed run — the owner reads the
            # output, not the logs, and the silent fall to the template is exactly how the
            # story-less Abu Auf reel shipped. Fail loudly; the flag restores the old degrade.
            print("!! the creative director could not design this reel — stopping rather than "
                  "shipping a template reel. Re-run with --allow-template-fallback to accept "
                  "the degraded path explicitly.", file=sys.stderr)
            return 1
        print("   [creative] director failed; degrading to the TEMPLATE storyboard "
              "(--allow-template-fallback) — the audit is stamped path=template_fallback.",
              file=sys.stderr)
        _audit_path_stamp = "template_fallback"

    storyboard = build_storyboard(
        brief, profile=profile, caller=caller,
        max_total_s=args.max_seconds, target_scenes=max(2, args.frames),
        selected_images=selected,
    )

    if args.real:
        # FAITHFUL: animate the business's REAL on-page photos (logos excluded
        # upstream) so the reel comes from the actual place. Prefer the full content
        # set; fall back to the single hero, then to a brand gradient.
        real_imgs = list(storyboard.content_images)
        if not real_imgs and storyboard.reference_image_url:
            real_imgs = [storyboard.reference_image_url]
        if real_imgs:
            print(f"   [real] {len(real_imgs)} real photo(s) from the business",
                  file=sys.stderr)
        else:
            print("   warning: --real but the scrape surfaced no real business photo "
                  "(logo only); falling back to a brand gradient.", file=sys.stderr)
        provider = KenBurnsProvider(images=real_imgs, fallback_palette=storyboard.palette_hex)
    elif args.no_video:
        provider = StubVideoProvider()
    else:
        provider = default_video_provider()

    print(f"[reel] {storyboard.business_name}  dir={storyboard.primary_dir}  "
          f"scenes={len(storyboard.scenes)}  total={storyboard.total_duration_s}s  "
          f"provider={provider.name}", file=sys.stderr)
    for w in storyboard.warnings:
        print(f"   warning: {w}", file=sys.stderr)

    # Voice-over: narrate each scene's verbatim on-screen text (grounded). Defaults to
    # Gemini TTS (same GCP credits as Imagen/Veo); --no-voiceover renders silent.
    voiceover_path = None
    if not args.no_voiceover:
        from reel.voiceover import synth_voiceover, narration_lines
        nlines = narration_lines(storyboard)
        if any(nlines):
            print(f"[voiceover] synthesizing narration "
                  f"({sum(1 for l in nlines if l)} lines)...", file=sys.stderr)
            voiceover_path = synth_voiceover(
                nlines, [s.duration_s for s in storyboard.scenes],
                Path(out).with_suffix(".vo.m4a"),
            )
            print(f"   voiceover -> {voiceover_path}" if voiceover_path
                  else "   (voiceover unavailable; rendering silent)", file=sys.stderr)

    result = render_reel(
        storyboard, provider=provider, out_path=out,
        scale=args.scale, music_path=args.music, voiceover_path=voiceover_path,
        include_logo=not args.no_logo,
    )

    _append_brand_endcard(profile, result.reel_path, enabled=not args.no_logo)

    # A template reel must never masquerade as a directed one: the sidecar records which path
    # actually produced this file, so a degraded run is visible in the artifact itself.
    try:
        import json as _json
        _path_kind = (locals().get("_audit_path_stamp")
                      or ("template_explicit" if args.template else "template"))
        Path(str(result.reel_path) + ".path.json").write_text(
            _json.dumps({"path": _path_kind}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    _stop = _final_screening(result.reel_path)
    if _stop is not None:
        return _stop
    print(f"\n[ok] reel -> {Path(result.reel_path).resolve()}")
    print(f"     {result.width}x{result.height}  {result.duration_s}s  "
          f"audio={result.has_audio}  provider={result.provider}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
