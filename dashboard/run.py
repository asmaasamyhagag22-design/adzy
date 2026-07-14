"""One-command demo: a URL in, a finished Baseera dashboard out.

    python -m dashboard.run https://brand.com            # full run (adds the one-shot poster)
    python -m dashboard.run https://brand.com --fast     # skip the poster (fastest, for a live demo)
    python -m dashboard.run https://brand.com --open      # open the dashboard when done

Runs the REAL pipeline end to end - competitor.full_run (scrape -> profile -> competitors -> SWOT
-> TOWS) -> strategy (content calendar) -> poster (one-shot, crisp logo) -> reel (Opus-directed,
Veo 3.1) -> dashboard - driving the existing tested CLIs as subprocesses so each loads .env and
handles its own errors. The finished dashboard embeds BOTH the poster and the reel, so everything
shows in one place. Prints a clean stage-by-stage progress log so it reads well when demoed live.
Every stage is best-effort: a stage that fails is skipped and the dashboard is still built from
whatever succeeded. `--fast` skips the heavy poster + reel for a snappy preview.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

# Windows consoles default to cp1252 and choke on non-ASCII; force UTF-8 so the progress log
# (and any Arabic in stage output) never crashes a live demo.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _slug(url: str) -> str:
    host = (urlparse(url).netloc or url).replace("www.", "").replace(":", "_")
    # ASCII-only: str.isalnum() is True for Unicode letters (é, ü, 日), which would make a slug the
    # server's ASCII guard (_SLUG_RE) rejects — an IDN brand would analyze fine then 400 at /studio.
    # These bytes also become on-disk filenames, so keep them plain ASCII.
    return "".join(ch if (ch.isascii() and ch.isalnum()) else "_"
                   for ch in host).strip("_") or "brand"


def _emit(on_progress, event: str, label: str, msg: str) -> None:
    """Print for the CLI and, when a live UI is watching, forward (event, label, msg)."""
    print(msg, flush=True)
    if on_progress:
        try:
            on_progress(event, label, msg)
        except Exception:
            pass


def _run(cmd: list[str], *, timeout: int, label: str, on_progress=None,
         env: dict | None = None) -> tuple[bool, str]:
    """Run one stage; stream a compact status. Returns (ok, tail_of_output)."""
    _emit(on_progress, "stage_start", label, f"  -> {label} ...")
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        _emit(on_progress, "stage_fail", label, f"    [X] {label} timed out after {timeout}s")
        return False, ""
    dt = time.monotonic() - t0
    tail = "\n".join((p.stdout or "").strip().splitlines()[-3:])
    if p.returncode == 0:
        _emit(on_progress, "stage_ok", label, f"    [OK] {label}  ({dt:.0f}s)")
        return True, tail
    err = "\n".join((p.stderr or p.stdout or "").strip().splitlines()[-3:])
    _emit(on_progress, "stage_fail", label, f"    [X] {label} failed ({dt:.0f}s): {err[:200]}")
    return False, tail


def paths(slug: str, out_dir: str = "outputs") -> dict:
    """Every artifact path for a brand slug — shared by analyze / generate_* / build so the
    interactive studio and the one-shot CLI address the exact same files."""
    out = Path(out_dir)
    return {
        "out": out, "slug": slug,
        "result": out / f"{slug}_result.json",
        "profile": out / f"{slug}_profile.json",
        "plan": out / f"{slug}_plan.json",
        "poster": out / "posters" / f"{slug}_poster.png",
        "reel": out / "reels" / f"{slug}_reel.mp4",
        "dash": out / f"{slug}_dashboard.html",
    }


def analyze(url: str, *, out_dir: str = "outputs", on_progress=None) -> str | None:
    """The FAST core: scrape -> profile -> competitors -> SWOT -> TOWS -> content calendar. No
    poster/reel (those are generated on demand from the studio). Returns the brand slug, or None
    if the core analysis failed. Writes <slug>_result/profile/plan.json into out_dir."""
    py = sys.executable
    slug = _slug(url)
    P = paths(slug, out_dir)
    P["out"].mkdir(parents=True, exist_ok=True)
    _emit(on_progress, "run_start", "Analyze", f"\n* Baseera - analyzing {url}\n")

    # 1500s (25 min): a full competitive analysis of a rich e-commerce brand (30-page crawl +
    # discovering & scraping each peer + profile LLM + SWOT) genuinely exceeded the old 900s and
    # was killed mid-run (MEASURED: rawafrican.net). The subprocess prints its own [1/5]... progress.
    ok, _ = _run([py, "competitor/full_run.py", url, "--out", str(P["result"]), "--no-themes"],
                 timeout=1500, label="Scrape + Profile + Competitors + SWOT", on_progress=on_progress)
    if not ok or not P["result"].is_file():
        return None

    try:
        prof = (json.loads(P["result"].read_text(encoding="utf-8")).get("profile") or {})
        P["profile"].write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
        have_profile = bool(prof)
    except Exception:
        have_profile = False

    if have_profile:
        _run([py, "-m", "strategy", str(P["profile"]), "--days", "14", "--out", str(P["plan"])],
             timeout=300, label="Content calendar", on_progress=on_progress)
        # U1 on the dashboard (owner directive 2026-07-12): build the grounded MediaPlan
        # (one cheap Gemini text call) so the studio shows objective/KPI/geo at a glance.
        _emit(on_progress, "stage_start", "Media plan", "  -> Media plan (U1) ...")
        try:
            _hitl_env()
            import json as _json
            from media_plan.builder import build_media_plan
            prof = _json.loads(P["profile"].read_text(encoding="utf-8"))
            mp = build_media_plan(prof, run_id=f"studio_{slug}")
            if mp is not None:
                (P["out"] / f"{slug}_media_plan.json").write_text(
                    mp.model_dump_json(indent=2), encoding="utf-8")
                _emit(on_progress, "stage_ok", "Media plan", "    [OK] Media plan")
            else:
                _emit(on_progress, "stage_fail", "Media plan",
                      "    [X] Media plan (no grounded objective)")
        except Exception as exc:  # noqa: BLE001 — the plan never blocks the analyze flow
            _emit(on_progress, "stage_fail", "Media plan", f"    [X] Media plan ({type(exc).__name__})")
    return slug


def _product_args(product_name: str | None, product_image: str | None) -> list[str]:
    """CLI flags so the poster/reel FEATURE the user-picked product (else whole-brand)."""
    args: list[str] = []
    if product_name:
        args += ["--product-name", product_name]
    if product_image:
        args += ["--product-image", product_image]
    return args


def generate_poster(slug: str, *, out_dir: str = "outputs", on_progress=None,
                    product_name: str | None = None, product_image: str | None = None,
                    use_hitl: bool = False) -> Path | None:
    """On-demand: (re)generate the one-shot poster from the saved profile, optionally FEATURING a
    picked product. Returns its path."""
    py = sys.executable
    P = paths(slug, out_dir)
    if not P["profile"].is_file():
        return None
    P["poster"].parent.mkdir(parents=True, exist_ok=True)
    # No --research on the studio path (deep web research pushed it past 420s). 900s: the one-shot
    # engine regenerates the slow image model on a QA-gate fail (bounded retries), so a legitimately
    # retrying poster needs room (MEASURED: rawafrican exceeded 600s). Still profile-grounded.
    args = [py, "-m", "poster", str(P["profile"]), "--engine", "oneshot",
            "--out", str(P["poster"])] + _product_args(product_name, product_image)
    # HITL: an EXECUTE-approved prompt (saved by hitl_preview/hitl_refine) overrides the brief.
    pfile = P["out"] / f"{slug}_poster_prompt.txt"
    if use_hitl and pfile.is_file():
        args += ["--prompt-file", str(pfile)]
    ok, _ = _run(args, timeout=900, label="Poster (one-shot)", on_progress=on_progress)
    return P["poster"] if (ok and P["poster"].is_file()) else None


def generate_reel(slug: str, *, out_dir: str = "outputs", on_progress=None,
                 product_name: str | None = None, product_image: str | None = None,
                 language: str | None = None, dialect: str | None = None,
                 use_hitl: bool = False) -> Path | None:
    """On-demand reel. OWNER RULE (2026-07-11): the output LANGUAGE (ar/en) and the Arabic
    register (fusha/masri) are the USER's explicit choices from the studio UI — passed as
    --language + the REEL_ARABIC_DIALECT env (which also routes the ratified TTS backend:
    masri -> gpt-audio, fusha -> Gemini directed)."""
    import os
    py = sys.executable
    P = paths(slug, out_dir)
    if not P["profile"].is_file():
        return None
    P["reel"].parent.mkdir(parents=True, exist_ok=True)
    args = [py, "-m", "reel", str(P["profile"]), "--creative", "--out", str(P["reel"])]
    if language in ("ar", "en"):
        args += ["--language", language]
    # HITL: an EXECUTE-approved plan (saved by hitl_preview/hitl_refine) replaces the director.
    plan = P["out"] / f"{slug}_reel_plan.json"
    if use_hitl and plan.is_file():
        args += ["--plan-file", str(plan)]
    env = dict(os.environ)
    if dialect in ("masri", "fusha"):
        env["REEL_ARABIC_DIALECT"] = dialect
    ok, _ = _run(args + _product_args(product_name, product_image),
                 timeout=1500, label="Reel (Veo 3.1, user-directed)", on_progress=on_progress,
                 env=env)
    return P["reel"] if (ok and P["reel"].is_file()) else None


def build_dashboard_file(slug: str, *, out_dir: str = "outputs", poster: Path | None = None,
                         reel: Path | None = None, on_progress=None) -> Path | None:
    """Build the self-contained dashboard HTML from whatever artifacts exist for this slug."""
    py = sys.executable
    P = paths(slug, out_dir)
    if not P["result"].is_file():
        return None
    cmd = [py, "-m", "dashboard", str(P["result"]), "--out", str(P["dash"])]
    if P["profile"].is_file():
        cmd += ["--profile", str(P["profile"])]
    if P["plan"].is_file():
        cmd += ["--plan", str(P["plan"])]
    if poster and Path(poster).is_file():
        cmd += ["--poster", str(poster)]
    if reel and Path(reel).is_file():
        cmd += ["--reel", str(reel)]
    ok, _ = _run(cmd, timeout=120, label="Dashboard", on_progress=on_progress)
    return P["dash"] if (ok and P["dash"].is_file()) else None


def run_pipeline(url: str, *, fast: bool = False, out_dir: str = "outputs",
                 open_when_done: bool = False, on_progress=None) -> Path | None:
    """One-shot CLI: URL in, a finished dashboard out (analyze -> poster -> reel -> build)."""
    slug = analyze(url, out_dir=out_dir, on_progress=on_progress)
    if not slug:
        print("\n[X] analysis failed - cannot build a dashboard without the core result.")
        return None
    poster = None if fast else generate_poster(slug, out_dir=out_dir, on_progress=on_progress)
    reel = None if fast else generate_reel(slug, out_dir=out_dir, on_progress=on_progress)
    dash_html = build_dashboard_file(slug, out_dir=out_dir, poster=poster, reel=reel,
                                     on_progress=on_progress)
    if not dash_html:
        return None
    print(f"\n[OK] DONE -> {dash_html.resolve()}")
    if open_when_done:
        try:
            webbrowser.open(dash_html.resolve().as_uri())
        except Exception:
            pass
    return dash_html


def main() -> int:
    ap = argparse.ArgumentParser(prog="dashboard.run",
                                 description="URL -> full analysis -> Baseera dashboard.")
    ap.add_argument("url", help="the brand's website URL")
    ap.add_argument("--fast", action="store_true", help="skip the poster (fastest - for a live demo)")
    ap.add_argument("--out-dir", default="outputs", help="output directory")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser when done")
    args = ap.parse_args()
    dash = run_pipeline(args.url, fast=args.fast, out_dir=args.out_dir, open_when_done=args.open)
    return 0 if dash else 1


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------
# HITL prompt gate (owner token-economics law, 2026-07-12): preview -> refine -> EXECUTE.
# Everything here is TEXT-ONLY (cheap Gemini calls); no image/video API is ever touched.
# ---------------------------------------------------------------------

def _hitl_env() -> None:
    from dotenv import load_dotenv
    load_dotenv(".env")


def _arabic_rendering(text: str) -> str:
    """Best-effort Arabic rendering of a generation prompt/plan for the user's review.
    One cheap Flash call; empty string on any failure (the English pane always shows)."""
    try:
        from pydantic import BaseModel

        class _T(BaseModel):
            arabic: str = ""

        from business_profile.llm import default_caller
        caller = default_caller(strong=False)
        if caller is None:
            return ""
        out, _u = caller(
            "ترجمي بريف التوليد التالي إلى العربية بأمانة كاملة (للعرض على المستخدم فقط — "
            "بدون إضافة أو حذف أي تعليمات). أعيدي النص العربي في الحقل arabic.",
            text[:6000], _T, group_name="hitl_arabic")
        return (out.arabic or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _product_raw_facts(slug: str, product_name: str | None,
                       product_url: str | None = None, limit_chars: int = 1800) -> str:
    """The picked product's RAW text (verbatim manifest blocks) — the owner's
    raw-data-first rule: generation grounds in the page's actual words, not only the
    capped profile projection. PRECISION-FIRST: the product's OWN page (URL match, or
    its full name in the page — strongest when the name OPENS the page) is taken WHOLE
    before any single-word mention elsewhere; measured on the real NTI manifest, any-word
    matching let campus pages that merely say 'fiber' eat the whole budget and the popup
    course's actual description/hours never reached generation."""
    if not (product_name or product_url):
        return ""
    try:
        import json as _json
        import re as _re
        from dashboard.products import _latest_manifest
        mp = _latest_manifest(slug)
        if not mp:
            return ""
        m = _json.loads(mp.read_text(encoding="utf-8"))
        words = [w for w in _re.findall(r"[\w؀-ۿ]+", (product_name or "").lower())
                 if len(w) >= 3]
        phrase = " ".join(words)
        tgt = (product_url or "").rstrip("/").lower()
        ranked: list[tuple[int, list[str], list[str]]] = []
        for pg in (m.get("pages") or []):
            u = str(pg.get("final_url") or "").rstrip("/").lower()
            blocks = [t for t in (str((b.get("text") if isinstance(b, dict) else b) or "").strip()
                                  for b in (pg.get("text_blocks") or [])) if t]
            if not blocks:
                continue
            lows = [t.lower() for t in blocks]
            url_hit = bool(tgt) and (u == tgt or (tgt in u))
            first_hit = bool(phrase) and phrase in lows[0]
            phrase_hit = bool(phrase) and any(phrase in t for t in lows)
            score = (4 if url_hit else 0) + (2 if first_hit else 0) + (1 if phrase_hit else 0)
            ranked.append((score, blocks, lows))
        ranked.sort(key=lambda r: -r[0])   # stable: manifest order within equal scores
        any_own_page = bool(ranked) and ranked[0][0] > 0
        out: list[str] = []
        seen: set[str] = set()   # manifests repeat nested container text — don't spend budget twice

        def _take(ts) -> None:
            for t in ts:
                if t not in seen:
                    seen.add(t)
                    out.append(t)

        for score, blocks, lows in ranked:
            if score > 0:
                _take(blocks)   # the product's own page: description/hours/prereqs all belong
            elif not any_own_page and words:
                # nothing names the product exactly — honest best-effort any-word fallback
                _take(t for t, tl in zip(blocks, lows) if any(w in tl for w in words))
            if sum(len(x) for x in out) > limit_chars:
                break
        return "\n".join(out)[:limit_chars]
    except Exception:  # noqa: BLE001
        return ""


