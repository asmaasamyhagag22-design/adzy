"""MEASURE the one-shot designed-poster pilot (step ب) — Arabic/English text fidelity.

The GO/NO-GO question (rule 2 — measure before wiring anything): when a Gemini image model
designs the WHOLE poster with our Ledger-gated copy rendered INSIDE the image, does the
rendered text stay VERBATIM (correct, connected Arabic) — and does the model refrain from
inventing extra text?

Per render: generate -> a vision model reads the image back -> `poster.oneshot.text_fidelity`
(normalized verbatim compare + extra-text flag). Copy = the REAL gated pipeline copy
(`build_creative_concept(enforce_grounding=True)` on a saved profile). Renders land in
outputs/posters/oneshot_pilot/ for eyeballing; results print as a table.

Usage (live, costs money — Vertex ADC):
    python -m benchmark.measure_oneshot_poster [--n 3]
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "posters" / "oneshot_pilot"

MODELS = ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview"]


def _logo_bytes(brief) -> tuple[bytes | None, str]:
    url = getattr(brief, "logo_url", None) or ""
    try:
        if url.startswith("data:"):
            head, b64 = url.split(",", 1)
            mime = head.split(";")[0].split(":", 1)[1] or "image/png"
            return base64.b64decode(b64), mime
        if url.startswith("http"):
            from reel.video_provider import _load_reference_image
            got = _load_reference_image(url)
            if got:
                return got[0], got[1]
    except Exception:
        pass
    return None, "image/png"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="Arabic renders per model")
    ap.add_argument("--profile", default=str(ROOT / "te_eg_fresh.json"))
    args = ap.parse_args()

    # CS-116/135: the historical default profile no longer exists — fail with a USEFUL message
    # (the real candidates on disk) instead of a bare FileNotFoundError before any render.
    if not Path(args.profile).is_file():
        cands = sorted(p.name for p in (ROOT / "outputs").glob("*_profile.json"))[:10]
        hint = ("\n  available: " + ", ".join(cands)) if cands else ""
        raise SystemExit(f"[x] profile not found: {args.profile}\n"
                         f"    pass one explicitly: --profile outputs/<slug>_profile.json{hint}")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from business_profile.llm.caller import GeminiCaller, default_caller
    from poster.art_director import _palette_names
    from poster.concept import build_creative_concept
    from poster.from_profile import build_poster_brief
    from poster.oneshot import build_oneshot_prompt, generate_oneshot_poster, read_rendered_text, text_fidelity
    from poster.pipeline import load_or_build_dna

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    brief = build_poster_brief(profile)
    caller = default_caller(strong=True)
    ocr = GeminiCaller(model="gemini-2.5-flash")

    # THE REAL PIPELINE COPY (Ledger-gated) — the exact text a shipped poster would carry.
    concept = build_creative_concept(profile, caller=caller, enforce_grounding=True)
    ar_copy = {"headline": concept.headline, "subheadline": concept.subheadline,
               "cta": concept.cta}
    print("AR copy (gated):", json.dumps(ar_copy, ensure_ascii=False))
    # EN sanity case: the brand's verbatim scraped EN line (evidence text, grounded by source).
    en_copy = {"headline": (brief.headline or "Stay Connected"),
               "cta": "Learn more"}
    print("EN copy        :", json.dumps(en_copy, ensure_ascii=False))

    palette = _palette_names(brief.palette_hex)
    dna = load_or_build_dna(profile, None)          # cached only (caller=None -> no vision spend)
    dna_lines = ""
    for attr in ("imagery_style", "mood", "motifs"):
        v = getattr(dna, attr, None) if dna is not None else None
        if v:
            dna_lines += f"{attr}: {v if isinstance(v, str) else ', '.join(map(str, v))}. "
    logo, logo_mime = _logo_bytes(brief)
    print(f"palette: {palette} | dna: {bool(dna_lines)} | logo: {len(logo) if logo else 0}B")

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in MODELS:
        cases = [("AR", ar_copy, True)] * args.n + ([("EN", en_copy, False)] if model == MODELS[0] else [])
        for k, (lang, copy, rtl) in enumerate(cases):
            tag = f"{model.split('-image')[0]}_{lang}{k}"
            out = OUT / f"{tag}.png"
            prompt = build_oneshot_prompt(copy, brand_name=brief.business_name or "",
                                          palette_names=palette, dna_lines=dna_lines[:400],
                                          rtl=rtl, has_logo=bool(logo))
            try:
                generate_oneshot_poster(prompt, out, model=model,
                                        logo_bytes=logo, logo_mime=logo_mime)
                seen = read_rendered_text(out, ocr)
                fid = text_fidelity(copy, seen)
                rows.append((tag, fid["rate"], fid["fields"], fid["extra_lines"]))
                print(f"[{tag}] rate={fid['rate']} fields={fid['fields']}")
                if fid["extra_lines"]:
                    print(f"        extra: {fid['extra_lines']}")
            except Exception as exc:  # noqa: BLE001 — a failed render is a data point
                rows.append((tag, None, {}, []))
                print(f"[{tag}] RENDER FAILED: {type(exc).__name__}: {str(exc)[:120]}")

    print("\n=== SUMMARY ===")
    for model in MODELS:
        key = model.split("-image")[0]
        rs = [r for t, r, _, _ in rows if t.startswith(key) and "_AR" in t and r is not None]
        if rs:
            print(f"{model}: AR mean fidelity {sum(rs)/len(rs):.2f} over {len(rs)} renders "
                  f"(perfect: {sum(1 for r in rs if r == 1.0)}/{len(rs)})")
    (OUT / "results.json").write_text(
        json.dumps([{"tag": t, "rate": r, "fields": f, "extra": e} for t, r, f, e in rows],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("renders + results.json ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
