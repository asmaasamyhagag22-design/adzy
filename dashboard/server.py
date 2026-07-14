"""Baseera Studio - a LOCAL, INTERACTIVE marketing web app.

    python -m dashboard.server              # -> http://127.0.0.1:8770  (opens the browser)
    python -m dashboard.server --port 9000 --no-open

Paste a brand URL, press Analyze, and WATCH the fast core run (scrape -> profile -> competitors
-> SWOT -> content calendar). Then you land INSIDE the dashboard - the SWOT, competitors and
calendar are there, and a Creative Studio lets you GENERATE on demand: press "Generate poster" for
an image, "Generate reel" for the video, and Regenerate until it's right. Every stage streams over
SSE. Heavy generation (Veo/Imagen) runs only when YOU ask for it - the owner wanted to sit in the
heart of the dashboard and drive it, not wait for a one-shot report.

Stdlib-only (http.server + threads): no framework, no async, no build step. The heavy lifting is
the SAME tested pipeline (dashboard.run.analyze / generate_poster / generate_reel).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dashboard import run as _run_mod
from dashboard.archive_qa import answer_question
from dashboard.build import _esc, build_dashboard_html

# Baseera palette (in lock-step with dashboard/build.py::_C so studio + dashboard read as one).
_C = {
    "bg": "#FAF5F1", "surface": "#FFFFFF", "ink": "#2A1A1F", "inkSoft": "#5C4A52",
    "muted": "#9B8A92", "line": "rgba(168,69,107,0.14)",
    "blush100": "#F6E0E7", "blush500": "#B85C7A", "blush600": "#9C4564", "blush700": "#7E3450",
    "ok": "#3F8F6B", "bad": "#B54848", "gold": "#C68A3C",
}

_SLUG_RE = re.compile(r"^[A-Za-z0-9_]+$")

# One in-flight generation per (slug, kind) across all request threads — a duplicate request
# (refresh / second tab / SSE reconnect) is rejected instead of racing on the same output file.
_INFLIGHT: set = set()
_INFLIGHT_LOCK = threading.Lock()


def sse(event: str, data) -> bytes:
    """Format one Server-Sent Event frame (JSON-encoded data, so newlines can't break framing)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _ok_slug(slug: str) -> bool:
    return bool(slug) and bool(_SLUG_RE.match(slug))


# ------------------------------------------------------------------------------------------------
# Landing page (URL -> Analyze -> redirect to the studio)
# ------------------------------------------------------------------------------------------------
def _landing_html() -> str:
    c = _C
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Baseera Studio</title>
<style>
  :root{{color-scheme:light;}} *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
    color:{c['ink']};background:{c['bg']};line-height:1.5;-webkit-font-smoothing:antialiased;
    background-image:
      radial-gradient(circle at 8% 4%,rgba(236,203,214,.38) 0%,transparent 26%),
      radial-gradient(circle at 94% 8%,rgba(240,228,211,.30) 0%,transparent 28%),
      radial-gradient(circle at 4% 98%,rgba(246,224,231,.36) 0%,transparent 28%);
    min-height:100vh;padding:34px clamp(14px,4vw,44px) 60px;}}
  .serif{{font-family:'Fraunces',Georgia,'Times New Roman',serif;}}
  .wrap{{max-width:900px;margin:0 auto;}}
  .brand{{display:flex;align-items:center;gap:11px;margin-bottom:22px;}}
  .dot{{width:34px;height:34px;border-radius:11px;flex:none;
    background:linear-gradient(135deg,{c['blush500']},{c['blush700']});box-shadow:0 6px 16px rgba(158,69,100,.28);}}
  .brand-n{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:22px;letter-spacing:-.3px;}}
  .brand-t{{font-size:12px;color:{c['muted']};letter-spacing:.4px;text-transform:uppercase;}}
  .card{{background:{c['surface']};border:1px solid {c['line']};border-radius:20px;
    padding:clamp(20px,4vw,34px);box-shadow:0 14px 40px rgba(126,52,80,.09);}}
  h1{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:clamp(24px,4vw,34px);
    line-height:1.12;letter-spacing:-.6px;margin-bottom:8px;}} h1 em{{font-style:normal;color:{c['blush600']};}}
  .sub{{color:{c['inkSoft']};font-size:15px;max-width:60ch;margin-bottom:24px;}}
  .row{{display:flex;gap:10px;flex-wrap:wrap;}}
  input[type=url]{{flex:1 1 340px;min-width:0;padding:14px 16px;font-size:15px;border:1.5px solid {c['line']};
    border-radius:12px;background:#fff;color:{c['ink']};font-family:inherit;}}
  input[type=url]:focus{{outline:none;border-color:{c['blush500']};box-shadow:0 0 0 4px rgba(184,92,122,.14);}}
  button{{padding:14px 26px;font-size:15px;font-weight:600;font-family:inherit;cursor:pointer;color:#fff;
    border:none;border-radius:12px;white-space:nowrap;background:linear-gradient(135deg,{c['blush500']},{c['blush700']});
    box-shadow:0 8px 20px rgba(158,69,100,.26);transition:transform .12s,opacity .12s;}}
  button:hover:not(:disabled){{transform:translateY(-1px);}} button:disabled{{opacity:.5;cursor:not-allowed;}}
  .stage-strip{{display:flex;flex-wrap:wrap;gap:7px;margin-top:24px;}}
  .chip{{font-size:11.5px;font-weight:600;padding:6px 12px;border-radius:999px;background:#fff;
    border:1px solid {c['line']};color:{c['muted']};transition:all .2s;}}
  .chip.run{{color:{c['blush700']};border-color:{c['blush500']};background:{c['blush100']};animation:pulse 1.1s ease-in-out infinite;}}
  .chip.ok{{color:{c['ok']};border-color:rgba(63,143,107,.4);background:rgba(63,143,107,.09);}}
  .chip.bad{{color:{c['bad']};border-color:rgba(181,72,72,.4);background:rgba(181,72,72,.08);}}
  @keyframes pulse{{0%,100%{{opacity:1;}}50%{{opacity:.55;}}}}
  #log{{margin-top:18px;background:#2A1A1F;color:#F3E7EC;border-radius:14px;padding:16px 18px;
    font-family:'Space Grotesk',ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.7;
    max-height:300px;overflow:auto;display:none;white-space:pre-wrap;word-break:break-word;}}
  #log .l-ok{{color:#8FE0B8;}} #log .l-bad{{color:#F3A8A8;}} #log .l-run{{color:#E8C7D4;}}
  .foot{{text-align:center;color:{c['muted']};font-size:12px;margin-top:26px;}}
</style></head><body>
<div class="wrap">
  <div class="brand"><div class="dot"></div>
    <div><div class="brand-n">Baseera Studio</div><div class="brand-t">Marketing Intelligence</div></div></div>
  <div class="card">
    <h1 class="serif">One URL in, a <em>campaign you drive</em>.</h1>
    <p class="sub">Paste a brand's website. Baseera studies it — profile, cited SWOT, real
      competitors, a content calendar — then hands you a live dashboard where you generate the
      poster and the reel on demand, and regenerate until they're right.</p>
    <div class="row">
      <input id="url" type="url" placeholder="https://your-brand.com" autocomplete="off" spellcheck="false">
      <button id="go">Analyze</button>
    </div>
    <div id="strip" class="stage-strip" style="display:none"></div>
    <div id="log"></div>
  </div>
  <div class="foot">Runs locally. Heavy generation only happens when you ask for it.</div>
</div>
<script>
const STAGES=["Scrape + Profile + Competitors + SWOT","Content calendar"];
const $=s=>document.querySelector(s);
const url=$("#url"),go=$("#go"),strip=$("#strip"),log=$("#log"); let es=null;
function chips(){{strip.innerHTML="";strip.style.display="flex";
  STAGES.forEach(s=>{{const d=document.createElement("div");d.className="chip";d.dataset.k=s;
    d.textContent=s.replace(" + Competitors + SWOT","...");strip.appendChild(d);}});}}
function setChip(label,cls){{const el=[...strip.children].find(c=>label.startsWith(c.dataset.k.split(" ...")[0].split(" +")[0])||c.dataset.k===label); if(el)el.className="chip "+cls;}}
function line(t){{const d=document.createElement("div");
  if(t.includes("[OK]"))d.className="l-ok";else if(t.includes("[X]"))d.className="l-bad";
  else if(t.trim().startsWith("->"))d.className="l-run";d.textContent=t;log.appendChild(d);log.scrollTop=log.scrollHeight;}}