def hitl_preview(slug: str, kind: str, *, out_dir: str = "outputs",
                 product_name: str | None = None, product_image: str | None = None,
                 language: str | None = None, dialect: str | None = None) -> dict:
    """Build the EXACT generation brief (poster prompt / reel plan) with zero paid renders.
    Saves the artifact; returns {text, arabic, file}. {'error': ...} on failure."""
    import json as _json
    import os
    _hitl_env()
    P = paths(slug, out_dir)
    if not P["profile"].is_file():
        return {"error": "no profile — run Analyze first"}
    profile = _json.loads(P["profile"].read_text(encoding="utf-8"))
    if kind == "poster":
        from poster.pipeline import build_generation_preview
        prev = build_generation_preview(
            profile, product_image=product_image, language=(language or "auto"),
            raw_facts=_product_raw_facts(slug, product_name))
        text = prev["prompt"]
        path = P["out"] / f"{slug}_poster_prompt.txt"
        path.write_text(text, encoding="utf-8")
    else:
        if dialect in ("masri", "fusha"):
            os.environ["REEL_ARABIC_DIALECT"] = dialect
        from reel.coherence import plan_reel_gated
        photos = (profile.get("visual") or {}).get("content_images") or []
        rf = _product_raw_facts(slug, product_name)
        try:
            from business_profile.llm import default_caller
            _critic = default_caller(strong=False)
        except Exception:  # noqa: BLE001
            _critic = None
        # The coherence gate authors + auto-corrects the plan BEFORE the owner sees it, so the
        # 8 creative contracts are enforced (product shown, one CTA, one dialect, sound, brand-
        # specific, …), not hand-fixed each run. Residual issues are surfaced below, not hidden.
        creative, crep = plan_reel_gated(
            profile, photos, n_scenes=6, language=(language or None),
            featured_product=product_name, critic_caller=_critic,
            user_note=("REAL PRODUCT-PAGE FACTS (verbatim):\n" + rf) if rf else None)
        if creative is None or not creative.scenes:
            return {"error": "the director could not design a plan (no usable photos/creds)"}
        path = P["out"] / f"{slug}_reel_plan.json"
        path.write_text(creative.model_dump_json(indent=2), encoding="utf-8")

        # The render-side grounding gate BLANKS unsourced lines — the preview must show the
        # user the same verdicts NOW, not diverge after approval (owner check, 2026-07-12).
        def _flag(line: str) -> str:
            try:
                from grounding import EvidenceLedger
                led = getattr(_flag, "_led", None)
                if led is None:
                    led = _flag._led = EvidenceLedger.from_profile(profile)
                uns = [v for v in led.audit_text(line or "") if not v.sourced]
                return "  ⚠ غير مدعوم — سيُحذف عند التنفيذ" if uns else ""
            except Exception:  # noqa: BLE001
                return ""

        lines = [f"CONCEPT: {creative.concept}", f"HOOK: {creative.hook}{_flag(creative.hook)}",
                 f"CTA: {creative.cta}{_flag(creative.cta)}", f"LANGUAGE: {creative.language}", ""]
        for i, sc in enumerate(creative.scenes, 1):
            lines += [f"SCENE {i} ({sc.duration_s:.0f}s, photo #{sc.image_index}):",
                      f"  MOTION: {sc.veo_prompt}",
                      f"  VOICE-OVER: {sc.voiceover}{_flag(sc.voiceover)}",
                      f"  CAPTION: {sc.on_screen_text or '—'}", ""]
        if crep is not None and not crep.ok and crep.issues:
            lines += ["⚠ نقاط لم تُحسَم تلقائياً · gate could not fully resolve:"]
            lines += [f"  - {it}" for it in crep.issues[:8]]
        text = "\n".join(lines)
    return {"text": text, "arabic": _arabic_rendering(text), "file": str(path)}


