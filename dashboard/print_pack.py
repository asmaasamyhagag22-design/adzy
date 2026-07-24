# -*- coding: utf-8 -*-
"""Per-brand PRINT PACK — a folder named after the website with one printable HTML page per
artifact (owner 2026-07-18: "اعمل فولدر باسم الويب سايت فيها طباعة للترند والسوات والبيزنس بروفايل
والكامبين والبلان والكالندر — كل حاجة، نتايج أتابعها وأطبعها").

It REUSES the dashboard's section builders (dashboard/build.py) — no new rendering — and wraps each
in a minimal, self-contained, PRINT-friendly page (`@media print` + `@page`, absent everywhere else
in the repo). Sources the artifacts from the same on-disk files the studio reads
(`dashboard/run.py:paths`): `{slug}_result.json` (profile/swot/tows/trends/competitors),
`{slug}_media_plan.json`, `{slug}_plan.json`.

Zero spend: pure dict->HTML off existing files. Reels/posters are EMBEDDED (base64), never rendered.

    python -m dashboard.print_pack <slug> [--out outputs/reports]
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from dashboard import build as B
from dashboard import run as _run


# --- the print-friendly page wrapper (the piece that's genuinely new) ----------------------------
def _print_css() -> str:
    return """
    <style>
    .pp-head{max-width:1180px;margin:0 auto 18px;display:flex;align-items:baseline;
      justify-content:space-between;gap:14px;flex-wrap:wrap;}
    .pp-title{font-family:var(--serif);font-size:24px;color:#2b2426;font-weight:600;}
    .pp-meta{font-size:12px;color:#9a8f93;}
    .pp-actions{margin:0 auto 20px;max-width:1180px;}
    .pp-btn{display:inline-block;background:#A98BD0;color:#fff;text-decoration:none;
      padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;border:none;cursor:pointer;}
    .pp-nav{max-width:1180px;margin:0 auto 22px;font-size:12.5px;}
    .pp-nav a{color:#A98BD0;text-decoration:none;margin-inline-end:12px;}
    @page{margin:1.4cm;}
    @media print{
      .no-print{display:none!important;}
      body{background:#fff!important;}
      .bsr{background:#fff!important;}
      .sec,.card,.quad,.comp,.kpi,.ex-card{box-shadow:none!important;break-inside:avoid;}
      .hero::before,.bsr::before{display:none!important;}
      *{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
    }
    </style>"""


def _page(title: str, inner: str, *, brand: str, source: str, gen: str,
          siblings: Optional[list] = None) -> str:
    """Wrap one artifact's section HTML in a standalone, printable, RTL page (reuses _css())."""
    nav = ""
    if siblings:
        nav = ('<nav class="pp-nav no-print">' +
               "".join(f'<a href="{fn}">{lbl}</a>' for fn, lbl in siblings) + "</nav>")
    head = (
        f'<div class="bsr"><div class="wrap">'
        f'<div class="pp-actions no-print"><button class="pp-btn" onclick="window.print()">'
        f'🖨️ اطبع · Print</button></div>'
        f'{nav}'
        f'<div class="pp-head"><div class="pp-title">{B._esc(title)}</div>'
        f'<div class="pp-meta">{B._esc(brand)} · {B._esc(gen)}<br>{B._esc(source)}</div></div>'
        f'{inner}'
        f'<div class="foot">Baseera · {B._esc(gen)} · كل سطر واقعي يحمل مصدره · every factual line carries its source.</div>'
        f'</div></div>'
    )
    return (f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Baseera · {B._esc(brand)} · {B._esc(title)}</title>'
            f'{B._css()}{_print_css()}</head><body>{head}</body></html>')


# --- per-artifact section renderers (REUSE build.py; return "" when the data is absent) -----------
def _swot_tows_inner(comp: dict) -> str:
    swot = comp.get("swot") or {}
    quads = (B._swot_quad('Strengths', '💪', swot.get('strengths'), 's') +
             B._swot_quad('Weaknesses', '⚠', swot.get('weaknesses'), 'w') +
             B._swot_quad('Opportunities', '✦', swot.get('opportunities'), 'o') +
             B._swot_quad('Threats', '⚡', swot.get('threats'), 't'))
    swot_html = f'<div class="sec"><div class="sec-h"><span class="bar"></span><h2>SWOT</h2></div><div class="swot">{quads}</div></div>'
    tows = comp.get("tows") or {}
    strategies = tows.get("strategies") or []
    trows = ""
    for s in strategies:
        trows += (f'<div class="tows-row"><div class="tows-type">{B._esc(s.get("type") or "")}</div>'
                  f'<div><b>{B._esc(s.get("title") or "")}</b>'
                  f'<div class="tows-desc">{B._esc(s.get("description") or s.get("rationale") or "")}</div></div></div>')
    tows_html = (f'<div class="sec"><div class="sec-h"><span class="bar"></span>'
                 f'<h2>الاستراتيجية · Strategy (TOWS)</h2></div><div class="card">{trows}</div></div>'
                 if trows else "")
    return swot_html + tows_html


def _creative_inner(paths: dict) -> str:
    parts = []
    poster_uri = B._data_uri(str(paths["poster"])) if paths["poster"].is_file() else None
    reel_uri = B._data_uri(str(paths["reel"])) if paths["reel"].is_file() else None
    if poster_uri:
        parts.append(f'<div class="sec"><div class="sec-h"><span class="bar"></span><h2>البوستر · Poster</h2></div>'
                     f'<div class="card"><img src="{poster_uri}" style="max-width:100%;border-radius:12px" alt="poster"></div></div>')
    if reel_uri:
        parts.append(f'<div class="sec no-print"><div class="sec-h"><span class="bar"></span><h2>الريل · Reel</h2></div>'
                     f'<div class="card"><video src="{reel_uri}" controls style="max-width:100%;border-radius:12px"></video></div></div>')
    return "".join(parts)


def build_print_pack(slug: str, out_dir: str = "outputs", reports_root: str = "outputs/reports") -> Path:
    """Write outputs/reports/<slug>/ with one printable HTML per artifact + an index. Returns the dir."""
    P = _run.paths(slug, out_dir)
    if not P["result"].is_file():
        raise FileNotFoundError(f"no analysis on disk for {slug!r} (missing {P['result']})")

    comp = B._load(str(P["result"]))
    prof_file = B._load(str(P["profile"])) if P["profile"].is_file() else {}
    prof = (prof_file.get("profile", prof_file) if prof_file else comp.get("profile")) or {}
    plan = B._load(str(P["plan"])) if P["plan"].is_file() else {}
    # media plan is the auto-discovered sibling {slug}_media_plan.json (not in run.paths)
    mp_path = Path(out_dir) / f"{slug}_media_plan.json"
    mp = B._load(str(mp_path)) if mp_path.is_file() else {}
    gen = (comp.get("generated_at") or datetime.now().isoformat())[:10]
    brand = B._val((prof.get("name") if isinstance(prof, dict) else None)) or slug
    src = comp.get("subject_url") or ""
    category = B._val(prof.get("category")) if isinstance(prof, dict) else ""

    # (filename, human label, section-HTML) — only the artifacts that actually have data
    pages: list[tuple[str, str, str]] = []

    def add(fn: str, label: str, html: str):
        if html and html.strip():
            pages.append((fn, label, html))

    # 1 business profile
    add("01_business_profile.html", "البروفايل · Business profile", B._brand_assets(prof))
    # 2 SWOT + TOWS
    add("02_swot_tows.html", "SWOT + TOWS", _swot_tows_inner(comp))
    # 3 trends
    add("03_trends.html", "الترند · Trends", B._market_pulse(comp, gen))
    # 4 media plan
    mp_obj = mp or (comp.get("media_plan") or {})
    add("04_media_plan.html", "خطة الإعلان · Media plan", B._explained_plan(mp_obj, category) if mp_obj else "")
    # 5 content calendar
    items = plan.get("items") or []
    cal = "".join(B._calendar_row(it) for it in items)
    add("05_calendar.html", "التقويم · Content calendar",
        f'<div class="sec"><div class="sec-h"><span class="bar"></span><h2>Content calendar</h2>'
        f'<span class="cnt">{len(items)} posts</span></div><div class="card">{cal}</div></div>' if cal else "")
    # 6 advisor gaps
    n_voice = len(comp.get("customer_voice") or comp.get("reviews") or [])
    add("06_advisor_gaps.html", "ما لا نعرفه · What we don't know yet",
        B._honesty_surface(comp, mp_obj, prof, n_voice))
    # 7 creative (poster + reel embed)
    add("07_creative.html", "الإبداع · Creative", _creative_inner(P))

    dest = Path(reports_root) / slug
    dest.mkdir(parents=True, exist_ok=True)
    siblings = [("index.html", "الفهرس · Index")] + [(fn, lbl) for fn, lbl, _ in pages]
    for fn, label, html in pages:
        (dest / fn).write_text(_page(label, html, brand=brand, source=src, gen=gen, siblings=siblings),
                               encoding="utf-8")

    # index: links + a "print all" that opens each and prints (screen-only)
    links = "".join(
        f'<a class="pp-idx" href="{fn}"><b>{B._esc(lbl)}</b></a>' for fn, lbl, _ in pages)
    idx_inner = (
        f'<div class="sec"><div class="sec-h"><span class="bar"></span>'
        f'<h2>ملف {B._esc(brand)} — كل المخرجات · the full brand pack</h2>'
        f'<span class="cnt">{len(pages)} تقارير</span></div>'
        f'<div class="card" style="display:flex;flex-direction:column;gap:10px">{links}</div></div>'
        '<style>.pp-idx{display:block;padding:12px 14px;border:1px solid #eaddE1;border-radius:10px;'
        'text-decoration:none;color:#2b2426;background:#fff;} .pp-idx:hover{background:#faf1f4;}</style>')
    (dest / "index.html").write_text(
        _page("الفهرس · Index", idx_inner, brand=brand, source=src, gen=gen), encoding="utf-8")
    return dest


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="print_pack",
                                 description="Per-brand printable report folder.")
    ap.add_argument("slug", help="brand slug (e.g. rawafrican_net) — must have outputs/<slug>_result.json")
    ap.add_argument("--out", default="outputs", help="where the {slug}_*.json artifacts live")
    ap.add_argument("--reports", default="outputs/reports", help="root for the per-brand folders")
    args = ap.parse_args(argv)
    dest = build_print_pack(args.slug, out_dir=args.out, reports_root=args.reports)
    n = len(list(dest.glob("*.html")))
    print(f"[print_pack] {n} printable pages -> {dest}")
    print(f"[print_pack] open {dest / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