go.onclick=()=>{{const u=url.value.trim();if(!u){{url.focus();return;}}
  if(es)es.close();go.disabled=true;log.style.display="block";log.innerHTML="";chips();
  line("* Baseera - analyzing "+u+"\\n");
  es=new EventSource("/analyze?url="+encodeURIComponent(u));
  es.addEventListener("stage",e=>{{const d=JSON.parse(e.data);line(d.msg);
    if(d.event==="stage_start")setChip(d.label,"run");else if(d.event==="stage_ok")setChip(d.label,"ok");
    else if(d.event==="stage_fail")setChip(d.label,"bad");}});
  es.addEventListener("done",e=>{{const d=JSON.parse(e.data);es.close();
    line("\\n[OK] opening your studio ...");window.location="/studio?slug="+encodeURIComponent(d.slug);}});
  es.addEventListener("failed",e=>{{const d=JSON.parse(e.data);es.close();go.disabled=false;
    line("\\n[X] "+(d.msg||"analysis failed"));}});
  es.onerror=()=>{{if(es.readyState===EventSource.CLOSED)go.disabled=false;}};}};
url.addEventListener("keydown",e=>{{if(e.key==="Enter")go.click();}});
</script></body></html>"""


# ------------------------------------------------------------------------------------------------
# Studio page (the report body + an interactive Creative Studio)
# ------------------------------------------------------------------------------------------------
def _panel(kind: str, icon: str, title: str, sub: str, has_asset: bool, slug: str) -> str:
    """One Creative-Studio panel: a stage (asset or placeholder) + log + a generate/regenerate button."""
    asset_url = f"/asset?slug={slug}&amp;kind={kind}"
    reel_cls = " is-reel" if kind == "reel" else ""
    if has_asset and kind == "poster":
        stage_cls, inner = f"cst-stage{reel_cls} has-asset", f'<img src="{asset_url}" alt="poster">'
    elif has_asset and kind == "reel":
        stage_cls = f"cst-stage{reel_cls} has-asset"
        inner = f'<video src="{asset_url}" controls playsinline preload="metadata"></video>'
    else:
        stage_cls = f"cst-stage{reel_cls}"
        hint = ("press Generate — a few minutes" if kind == "poster"
                else "press Generate — Veo takes ~10–20 min")
        inner = (f'<div class="cst-empty">{icon}<span>Not generated yet</span>'
                 f'<small>{hint}</small></div>')
    btn = "Regenerate" if has_asset else f"Generate {kind}"
    # OWNER RULE (2026-07-11): the reel's LANGUAGE and Arabic REGISTER are the user's
    # explicit choices — selects live next to the button and ride the /generate URL.
    controls = ""
    if kind == "reel":
        controls = (
            '<div class="cst-opts">'
            '<label>اللغة <select id="reel-lang">'
            '<option value="ar" selected>العربية</option>'
            '<option value="en">English</option></select></label>'
            '<label id="reel-dialect-wrap">اللهجة <select id="reel-dialect">'
            '<option value="masri" selected>عامية مصرية</option>'
            '<option value="fusha">فصحى</option></select></label>'
            '</div>'
            '<script>document.getElementById("reel-lang").addEventListener("change",function(){'
            'document.getElementById("reel-dialect-wrap").style.display='
            'this.value==="ar"?"inline-flex":"none";});</script>')
    return f"""<div class="cst-panel" id="{kind}-panel">
      <div class="cst-head">{icon} {title} <span class="cst-sub">{sub}</span></div>
      <div class="{stage_cls}" id="{kind}-stage">{inner}</div>
      <div class="cst-log" id="{kind}-log"></div>
      {controls}
      <button class="cst-btn" id="{kind}-btn" onclick="gen('{kind}')">{btn}</button>
    </div>"""


def _studio_css() -> str:
    c = _C
    return f"""<style>
    .cst-wrap{{max-width:1180px;margin:0 auto;}}
    .mp-card{{background:{c['surface']};border:1px solid {c['line']};border-radius:18px;
      padding:20px 24px;margin:0 0 18px;box-shadow:0 10px 30px rgba(126,52,80,.07);}}
    .mp-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;}}
    .mp-title{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:19px;}}
    .mp-map{{display:flex;align-items:center;gap:0;flex-wrap:wrap;margin:4px 0 16px;}}
    .mp-step{{display:flex;align-items:center;font-size:12px;font-weight:600;color:{c['inkSoft']};}}
    .mp-step:not(:last-child)::after{{content:"";width:34px;height:2px;margin:0 8px;
      background:linear-gradient(90deg,{c['blush500']},{c['blush100']});border-radius:2px;}}
    .mp-dot{{width:9px;height:9px;border-radius:50%;margin-inline-end:6px;
      background:linear-gradient(135deg,{c['blush500']},{c['blush700']});}}
    .mp-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;}}
    .mp-grid div{{background:{c['bg']};border:1px solid {c['line']};border-radius:12px;
      padding:10px 14px;display:flex;flex-direction:column;gap:3px;}}
    .mp-grid small{{color:{c['muted']};font-size:11px;letter-spacing:.3px;}}
    .mp-grid b{{font-size:14px;}}
    .mp-why{{margin-top:12px;font-size:13px;color:{c['inkSoft']};line-height:1.65;
      border-inline-start:3px solid {c['blush100']};padding-inline-start:12px;}}
    .hitl-ov{{position:fixed;inset:0;background:rgba(42,26,31,.55);display:none;
      align-items:center;justify-content:center;z-index:60;padding:18px;}}
    .hitl-box{{background:#fff;border-radius:18px;max-width:860px;width:100%;max-height:88vh;
      display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(126,52,80,.35);overflow:hidden;}}
    .hitl-head{{padding:16px 22px;border-bottom:1px solid {c['line']};display:flex;
      align-items:center;justify-content:space-between;font-weight:700;}}
    .hitl-tabs button{{background:#fff;color:{c['inkSoft']};border:1.5px solid {c['line']};
      box-shadow:none;padding:7px 16px;border-radius:9px;font-size:12.5px;margin-inline-start:6px;}}
    .hitl-tabs button.on{{background:{c['blush100']};color:{c['blush700']};border-color:{c['blush500']};}}
    .hitl-body{{padding:14px 22px;overflow:auto;flex:1;}}
    .hitl-body textarea{{width:100%;min-height:300px;border:1.5px solid {c['line']};border-radius:12px;
      padding:12px 14px;font-family:'Space Grotesk',ui-monospace,monospace;font-size:12.5px;
      line-height:1.65;color:{c['ink']};resize:vertical;}}
    .hitl-note{{font-size:12px;color:{c['muted']};margin:8px 0 4px;}}
    .hitl-edit{{display:flex;gap:8px;margin-top:10px;}}
    .hitl-edit input{{flex:1;padding:11px 13px;border:1.5px solid {c['line']};border-radius:10px;
      font-family:inherit;font-size:13.5px;}}
    .hitl-foot{{padding:14px 22px;border-top:1px solid {c['line']};display:flex;gap:10px;
      justify-content:flex-end;align-items:center;}}
    .hitl-exec{{background:linear-gradient(135deg,{c['ok']},#2E7355)!important;font-size:15px;
      padding:13px 30px;}}
    .hitl-cancel{{background:#fff!important;color:{c['inkSoft']}!important;border:1.5px solid {c['line']}!important;
      box-shadow:none!important;}}
    .cst-opts{{display:flex;gap:14px;margin:8px 0 6px;flex-wrap:wrap;}}
    .cst-opts label{{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;
      color:{c['inkSoft']};font-weight:600;}}
    .cst-opts select{{padding:7px 10px;border:1.5px solid {c['line']};border-radius:9px;
      background:#fff;color:{c['ink']};font-family:inherit;font-size:13px;}}
    .cst-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;}}
    @media(max-width:760px){{.cst-grid{{grid-template-columns:1fr;}}}}
    .cst-pick{{margin-bottom:18px;}}
    .cst-pick-h{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:15px;margin-bottom:10px;}}
    .cst-pick-h span{{font-family:'Inter',sans-serif;font-weight:400;font-size:12.5px;color:{c['muted']};}}
    .cst-pick-row{{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 8px;}}
    .cst-pick-load{{color:{c['muted']};font-size:13px;}}
    .cst-pick-url{{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap;}}
    .cst-pick-url input{{flex:1 1 300px;min-width:0;padding:8px 11px;border:1.5px solid {c['line']};
      border-radius:9px;background:#fff;color:{c['ink']};font-family:'Inter',sans-serif;font-size:13px;}}
    .cst-pick-url input:focus{{outline:none;border-color:{c['blush500']};box-shadow:0 0 0 3px rgba(184,92,122,.13);}}
    .cst-pick-url button{{padding:8px 16px;border:1px solid {c['blush600']};color:{c['blush700']};
      background:transparent;border-radius:9px;cursor:pointer;font-size:13px;font-weight:600;}}
    .cst-pick-url button:disabled{{opacity:.5;cursor:default;}}
    .cst-pick-url .msg{{font-size:12.5px;color:{c['muted']};}}
    .cst-qa{{margin-bottom:18px;border:1px solid {c['line']};border-radius:14px;overflow:hidden;}}
    .cst-qa-h{{display:flex;justify-content:space-between;align-items:center;gap:10px;
      padding:11px 15px;background:{c['blush100']};cursor:pointer;font-family:'Fraunces',Georgia,serif;
      font-weight:600;font-size:14.5px;}}
    .cst-qa-h .rd{{font-family:'Inter',sans-serif;font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;}}
    .cst-qa-h .rd.ok{{color:{c['ok']};background:rgba(63,143,107,.12);}}
    .cst-qa-h .rd.no{{color:{c['bad']};background:rgba(181,72,72,.1);}}
    .cst-qa-body{{padding:14px 15px;}}
    .cst-qa-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:9px;margin-bottom:12px;}}
    .cst-qa-stat{{background:{c['surface']};border:1px solid {c['line']};border-radius:10px;padding:9px 11px;text-align:center;}}
    .cst-qa-stat b{{display:block;font-family:'Fraunces',Georgia,serif;font-size:20px;color:{c['ink']};line-height:1.1;}}
    .cst-qa-stat span{{font-size:11px;color:{c['muted']};}}
    .cst-qa-notes{{display:flex;flex-direction:column;gap:5px;margin-bottom:10px;}}
    .cst-qa-notes .n{{font-size:12px;color:{c['blush700']};background:{c['blush100']};padding:4px 9px;border-radius:7px;}}
    .cst-qa-pages{{max-height:150px;overflow-y:auto;border-top:1px dashed {c['line']};padding-top:8px;}}
    .cst-qa-pages a{{display:flex;justify-content:space-between;gap:10px;font-size:11.5px;color:{c['muted']};
      text-decoration:none;padding:2px 0;}}
    .cst-qa-pages a:hover{{color:{c['blush700']};}}
    .cst-qa-pages a b{{color:{c['ink']};font-weight:600;flex-shrink:0;}}
    .cst-chip{{flex:0 0 auto;width:104px;cursor:pointer;border:2px solid transparent;border-radius:12px;
      background:{c['surface']};box-shadow:0 4px 12px rgba(126,52,80,.06);overflow:hidden;transition:all .15s;}}
    .cst-chip:hover{{transform:translateY(-2px);}}
    .cst-chip.sel{{border-color:{c['blush500']};box-shadow:0 6px 16px rgba(184,92,122,.24);}}
    .cst-chip img{{width:104px;height:88px;object-fit:cover;display:block;background:{c['blush100']};}}
    .cst-chip .lab{{font-size:11px;font-weight:600;color:{c['ink']};padding:6px 7px;line-height:1.25;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
    .cst-chip.all{{width:auto;padding:0 12px;display:flex;align-items:center;min-height:88px;}}
    .cst-chip.all .lab{{white-space:normal;}}
    .cst-panel{{background:{c['surface']};border:1px solid {c['line']};border-radius:16px;padding:16px;
      display:flex;flex-direction:column;gap:12px;box-shadow:0 8px 24px rgba(126,52,80,.06);}}
    .cst-head{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:16px;display:flex;
      align-items:center;gap:8px;}}
    .cst-sub{{margin-left:auto;font-size:11px;font-weight:400;color:{c['muted']};font-family:'Inter',sans-serif;}}
    .cst-stage{{position:relative;border-radius:12px;overflow:hidden;display:flex;align-items:center;
      justify-content:center;min-height:240px;
      background:linear-gradient(140deg,{c['blush100']},#F3E9DD);
      border:1.5px dashed rgba(168,69,107,0.30);}}
    .cst-stage.is-reel{{aspect-ratio:9/16;max-height:520px;margin:0 auto;width:auto;min-height:0;}}
    .cst-stage.has-asset{{background:#0e0e13;border:1px solid {c['line']};border-style:solid;}}
    .cst-stage img,.cst-stage video{{width:100%;height:auto;display:block;}}
    .cst-stage.is-reel video{{height:100%;object-fit:contain;}}
    .cst-empty{{display:flex;flex-direction:column;align-items:center;gap:9px;color:{c['blush600']};
      font-size:30px;padding:30px 14px;text-align:center;}}
    .cst-empty span{{font-size:13.5px;font-weight:600;color:{c['inkSoft']};}}
    .cst-empty small{{font-size:11.5px;color:{c['muted']};font-weight:400;}}
    .cst-stage.is-gen{{border-style:solid;border-color:rgba(184,92,122,.38);}}
    .cst-stage.is-gen::after{{content:'';position:absolute;inset:0;pointer-events:none;
      background:linear-gradient(100deg,transparent 25%,rgba(184,92,122,.16) 50%,transparent 75%);
      background-size:220% 100%;animation:cstshim 1.5s linear infinite;}}
    @keyframes cstshim{{from{{background-position:220% 0}}to{{background-position:-220% 0}}}}
    @media(prefers-reduced-motion:reduce){{.cst-stage.is-gen::after{{animation:none}}}}
    .cst-log{{display:none;background:#2A1A1F;color:#F3E7EC;border-radius:10px;padding:11px 13px;
      font-family:'Space Grotesk',ui-monospace,Consolas,monospace;font-size:11.5px;line-height:1.65;
      max-height:150px;overflow:auto;white-space:pre-wrap;word-break:break-word;}}
    .cst-log .l-ok{{color:#8FE0B8;}} .cst-log .l-bad{{color:#F3A8A8;}} .cst-log .l-run{{color:#E8C7D4;}}
    .cst-btn{{padding:12px;font-size:14px;font-weight:600;font-family:'Inter',sans-serif;cursor:pointer;
      color:#fff;border:none;border-radius:11px;background:linear-gradient(135deg,{c['blush500']},{c['blush700']});
      box-shadow:0 8px 18px rgba(158,69,100,.22);transition:transform .12s,opacity .12s;}}
    .cst-btn:hover:not(:disabled){{transform:translateY(-1px);}} .cst-btn:disabled{{opacity:.55;cursor:not-allowed;}}
    .cst-bar{{display:flex;gap:10px;align-items:center;max-width:1180px;margin:0 auto 4px;padding:0 4px;}}
    .cst-bar a{{margin-left:auto;font-size:13px;color:{c['blush600']};text-decoration:none;font-weight:600;}}
    </style>"""


def _studio_section(slug: str, has_poster: bool, has_reel: bool, strategy_tie: str = "") -> str:
    """The interactive Creative Studio block (uses the dashboard's own .sec/.card/.bar classes).
    `strategy_tie` (from `_strategy_tie`) surfaces the WHY — the strategy/funnel/audience each
    creative serves — at the top of the studio, so the rationale is beside the live asset, not
    only in the export (RUBRIC D)."""
    return f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>Creative Studio — generate on demand</h2><span class="cnt">grounded · brand-safe · yours to drive</span></div>
      <div class="card">
        {strategy_tie}
        <div class="cst-qa" id="scrape-qa">
          <div class="cst-qa-h" onclick="toggleQA()">
            <span>🔎 Scraper quality <span id="qa-sub" style="font-family:'Inter',sans-serif;font-weight:400;font-size:12px;color:#8a7480"></span></span>
            <span class="rd" id="qa-ready">…</span>
          </div>
          <div class="cst-qa-body" id="qa-body"><div class="cst-pick-load">loading scrape quality…</div></div>
        </div>
        <div class="cst-pick">
          <div class="cst-pick-h">🛍️ Choose a product to feature <span id="pick-sel">— whole brand</span></div>
          <div class="cst-pick-row" id="product-picker"><span class="cst-pick-load">loading products…</span></div>
          <div class="cst-pick-url">
            <input id="purl" type="url" placeholder="…or paste a product link not in the list (we'll scrape it)"/>
            <button id="purl-btn" onclick="addByUrl()">Add product</button>
            <span class="msg" id="purl-msg"></span>
          </div>
        </div>
        <div class="cst-grid">
        {_panel('poster', '🎨', 'Poster', 'one-shot · crisp logo', has_poster, slug)}
        {_panel('reel', '🎬', 'Reel', 'Veo 3.1 · branded end-card', has_reel, slug)}
      </div></div></div>"""


def _ask_archive_section() -> str:
    """اسأل الأرشيف · Ask the archive — a grounded Q&A box over the brand's whole saved run. Self-
    contained CSS so it needs no change to build.py's stylesheet. Answers cite sources and say so
    when the archive doesn't cover a question (honesty, not a chatbot that guesses)."""
    return """<style>
      .ask-row{display:flex;gap:8px;margin-bottom:8px}
      .ask-row input{flex:1;padding:11px 13px;border:1px solid #e7d3da;border-radius:10px;
        font-family:inherit;font-size:14px;background:#fff}
      .ask-row button{padding:0 18px;border:0;border-radius:10px;background:#B85C7A;color:#fff;
        font-weight:700;cursor:pointer;white-space:nowrap}
      .ask-row button:disabled{opacity:.6;cursor:default}
      .ask-ex{font-size:12px;color:#8a7480;margin-bottom:6px}
      .ask-ex a{color:#B85C7A;cursor:pointer;text-decoration:none;border-bottom:1px dotted #d9a9ba}
      .ask-ans{margin-top:10px;padding:13px 15px;background:#fdf7f9;border:1px solid #f0dfe6;
        border-radius:12px;line-height:1.7}
      .ask-a{font-size:15px;color:#2b2226;white-space:pre-wrap}
      .ask-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px}
      .ask-g{font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px}
      .ask-g.ok{color:#1f7a4d;background:#e3f5ec}
      .ask-g.warn{color:#8a5a00;background:#fbf0d8}
      .ask-cites{display:flex;flex-wrap:wrap;gap:6px}
      .ask-cites a,.ask-cites span{font-size:11px;color:#6b5560;background:#fff;border:1px solid #eddde4;
        border-radius:14px;padding:2px 9px;text-decoration:none}
      .ask-cites a:hover{border-color:#B85C7A;color:#B85C7A}
      .ask-load,.ask-note{font-size:13px;color:#8a7480}
    </style>
    <div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>اسأل الأرشيف · Ask the archive</h2>
      <span class="cnt">يردّ من بيانات العلامة المحفوظة فقط · يذكر المصدر · ويقول لما ما يعرفش</span></div>
      <div class="card">
        <div class="ask-row">
          <input id="ask-q" type="text" dir="auto"
            placeholder="اسأل عن العلامة… (اعتمادات؟ منافسين؟ عروض؟ what does it offer?)"
            onkeydown="if(event.key==='Enter')askArchive()"/>
          <button id="ask-btn" onclick="askArchive()">اسأل · Ask</button>
        </div>
        <div class="ask-ex">جرّب: <a onclick="askEx(this)">إيه اعتمادات المعهد؟</a> ·
          <a onclick="askEx(this)">مين المنافسين؟</a> ·
          <a onclick="askEx(this)">إيه العروض اللي بيقدمها؟</a></div>
        <div id="ask-ans" class="ask-ans" style="display:none"></div>
      </div></div>"""


def _studio_js(slug: str, auto_poster: bool, auto_reel: bool) -> str:
    return f"""<script>
const SLUG={json.dumps(slug)};
const AUTO={{poster:{str(auto_poster).lower()},reel:{str(auto_reel).lower()}}};
const ICON={{poster:'🎨',reel:'🎬'}};
let SEL=null;   // the chosen product to feature (null = whole brand)
const esc=s=>String(s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
function pick(el,prod){{
  document.querySelectorAll('.cst-chip').forEach(c=>c.classList.remove('sel'));
  el.classList.add('sel'); SEL=prod;
  document.getElementById('pick-sel').textContent = prod ? ('— '+prod.name) : '— whole brand';
  // OWNER (2026-07-12): switching the product (or scraping a new one) must NOT keep showing
  // the LAST poster/reel as if they were this product's. Reset both stages to the honest
  // placeholder — but never clobber a render that is in flight.
  ['poster','reel'].forEach(k=>{{
    const st=document.getElementById(k+'-stage');
    if(!st||st.classList.contains('is-gen'))return;
    setEmpty(st,k, prod?('for “'+esc(prod.name)+'” — not generated yet'):'Not generated yet',
             k==='poster'?'press Generate — a few minutes':'press Generate — Veo takes ~10–20 min');
    const b=document.getElementById(k+'-btn'); if(b){{b.textContent='Generate '+k;}}
  }});
}}
function loadProducts(){{
  const row=document.getElementById('product-picker'); if(!row)return;
  fetch('/products?slug='+encodeURIComponent(SLUG)).then(r=>r.json()).then(d=>{{
    const items=(d&&d.products)||[]; row.innerHTML='';
    const all=document.createElement('div'); all.className='cst-chip all sel';
    all.innerHTML='<div class="lab">✦ Whole brand</div>'; all.onclick=()=>pick(all,null); row.appendChild(all);
    if(!items.length){{const s=document.createElement('span'); s.className='cst-pick-load';
      s.textContent='(no distinct products in the scrape — using the whole brand)'; row.appendChild(s); return;}}
    items.forEach(p=>{{const c=document.createElement('div'); c.className='cst-chip';
      c.innerHTML='<img src="'+esc(p.image)+'" alt="" onerror="this.style.visibility=\\'hidden\\'"><div class="lab">'+esc(p.name)+'</div>';
      c.onclick=()=>pick(c,p); row.appendChild(c);}});
  }}).catch(()=>{{row.innerHTML='<span class="cst-pick-load">(couldn\\'t load products)</span>';}});
}}
function addByUrl(){{
  const inp=document.getElementById('purl'),msg=document.getElementById('purl-msg'),btn=document.getElementById('purl-btn');
  const url=(inp.value||'').trim(); if(!url){{msg.textContent='paste a product link first';return;}}
  msg.textContent='scraping the product…'; btn.disabled=true;
  fetch('/product_by_url?slug='+encodeURIComponent(SLUG)+'&url='+encodeURIComponent(url))
    .then(r=>r.json()).then(d=>{{
      btn.disabled=false;
      if(!d||!d.ok){{msg.textContent='✗ '+((d&&d.error)||'couldn\\'t scrape that page');return;}}
      const row=document.getElementById('product-picker');
      const c=document.createElement('div'); c.className='cst-chip';
      c.innerHTML='<img src="'+esc(d.product.image)+'" alt="" onerror="this.style.visibility=\\'hidden\\'"><div class="lab">'+esc(d.product.name)+'</div>';
      c.onclick=()=>pick(c,d.product); row.appendChild(c);
      pick(c,d.product);                 // auto-select the freshly scraped product
      msg.textContent='✓ added & selected'; inp.value='';
    }}).catch(()=>{{btn.disabled=false;msg.textContent='✗ error scraping';}});
}}
function toggleQA(){{const b=document.getElementById('qa-body');if(b)b.style.display=(b.style.display==='none'?'block':'none');}}
function loadScrapeQA(){{
  const body=document.getElementById('qa-body'),ready=document.getElementById('qa-ready'),sub=document.getElementById('qa-sub');
  if(!body)return;
  fetch('/scrape_qa?slug='+encodeURIComponent(SLUG)).then(r=>r.json()).then(d=>{{
    const q=d&&d.qa;
    if(!q){{ready.textContent='no scrape';ready.className='rd no';
      body.innerHTML='<div class="cst-pick-load">(no saved scrape found for this brand)</div>';return;}}
    ready.textContent=q.ready?'✓ ready':'⚠ not ready';ready.className='rd '+(q.ready?'ok':'no');
    if(sub)sub.textContent='· '+(q.final_url||'').replace(/^https?:\\/\\//,'').replace(/\\/$/,'');
    const stat=(n,l)=>'<div class="cst-qa-stat"><b>'+n+'</b><span>'+l+'</span></div>';
    let h='<div class="cst-qa-grid">'
      +stat(q.pages_succeeded+'/'+q.pages_attempted,'pages')
      +stat(q.products,'products')
      +stat(q.images,'images')
      +stat(q.text_blocks,'text blocks')
      +stat((q.duration_ms/1000).toFixed(0)+'s','duration')
      +stat(q.failures,'failures')
      +'</div>';
    if(q.budget_exceeded)h+='<div class="cst-qa-notes"><div class="n" style="color:#b3402e;background:#fdecec">⚠ budget exceeded — some pages skipped</div></div>';
    if((q.major_missing||[]).length)h+='<div class="cst-qa-notes"><div class="n" style="color:#b3402e;background:#fdecec">missing: '+q.major_missing.map(esc).join(', ')+'</div></div>';
    if((q.key_notes||[]).length){{h+='<div class="cst-qa-notes">';q.key_notes.forEach(n=>{{h+='<div class="n">'+esc(n)+'</div>';}});h+='</div>';}}
    if((q.pages||[]).length){{h+='<div class="cst-qa-pages">';q.pages.forEach(p=>{{
      h+='<a href="'+esc(p.url)+'" target="_blank" rel="noopener"><span>'+esc(p.url.replace(/^https?:\\/\\//,''))+'</span><b>'+p.blocks+'</b></a>';}});h+='</div>';}}
    body.innerHTML=h;
  }}).catch(()=>{{ready.textContent='error';ready.className='rd no';
    body.innerHTML='<div class="cst-pick-load">(couldn\\'t load scrape quality)</div>';}});
}}
function stageBase(kind){{return 'cst-stage'+(kind==='reel'?' is-reel':'');}}
function setEmpty(stage,kind,head,sub){{
  stage.className=stageBase(kind);
  stage.innerHTML='<div class="cst-empty">'+ICON[kind]+'<span>'+head+'</span><small>'+sub+'</small></div>';
}}
const HITL={{kind:null,arText:'',enText:''}};
function hitlShow(kind,data){{
  HITL.kind=kind;HITL.enText=data.text||'';HITL.arText=data.arabic||'';
  document.getElementById('hitl-title').textContent=(kind==='poster'?'Poster':'Reel')+' — راجعي البرومبت قبل التوليد';
  hitlTab('ar');
  document.getElementById('hitl-ov').style.display='flex';
}}
function hitlTab(which){{
  const ta=document.getElementById('hitl-text');
  document.getElementById('tab-ar').classList.toggle('on',which==='ar');
  document.getElementById('tab-en').classList.toggle('on',which==='en');
  ta.value=(which==='ar'&&HITL.arText)?HITL.arText:HITL.enText;
  ta.dataset.tab=which; ta.readOnly=(which==='ar');
  document.getElementById('hitl-armiss').style.display=(which==='ar'&&!HITL.arText)?'block':'none';
}}
function hitlClose(){{document.getElementById('hitl-ov').style.display='none';
  const b=document.getElementById(HITL.kind+'-btn');if(b){{b.disabled=false;b.textContent='Generate '+HITL.kind;}}}}
async function hitlRefine(){{
  const inst=document.getElementById('hitl-inst').value.trim();
  if(!inst)return;
  const rb=document.getElementById('hitl-refine-btn');rb.disabled=true;rb.textContent='بيحدّث…';
  const ta=document.getElementById('hitl-text');
  // manual English edits count as the current text
  if(ta.dataset.tab==='en')HITL.enText=ta.value;
  const body={{slug:SLUG,kind:HITL.kind,instruction:inst,
    pname:SEL?SEL.name:'',lang:(document.getElementById('reel-lang')||{{}}).value||'',
    dialect:(document.getElementById('reel-dialect')||{{}}).value||''}};
  try{{
    const r=await fetch('/hitl/refine',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify(body)}});
    const d=await r.json();
    if(d.error){{alert(d.error);}}else{{HITL.enText=d.text||'';HITL.arText=d.arabic||'';
      document.getElementById('hitl-inst').value='';hitlTab('ar');}}
  }}catch(e){{alert('refine failed');}}
  rb.disabled=false;rb.textContent='حدّثي البرومبت';
}}
function hitlExecute(){{
  const ta=document.getElementById('hitl-text');
  if(ta.dataset.tab==='en')HITL.enText=ta.value;   // freeform English edits are allowed
  document.getElementById('hitl-ov').style.display='none';
  genExecute(HITL.kind);
}}
async function gen(kind){{
  // HITL GATE (owner law): NOTHING hits a paid API before the user sees the exact
  // brief and clicks EXECUTE. This first phase is text-only.
  const btn=document.getElementById(kind+'-btn');
  btn.disabled=true;btn.textContent='بيجهّز البرومبت…';
  let pu='/hitl/preview?kind='+kind+'&slug='+encodeURIComponent(SLUG);
  if(SEL){{pu+='&pname='+encodeURIComponent(SEL.name)+'&pimg='+encodeURIComponent(SEL.image);}}
  if(kind==='reel'){{const L=document.getElementById('reel-lang'),D=document.getElementById('reel-dialect');
    if(L)pu+='&lang='+L.value; if(D&&L&&L.value==='ar')pu+='&dialect='+D.value;}}
  try{{
    const r=await fetch(pu); const d=await r.json();
    if(d.error){{alert(d.error);btn.disabled=false;btn.textContent='Generate '+kind;return;}}
    hitlShow(kind,d);
  }}catch(e){{alert('preview failed');btn.disabled=false;btn.textContent='Generate '+kind;}}
}}
function genExecute(kind){{
  const btn=document.getElementById(kind+'-btn'),log=document.getElementById(kind+'-log'),
        stage=document.getElementById(kind+'-stage');
  btn.disabled=true;btn.textContent='Generating…';log.style.display='block';log.textContent='';
  stage.className=stageBase(kind)+' is-gen';
  stage.innerHTML='<div class="cst-empty">'+ICON[kind]+'<span>Generating your '+kind+'…</span><small>'
    +(kind==='reel'?'this usually takes 10–20 minutes (Veo) — you can leave it running':'this usually takes a few minutes — you can leave it running')+'</small></div>';
  let gu='/generate/'+kind+'?slug='+encodeURIComponent(SLUG)+'&hitl=1';
  if(SEL){{gu+='&product='+encodeURIComponent(SEL.url)+'&pimg='+encodeURIComponent(SEL.image)+'&pname='+encodeURIComponent(SEL.name);}}
  if(kind==='reel'){{const L=document.getElementById('reel-lang'),D=document.getElementById('reel-dialect');
    if(L)gu+='&lang='+L.value; if(D&&L&&L.value==='ar')gu+='&dialect='+D.value;}}
  const es=new EventSource(gu);
  const line=(t,cls)=>{{const d=document.createElement('div');if(cls)d.className=cls;d.textContent=t;
    log.appendChild(d);log.scrollTop=log.scrollHeight;}};
  es.addEventListener('stage',e=>{{const d=JSON.parse(e.data);
    line(d.msg, d.msg.includes('[OK]')?'l-ok':d.msg.includes('[X]')?'l-bad':'l-run');}});
  es.addEventListener('done',e=>{{const d=JSON.parse(e.data);es.close();btn.disabled=false;btn.textContent='Regenerate';
    const u=d.asset+'&v='+Date.now();
    stage.className=stageBase(kind)+' has-asset';
    stage.innerHTML=(kind==='poster')?'<img src="'+u+'" alt="poster">'
      :'<video src="'+u+'" controls playsinline preload="metadata"></video>';
  }});
  es.addEventListener('failed',e=>{{const d=JSON.parse(e.data);es.close();btn.disabled=false;btn.textContent='Regenerate';
    line('[X] '+(d.msg||'generation failed'),'l-bad');
    setEmpty(stage,kind,'Couldn\\'t generate the '+kind,'press Regenerate to try again');}});
  es.onerror=()=>{{if(es.readyState===EventSource.CLOSED){{btn.disabled=false;}}}};
}}
function askArchive(){{
  const inp=document.getElementById('ask-q'),btn=document.getElementById('ask-btn'),box=document.getElementById('ask-ans');
  const q=(inp.value||'').trim(); if(!q)return;
  btn.disabled=true; box.style.display='block'; box.innerHTML='<div class="ask-load">…بيدوّر في الأرشيف</div>';
  fetch('/ask?slug='+encodeURIComponent(SLUG)+'&q='+encodeURIComponent(q)).then(r=>r.json()).then(d=>{{
    btn.disabled=false;
    if(!d||(!d.answer&&!d.note)){{box.innerHTML='<div class="ask-load">لا يوجد رد</div>';return;}}
    if(!d.answer){{box.innerHTML='<div class="ask-note">'+esc(d.note||'—')+'</div>';return;}}
    let cites='';
    (d.citations||[]).forEach(c=>{{const ok=c.url&&/^https?:\\/\\//i.test(c.url);  // only http(s) may be a link (no javascript: XSS)
      cites+=(ok
      ?'<a href="'+esc(c.url)+'" target="_blank" rel="noopener">['+c.n+'] '+esc(c.label)+'</a>'
      :'<span>['+c.n+'] '+esc(c.label)+'</span>');}});
    const badge=d.grounded?'<span class="ask-g ok">✓ من الأرشيف</span>'
                          :'<span class="ask-g warn">⚠ مش مغطّى بالأرشيف بالكامل</span>';
    box.innerHTML='<div class="ask-a">'+esc(d.answer)+'</div><div class="ask-meta">'+badge
      +'<span class="ask-cites">'+cites+'</span></div>';
  }}).catch(()=>{{btn.disabled=false;box.innerHTML='<div class="ask-load">✗ خطأ في الاتصال</div>';}});
}}
function askEx(a){{document.getElementById('ask-q').value=a.textContent;askArchive();}}
window.addEventListener('load',()=>{{
  loadScrapeQA();                                  // scraper quality panel
  loadProducts();                                  // populate the product picker
  if(AUTO.poster)gen('poster');
  if(AUTO.reel)setTimeout(()=>gen('reel'),500);
}});
</script>"""


_FUN_L = {"tofu": "وعي · TOFU", "mofu": "تفكير · MOFU", "bofu": "تحويل · BOFU"}


def _strategy_tie(slug: str, out_dir: str) -> str:
    """RUBRIC D in the LIVE studio: the WHY beside the on-demand creative — the strategy line,
    funnel stage and audience each asset serves. Mirrors the export's tie chips (build.py `_tie`)
    so the owner sees the strategic rationale next to the poster/reel they're about to spend on,
    not only in the downloaded dashboard (the live studio previously showed neither). Empty when
    there is no media plan yet (analyze not re-run)."""
    mp_path = Path(out_dir) / f"{slug}_media_plan.json"
    if not mp_path.is_file():
        return ""
    try:
        mp = json.loads(mp_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    obj = (mp.get("objectives") or [{}])[0]
    tows = {}
    res_path = Path(out_dir) / f"{slug}_result.json"
    if res_path.is_file():
        try:
            tows = (json.loads(res_path.read_text(encoding="utf-8")).get("tows") or {})
        except Exception:  # noqa: BLE001
            tows = {}
    strat = ""
    if tows.get("priority_actions"):
        strat = re.sub(r"^(Leverage|Fix|Address|Defend|Exploit)\s*:\s*", "",
                       str(tows["priority_actions"][0].get("action") or "")).strip()
    elif tows.get("strategies"):
        strat = str(tows["strategies"][0].get("title") or "")
    fun = str(obj.get("funnel_stage") or "").lower()
    aud = ""
    for itx in ((mp.get("base_persona") or {}).get("interests") or [])[:1]:
        aud = str(itx.get("claim") if isinstance(itx, dict) else itx).replace(
            "the brand's audience:", "").strip()
    chips = []
    if strat:
        chips.append(f'<span class="tie-chip tie-strat">🎯 {_esc(strat[:60])}</span>')
    if fun in _FUN_L:
        chips.append(f'<span class="tie-chip">{_esc(_FUN_L[fun])}</span>')
    if aud:
        chips.append(f'<span class="tie-chip">👥 {_esc(aud[:40])}</span>')
    if not chips:
        return ""
    return ('<div class="tie" style="margin-bottom:12px">'
            '<div class="tie-h">الإبداع تحت — يخدم استراتيجيتك · the creative below serves your '
            f'strategy</div><div class="tie-chips">{"".join(chips)}</div></div>')


def _media_plan_card(slug: str, out_dir: str) -> str:
    """U1 on the dashboard (owner directive): the grounded MediaPlan at a glance + the
    end-to-end process map. Empty string when no plan exists (analyze not re-run yet)."""
    import json as _json
    from pathlib import Path as _P
    path = _P(out_dir) / f"{slug}_media_plan.json"
    if not path.is_file():
        return ""
    try:
        mp = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    obj = (mp.get("objectives") or [{}])[0]
    kpi = obj.get("kpi_target") or {}
    geo = mp.get("base_geo") or {}
    ev = (obj.get("evidence") or [{}])[0]
    grounded = bool(ev.get("resolved"))
    c = _C
    badge = (f'<span style="color:{c["ok"]};font-weight:700">مؤكد بالأدلة ✓</span>' if grounded
             else f'<span style="color:{c["gold"]};font-weight:700">للمراجعة ⚠</span>')
    lbl = {"OUTCOME_SALES": "مبيعات Sales", "OUTCOME_LEADS": "عملاء محتملون Leads",
           "OUTCOME_TRAFFIC": "زيارات Traffic", "OUTCOME_AWARENESS": "وعي Awareness",
           "OUTCOME_ENGAGEMENT": "تفاعل Engagement", "OUTCOME_APP_PROMOTION": "تطبيق App"}
    dest_l = {"online_store": "المتجر الإلكتروني", "whatsapp": "واتساب", "phone_call": "مكالمة",
              "lead_form": "نموذج تسجيل", "website": "الموقع", "physical_store": "الفرع",
              "messenger": "ماسنجر", "instagram_dm": "دايركت", "app": "التطبيق"}
    geo_txt = ("قومي — كل البلد" if geo.get("mode") == "national" else
               f"{geo.get('center_address','')[:44]} · {geo.get('radius_km')} كم")
    steps = "".join(
        f'<div class="mp-step"><span class="mp-dot"></span>{t}</div>'
        for t in ("Scrape", "Profile", "SWOT", "Media Plan", "Poster/Reel"))
    rationale = (obj.get("rationale") or "").strip()[:220]
    return f'''<div class="mp-card">
      <div class="mp-head"><span class="mp-title">📋 الميديا بلان (U1)</span>{badge}</div>
      <div class="mp-map">{steps}</div>
      <div class="mp-grid">
        <div><small>الهدف</small><b>{lbl.get(obj.get("objective"), obj.get("objective",""))}</b></div>
        <div><small>الوجهة</small><b>{dest_l.get(obj.get("destination"), obj.get("destination",""))}</b></div>
        <div><small>مرحلة الفانل</small><b>{(obj.get("funnel_stage") or "").upper()}</b></div>
        <div><small>الميزانية</small><b>{obj.get("budget_allocation_pct", 100):.0f}%</b></div>
        <div><small>مؤشر القياس</small><b>{kpi.get("metric", "—")}</b></div>
        <div><small>الاستهداف الجغرافي</small><b>{geo_txt}</b></div>
      </div>
      <div class="mp-why">{rationale}</div>
    </div>'''


def _studio_page(slug: str, out_dir: str) -> str | None:
    P = _run_mod.paths(slug, out_dir)
    if not P["result"].is_file():
        return None
    report = build_dashboard_html(
        str(P["result"]),
        profile_path=str(P["profile"]) if P["profile"].is_file() else None,
        plan_path=str(P["plan"]) if P["plan"].is_file() else None,
        include_media_plan=False,   # the studio renders its OWN Arabic media-plan card below —
        standalone=False,           # the English grid duplicated it (owner: the plan mess)
    )
    has_poster, has_reel = P["poster"].is_file(), P["reel"].is_file()
    section = _studio_section(slug, has_poster, has_reel, _strategy_tie(slug, out_dir))
    # inject the Creative Studio just before the report's footer (where the static creative was)
    marker = '<div class="foot"'
    i = report.rfind(marker)
    mp_card = _media_plan_card(slug, out_dir)
    ask = _ask_archive_section()
    block = f'<div class="bsr"><div class="wrap">{mp_card}{ask}{section}</div></div>'
    body = (report[:i] + block + report[i:] if i >= 0 else report + block)
    bar = ('<div class="cst-bar"><a href="/dashboard?slug=' + slug +
           '" target="_blank">⤓ Download the full dashboard</a></div>')
    # Nothing auto-runs: BOTH the poster (~a few min, slow image model + QA retries) and the reel
    # (10–20 min Veo) are heavy, so the report shows INSTANTLY and each creative is an explicit
    # button with an honest estimate — the owner never lands on a wait they didn't ask for.
    hitl = (
        '<div class="hitl-ov" id="hitl-ov"><div class="hitl-box">'
        '<div class="hitl-head"><span id="hitl-title">راجعي البرومبت</span>'
        '<span class="hitl-tabs">'
        "<button id=\"tab-ar\" class=\"on\" onclick=\"hitlTab('ar')\">عربي</button>"
        "<button id=\"tab-en\" onclick=\"hitlTab('en')\">English</button></span></div>"
        '<div class="hitl-body">'
        '<div class="hitl-note" id="hitl-armiss" style="display:none">'
        '(الترجمة العربية غير متاحة — النص الإنجليزي هو المُنفَّذ)</div>'
        '<textarea id="hitl-text" dir="auto" spellcheck="false"></textarea>'
        '<div class="hitl-note">النص الإنجليزي هو اللي بيتنفّذ. عايزة تعديل؟ اكتبي طلبك تحت '
        'وهيتحدّث البرومبت ويرجع يتأكد منك — النص التسويقي المعتمد وبنود الالتزام محمية ولا '
        'يتخطّاها أي تعديل.</div>'
        '<div class="hitl-edit"><input id="hitl-inst" dir="auto" '
        'placeholder="مثال: خلّي المشهد ليلي وركّزي على المنتج الأحمر…">'
        '<button id="hitl-refine-btn" onclick="hitlRefine()">حدّثي البرومبت</button></div>'
        '</div>'
        '<div class="hitl-foot">'
        '<button class="hitl-cancel" onclick="hitlClose()">إلغاء</button>'
        '<button class="hitl-exec" onclick="hitlExecute()">EXECUTE — نفّذي التوليد</button>'
        '</div></div></div>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Baseera Studio · {slug}</title>{_studio_css()}</head><body>'
            f'{bar}{body}{hitl}{_studio_js(slug, False, False)}</body></html>')


# ------------------------------------------------------------------------------------------------
# HTTP handler
# ------------------------------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    out_dir: str = "outputs"
    server_version = "BaseeraStudio/2.0"

    def log_message(self, *a):
        return

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError):
            pass

    def _begin_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")   # no keep-alive: the stream terminates
        self.end_headers()

    def _sse_write(self, frame: bytes) -> bool:
        try:
            self.wfile.write(frame)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionError, OSError):
            return False

    def do_GET(self):
        parsed = urlparse(self.path)
        route, q = parsed.path, parse_qs(parsed.query)
        if route in ("/", "/index.html"):
            self._send(200, _landing_html().encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/analyze":
            self._stream_analyze(q)
        elif route == "/studio":
            self._serve_studio(q)
        elif route == "/products":
            self._serve_products(q)
        elif route == "/product_by_url":
            self._serve_product_by_url(q)
        elif route == "/scrape_qa":
            self._serve_scrape_qa(q)
        elif route == "/ask":
            self._serve_ask(q)
        elif route == "/generate/poster":
            self._stream_generate(q, "poster")
        elif route == "/generate/reel":
            self._stream_generate(q, "reel")
        elif route == "/hitl/preview":
            self._serve_hitl_preview(q)
        elif route == "/asset":
            self._serve_asset(q)
        elif route == "/dashboard":
            self._serve_dashboard(q)
        elif route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/hitl/refine":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:  # noqa: BLE001
            self._send(400, b"bad body", "text/plain; charset=utf-8")
            return
        slug = str(body.get("slug") or "")
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        try:
            out = _run_mod.hitl_refine(
                slug, str(body.get("kind") or "poster"),
                str(body.get("instruction") or ""), out_dir=self.out_dir,
                product_name=(body.get("pname") or None),
                language=(body.get("lang") or None), dialect=(body.get("dialect") or None))
        except Exception as exc:  # noqa: BLE001
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _serve_hitl_preview(self, q: dict):
        """HITL step 1 (owner law): assemble + show the EXACT brief; zero paid renders."""
        slug = (q.get("slug") or [""])[0]
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        try:
            out = _run_mod.hitl_preview(
                slug, (q.get("kind") or ["poster"])[0], out_dir=self.out_dir,
                product_name=((q.get("pname") or [""])[0].strip() or None),
                product_image=((q.get("pimg") or [""])[0].strip() or None),
                language=((q.get("lang") or [""])[0].strip() or None),
                dialect=((q.get("dialect") or [""])[0].strip() or None))
        except Exception as exc:  # noqa: BLE001
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---- pages ----
    def _serve_studio(self, q: dict):
        slug = (q.get("slug") or [""])[0]
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        page = _studio_page(slug, self.out_dir)
        if page is None:
            self._send(404, b"no analysis for that brand yet - run Analyze first",
                       "text/plain; charset=utf-8")
            return
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_products(self, q: dict):
        """Grounded pickable products from the brand's raw scrape — the product-picker's data."""
        slug = (q.get("slug") or [""])[0]
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        try:
            from dashboard.products import products_for_slug
            items = products_for_slug(slug, out_dir=self.out_dir)
        except Exception:
            items = []
        self._send(200, json.dumps({"products": items}).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _serve_scrape_qa(self, q: dict):
        """A compact quality summary of the brand's freshest scrape — so the owner can verify the
        SCRAPER (pages/products/images/coverage/readiness + notable notes) at a glance."""
        slug = (q.get("slug") or [""])[0]
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        try:
            from dashboard.products import scrape_qa_for_slug
            qa = scrape_qa_for_slug(slug)
        except Exception:
            qa = None
        self._send(200, json.dumps({"qa": qa}).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _serve_ask(self, q: dict):
        """اسأل الأرشيف · Ask the archive — a grounded answer about this brand, drawn ONLY from its
        saved run (profile / SWOT / competitors / TOWS / media plan / scraped pages), citing sources
        and refusing when the archive doesn't cover it. One cheap text call; never raises."""
        slug = (q.get("slug") or [""])[0]
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        question = (q.get("q") or [""])[0].strip()
        try:
            ans = answer_question(slug, question, out_dir=self.out_dir)
            payload = {"answer": ans.answer, "grounded": ans.grounded,
                       "citations": ans.citations, "corpus_size": ans.corpus_size,
                       "note": ans.note}
        except Exception as exc:  # noqa: BLE001
            payload = {"answer": "", "grounded": False, "citations": [],
                       "corpus_size": 0, "note": f"{type(exc).__name__}"}
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _serve_product_by_url(self, q: dict):
        """Scrape ONE product from a user-pasted URL (an item the crawl never reached) and return
        its name+image so it can be featured directly in the reel/poster. Public-URL guarded."""
        def _json(payload: dict):
            self._send(200, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")
        raw = (q.get("url") or [""])[0].strip()
        if not raw:
            _json({"ok": False, "error": "no url"})
            return
        try:
            from scraper.url_utils import ensure_scheme, is_safe_public_url
            url = ensure_scheme(raw)
            if not is_safe_public_url(url):
                _json({"ok": False, "error": "that link isn't a public URL"})
                return
            from scraper.product_page import scrape_product_page
            info = scrape_product_page(url)
        except Exception as exc:  # noqa: BLE001
            _json({"ok": False, "error": type(exc).__name__})
            return
        if info is None or not info.usable:
            _json({"ok": False, "error": "no product found on that page"})
            return
        _json({"ok": True, "product": {
            "name": info.name, "image": info.image, "url": info.url,
            "price": info.price, "currency": info.currency}})

    def _serve_dashboard(self, q: dict):
        """Build + serve the full self-contained dashboard (with whatever assets exist) for export."""
        slug = (q.get("slug") or [""])[0]
        if not _ok_slug(slug):
            self._send(400, b"bad slug", "text/plain; charset=utf-8")
            return
        P = _run_mod.paths(slug, self.out_dir)
        if not P["result"].is_file():
            self._send(404, b"no analysis for that brand yet", "text/plain; charset=utf-8")
            return
        html = build_dashboard_html(
            str(P["result"]),
            profile_path=str(P["profile"]) if P["profile"].is_file() else None,
            plan_path=str(P["plan"]) if P["plan"].is_file() else None,
            poster_path=str(P["poster"]) if P["poster"].is_file() else None,
            reel_path=str(P["reel"]) if P["reel"].is_file() else None,
            standalone=True,
        )
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_asset(self, q: dict):
        slug = (q.get("slug") or [""])[0]
        kind = (q.get("kind") or [""])[0]
        if not _ok_slug(slug) or kind not in ("poster", "reel"):
            self._send(400, b"bad request", "text/plain; charset=utf-8")
            return
        P = _run_mod.paths(slug, self.out_dir)
        path = P[kind]
        if not path.is_file():
            self._send(404, b"asset not generated yet", "text/plain; charset=utf-8")
            return
        ctype = "image/png" if kind == "poster" else "video/mp4"
        self._serve_file(path, ctype)

    def _serve_file(self, path: Path, ctype: str):
        try:
            data = path.read_bytes()
        except (OSError, PermissionError):
            # e.g. Windows holds an exclusive lock while ffmpeg rewrites the mp4 mid-regenerate —
            # return a clean 503 instead of letting the read escape do_GET as an unhandled 500.
            self._send(503, b"asset is being regenerated - try again in a moment",
                       "text/plain; charset=utf-8")
            return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):     # minimal single-range (video seeking)
            try:
                start_s, end_s = rng[6:].split("-", 1)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(data) - 1
                end = min(end, len(data) - 1)
                if 0 <= start <= end:
                    chunk = data[start:end + 1]
                    self._send(206, chunk, ctype, {
                        "Content-Range": f"bytes {start}-{end}/{len(data)}",
                        "Accept-Ranges": "bytes",
                    })
                    return
            except (ValueError, IndexError):
                pass
        self._send(200, data, ctype, {"Accept-Ranges": "bytes"})

    # ---- streaming pipeline stages ----
    def _stream_analyze(self, q: dict):
        url = (q.get("url") or [""])[0].strip()
        self._begin_sse()
        if not (url.startswith("http://") or url.startswith("https://")):
            self._sse_write(sse("failed", {"msg": "Enter a full URL starting with http:// or https://"}))
            return

        # One analyze per brand at a time. A browser refresh / second tab / SSE reconnect / a
        # double-click would otherwise spawn a DUPLICATE scrape of the SAME host — and TWO
        # concurrent crawls hammering the site trip its rate limit (MEASURED: topshoes.store &
        # bobana-eg.com both serve 200 to sequential requests but return 429 to our own
        # double-fire). Reject the duplicate cleanly instead of racing (same guard as generate).
        key = (_run_mod._slug(url), "analyze")
        with _INFLIGHT_LOCK:
            if key in _INFLIGHT:
                self._sse_write(sse("failed",
                    {"msg": "This brand is already being analyzed — please wait for it to finish."}))
                return
            _INFLIGHT.add(key)

        alive = {"v": True}
        saw_rate_limit = {"v": False}

        def on_progress(event, label, msg):
            # Watch the streamed log for a rate-limit so the FINAL banner stays honest: a 429
            # means the site throttled us (temporary), NOT that it blocks crawlers or is down.
            if "429" in (msg or "") or "rate-limit" in (msg or "").lower():
                saw_rate_limit["v"] = True
            if alive["v"] and not self._sse_write(sse("stage", {"event": event, "label": label, "msg": msg})):
                alive["v"] = False

        try:
            try:
                slug = _run_mod.analyze(url, out_dir=self.out_dir, on_progress=on_progress)
            except Exception as exc:
                self._sse_write(sse("failed", {"msg": f"{type(exc).__name__}: {exc}"}))
                return
            if not alive["v"]:
                return
            if slug:
                self._sse_write(sse("done", {"slug": slug}))
            elif saw_rate_limit["v"]:
                self._sse_write(sse("failed", {"msg": "The site is rate-limiting us (HTTP 429) — "
                    "it isn't blocking us and isn't down. Wait about a minute and analyze it again."}))
            else:
                self._sse_write(sse("failed", {"msg": "The site may block scraping or be unreachable."}))
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(key)

    def _stream_generate(self, q: dict, kind: str):
        slug = (q.get("slug") or [""])[0]
        # the chosen product to FEATURE (from the picker); absent -> whole-brand creative
        product_name = (q.get("pname") or [""])[0].strip() or None
        product_image = (q.get("pimg") or [""])[0].strip() or None
        # OWNER RULE: reel language (ar/en) + Arabic register (fusha/masri) are the USER's
        # explicit UI choices, passed through to the reel subprocess.
        lang = (q.get("lang") or [""])[0].strip().lower() or None
        dialect = (q.get("dialect") or [""])[0].strip().lower() or None
        if product_image and not product_image.startswith(("http://", "https://")):
            product_image = None
        self._begin_sse()
        if not _ok_slug(slug):
            self._sse_write(sse("failed", {"msg": "bad slug"}))
            return
        alive = {"v": True}

        def on_progress(event, label, msg):
            if alive["v"] and not self._sse_write(sse("stage", {"event": event, "label": label, "msg": msg})):
                alive["v"] = False

        # One generation per (slug, kind) at a time. A browser refresh / second tab / SSE reconnect
        # would otherwise spawn a DUPLICATE subprocess writing the SAME file — a torn PNG/MP4 and a
        # wasted paid Veo render. Reject the duplicate cleanly instead.
        key = (slug, kind)
        with _INFLIGHT_LOCK:
            if key in _INFLIGHT:
                self._sse_write(sse("failed",
                    {"msg": f"A {kind} is already generating for this brand — please wait for it."}))
                return
            _INFLIGHT.add(key)
        try:
            # OWNER FEATURE: a product-specific generation drops a product-only scrape snapshot
            # into the brand's scrape dir (scrapes/<brand>_<ts>/product_scrape_<product>.json) so
            # the owner can inspect how far the crawl reached for THAT product. Whole-brand
            # generations (no picked product) write nothing. Never blocks generation.
            if product_name:
                try:
                    from dashboard.products import save_product_scrape
                    snap = save_product_scrape(slug, product_name, product_image=product_image,
                                               kind=kind)
                    if snap is not None:
                        on_progress("stage", "Product scrape",
                                    f"* product scrape snapshot -> {snap}\n")
                except Exception:
                    pass
            try:
                use_hitl = (q.get("hitl") or ["0"])[0] == "1"
                if kind == "poster":
                    asset = _run_mod.generate_poster(
                        slug, out_dir=self.out_dir, on_progress=on_progress,
                        product_name=product_name, product_image=product_image,
                        use_hitl=use_hitl)
                else:
                    asset = _run_mod.generate_reel(
                        slug, out_dir=self.out_dir, on_progress=on_progress,
                        product_name=product_name, product_image=product_image,
                        language=lang, dialect=dialect, use_hitl=use_hitl)
            except Exception as exc:
                self._sse_write(sse("failed", {"msg": f"{type(exc).__name__}: {exc}"}))
                return
            if not alive["v"]:
                return
            if asset and Path(asset).is_file():
                self._sse_write(sse("done", {"kind": kind, "asset": f"/asset?slug={slug}&kind={kind}"}))
            else:
                self._sse_write(sse("failed", {"msg": f"{kind} generation did not produce a file."}))
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(key)


def serve(host: str = "127.0.0.1", port: int = 8770, out_dir: str = "outputs",
          open_browser: bool = True) -> None:
    _Handler.out_dir = out_dir
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    urlstr = f"http://{host}:{port}"
    print(f"\n  Baseera Studio is live -> {urlstr}\n  (Ctrl+C to stop)\n", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(urlstr)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Baseera Studio stopped.", flush=True)
    finally:
        httpd.server_close()


def main() -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="dashboard.server", description="Local interactive Baseera studio.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--out", default="outputs", help="where run artifacts are written")
    ap.add_argument("--no-open", action="store_true", help="don't auto-open the browser")
    args = ap.parse_args()
    serve(host=args.host, port=args.port, out_dir=args.out, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