def hitl_refine(slug: str, kind: str, instruction: str, *, out_dir: str = "outputs",
                product_name: str | None = None, language: str | None = None,
                dialect: str | None = None) -> dict:
    """Apply the user's change request and return the UPDATED brief for re-approval.
    Poster: a text-only revision that must preserve the verbatim copy lines (and the
    render-side fidelity gate enforces them regardless). Reel: the director re-plans
    WITH the request (user_note) — grounding rules unchanged."""
    import json as _json
    import os
    _hitl_env()
    P = paths(slug, out_dir)
    if kind == "poster":
        path = P["out"] / f"{slug}_poster_prompt.txt"
        if not path.is_file():
            return {"error": "no previewed prompt to refine"}
        current = path.read_text(encoding="utf-8")
        try:
            from pydantic import BaseModel

            class _R(BaseModel):
                revised_prompt: str = ""

            from business_profile.llm import default_caller
            caller = default_caller(strong=False)
            out, _u = caller(
                "You revise an image-generation brief per the user's change request. "
                "HARD RULES: never remove, translate or alter the quoted verbatim copy lines "
                "(HEADLINE/SUBHEADLINE/CALL-TO-ACTION) or the BRAND INTEGRITY / compliance "
                "clauses; apply the request to the creative aspects only. Return the FULL "
                "revised brief in revised_prompt.",
                f"CURRENT BRIEF:\n{current}\n\nUSER CHANGE REQUEST:\n{instruction}",
                _R, group_name="hitl_refine")
            revised = (out.revised_prompt or "").strip()
            if not revised:
                return {"error": "refine returned empty"}
            path.write_text(revised, encoding="utf-8")
            return {"text": revised, "arabic": _arabic_rendering(revised), "file": str(path)}
        except Exception as e:  # noqa: BLE001
            return {"error": f"refine failed: {type(e).__name__}"}
    # reel: re-plan with the note (the coherence gate still enforces the contracts on the revision)
    if dialect in ("masri", "fusha"):
        os.environ["REEL_ARABIC_DIALECT"] = dialect
    profile = _json.loads(P["profile"].read_text(encoding="utf-8"))
    from reel.coherence import plan_reel_gated
    photos = (profile.get("visual") or {}).get("content_images") or []
    try:
        from business_profile.llm import default_caller
        _critic = default_caller(strong=False)
    except Exception:  # noqa: BLE001
        _critic = None
    creative, crep = plan_reel_gated(profile, photos, n_scenes=6, language=(language or None),
                                     featured_product=product_name, user_note=instruction,
                                     critic_caller=_critic)
    if creative is None or not creative.scenes:
        return {"error": "the director could not apply the request"}
    path = P["out"] / f"{slug}_reel_plan.json"
    path.write_text(creative.model_dump_json(indent=2), encoding="utf-8")
    lines = [f"CONCEPT: {creative.concept}", f"HOOK: {creative.hook}",
             f"CTA: {creative.cta}", f"LANGUAGE: {creative.language}", ""]
    for i, sc in enumerate(creative.scenes, 1):
        lines += [f"SCENE {i} ({sc.duration_s:.0f}s, photo #{sc.image_index}):",
                  f"  MOTION: {sc.veo_prompt}",
                  f"  VOICE-OVER: {sc.voiceover}",
                  f"  CAPTION: {sc.on_screen_text or '—'}", ""]
    if crep is not None and not crep.ok and crep.issues:
        lines += ["⚠ نقاط لم تُحسَم تلقائياً · gate could not fully resolve:"]
        lines += [f"  - {it}" for it in crep.issues[:8]]
    text = "\n".join(lines)
    return {"text": text, "arabic": _arabic_rendering(text), "file": str(path)}
