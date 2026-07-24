# -*- coding: utf-8 -*-
"""War Room — غرفة العمليات التسويقية. A PAGED (no body scroll) dark-navy client dashboard.

Six full-viewport pages (القيادة · الذكاء · خلايا الاختبار · الخزنة · الاستوديو · براءة الذمة)
rendered from the SAME grounded artifacts the classic dashboard reads, plus the Media Plan v2
strategist layers computed on the fly (deterministic — `media_plan_v2.build_media_plan_v2`, no
LLM, no spend). Every number on screen traces to data or config; every missing input renders an
HONEST designed empty state ("Awaiting Meta Pull" / "Pending Creative" / "مطلوب رقمك") — never an
invented value.

Its OWN self-containment contract (distinct from dashboard/build.py's no-script pin, which stays
untouched): one file, inline CSS, exactly ONE inline vanilla <script> (the page navigation), ZERO
external resource loads — no CDN, no <link>, no web fonts, no src="http…". External <a href> links
in the Evidence Ledger are allowed (they load nothing; sanitized via `_safe_href`).

Build:  python -m dashboard.war_room <slug> [--out outputs] [--no-embed]
Serve:  dashboard/server.py -> /war_room?slug=<slug>  (media via /asset, Range-seekable video)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from dashboard.build import (_clean_citation, _data_uri, _esc, _load, _plain,  # noqa: F401
                             _safe_href, _val)

# ---------------------------------------------------------------------------------------------
# Design tokens — the War Room's own dark-navy palette (NOT build.py's light `_C`).
# ---------------------------------------------------------------------------------------------
# The LAVENDER identity (owner's lilac reference, 2026-07-19): violet-tinted charcoal ground,
# lavender replaces gold as THE accent; kill-switch stays red, the ledger receipt stays light-green.
# Lanes are redesigned so none collides with the lavender accent family.
_W = {
    "bg": "#100E1A", "bg2": "#151226", "surface": "#1C1832", "raise": "#252041",
    "vault": "#0A0813", "ink": "#EFEAF8", "inkSoft": "#B3A9CC", "muted": "#756C91",
    "line": "rgba(196,170,232,.16)",
    "gold": "#CDB4E4", "goldDeep": "#A98BD0", "accentBright": "#E7DAF6",
    "laneBlue": "#5CB3FF", "lanePurple": "#3FD8C2", "laneOrange": "#FF9E7A",
    "red": "#FF6B6B", "redDeep": "#C24444", "teal": "#3FD8C2", "green": "#4ADE9C",
    "ledgerBg": "#DDF7E9", "ledgerInk": "#14532D",
    "grayCard": "#1A1628", "grayInk": "#847BA0",
}
# NOTE: the "gold"/"goldDeep" KEYS are kept (dozens of CSS refs) but their VALUES are now the
# lavender accents — the identity swap is total; no yellow hex remains anywhere in the file.

_LANES = [_W["laneBlue"], _W["lanePurple"], _W["laneOrange"]]

# Named page anchors (pages renumbered in the unified 10-page deck — keep links in ONE place).
_P_STUDIO = "#p8"
_P_LEDGER = "#p10"

# The DECLARED deterministic creative<->cell mapping (owner decision 2026-07-19): the poster is a
# direct-offer ad -> the offer/BOFU cell; the reel is awareness storytelling -> the audience/TOFU
# cell. A marketing DECISION surfaced on the page itself as changeable — never claimed as a fact.
_CREATIVE_CELL = {"poster": "offer", "reel": "audience"}

_OBJECTIVE_AR = {
    "leads": "عملاء محتملون · Leads", "sales": "مبيعات · Sales", "traffic": "زيارات · Traffic",
    "awareness": "وعي · Awareness", "engagement": "تفاعل · Engagement",
    "app_promotion": "تنزيلات · App",
}
_FUNNEL_AR = {"bofu": "تحويل · BOFU", "mofu": "اعتبار · MOFU", "tofu": "وعي · TOFU"}


def _fv(obj: Any, *keys: str) -> str:
    """profile field -> plain string through the evidence wrapper. '' when absent/null."""
    cur = obj or {}
    for k in keys:
        cur = cur.get(k) if isinstance(cur, dict) else getattr(cur, k, None)
        if cur is None:
            return ""
    return _plain(cur)


def _num(v: Any, *, unit: str = "") -> str:
    """A number span: monospace, tabular, LTR-isolated inside RTL text. Western digits always."""
    try:
        f = float(v)
        s = f"{int(f):,}" if f == int(f) else f"{f:,.2f}"
    except (TypeError, ValueError):
        s = str(v)
    u = f' <span class="unit">{_esc(unit)}</span>' if unit else ""
    return f'<span class="num">{_esc(s)}</span>{u}'


def _objective_label(raw: str) -> str:
    key = str(raw or "").strip().lower().removeprefix("outcome_").removeprefix("metaobjective.")
    return _OBJECTIVE_AR.get(key, "")


def _funnel_label(raw: str) -> str:
    return _FUNNEL_AR.get(str(raw or "").strip().lower(), "")


def _quarter_chip(generated_at: str) -> str:
    """'Q3 2026' from the run's real generated_at ISO string. '' when unparseable (no guess)."""
    s = str(generated_at or "")[:10]
    try:
        y, m = int(s[:4]), int(s[5:7])
        return f"Q{(m - 1) // 3 + 1} {y}"
    except (ValueError, IndexError):
        return ""


def _bundle_state(out_dir: str, slug: str, brand_name: str) -> dict:
    """Honest launch-bundle probe. The CLI writes to launch_bundle/<slugify(NAME)> while the run
    slug differs (verified: nti_sci_eg vs nti) — probe BOTH. The button confirms, never executes."""
    import re
    name_slug = re.sub(r"[^a-z0-9]+", "-", (brand_name or "").strip().lower()).strip("-")
    for cand in (slug, name_slug):
        if not cand:
            continue
        p = Path(out_dir) / "launch_bundle" / cand / "launch_bundle.json"
        if p.is_file():
            return {"ready": True, "dir": str(p.parent),
                    "files": sorted(f.name for f in p.parent.iterdir() if f.is_file())[:6]}
    return {"ready": False, "command": f"python -m launch_bundle outputs/{slug}_result.json"}


def _ensure_v2(profile: dict, mp: dict, swot: dict, marketing_intel: Any,
               ad_presence: Any = None, competitor_intel: Any = None,
               owner_inputs: Optional[dict] = None) -> dict:
    """The strategist layers, computed on the fly (recomputed here even though full_run now persists media_plan_v2 (freshness beats staleness for a served page)).
    Deterministic + no network. Any failure -> an empty shell that renders honest empty states."""
    try:
        from media_plan_v2 import build_media_plan_v2
        objective = None
        objs = (mp or {}).get("objectives") or []
        if objs and isinstance(objs[0], dict):
            objective = _plain(objs[0].get("objective")) or None
        oi = owner_inputs or {}
        from dashboard.owner_inputs import margin_fraction
        v2 = build_media_plan_v2(profile or {}, media_plan=mp or None, swot=swot or None,
                                 marketing_intel=marketing_intel, objective=objective,
                                 ad_presence=ad_presence, competitor_intel=competitor_intel,
                                 capacity=oi.get("capacity_per_day"),
                                 monthly_budget=oi.get("monthly_budget"),
                                 margin=margin_fraction(oi))
        return v2.model_dump(mode="json")
    except Exception:  # noqa: BLE001 — a malformed profile must render honestly, not traceback
        return {"brand_ref": "", "strategic_narrative": "", "test_cells": [],
                "reality_check": None, "offers": [], "usps": [], "pain_points": [],
                "guardrails": [], "open_inputs": [], "competitor_rationale": []}


def _mi_items(v2: dict, result: dict, key: str) -> list[dict]:
    """Marketing-intel items as [{text, quote, url}]. Prefers the raw result.marketing_intel
    (carries the verbatim quote + permalink); falls back to v2's plain values."""
    out: list[dict] = []
    raw = ((result.get("marketing_intel") or {}).get(key)) or []
    for it in raw:
        if not isinstance(it, dict):
            continue
        ev = (it.get("evidence") or [{}])[0]
        out.append({"text": _plain(it.get("value")), "quote": str(ev.get("quote") or ""),
                    "url": str(ev.get("page_url") or "")})
    if not out:
        for it in (v2.get(key) or []):
            txt = _plain(it.get("value")) if isinstance(it, dict) else _plain(it)
            if txt:
                out.append({"text": txt, "quote": "", "url": ""})
    return [o for o in out if o["text"]]


# ---------------------------------------------------------------------------------------------
# Tiny inline SVG icons (simple shapes only)
# ---------------------------------------------------------------------------------------------
def _svg(body: str, stroke: str, size: int = 22) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{stroke}" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">{body}</svg>')


def _ic_percent(c: str) -> str:
    return _svg('<line x1="19" y1="5" x2="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/>'
                '<circle cx="17.5" cy="17.5" r="2.5"/>', c)


def _ic_pulse(c: str) -> str:
    return _svg('<polyline points="2 12 6 12 9 5 13 19 16 12 22 12"/>', c)


def _ic_radar(c: str) -> str:
    return _svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>'
                '<circle cx="12" cy="12" r="1"/><line x1="12" y1="12" x2="18" y2="5"/>', c)


def _ic_shield(c: str) -> str:
    return _svg('<path d="M12 3 L20 6 V12 C20 17 16.5 20 12 21.5 C7.5 20 4 17 4 12 V6 Z"/>'
                '<line x1="12" y1="9" x2="12" y2="13"/><circle cx="12" cy="16" r=".6"/>', c, 30)


def _ic_scale(c: str) -> str:
    return _svg('<line x1="12" y1="4" x2="12" y2="20"/><line x1="5" y1="7" x2="19" y2="7"/>'
                '<path d="M5 7 L3 12 H7 Z"/><path d="M19 7 L17 12 H21 Z"/>'
                '<line x1="8" y1="20" x2="16" y2="20"/>', c, 26)


def _ic_up(c: str) -> str:
    return _svg('<polyline points="3 17 9 11 13 14 21 6"/><polyline points="15 6 21 6 21 12"/>', c, 26)


# ---------------------------------------------------------------------------------------------
# Page 1 — القيادة (Command / Hero)
# ---------------------------------------------------------------------------------------------
# business_model enum -> a human word for the deck (process.md §8.11). "unknown"/absent -> no chip.
_MODEL_LABEL = {"ecommerce": "متجر أونلاين", "local": "نشاط محلي",
                "service": "خدمة", "hybrid": "أونلاين + محلي"}


def _brand_chips(profile: dict) -> str:
    """The brand's MARKET (what it sells) and, when known, its MODEL (how) — the two axes the old
    single `category` chip conflated. Prefers the free-text `market_category`; falls back to the
    legacy `category` for un-migrated profiles. Returns "" when neither exists."""
    market = _fv(profile, "market_category") or _fv(profile, "category")
    model = str(_fv(profile, "business_model") or "").strip().lower()
    out = []
    if market:
        out.append(f'<span class="chip">{_esc(market)}</span>')
    label = _MODEL_LABEL.get(model)
    if label:
        out.append(f'<span class="chip">{_esc(label)}</span>')
    return "".join(out)


def _page_command(profile: dict, v2: dict, mp: dict, bundle: dict, *, slug: str,
                  logo_uri: Optional[str], generated_at: str,
                  tows: Optional[dict] = None, counts: Optional[dict] = None,
                  maturity: Optional[dict] = None) -> str:
    name = _fv(profile, "name") or slug or "البراند"
    # the brand-sub line shows WHAT the brand is — prefer the market over the coarse/model label
    category = _fv(profile, "market_category") or _fv(profile, "category")
    tagline = _fv(profile, "tagline")
    primary = _fv(profile, "visual", "primary_color") or _W["laneBlue"]

    logo = (f'<img class="logo" src="{logo_uri}" alt="">' if logo_uri
            else f'<div class="logo badge">{_esc((name.strip()[:1] or "B").upper())}</div>')

    objs = (mp or {}).get("objectives") or []
    mp_obj = objs[0] if objs and isinstance(objs[0], dict) else {}
    obj_label = _objective_label(_plain(mp_obj.get("objective")))
    funnel = _funnel_label(_plain(mp_obj.get("funnel_stage")))
    q = _quarter_chip(generated_at)
    campaign = f"خطة إطلاق — {obj_label.split('·')[0].strip()}" if obj_label else "خطة الإطلاق"
    if q:
        campaign += f" · {q}"

    # Budget tag: ONLY a real money number (test_budget). budget_allocation_pct=100 is an
    # allocation, not money — absent budget renders the honest gold "مطلوب رقمك" ask.
    tb = _plan_test_budget(mp)
    if isinstance(tb, (int, float)) and tb > 0:
        cur = ""
        rc = v2.get("reality_check") or {}
        cur = (rc.get("kill_switch") or {}).get("currency") or ""
        # It can only be here because SHE typed it (the builder sets test_budget from
        # owner_inputs.monthly_budget and from nothing else) — so say so.
        budget_tag = f'<span class="tag">الميزانية: {_num(tb, unit=cur)} — من رقمك ✓</span>'
    else:
        budget_tag = '<span class="tag ask">الميزانية: مطلوب رقمك</span>'
    all_objs = [o for o in objs if isinstance(o, dict)]
    if len(all_objs) > 1:
        # خطة Funnel (نضج البراند): كل الأهداف بنسبها — مش objectives[0] الصامتة
        parts = []
        for o in all_objs:
            lbl = _objective_label(_plain(o.get("objective"))) or "?"
            pct = o.get("budget_allocation_pct")
            parts.append(f"{lbl.split('·')[0].strip()} {pct:.0f}%" if isinstance(pct, (int, float))
                         else lbl)
        obj_tag = f'<span class="tag">Funnel: {_esc(" + ".join(parts))}</span>'
    else:
        obj_tag = (f'<span class="tag">Objective: {_esc(obj_label)}</span>' if obj_label
                   else '<span class="tag ask">مطلوب: هدف الحملة</span>')
    funnel_tag = f'<span class="tag">Funnel: {_esc(funnel)}</span>' if funnel else ""

    if bundle.get("ready"):
        files = "".join(f'<li class="num">{_esc(f)}</li>' for f in bundle.get("files", []))
        export = f"""<div class="export-wrap">
          <button class="export ready" id="bundle-btn">جاهز ✓ &nbsp;Launch Bundle</button>
          <div class="bundle-panel" id="bundle-panel">
            <div class="bp-t">الحزمة متولّدة وجاهزة للمراجعة (DRAFT — الإطلاق بموافقتك):</div>
            <ul>{files}</ul>
            <div class="bp-path num">{_esc(bundle.get("dir", ""))}</div>
          </div></div>"""
    else:
        cmd = bundle.get("command", "")
        export = f"""<div class="export-wrap">
          <button class="export" id="bundle-btn">Export Launch Bundle</button>
          <div class="bundle-panel" id="bundle-panel">
            <div class="bp-t">هذه خطوة بموافقتك — الزر يؤكّد ولا يُنفّذ (PD-7). انسخي الأمر ونفّذيه:</div>
            <div class="cmd-row"><code class="num">{_esc(cmd)}</code>
              <button class="copy" data-copy="{_esc(cmd)}">نسخ</button></div>
          </div></div>"""

    narrative = _plain(v2.get("strategic_narrative")) or \
        "لم تُحسب السردية الاستراتيجية بعد — تحتاج ملف نتائج مكتمل."
    tagline_html = f'<div class="brand-tag">{_esc(tagline)}</div>' if tagline else ""

    posture_html = ""
    t = tows or {}
    key = str(t.get("posture") or "").strip().lower()
    try:
        from dashboard.build import _POSTURE, _imperative_move
        pline = _POSTURE.get(key, ("", ""))[0]
        move = ""
        pas = t.get("priority_actions") or []
        if pas and isinstance(pas[0], dict):
            move = _imperative_move(pas[0])
        if pline or move:
            posture_html = (f'<div class="posture"><span class="chip">{_esc(pline)}</span>'
                            + (f'<div class="move">الخطوة رقم واحد: {_esc(move)}</div>' if move else "")
                            + "</div>")
    except Exception:  # noqa: BLE001
        posture_html = ""
    counts_html = ""
    if counts:
        bits = "".join(f'<span class="tag">{_esc(lbl)}: {_num(n)}</span>'
                       for lbl, n in counts.items() if isinstance(n, int))
        counts_html = f'<div class="ns-tags counts">{bits}</div>' if bits else ""

    # 1.7 (owner ask): "بعد ما تسكرايب حط ملخص للبراند الأول" — a compact evidence-grounded
    # brand card, first thing the business owner sees. Every element renders ONLY when the
    # profile actually carries it (honest degrade, no empty shells).
    summary_html = ""
    desc = _plain(_fv(profile, "description") or profile.get("description"))
    chips = [_brand_chips(profile)] if _brand_chips(profile) else []
    aud = _plain(_fv(profile, "audience_type") or "")
    if aud:
        chips.append(f'<span class="chip">الجمهور: {_esc(aud)}</span>')
    langs = profile.get("languages") or []
    lang_names = [str((l.get("lang") if isinstance(l, dict) else l) or "") for l in langs]
    lang_names = [x for x in lang_names if x]
    if lang_names:
        chips.append(f'<span class="chip">اللغات: {_esc(" · ".join(lang_names[:3]))}</span>')
    n_off = len(profile.get("offerings") or [])
    if n_off:
        chips.append(f'<span class="chip num">{n_off} خدمة/منتج مستخرَج</span>')
    if isinstance(maturity, dict) and maturity.get("level") in ("nascent", "established"):
        _mlbl = "ناشئ — خطة Funnel" if maturity.get("level") == "nascent" else "راسخ"
        chips.append(f'<span class="chip">نضج البراند: {_mlbl}</span>')
    for vp in (profile.get("value_propositions") or [])[:3]:
        v = _plain(vp.get("value") if isinstance(vp, dict) else vp)
        if v:
            chips.append(f'<span class="chip">✦ {_esc(v[:60])}</span>')
    if desc or chips:
        body = (f'<p dir="auto">{_esc(desc[:220])}{"…" if desc and len(desc) > 220 else ""}</p>'
                if desc else "")
        summary_html = (f'<div class="brand-summary"><div class="bs-t">بطاقة البراند · '
                        f'كما فهمناها من موقعك</div>{body}'
                        f'<div class="bs-chips">{"".join(chips)}</div></div>')
    return f"""<section class="page" id="p1" data-title="القيادة">
      <div class="page-inner">
        <header class="topbar">
          <div class="brand">
            {logo}
            <div>
              <div class="brand-name">{_esc(name)}</div>
              <div class="brand-sub">{_esc(campaign)}{(' · ' + _esc(category)) if category else ''}</div>
              {tagline_html}
            </div>
          </div>
          {export}
        </header>
        <div class="hero">
          <div class="eyebrow">01 · الملخص الاستراتيجي — The North Star</div>
          <div class="north-star" style="--brand:{_esc(primary)}">
            <p class="ns-text" dir="auto">{_esc(narrative)}</p>
            <div class="ns-tags">{obj_tag} {budget_tag} {funnel_tag}</div>
          </div>
          {summary_html}
          {posture_html}
          {counts_html}
        </div>
      </div>
    </section>"""


# ---------------------------------------------------------------------------------------------
# Page 2 — شبكة الذكاء التسويقي (Marketing Intel Grid)
# ---------------------------------------------------------------------------------------------
def _intel_card(icon: str, title: str, body: str, source: str, *, awaiting: bool = False,
                awaiting_label: str = "") -> str:
    cls = "glass intel is-awaiting" if awaiting else "glass intel"
    aw = (f'<div class="awaiting-big">{_esc(awaiting_label)}</div>' if awaiting_label else "")
    src = f'<div class="intel-src num">Source: {_esc(source)}</div>' if source else ""
    return f"""<div class="{cls}">
      <div class="intel-head"><span class="intel-ic">{icon}</span><h3>{_esc(title)}</h3></div>
      {aw}<div class="intel-body">{body}</div>{src}
    </div>"""


def _page_intel(v2: dict, result: dict) -> str:
    offers = _mi_items(v2, result, "offers")
    pains = _mi_items(v2, result, "pain_points")

    if offers:
        rows = "".join(
            f'<div class="mi-item"><div class="mi-t">{_esc(o["text"])}</div>'
            + (f'<blockquote class="mi-q">«{_esc(o["quote"])}»</blockquote>' if o["quote"] else "")
            + (f'<div class="intel-src num"><a href="{_safe_href(o["url"])}" target="_blank" '
               f'rel="noopener">{_esc(_clean_citation(o["url"]))}</a></div>'
               if o.get("url") and _safe_href(o["url"]) != "#" else "")
            + "</div>"
            for o in offers[:4])
        offers_card = _intel_card(_ic_percent(_W["gold"]), "عروض نشطة", rows, "Instagram Snapshot")
    else:
        offers_card = _intel_card(
            _ic_percent(_W["inkSoft"]), "عروض نشطة",
            '<p class="mi-empty">لا عرض نشط مستخرَج بدليل بعد — يُستخرج حرفياً من منشورات '
            'البراند، وما يفشل التوثيق يُستبعد.</p>'
            '<div class="mi-await num">Awaiting Instagram Snapshot</div>',
            "")

    if pains:
        rows = "".join(
            f'<div class="mi-item"><div class="mi-t">{_esc(p["text"])}</div>'
            + (f'<blockquote class="mi-q">«{_esc(p["quote"])}»</blockquote>' if p["quote"] else "")
            + (f'<div class="intel-src num"><a href="{_safe_href(p["url"])}" target="_blank" '
               f'rel="noopener">{_esc(_clean_citation(p["url"]))}</a></div>'
               if p.get("url") and _safe_href(p["url"]) != "#" else "")
            + "</div>"
            for p in pains[:4])
        pains_card = _intel_card(_ic_pulse(_W["red"]), "نقاط ألم العملاء", rows, "Instagram Snapshot")
    else:
        pains_card = _intel_card(
            _ic_pulse(_W["inkSoft"]), "نقاط ألم العملاء",
            '<p class="mi-empty">لم نلتقط شكاوى عملاء موثّقة بعد — تُستخرج من تعليقات الجمهور '
            'حرفياً «بكلماتهم هم».</p><div class="mi-await num">Awaiting Instagram Snapshot</div>',
            "")

    ads_available = bool(result.get("ads_available"))
    ad_presence = result.get("ad_presence") or []
    if ads_available and ad_presence:
        chips = []
        for ap in ad_presence[:4]:
            pn = _esc(str(ap.get("page_name") or ap.get("brand_ref") or ""))
            facts = []
            if ap.get("winners_30d"):
                facts.append(f'<span class="chip">🏆 {_num(ap.get("winners_30d"))} فائز 30+ يوم</span>')
            if ap.get("fatigue_groups"):
                facts.append('<span class="chip">🔁 بيكرر إعلانه بصيغ متعددة (إجهاد)</span>')
            if ap.get("top_format"):
                facts.append(f'<span class="chip">الفورمات الغالب: {_esc(ap.get("top_format"))}</span>')
            if ap.get("top_cta"):
                facts.append(f'<span class="chip">CTA: {_esc(ap.get("top_cta"))}</span>')
            if ap.get("filtered_out"):
                # 1.1 honesty: the broad keyword pull dragged strangers in — we say so.
                facts.append(f'<span class="chip">استُبعد {_num(ap.get("filtered_out"))} '
                             f'إعلان غير مرتبط من السحب</span>')
            chips.append(
                f'<div class="mi-item"><div class="mi-t">«{pn}» — {_num(ap.get("active_count", 0))} '
                f'إعلان نشط · الأقدم شغّال من {_num(ap.get("longest_days", 0))} يوم'
                + (f' · {_num(ap.get("proven_winners", 0))} إعلان مُثبَت (60+ يوم)'
                   if ap.get("proven_winners") else "")
                + "</div>" + ('<div class="fact-chips">' + "".join(facts) + "</div>" if facts else "")
                + "</div>")
        radar_card = _intel_card(_ic_radar(_W["teal"]), "إعلانات المنافسين الجارية",
                                 "".join(chips), "Meta Ad Library")
    else:
        radar_card = _intel_card(
            _ic_radar(_W["grayInk"]), "إعلانات المنافسين الجارية",
            '<p class="mi-empty">خطوة مجانية لمرة واحدة: تأكيد هوية حساب Meta الإعلاني — '
            'بعدها إعلانات منافسيك الجارية تظهر هنا وتغذّي التهديدات تلقائياً.</p>',
            "", awaiting=True, awaiting_label="Awaiting Meta Pull")

    return f"""<section class="page" id="p4" data-title="الذكاء">
      <div class="page-inner">
        <div class="eyebrow">02 · شبكة الذكاء التسويقي — Marketing Intel</div>
        <h2 class="page-title">ما نعرفه بدليل، وما ننتظره بصراحة</h2>
        <div class="intel-grid">{offers_card}{pains_card}{radar_card}</div>
      </div>
    </section>"""


# ---------------------------------------------------------------------------------------------
# Page 3 — ساحة خلايا الاختبار (Test Cells Arena)
# ---------------------------------------------------------------------------------------------
def _cell_status(cell: dict, *, has_poster: bool, has_reel: bool) -> str:
    axis = str(cell.get("axis") or "")
    if _CREATIVE_CELL["poster"] == axis and has_poster:
        # src is wired by JS from the ONE embedded studio copy (data-src-from) — a second inline
        # base64 copy here doubled the file size for nothing.
        return ('<a class="cell-thumb-link" href="#p8"><img class="cell-thumb" '
                'data-src-from="wr-poster" alt="poster"><span>البوستر جاهز ↗</span></a>')
    if _CREATIVE_CELL["reel"] == axis and has_reel:
        return '<a class="cell-thumb-link reel" href="#p8"><span class="reel-chip">🎬 Reel جاهز ↗</span></a>'
    return '<span class="pending">Pending Creative</span>'


def _apply_saved_budget(mp: Any, owner_inputs: Any) -> Any:
    """Spend the owner's SAVED monthly budget on an already-built plan, at render time.

    The builder does this when a plan is authored, but authoring happens inside a full analysis.
    Without this, typing a number in the vault would change nothing until the next (slow, paid)
    re-analysis — which is exactly why the vault felt dead. Pure arithmetic through the same
    validated schema, so the learning floor still applies and nothing is invented.

    Returns the ORIGINAL object unchanged when there is no usable number, when the plan already
    carries a budget, or on any malformed input (a page must never break over a nice-to-have).
    """
    try:
        budget = float((owner_inputs or {}).get("monthly_budget"))
    except (TypeError, ValueError):
        return mp
    if budget <= 0 or not isinstance(mp, dict):
        return mp
    objs = mp.get("objectives")
    if not isinstance(objs, list) or not objs:
        return mp
    if any(isinstance(o, dict) and o.get("test_budget") for o in objs):
        return mp                       # already priced (built with her number) — don't re-apply
    try:
        from media_plan.builder import _apply_owner_budget
        from media_plan.schemas import CampaignObjective
        models = [CampaignObjective.model_validate(o) for o in objs]
        priced = _apply_owner_budget(models, budget)
        return {**mp, "objectives": [o.model_dump(mode="json") for o in priced]}
    except Exception:  # noqa: BLE001
        return mp


def _plan_test_budget(mp: Any) -> Optional[float]:
    """The WHOLE plan's test budget — the sum across objectives, because a funnel plan splits the
    owner's month between awareness and conversion while the test cells share all of it. None when
    she hasn't given a number (honest unknown, never 0)."""
    objs = (mp or {}).get("objectives") or [] if isinstance(mp, dict) else []
    vals = [o.get("test_budget") for o in objs if isinstance(o, dict)]
    nums = [float(v) for v in vals if isinstance(v, (int, float)) and v > 0]
    return sum(nums) if nums else None


def _page_cells(v2: dict, *, has_poster: bool, has_reel: bool,
                test_budget: Optional[float], currency: str) -> str:
    cells = v2.get("test_cells") or []
    lanes = []
    for i, cell in enumerate(cells[:3]):
        lane = _LANES[i % 3]
        share = float(cell.get("budget_share") or 0.0)
        pct = f"{share * 100:.0f}"
        if isinstance(test_budget, (int, float)) and test_budget > 0:
            abs_line = f'{_num(share * test_budget, unit=currency)} من ميزانية الاختبار'
        else:
            abs_line = "٪ من ميزانية الاختبار — الرقم المطلق بعد إدخال الميزانية"
        tip = f"الميزانية المتبقية: تُحسب بعد الإطلاق — المخطط {pct}%"
        lanes.append(f"""<div class="lane" style="--lane:{lane}" data-tip="{_esc(tip)}">
          <div class="lane-head">
            <span class="lane-no num">Test Cell 0{i + 1}</span>
            <h3>{_esc(cell.get("name"))}</h3>
            <span class="axis-chip">{_esc(cell.get("axis"))}</span>
          </div>
          <div class="lane-grid">
            <div class="lane-col">
              <div class="lbl">الفرضية</div>
              <p class="hyp">{_esc(cell.get("hypothesis"))}</p>
              <div class="grounded num">{_esc(cell.get("grounded_in"))}</div>
              {f'<div class="counter-basis"><span class="chip">أب القرار: {_esc(cell.get("counter_basis"))}</span></div>' if cell.get("counter_basis") else ''}
            </div>
            <div class="lane-col">
              <div class="lbl">الميزانية</div>
              <div class="bar"><div class="bar-fill" style="width:{pct}%"></div></div>
              <div class="bar-lbl"><span class="num">{pct}%</span> · {abs_line}</div>
            </div>
            <div class="lane-col brief">
              <div class="lbl">Creative Brief</div>
              <div class="brief-row"><span class="bk">Funnel</span>{_esc(_funnel_label(cell.get("funnel_stage")) or cell.get("funnel_stage"))}</div>
              <div class="brief-row"><span class="bk">Hook</span>{_esc(cell.get("angle"))}</div>
              <div class="brief-row"><span class="bk">CTA</span>{_esc(cell.get("cta"))}</div>
            </div>
            <div class="lane-col status">
              <div class="lbl">الحالة</div>
              {_cell_status(cell, has_poster=has_poster, has_reel=has_reel)}
            </div>
          </div>
        </div>""")
    if not lanes:
        lanes.append('<p class="mi-empty">لم تُحسب خلايا الاختبار — تحتاج ملف نتائج مكتمل.</p>')

    return f"""<section class="page" id="p5" data-title="الخلايا">
      <div class="page-inner">
        <div class="eyebrow">03 · ساحة المعركة — Test Cells</div>
        <h2 class="page-title">ثلاث رهانات، الفائز يتحدد بالأرقام مش بالرأي</h2>
        <div class="lanes">{''.join(lanes)}</div>
        <p class="map-note">قرار تسويقي مبدئي: البوستر لخلية العرض (BOFU) والريل لخلية الجمهور
        (TOFU) — قابل للتغيير.</p>
      </div>
    </section>"""


# ---------------------------------------------------------------------------------------------
# Page 4 — الخزنة (The Vault & Kill-Switch)
# ---------------------------------------------------------------------------------------------
def _kill_multiple() -> float:
    """The engine's REAL learning-spend multiple (one source of truth — never a hardcoded 2)."""
    try:
        from decision_engine.schemas import CampaignPolicy
        return float(CampaignPolicy.model_fields["min_spend_multiple"].default)
    except Exception:  # noqa: BLE001
        return 3.0


def _page_vault(v2: dict, *, serve_mode: bool = False,
                owner_inputs: Optional[dict] = None) -> str:
    rc = v2.get("reality_check") or {}
    if not rc:
        body = '<p class="mi-empty">لم تُحسب حواجز الأمان — تحتاج ملف نتائج مكتمل.</p>'
        return (f'<section class="page" id="p6" data-title="الخزنة"><div class="page-inner">'
                f'<div class="eyebrow">04 · الخزنة — The Vault</div>{body}</div></section>')

    ks = rc.get("kill_switch") or {}
    cur = _deck_currency({"kill_switch": ks})
    mult = _kill_multiple()

    if ks.get("basis") == "price_margin_owner":
        basis_line = "مبني على سعرك الحقيقي × هامش ربحك اللي أدخلتيه ✓ × 30%."
    elif ks.get("basis") == "price_margin":
        basis_line = ("مبني على سعر حقيقي مسحوب من موقعك × هامش ربح افتراضي للمجال × 30% — "
                      "أدخلي هامشك الحقيقي تحت وهيتظبط.")
    else:
        basis_line = _esc(ks.get("disclosure") or "قيمة افتراضية — تُعاير بعد إدخال الأسعار.")
    stop_line = (f'بعد صرف <span class="num">{mult:.0f}×</span> الحد الأقصى بدون نتيجة موثوقة '
                 f'← إيقاف تلقائي (Kill-Switch)')

    cap_src = rc.get("capacity_source")
    cap_note = ""
    if cap_src == "human":
        cap_note = '<span class="tag">طاقة الاستيعاب من رقمك ✓</span>'
    elif cap_src == "config_default":
        note_txt = next((n for n in (rc.get("notes") or []) if "طاقة" in str(n)),
                        "طاقة افتراضية للمجال — عدّليها بالرقم الحقيقي")
        cap_note = (f'<div class="cap-note">{_esc(note_txt)}</div>'
                    f'<span class="tag ask">مطلوب رقمك — أدخليه تحت</span>')

    oi = owner_inputs or {}
    inputs_panel = ""
    if serve_mode:
        # owner ask 2026-07-20 ("حط مكان يحطها فيه"): the §7 human numbers get a HOME — typed
        # once, saved per brand, every governing number recomputes on save.
        inputs_panel = f"""<div class="svc-actions owner-inputs">
          <div class="bp-t">أرقامك الحقيقية (تتحفظ للبراند وتعيد حساب كل الأرقام فوق):</div>
          <div class="svc-row">
            <label class="oi-l">هامش الربح %<input id="oi-margin" class="svc-input oi" dir="ltr"
              type="number" min="1" max="95" step="0.5"
              value="{_esc(str(oi.get('profit_margin_pct') or ''))}" placeholder="30"></label>
            <label class="oi-l">طاقة الاستيعاب/يوم<input id="oi-capacity" class="svc-input oi"
              dir="ltr" type="number" min="1"
              value="{_esc(str(oi.get('capacity_per_day') or ''))}" placeholder="200"></label>
            <label class="oi-l">الميزانية الشهرية<input id="oi-budget" class="svc-input oi"
              dir="ltr" type="number" min="1"
              value="{_esc(str(oi.get('monthly_budget') or ''))}" placeholder="30000"></label>
            <button class="btn gold" id="oi-save">حفظ وإعادة الحساب</button>
          </div>
          <div class="svc-log num" id="oi-log"></div>
        </div>"""
    return f"""<section class="page" id="p6" data-title="الخزنة">
      <div class="page-inner">
        <div class="eyebrow">04 · الخزنة وزر الطوارئ — The Vault</div>
        <h2 class="page-title">أرقام حاكمة، محسوبة بقواعد — مش بآراء</h2>
        <div class="vault">
          <div class="v-col">
            <div class="v-ic">{_ic_scale(_W["teal"])}</div>
            <div class="v-t">طاقة الاستيعاب · Capacity</div>
            <div class="v-big">{_num(rc.get("max_daily_leads", 0))}<span class="unit">عميل/يوم</span></div>
            <p class="v-p">بناءً على طاقتك التشغيلية، سقف الإنفاق اليومي
              {_num(rc.get("daily_spend_ceiling", 0), unit=cur)} — عشان الطلبات ما تغرقش فريقك.</p>
            {cap_note}
          </div>
          <div class="v-col kill">
            <div class="v-ic kill-shield">{_ic_shield(_W["red"])}</div>
            <div class="v-t">زر الطوارئ · Kill-Switch</div>
            <div class="v-big red">{_num(ks.get("max_cpl", 0), unit=cur)}</div>
            <div class="v-sub">أقصى تكلفة للعميل المحتمل (Max CPL)</div>
            <p class="v-p">{basis_line}</p>
            <p class="v-p stop">{stop_line}</p>
          </div>
          <div class="v-col">
            <div class="v-ic">{_ic_up(_W["green"])}</div>
            <div class="v-t">قواعد التوسع · Scale Rules</div>
            <p class="v-p">{_esc(rc.get("scale_rule"))}</p>
            <p class="v-p">{_esc(rc.get("fatigue_rule"))}</p>
          </div>
        </div>
        {inputs_panel}
      </div>
    </section>"""


# ---------------------------------------------------------------------------------------------
# Page 5 — معرض الإبداع (Creative Studio)
# ---------------------------------------------------------------------------------------------
def _belongs_tag(v2: dict, kind: str) -> str:
    axis = _CREATIVE_CELL.get(kind)
    for i, cell in enumerate((v2.get("test_cells") or [])[:3]):
        if cell.get("axis") == axis:
            return (f'<span class="belongs" style="--lane:{_LANES[i % 3]}">'
                    f'Belongs to: {_esc(cell.get("name"))}</span>')
    return ""


def _poster_status_note(slug: str, out_dir: Optional[str]) -> str:
    """البند 6: a TRUE failure leaves <slug>_poster.status.json — surface its reason instead
    of a silent Pending ("آخر محاولة فشلت وليه")."""
    try:
        import json as _json
        p = Path(out_dir or "outputs") / f"{slug}_poster.status.json"
        if p.is_file():
            d = _json.loads(p.read_text(encoding="utf-8"))
            return f"آخر محاولة فشلت ({d.get('at', '')}): {d.get('reason', '')}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _page_studio(v2: dict, *, poster_src: Optional[str], reel_src: Optional[str],
                 slug: str, has_audit: bool, status_note: str = "",
                 serve_mode: bool = False, poster_stale: str = "",
                 reel_stale: str = "") -> str:
    status_html = (f'<br><span class="warn">{_esc(status_note)}</span>' if status_note else "")

    def _stale_chip(note: str) -> str:
        return f'<div class="stale-chip">⏳ {_esc(note)}</div>' if note else ""

    if poster_src:
        compliance = ('<a class="btn ghost" href="#p10" data-open-audit="1">View Compliance</a>'
                      if has_audit else "")
        # the ONE embedded copy of the poster lives here (id=wr-poster); the download href and the
        # test-cell thumbnail are wired FROM it by JS, so a multi-MB data URI is never duplicated.
        poster_half = f"""<figure class="studio-half">
          {_stale_chip(poster_stale)}
          <img id="wr-poster" class="poster" src="{poster_src}" alt="poster">
          <figcaption>
            <a class="btn" href="#" data-dl="wr-poster" download="{_esc(slug)}_poster.png">Download PNG</a>
            {compliance} {_belongs_tag(v2, "poster")}
          </figcaption>
        </figure>"""
    else:
        poster_half = (f'<div class="studio-half placeholder"><span class="pending">Pending Creative'
                       f'</span><p>لم يُولَّد البوستر بعد — يتولّد من هنا مباشرة.{status_html}</p></div>')

    if reel_src:
        reel_half = f"""<figure class="studio-half">
          {_stale_chip(reel_stale)}
          <div class="phone"><video id="wr-reel" controls playsinline autoplay muted loop preload="auto" src="{reel_src}"></video></div>
          <figcaption>
            <a class="btn" href="#" data-dl="wr-reel" download="{_esc(slug)}_reel.mp4">Download MP4</a>
            {_belongs_tag(v2, "reel")}
          </figcaption>
        </figure>"""
    else:
        reel_half = ('<div class="studio-half placeholder"><div class="phone empty"><span '
                     'class="pending">Pending Creative</span></div><p>الريل يُرندَر بموافقتك فقط.</p></div>')

    # p8 generation controls (owner ask 2026-07-20 "عايزة أطلع ريل أضغط فين؟" — the unified deck
    # had NO generate buttons; they lived only in the legacy /studio). Serve mode only; the reel
    # is PAID (Veo) so the HITL preview -> EXECUTE gate is mandatory, with her language/dialect.
    controls = ""
    if serve_mode:
        controls = f"""<div class="svc-actions studio-controls">
          <div class="svc-row">
            <button class="btn gold" id="st-poster">🎨 توليد بوستر</button>
            <button class="btn gold" id="st-reel">🎬 توليد ريل (مدفوع — Veo)</button>
            <select id="st-lang" class="st-sel"><option value="ar">عربي</option>
              <option value="en">English</option></select>
            <select id="st-dialect" class="st-sel"><option value="masri">مصري</option>
              <option value="fusha">فصحى</option></select>
            <button class="btn ghost" id="st-fresh">🧹 ابدأ من جديد</button>
          </div>
          <div class="hitl-panel" id="st-hitl">
            <div class="svc-row"><span class="bp-t">البريف (بموافقتك فقط — HITL):</span>
              <button class="btn ghost br-l" id="st-br-ar">عربي</button>
              <button class="btn ghost br-l" id="st-br-en">EN</button></div>
            <pre class="hitl-brief" id="st-brief" dir="auto"></pre>
            <div class="svc-row"><input id="st-note" class="svc-input"
              placeholder="عندك اقتراح؟ اكتبيه — البرومبت/خطة الريل تتعدل بيه وترجعلك للمراجعة">
              <button class="btn" id="st-refine">🔁 عدّلي البرومبت</button></div>
            <div class="svc-row"><button class="btn gold" id="st-exec">EXECUTE ✓</button>
            <button class="btn ghost" id="st-cancel">إلغاء</button></div>
          </div>
          <div class="svc-log num" id="st-log"></div>
        </div>"""
    return f"""<section class="page" id="p8" data-title="الاستوديو">
      <div class="page-inner">
        <div class="eyebrow">05 · معرض الإبداع — Creative Studio</div>
        <h2 class="page-title">الإعلان مربوط بالرهان اللي بيجاوب عليه</h2>
        <div class="studio">{poster_half}{reel_half}</div>
        {controls}
      </div>
    </section>"""


# ---------------------------------------------------------------------------------------------
# Page 6 — براءة الذمة (Evidence Ledger)
# ---------------------------------------------------------------------------------------------
def _audit_rows(audit: dict, cap: int = 300, fallback_url: str = "") -> str:
    rows: list[str] = []
    for item in (audit.get("items") or []):
        sc = item.get("shipped_copy") or {}
        head = " · ".join(str(item.get(k) or "") for k in ("date", "platform", "content_type") if item.get(k))
        rem = ('<span class="rem-badge">تم التقويم</span>' if item.get("remediated") else "")
        first_of_item = True

        def _row(field_text: str, token: str, link: str, quote: str, mark: str) -> str:
            nonlocal first_of_item
            head_cell = f'{_esc(head)} {rem}' if first_of_item else ""
            first_of_item = False
            return (f'<tr><td class="au-head">{head_cell}</td>'
                    f'<td class="au-text">{_esc(field_text[:140])}</td>'
                    f'<td class="au-token num">{token}</td>'
                    f'<td class="au-src">{link}</td>'
                    f'<td class="au-quote">{quote}</td>'
                    f'<td class="au-mark">{mark}</td></tr>')

        for field in (sc.get("fields") or []):
            ftext = str(field.get("text") or "")
            if not ftext.strip():
                continue
            claims = field.get("claims") or []
            if not claims:
                # a CHECKED sentence with no hard claims — it passed because it needed no source
                if len(rows) < cap:
                    rows.append(_row(ftext, '<span class="au-safe">—</span>',
                                     '<span class="au-safe">صياغة آمنة</span>',
                                     '<span class="au-safe">لا ادّعاء يحتاج مصدر</span>',
                                     '<span class="ok">✓</span>'))
                continue
            for claim in claims:
                if len(rows) >= cap:
                    break
                url = str(claim.get("source_url") or "")
                safe = _safe_href(url)
                quote_txt = str(claim.get("matched_quote") or "")
                if url and safe != "#":
                    link = (f'<a href="{safe}" target="_blank" rel="noopener">'
                            f'{_esc(_clean_citation(url))}</a>')
                elif claim.get("sourced") and quote_txt and fallback_url                         and _safe_href(fallback_url) != "#":
                    # 1.5 (owner ask): a SOURCED claim always links to its original — brand-tier
                    # evidence (contacts, deep-search) carries no page URL, so the brand's own
                    # site is the honest origin. A REJECTED claim never gets a link.
                    link = (f'<a href="{_safe_href(fallback_url)}" target="_blank" '
                            f'rel="noopener">من موقعك</a>')
                else:
                    link = "—"       # no source, or an unsafe scheme — never echo it as a label
                ok = bool(claim.get("sourced"))
                mark = '<span class="ok">✓</span>' if ok else '<span class="warn">⚠</span>'
                rows.append(_row(ftext, f'{_esc(str(claim.get("token") or ""))}',
                                 link, ("«" + _esc(quote_txt[:120]) + "»") if quote_txt else "—",
                                 mark))
    return "".join(rows)


def _page_ledger(audit: dict, result: Optional[dict] = None, mp: Optional[dict] = None,
                 profile: Optional[dict] = None) -> str:
    ev_count = audit.get("evidence_count")
    count_html = _num(ev_count) + ' <span class="unit">دليل</span>' if ev_count else '<span class="num">—</span>'
    rows = _audit_rows(audit, fallback_url=_plain(_fv(profile or {}, "source_url")) or "") if audit else ""
    if rows:
        table = f"""<button class="btn gold" id="audit-toggle">Expand Audit Trail</button>
        <div class="audit-body" id="audit-body">
          <div class="audit-scroll"><table class="audit">
            <thead><tr><th>العنصر</th><th>الجملة</th><th>الادّعاء</th><th>المصدر</th>
            <th>الاقتباس المطابق</th><th></th></tr></thead>
            <tbody>{rows}</tbody>
          </table></div>
        </div>"""
    else:
        table = ('<button class="btn gold" id="audit-toggle" disabled>Expand Audit Trail</button>'
                 '<p class="mi-empty">لا يوجد سجل تدقيق لهذه الحملة بعد — يتولّد مع خطة المحتوى.</p>')

    return f"""<section class="page" id="p10" data-title="الذمة">
      <div class="page-inner">
        <div class="eyebrow">06 · فوتر الأدلة — Evidence Ledger</div>
        <div class="ledger-banner">
          <span class="lb-check">✓</span>
          <div>
            <div class="lb-t">براءة الذمة التسويقية: كل كلمة وعرض في الحملة دي متوثق من بياناتك — وما يفشل التوثيق يُستبعد.</div>
            <div class="lb-s">{count_html} — كل ادّعاء قابل للتتبّع لمصدره الحرفي</div>
          </div>
        </div>
        {table}
        {_wr_honesty(result or {}, mp or {}, profile or {})}
      </div>
    </section>"""


# ---------------------------------------------------------------------------------------------
# Unified-package pages (native DARK renderers over the same dicts build.py reads — the light
# `.bsr` system is deliberately NOT embedded; the owner is design-sensitive and a scoped override
# of a whole light design system reads broken. build.py itself stays untouched.)
# ---------------------------------------------------------------------------------------------
_QUAD_KIND = {"s": ("قوتك", _W["green"]), "w": ("ضعفك", _W["red"]),
              "o": ("فرصك", _W["gold"]), "t": ("تهديداتك", _W["laneOrange"])}


def _readiness_badge(readiness: Any) -> str:
    """"This SWOT shipped on thin evidence" — stated on the page.

    Owner ruling (2026-07-21): a profile with `ready_for_swot: false` still ships (something beats
    nothing), but the client must see the caveat and its named blocker. Volume Qatar carried
    `swot_blockers: ["low_evidence_coverage"]` and the deck said nothing at all.
    """
    r = readiness if isinstance(readiness, dict) else {}
    if r.get("ready_for_swot") is not False:
        return ""
    blockers = [str(b) for b in (r.get("swot_blockers") or []) if str(b).strip()]
    why = f" ({', '.join(blockers)})" if blockers else ""
    return (f'<div class="readiness-badge">⚠ تغطية الأدلة ضعيفة — النتيجة أولية{_esc(why)}</div>')


def _evidence_notes_html(*, swot_notes=None, discovery_notes=None) -> str:
    """Surface what the run already admits to itself.

    `swot.notes` and `discovery_notes` were referenced NOWHERE in dashboard/ — yet the discovery
    log says things a client deserves to read verbatim: "only 1 of 4 peers cleared the floor ...
    Not padding with sub-floor candidates", "Consider a larger radius". Withholding that turns a
    disclosed limit into an apparent result.
    """
    rows = [str(n).strip() for n in list(swot_notes or []) + list(discovery_notes or [])
            if str(n).strip()]
    if not rows:
        return ""
    lis = "".join(f'<li dir="auto">{_esc(n)}</li>' for n in rows[:10])
    return ('<div class="glass intel evidence-notes"><div class="intel-head">'
            '<h3>حدود هذه التشغيلة · What this run could not measure</h3></div>'
            f'<ul class="ev-notes">{lis}</ul></div>')


def _wr_swot_quad(items: list, kind: str, reason: str = "") -> str:
    """One SWOT quadrant. An EMPTY quadrant states WHY when the run knows the reason — a `0` badge
    over an em-dash is not honesty, it is silence (owner-caught on Volume Qatar's Threats box)."""
    label, color = _QUAD_KIND[kind]
    rows = "".join(f'<li>{_esc(_plain(it.get("text")) if isinstance(it, dict) else it)}</li>'
                   for it in (items or [])[:6])
    if not rows:
        rows = (f'<li class="quad-why">{_esc(reason)}</li>' if reason else "<li>—</li>")
    return (f'<div class="quad" style="--q:{color}"><div class="q-t">{label} '
            f'<span class="num">{len(items or [])}</span></div><ul>{rows}</ul></div>')


def _deck_currency(v2: Any) -> str:
    """The currency the deck prints. D-8: an unresolved country yields NO currency word — the old
    `or "جنيه"` denominated a Qatari brand's numbers in Egyptian pounds, silently and confidently.
    """
    ks = ((v2 or {}).get("kill_switch") if isinstance(v2, dict) else None) or {}
    if not isinstance(v2, dict):
        return ""
    if "kill_switch" not in v2 and "currency" in v2:
        ks = v2
    return str(ks.get("currency") or "").strip()


def _staleness_note(artifact: Any, analysis: Any) -> str:
    """"This creative predates the analysis next to it" — stated plainly, with its age.

    Owner-caught (NTI, 2026-07-20): a fresh analysis sat beside a poster and reel from eight days
    earlier, built from a plan that no longer exists, and nothing on the page said so. Presenting
    superseded work as current is the same failure class as an unsourced claim: it reads true and
    answers a question nobody asked.
    """
    try:
        a, r = Path(artifact), Path(analysis)
        if not a.is_file() or not r.is_file():
            return ""
        days = (r.stat().st_mtime - a.stat().st_mtime) / 86400.0
        if days < 0.5:                      # same working session — not stale
            return ""
        n = int(days) if days >= 1 else 1
        return (f"قديم — اتعمل قبل التحليل الحالي بـ{n} يوم؛ "
                f"دي مش نتيجة الاستراتيجية اللي على الصفحة دلوقتي")
    except Exception:  # noqa: BLE001
        return ""


def _trends_file_line(slug: str, out_dir: str) -> str:
    """A pointer to `outputs/<slug>_trends.json` — the full pull with its seeds, market and noise
    disclosure. Empty string when the file isn't there, so the page never shows a dead reference."""
    if not slug:
        return ""
    try:
        p = Path(out_dir or "outputs") / f"{slug}_trends.json"
        if not p.is_file():
            return ""
        import json as _json
        doc = _json.loads(p.read_text(encoding="utf-8"))
        n = len(doc.get("items") or [])
        return (f'<div class="intel-src">الملف الكامل ({n} إشارة) — '
                f'<span class="num">{_esc(p.name)}</span> في مجلد outputs</div>')
    except Exception:  # noqa: BLE001
        return ""


def _page_market(result: dict, *, slug: str = "", out_dir: str = "outputs") -> str:
    trends = [t for t in (result.get("trends") or []) if (t or {}).get("title")][:8]
    comps = result.get("competitors") or []
    if trends:
        # Owner ask (2026-07-20): show WHY each trend counts — the relevance score + the brand's
        # own matched terms — so she can judge "جبنا ترند صح" herself, not trust a bare title.
        def _t_row(t: dict) -> str:
            score = t.get("trend_score")
            chips = []
            if isinstance(score, (int, float)) and score > 0:
                chips.append(f'<span class="chip num">صلة {int(round(score * 100))}%</span>')
            terms = [str(m) for m in (t.get("matched_terms") or []) if str(m).strip()][:4]
            if terms:
                chips.append(f'<span class="chip">مطابق لبراندك: {_esc(" · ".join(terms))}</span>')
            src = str(t.get("source") or "")
            if src:
                chips.append(f'<span class="chip num">{_esc(src)}</span>')
            return (f'<div class="mi-item"><div class="mi-t" dir="auto">{_esc(t.get("title"))}</div>'
                    + (f'<div class="fact-chips">{"".join(chips)}</div>' if chips else "")
                    + f'<div class="intel-src num"><a href="{_safe_href(t.get("url"))}" '
                      f'target="_blank" rel="noopener">'
                      f'{_esc(_clean_citation(str(t.get("url") or "")))}</a></div></div>')

        t_rows = "".join(_t_row(t) for t in trends)
        try:
            from trends.engine import NOISE_DISCLOSURE
            t_rows += f'<div class="intel-src">{_esc(NOISE_DISCLOSURE)}</div>'
        except Exception:  # noqa: BLE001
            pass
        # Owner rule: every dataset gets its own file — and a file she can't find from the page
        # isn't an artifact. Name it (with the FULL count, not just the rows shown) when it exists.
        t_rows += _trends_file_line(slug, out_dir)
    else:
        t_rows = '<p class="mi-empty">لا إشارات سوق حيّة في هذا التشغيل.</p>'
    cards = []
    for c in comps[:6]:
        cand, sel = c.get("candidate") or {}, c.get("selection") or {}
        name = _plain(cand.get("name")) or "—"
        why = str(sel.get("why_selected") or "")
        badge = ""
        if why.startswith("web search peer"):
            import re as _re
            m = _re.search(r"rank (\d+)", why)
            badge = f'<span class="chip">#{m.group(1) if m else "?"} نتيجة بحث</span>'
        elif cand.get("rating"):
            badge = (f'<span class="chip">★ {_num(cand.get("rating"))} · '
                     f'{_num(cand.get("review_count") or 0)} مراجعة</span>')
        site = _plain(cand.get("website"))
        link = (f'<a class="intel-src num" href="{_safe_href(site)}" target="_blank" '
                f'rel="noopener">{_esc(_clean_citation(site))}</a>') if site else ""
        cards.append(f'<div class="glass intel comp-card"><div class="mi-t">{_esc(name)}</div>'
                     f'{badge}{link}</div>')
    comp_html = ("".join(cards) if cards else
                 '<p class="mi-empty">لا منافسين مُسجّلين بعد — يتطلب تشغيل التحليل الكامل.</p>')
    return f"""<section class="page" id="p2" data-title="السوق">
      <div class="page-inner">
        <div class="eyebrow">02 · السوق والمنافسون — Market</div>
        <h2 class="page-title">إشارات السوق الحيّة ومَن تنافس فعلاً</h2>
        <div class="market-grid">
          <div class="glass intel"><div class="intel-head"><h3>نبض السوق · Market Pulse</h3></div>
            <div class="intel-body">{t_rows}</div></div>
          <div class="comp-col">{comp_html}</div>
        </div>
      </div>
    </section>"""


def _page_swot(result: dict, *, profile: Any = None) -> str:
    swot = result.get("swot") or {}
    tows = result.get("tows") or {}
    # Each quadrant's own note (competitor/swot.disclose_empty_quadrants) explains an empty box.
    _notes = [str(n) for n in (swot.get("notes") or [])]

    def _reason_for(label: str) -> str:
        return next((n for n in _notes if n.startswith(label)), "")

    quads = (_wr_swot_quad(swot.get("strengths"), "s", _reason_for("نقاط القوة"))
             + _wr_swot_quad(swot.get("weaknesses"), "w", _reason_for("نقاط الضعف"))
             + _wr_swot_quad(swot.get("opportunities"), "o", _reason_for("الفرص"))
             + _wr_swot_quad(swot.get("threats"), "t", _reason_for("التهديدات")))
    strat_rows = "".join(
        f'<div class="mi-item"><div class="mi-t">{_esc(_plain(st.get("title")))}</div>'
        f'<div class="strat-d">{_esc(_plain(st.get("description"))[:160])}</div></div>'
        for st in (tows.get("strategies") or [])[:6])
    strat = (f'<div class="glass intel"><div class="intel-head"><h3>استراتيجيات TOWS</h3></div>'
             f'<div class="intel-body">{strat_rows}</div></div>') if strat_rows else ""
    voice_items = [it for k in ("strengths", "weaknesses") for it in (swot.get(k) or [])
                   if "customer voice" in str(it.get("evidence") or "")]
    voice = ""
    if voice_items:
        vrows = "".join(
            f'<div class="mi-item"><div class="mi-t">{_esc(_plain(it.get("text")))}</div>'
            + "".join(
                (f'<div class="intel-src num"><a href="{_safe_href(str(q))}" target="_blank" '
                 f'rel="noopener">{_esc(_clean_citation(str(q)))}</a></div>')
                if str(q).startswith("http") and _safe_href(str(q)) != "#"
                else f'<blockquote class="mi-q">«{_esc(str(q))}»</blockquote>'
                for q in (it.get("citation") or [])[:2])
            + '<div class="intel-src">من مراجعات عملائك</div></div>'
            for it in voice_items[:4])
        voice = (f'<div class="glass intel"><div class="intel-head"><h3>صوت عملائك — بكلماتهم</h3>'
                 f'</div><div class="intel-body">{vrows}</div></div>')
    return f"""<section class="page" id="p3" data-title="SWOT">
      <div class="page-inner">
        <div class="eyebrow">03 · التحليل الرباعي — SWOT / TOWS</div>
        <h2 class="page-title">قوتك وضعفك بدليل، مش بمجاملة</h2>
        {_readiness_badge((profile or {}).get("readiness"))}
        <div class="swot-grid">{quads}</div>
        <div class="swot-lower">{strat}{voice}</div>
        {_evidence_notes_html(swot_notes=_notes, discovery_notes=result.get("discovery_notes"))}
      </div>
    </section>"""


def _page_services(services: list, *, slug: str, serve_mode: bool) -> str:
    chips = "".join(
        f'<div class="svc-chip" data-name="{_esc(s.get("name"))}" data-url="{_safe_href(s.get("url"))}" '
        f'data-img="{_safe_href(s.get("image")) if s.get("image") else ""}">'
        f'<div class="svc-n">{_esc(s.get("name"))}</div>'
        + (f'<div class="svc-p num">{_esc(s.get("price_text"))}</div>' if s.get("price_text") else "")
        + "</div>"
        for s in (services or [])[:24] if s.get("name"))
    empty = '' if chips else '<p class="mi-empty">لا خدمات مستخرَجة من الموقع بعد — شغّلي التحليل أولاً.</p>'
    if serve_mode:
        actions = f"""<div class="svc-actions">
          <div class="svc-row"><input id="purl" class="svc-input" dir="ltr"
            placeholder="https://…  الصقي رابط صفحة الخدمة/المنتج"><button class="btn" id="purl-go">سكرايب</button></div>
          <div class="svc-row"><button class="btn gold" id="svc-poster">توليد بوستر للخدمة المختارة</button>
          <span class="svc-sel" id="svc-sel">✦ البراند كله</span></div>
          <div class="hitl-panel" id="hitl-panel">
            <div class="svc-row"><span class="bp-t">البريف (بموافقتك فقط — HITL):</span>
              <button class="btn ghost br-l" id="svc-br-ar">عربي</button>
              <button class="btn ghost br-l" id="svc-br-en">EN</button></div>
            <pre class="hitl-brief" id="hitl-brief" dir="auto"></pre>
            <div class="svc-row"><input id="svc-note" class="svc-input"
              placeholder="عندك اقتراح؟ اكتبيه (مثال: خلّي الإضاءة نهارية) — والبرومبت يتعدل بيه">
              <button class="btn" id="svc-refine">🔁 عدّلي البرومبت</button></div>
            <div class="svc-row"><button class="btn gold" id="hitl-exec">EXECUTE ✓</button>
            <button class="btn ghost" id="hitl-cancel">إلغاء</button></div>
          </div>
          <div class="svc-log num" id="svc-log"></div>
          <div class="svc-out" id="svc-out"></div>
        </div>"""
    else:
        actions = f"""<div class="svc-actions">
          <div class="bp-t">التوليد التفاعلي (سكرايب خدمة → بوستر عليها) من <b>النسخة الحية</b>:</div>
          <div class="cmd-row"><code class="num">python -m dashboard.server</code>
            <button class="copy" data-copy="python -m dashboard.server">نسخ</button></div>
          <div class="bp-t">ثم افتحي /dashboard?slug={_esc(slug)} — صفحة الخدمات هناك تفاعلية بالكامل.</div>
        </div>"""
    return f"""<section class="page" id="p7" data-title="الخدمات">
      <div class="page-inner">
        <div class="eyebrow">07 · الخدمات — Services</div>
        <h2 class="page-title">اختاري خدمة (أو هاتيها برابط) واعملي عليها إعلان</h2>
        <div class="svc-grid" id="svc-grid">{chips}{empty}</div>
        {actions}
      </div>
    </section>"""


def _wr_calendar_row(it: dict) -> str:
    f = _funnel_label(it.get("funnel_stage")) or "—"
    cta = _plain(it.get("cta")) or "—"
    hook = _plain(it.get("hook")) or _plain(it.get("angle"))
    return (f'<tr><td class="num">{_esc(it.get("date"))}</td>'
            f'<td><span class="chip">{_esc(it.get("platform"))}</span></td>'
            f'<td>{_esc(it.get("content_type"))}</td>'
            f'<td class="cal-topic">{_esc(_plain(it.get("topic")))}'
            + (f'<div class="strat-d">{_esc(hook)}</div>' if hook else "") + "</td>"
            f'<td><span class="chip">{_esc(f)}</span></td><td>{_esc(cta)}</td></tr>')


_DEST_AR = {"lead_form": "نموذج عملاء", "whatsapp": "واتساب", "phone_call": "مكالمة",
            "online_store": "متجر أونلاين", "website": "الموقع", "physical_store": "زيارة الفرع"}


def _page_plan_calendar(mp: dict, plan: dict, profile: dict) -> str:
    objs = (mp or {}).get("objectives") or []
    o = objs[0] if objs and isinstance(objs[0], dict) else {}
    if o:
        geo = (mp or {}).get("base_geo") or {}
        head = (f'<div class="ns-tags plan-tags">'
                f'<span class="tag">الهدف: {_esc(" + ".join((_objective_label(_plain(x.get("objective"))) or "—").split("·")[0].strip() for x in objs if isinstance(x, dict)) or "—")}</span>'
                f'<span class="tag">الوجهة: {_esc(_DEST_AR.get(str(_plain(o.get("destination"))), _plain(o.get("destination")) or "—"))}</span>'
                f'<span class="tag">Funnel: {_esc(_funnel_label(o.get("funnel_stage")) or "—")}</span>'
                f'<span class="tag">الجغرافيا: {_esc(_plain(geo.get("mode")) or "—")}'
                + (f' · {_esc(_plain(geo.get("country")))}' if geo.get("country") else "") + "</span></div>")
    else:
        head = '<p class="mi-empty">لم تُبنَ خطة إعلانية بعد.</p>'
    items = (plan or {}).get("items") or []
    if items:
        rows = "".join(_wr_calendar_row(it) for it in items[:30])
        table = (f'<div class="audit-scroll cal-scroll"><table class="audit cal">'
                 f'<thead><tr><th>التاريخ</th><th>المنصة</th><th>النوع</th><th>الموضوع</th>'
                 f'<th>Funnel</th><th>CTA</th></tr></thead><tbody>{rows}</tbody></table></div>')
    else:
        table = '<p class="mi-empty">لا تقويم محتوى بعد — يتولّد مع التحليل.</p>'
    return f"""<section class="page" id="p9" data-title="التقويم">
      <div class="page-inner">
        <div class="eyebrow">09 · الخطة والتقويم — Plan &amp; Calendar</div>
        <h2 class="page-title">الخطة المشروحة وجدول المحتوى</h2>
        {head}{table}
      </div>
    </section>"""


def _wr_honesty(result: dict, mp: dict, profile: dict) -> str:
    """Native-dark port of the honesty gaps ('ما لا نعرفه بعد') — same conditions, same copy."""
    gaps: list[str] = []
    if not (result.get("competitors") or []):
        gaps.append("لم نؤكّد منافسين مباشرين بعد — التحليل مبنيّ على موقعك وحده.")
    swot = result.get("swot") or {}
    n_voice = sum(1 for k in ("strengths", "weaknesses") for it in (swot.get(k) or [])
                  if "customer voice" in str(it.get("evidence") or ""))
    # Owner-caught staleness (2026-07-20): IG-autopulled pain points ARE collected customer
    # voice — the gap line must not deny voice the intel page is displaying two pages earlier.
    n_ig_voice = len(((result.get("marketing_intel") or {}).get("pain_points")) or [])
    if n_voice == 0 and n_ig_voice == 0:
        gaps.append("لم نجمع آراء عملاء بعد — تُضاف بعد ربط تقييمات Google Maps.")
    elif n_voice == 0 and n_ig_voice > 0:
        gaps.append(f"آراء العملاء الحالية من إنستجرام فقط ({n_ig_voice} نقطة موثقة) — "
                    "تقييمات Google Maps تضيف مصدراً ثانياً عند توفر مكان مسجَّل.")
    if not result.get("ads_available"):
        gaps.append("إعلانات المنافسين الجارية غير متاحة بعد — تحتاج تأكيد هوية Meta (خطوة مجانية).")
    objs = (mp or {}).get("objectives") or []
    if objs and not ((objs[0].get("kpi_target") or {}).get("target_value")):
        gaps.append("الرقم المستهدف للتكلفة لم يُحدَّد — لا نخترع رقماً؛ يُعاير من أول أسبوعين تشغيل.")
    if not gaps:
        return ""
    rows = "".join(f'<div class="mi-item"><div class="mi-t">• {_esc(g)}</div></div>' for g in gaps)
    return (f'<div class="glass intel honesty"><div class="intel-head">'
            f'<h3>ما لا نعرفه بعد — الأمانة جزء من التقرير</h3></div>'
            f'<div class="intel-body">{rows}</div></div>')


def _services_js(slug: str) -> str:
    """The Services page's OWN inline script (serve mode only). HITL EXECUTE gate preserved:
    /generate/* is never called before the operator presses EXECUTE on the previewed brief."""
    return """<script>
(function(){
  var SLUG=%r,SEL=null,ES=null;
  function q(id){return document.getElementById(id);}
  function chipify(el){el.addEventListener('click',function(){
    var was=el.classList.contains('sel');
    document.querySelectorAll('.svc-chip.sel').forEach(function(c){c.classList.remove('sel');});
    if(!was){el.classList.add('sel');SEL={name:el.getAttribute('data-name'),
      url:el.getAttribute('data-url'),image:el.getAttribute('data-img')};}
    else SEL=null;
    q('svc-sel').textContent=SEL?('المختار: '+SEL.name):'✦ البراند كله';});}
  document.querySelectorAll('.svc-chip').forEach(chipify);
  fetch('/products?slug='+SLUG).then(function(r){return r.json();}).then(function(d){
    var g=q('svc-grid');if(!g||!d.products)return;
    d.products.forEach(function(p){
      if(document.querySelector('.svc-chip[data-name="'+(p.name||'').replace(/"/g,'&quot;')+'"]'))return;
      var c=document.createElement('div');c.className='svc-chip';
      c.setAttribute('data-name',p.name||'');c.setAttribute('data-url',p.url||'');
      c.setAttribute('data-img',p.image||'');
      c.innerHTML='<div class="svc-n"></div>';c.firstChild.textContent=p.name||'';
      g.appendChild(c);chipify(c);});}).catch(function(){});
  var pg=q('purl-go');
  if(pg)pg.addEventListener('click',function(){
    var u=(q('purl').value||'').trim();if(!u)return;
    q('svc-log').textContent='بجيب الخدمة…';
    fetch('/product_by_url?slug='+SLUG+'&url='+encodeURIComponent(u))
      .then(function(r){return r.json();}).then(function(d){
        if(!d.ok){q('svc-log').textContent='تعذّر: '+(d.error||'رابط غير صالح');return;}
        q('svc-log').textContent='';
        var g=q('svc-grid'),c=document.createElement('div');c.className='svc-chip sel';
        c.setAttribute('data-name',d.product.name||'');c.setAttribute('data-url',d.product.url||'');
        c.setAttribute('data-img',d.product.image||'');
        c.innerHTML='<div class="svc-n"></div>';c.firstChild.textContent=d.product.name||'';
        document.querySelectorAll('.svc-chip.sel').forEach(function(x){x.classList.remove('sel');});
        c.classList.add('sel');g.prepend(c);chipify(c);
        SEL={name:d.product.name,url:d.product.url,image:d.product.image};
        q('svc-sel').textContent='المختار: '+SEL.name;})
      .catch(function(){q('svc-log').textContent='تعذّر الاتصال';});});
  function pq(){return SEL?('&pname='+encodeURIComponent(SEL.name)+'&pimg='+
    encodeURIComponent(SEL.image||'')+'&product='+encodeURIComponent(SEL.url||'')):'';}
  var sp=q('svc-poster');
  if(sp)sp.addEventListener('click',function(){
    q('svc-log').textContent='ببني البريف (HITL — مفيش توليد قبل موافقتك)…';
    fetch('/hitl/preview?kind=poster&slug='+SLUG+pq())
      .then(function(r){return r.json();}).then(function(d){
        q('svc-log').textContent='';SVCBR.ar=d.arabic||'';SVCBR.en=d.text||'';svcShow();
        q('hitl-panel').classList.add('open');})
      .catch(function(){q('svc-log').textContent='تعذّر بناء البريف';});});
  var SVCBR={ar:'',en:'',cur:'ar'};
  function svcShow(){q('hitl-brief').textContent=
    (SVCBR.cur==='ar'?(SVCBR.ar||SVCBR.en):(SVCBR.en||SVCBR.ar))||'';}
  var sba=q('svc-br-ar'); if(sba)sba.addEventListener('click',function(){SVCBR.cur='ar';svcShow();});
  var sbe=q('svc-br-en'); if(sbe)sbe.addEventListener('click',function(){SVCBR.cur='en';svcShow();});
  var sRef=q('svc-refine'); if(sRef)sRef.addEventListener('click',function(){
    var note=(q('svc-note').value||'').trim(); if(!note)return;
    q('svc-log').textContent='بعدّل البرومبت باقتراحك…';
    fetch('/hitl/refine',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slug:SLUG,kind:'poster',instruction:note,
        pname:(SEL?SEL.name:'')})})
      .then(function(r){return r.json();}).then(function(d){
        if(d.error){q('svc-log').textContent=d.error;return;}
        SVCBR.ar=d.arabic||SVCBR.ar;SVCBR.en=d.text||SVCBR.en;svcShow();
        q('svc-note').value='';
        q('svc-log').textContent='اتعدل ✓ — راجعي النسخة الجديدة قبل EXECUTE';})
      .catch(function(){q('svc-log').textContent='تعذّر التعديل';});});
  var hc=q('hitl-cancel');
  if(hc)hc.addEventListener('click',function(){q('hitl-panel').classList.remove('open');});
  var he=q('hitl-exec');
  if(he)he.addEventListener('click',function(){
    q('hitl-panel').classList.remove('open');
    if(ES){ES.close();}
    q('svc-log').textContent='بيتولّد…';
    ES=new EventSource('/generate/poster?slug='+SLUG+'&hitl=1'+pq());
    ES.addEventListener('stage',function(e){try{q('svc-log').textContent=
      (JSON.parse(e.data).msg||'');}catch(_){}});
    ES.addEventListener('done',function(e){ES.close();q('svc-log').textContent='تم ✓';
      try{var a=JSON.parse(e.data).asset;
        q('svc-out').innerHTML='<img class="svc-poster" src="'+a+'">';}catch(_){}});
    ES.addEventListener('failed',function(e){ES.close();
      q('svc-log').textContent='فشل التوليد — راجعي اللوج';});});
  // ---- p6 owner inputs (الأرقام الحقيقية) ----
  var oiS=document.getElementById('oi-save');
  if(oiS)oiS.addEventListener('click',function(){
    var body={slug:SLUG,
      profit_margin_pct:(document.getElementById('oi-margin')||{}).value,
      capacity_per_day:(document.getElementById('oi-capacity')||{}).value,
      monthly_budget:(document.getElementById('oi-budget')||{}).value};
    document.getElementById('oi-log').textContent='بحفظ…';
    fetch('/owner_inputs',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)})
      .then(function(r){return r.json();})
      .then(function(){document.getElementById('oi-log').textContent='اتحفظ ✓ — بعيد الحساب…';
        setTimeout(function(){location.reload();},600);})
      .catch(function(){document.getElementById('oi-log').textContent='تعذّر الحفظ';});});

  // ---- p8 studio controls (poster + PAID reel, HITL-gated) ----
  var stKind=null, stES=null;
  function stq(id){return document.getElementById(id);}
  function stOpts(){var l=stq('st-lang'),d=stq('st-dialect');
    return '&lang='+(l?l.value:'ar')+'&dialect='+(d?d.value:'masri');}
  function stGen(kind){stKind=kind;
    stq('st-log').textContent='ببني البريف (HITL — مفيش توليد ولا صرف قبل موافقتك)…';
    fetch('/hitl/preview?kind='+kind+'&slug='+SLUG+stOpts())
      .then(function(r){return r.json();}).then(function(d){
        stq('st-log').textContent='';
        STBR.ar=d.arabic||'';STBR.en=d.text||JSON.stringify(d).slice(0,2000);stShow();
        var pn=stq('st-hitl');pn.classList.add('open');
        // the panel opens BELOW the buttons; without this it lands off-screen and pressing
        // Generate looks like it did nothing (owner-reported).
        try{pn.scrollIntoView({behavior:'smooth',block:'center'});}catch(e){pn.scrollIntoView();}})
      .catch(function(){stq('st-log').textContent='تعذّر بناء البريف';});}
  var sf=stq('st-fresh'); if(sf)sf.addEventListener('click',function(){
    if(!confirm('هيتشال البوستر والريل الحاليين من الصفحة (بيتحفظوا في أرشيف مش بيتمسحوا). نكمل؟'))return;
    stq('st-log').textContent='بنظّف…';
    fetch('/studio/reset',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slug:SLUG})})
      .then(function(r){return r.json();}).then(function(d){
        if(d.error){stq('st-log').textContent=d.error;return;}
        stq('st-log').textContent='اتنضّف ✓ ('+(d.archived||0)+' ملف في الأرشيف) — بتحدّث الصفحة…';
        setTimeout(function(){location.reload();},900);})
      .catch(function(){stq('st-log').textContent='تعذّر التنظيف';});});
  var STBR={ar:'',en:'',cur:'ar'};
  function stShow(){stq('st-brief').textContent=
    (STBR.cur==='ar'?(STBR.ar||STBR.en):(STBR.en||STBR.ar))||'';}
  var tba=stq('st-br-ar'); if(tba)tba.addEventListener('click',function(){STBR.cur='ar';stShow();});
  var tbe=stq('st-br-en'); if(tbe)tbe.addEventListener('click',function(){STBR.cur='en';stShow();});
  var tRef=stq('st-refine'); if(tRef)tRef.addEventListener('click',function(){
    var note=(stq('st-note').value||'').trim(); if(!note||!stKind)return;
    stq('st-log').textContent='بعدّل البرومبت باقتراحك…';
    var l=stq('st-lang'),dd=stq('st-dialect');
    fetch('/hitl/refine',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slug:SLUG,kind:stKind,instruction:note,
        lang:(l?l.value:''),dialect:(dd?dd.value:'')})})
      .then(function(r){return r.json();}).then(function(d){
        if(d.error){stq('st-log').textContent=d.error;return;}
        STBR.ar=d.arabic||STBR.ar;STBR.en=d.text||STBR.en;stShow();
        stq('st-note').value='';
        stq('st-log').textContent='اتعدل ✓ — راجعي قبل EXECUTE';})
      .catch(function(){stq('st-log').textContent='تعذّر التعديل';});}
  var sp2=stq('st-poster'); if(sp2)sp2.addEventListener('click',function(){stGen('poster');});
  var sr2=stq('st-reel'); if(sr2)sr2.addEventListener('click',function(){stGen('reel');});
  var sc2=stq('st-cancel'); if(sc2)sc2.addEventListener('click',function(){
    stq('st-hitl').classList.remove('open');});
  var se2=stq('st-exec'); if(se2)se2.addEventListener('click',function(){
    stq('st-hitl').classList.remove('open');
    if(stES)stES.close();
    stq('st-log').textContent=(stKind==='reel')?'الريل بيترندر (Veo) — من 10 لـ20 دقيقة…':'بيتولّد…';
    stES=new EventSource('/generate/'+stKind+'?slug='+SLUG+'&hitl=1'+stOpts());
    stES.addEventListener('stage',function(e){try{stq('st-log').textContent=
      (JSON.parse(e.data).msg||'');}catch(_){}});
    stES.addEventListener('done',function(e){stES.close();
      stq('st-log').textContent='تم ✓ — بحمّل الصفحة بالنتيجة…';
      setTimeout(function(){location.reload();},900);});
    stES.addEventListener('failed',function(e){stES.close();
      var m='فشل التوليد';try{m=JSON.parse(e.data).msg||m;}catch(_){}
      stq('st-log').textContent=m;});});
})();
</script>""" % (slug,)


# ---------------------------------------------------------------------------------------------
# CSS + JS
# ---------------------------------------------------------------------------------------------
def _css() -> str:
    w = _W
    return f"""<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{color-scheme:dark;
  --ar:'Cairo','Tajawal','Segoe UI','Noto Sans Arabic','Sakkal Majalla',Tahoma,sans-serif;
  --mono:'Cascadia Code','Cascadia Mono',Consolas,'Courier New',monospace;
  --line:{w['line']};--gold:{w['gold']};--ink:{w['ink']};--soft:{w['inkSoft']};--muted:{w['muted']}}}
html,body{{height:100%}}
body{{height:100vh;overflow:hidden;font-family:var(--ar);color:var(--ink);
  background:radial-gradient(1100px 700px at 85% -10%,rgba(205,180,228,.10),transparent 60%),
             radial-gradient(900px 650px at 8% 110%,rgba(169,139,208,.07),transparent 55%),
             linear-gradient(160deg,{w['bg']} 0%,{w['bg2']} 100%);line-height:1.6}}
.num,.unit{{font-family:var(--mono);font-variant-numeric:tabular-nums;direction:ltr;unicode-bidi:isolate}}
.unit{{font-size:.52em;color:var(--soft);margin-inline-start:.35em}}

/* ---- pages ---- */
.deck{{position:relative;height:100vh}}
.page{{position:absolute;inset:0;opacity:0;visibility:hidden;pointer-events:none;
  transform:translateY(16px);transition:opacity .28s ease,transform .28s ease,visibility .28s}}
.page.on{{opacity:1;visibility:visible;pointer-events:auto;transform:none}}
.page-inner{{height:100%;max-width:1720px;margin-inline:auto;padding:44px 96px 44px 56px;
  overflow:auto;overscroll-behavior:contain;display:flex;flex-direction:column}}
.page-inner>*{{flex-shrink:0}}
.eyebrow{{font-family:var(--mono);font-size:12.5px;letter-spacing:.16em;color:var(--gold);
  text-transform:uppercase;margin-bottom:10px}}
.page-title{{font-size:30px;font-weight:800;margin-bottom:26px;text-wrap:balance}}

/* ---- rail ---- */
.rail{{position:fixed;inset-inline-start:0;top:0;bottom:0;width:76px;z-index:40;display:flex;
  flex-direction:column;align-items:center;justify-content:center;gap:16px;
  border-inline-end:1px solid var(--line);background:rgba(11,17,32,.55)}}
.dot{{width:11px;height:11px;border-radius:50%;background:transparent;border:2px solid var(--muted);
  cursor:pointer;position:relative;transition:transform .12s,border-color .12s,background .12s}}
.dot:hover{{transform:scale(1.25);border-color:var(--soft)}}
.dot.act{{background:var(--gold);border-color:var(--gold);box-shadow:0 0 14px rgba(205,180,228,.55)}}
.dot .dlbl{{position:absolute;inset-inline-start:26px;top:50%;transform:translateY(-50%);
  white-space:nowrap;font-size:12.5px;color:var(--soft);opacity:0;transition:opacity .12s;
  background:{w['surface']};border:1px solid var(--line);padding:4px 10px;border-radius:8px}}
.dot:hover .dlbl{{opacity:1}}
.rail-hint{{position:absolute;bottom:18px;font-size:10.5px;color:var(--muted);writing-mode:vertical-rl}}
.pgno{{position:fixed;bottom:18px;left:24px;z-index:40;font-family:var(--mono);
  font-size:13px;color:var(--muted);direction:ltr;white-space:nowrap}}

/* ---- page 1 ---- */
.topbar{{display:flex;flex-direction:row-reverse;justify-content:space-between;align-items:flex-start;
  gap:24px;margin-bottom:7vh}}
.brand{{display:flex;flex-direction:row-reverse;gap:16px;align-items:center;text-align:left}}
.logo{{width:52px;height:52px;border-radius:14px;object-fit:contain;background:#fff;padding:5px}}
.logo.badge{{display:flex;align-items:center;justify-content:center;font-weight:800;font-size:24px;
  color:{w['bg']};background:linear-gradient(135deg,var(--gold),{w['goldDeep']});padding:0}}
.brand-name{{font-size:21px;font-weight:800}}
.brand-sub{{font-size:13.5px;color:var(--soft)}}
.brand-tag{{font-size:12.5px;color:var(--muted)}}
.export-wrap{{position:relative}}
.export{{font-family:var(--ar);font-weight:800;font-size:16px;padding:14px 30px;border-radius:14px;
  border:0;cursor:pointer;color:{w['bg']};background:linear-gradient(135deg,{w['gold']},{w['goldDeep']});
  box-shadow:0 6px 26px rgba(205,180,228,.28);transition:transform .12s,box-shadow .12s}}
.export:hover{{transform:translateY(-2px);box-shadow:0 10px 32px rgba(205,180,228,.4)}}
.export.ready{{background:linear-gradient(135deg,{w['green']},#26A96C);box-shadow:0 6px 26px rgba(62,207,142,.3)}}
.bundle-panel{{display:none;position:absolute;top:calc(100% + 12px);inset-inline-end:0;width:min(560px,80vw);
  background:{w['raise']};border:1px solid var(--line);border-radius:14px;padding:18px;z-index:30;
  box-shadow:0 18px 48px rgba(0,0,0,.55)}}
.bundle-panel.open{{display:block}}
.bp-t{{font-size:13.5px;color:var(--soft);margin-bottom:10px}}
.bundle-panel ul{{list-style:none;font-size:12.5px;color:var(--ink);margin-bottom:8px}}
.bp-path{{font-size:11px;color:var(--muted);word-break:break-all}}
.cmd-row{{display:flex;gap:10px;align-items:center}}
.cmd-row code{{flex:1;background:{w['bg']};border:1px solid var(--line);border-radius:10px;
  padding:10px 14px;font-size:13px;color:{w['green']};overflow-x:auto;white-space:nowrap}}
.copy{{font-family:var(--ar);border:1px solid var(--line);background:{w['surface']};color:var(--ink);
  padding:9px 16px;border-radius:10px;cursor:pointer;transition:transform .12s}}
.copy:hover{{transform:translateY(-1px)}} .copy.copied{{background:{w['green']};color:{w['bg']}}}
.hero{{display:flex;flex-direction:column;align-items:center;text-align:center;
  flex:1;justify-content:center;padding-bottom:6vh}}
.north-star{{width:min(72%,1180px);border-radius:22px;padding:52px 56px;position:relative;
  border:1px solid var(--line);
  background:linear-gradient(140deg,color-mix(in srgb,var(--brand) 16%,{w['surface']}) 0%,{w['surface']} 55%,{w['bg2']} 100%)}}
.ns-text{{font-size:27px;font-weight:700;line-height:1.85;text-wrap:balance}}
.ns-tags{{margin-top:28px;display:flex;gap:12px;justify-content:center;flex-wrap:wrap}}
.tag{{border:1px solid var(--line);background:rgba(11,17,32,.5);border-radius:999px;
  padding:8px 18px;font-size:13.5px;color:var(--soft)}}
.tag.ask{{border-color:var(--gold);color:var(--gold);font-weight:700}}

/* ---- page 2 ---- */
.glass{{background:rgba(28,24,50,.55);border:1px solid rgba(196,170,232,.22);border-radius:18px;
  backdrop-filter:blur(14px) saturate(140%);-webkit-backdrop-filter:blur(14px) saturate(140%)}}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){{
  .glass{{background:#1F1A38}}}}
.intel-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:26px;margin-block:auto;width:100%}}
.intel .intel-body{{max-height:56vh;overflow-y:auto;overscroll-behavior:contain;padding-inline-end:6px}}
.intel{{padding:28px;min-height:46vh;display:flex;flex-direction:column;
  transition:transform .12s,border-color .12s}}
.intel:hover{{transform:translateY(-3px);border-color:rgba(196,170,232,.4)}}
.intel-head{{display:flex;gap:12px;align-items:center;margin-bottom:18px}}
.intel-head h3{{font-size:17.5px}}
.intel-ic{{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;
  justify-content:center;background:rgba(196,170,232,.1);border:1px solid var(--line)}}
.intel-body{{flex:1}}
.mi-item{{padding:10px 0;border-bottom:1px dashed var(--line)}}
.mi-item:last-child{{border-bottom:0}}
.mi-t{{font-size:14.5px}}
.mi-q{{margin-top:6px;font-size:13px;color:var(--soft);border-inline-start:3px solid var(--gold);
  padding-inline-start:12px}}
.mi-empty{{font-size:13.5px;color:var(--soft);line-height:1.9}}
.mi-await{{margin-top:14px;font-size:12px;color:var(--muted);letter-spacing:.08em}}
.intel-src{{margin-top:14px;font-size:11px;color:var(--muted)}}
.is-awaiting{{background:{w['grayCard']};border-style:dashed;border-color:rgba(124,131,153,.3)}}
.is-awaiting .intel-head h3,.is-awaiting .mi-empty{{color:{w['grayInk']}}}
.awaiting-big{{font-family:var(--mono);font-size:19px;font-weight:700;color:{w['grayInk']};
  letter-spacing:.1em;margin-bottom:12px}}

/* ---- page 3 ---- */
.lanes{{display:flex;flex-direction:column;gap:20px}}
.lane{{position:relative;background:{w['surface']};border:1px solid var(--line);border-radius:16px;
  border-inline-start:5px solid var(--lane);padding:20px 26px;
  transition:transform .12s,box-shadow .12s}}
.lane:hover{{transform:translateY(-3px);box-shadow:0 14px 34px rgba(0,0,0,.35)}}
[data-tip]:hover::after{{content:attr(data-tip);position:absolute;bottom:calc(100% + 10px);
  inset-inline-start:18px;background:{w['bg']};border:1px solid var(--line);padding:8px 14px;
  border-radius:10px;font-size:12.5px;color:var(--ink);white-space:nowrap;z-index:50;
  box-shadow:0 10px 26px rgba(0,0,0,.5)}}
.lane-head{{display:flex;gap:14px;align-items:center;margin-bottom:14px}}
.lane-no{{font-size:11.5px;color:var(--lane);letter-spacing:.14em}}
.lane-head h3{{font-size:17px}}
.axis-chip{{font-family:var(--mono);font-size:10.5px;color:var(--lane);border:1px solid var(--lane);
  border-radius:999px;padding:2px 10px;opacity:.85}}
.lane-grid{{display:grid;grid-template-columns:1.5fr 1fr 1.2fr .8fr;gap:26px;align-items:start}}
.lbl{{font-size:11px;color:var(--muted);letter-spacing:.1em;margin-bottom:8px;text-transform:uppercase;
  font-family:var(--mono)}}
.hyp{{font-size:14px;line-height:1.75}}
.grounded{{margin-top:8px;font-size:10.5px;color:var(--muted)}}
.bar{{height:10px;border-radius:999px;background:rgba(196,170,232,.12);overflow:hidden}}
.bar-fill{{height:100%;border-radius:999px;background:var(--lane)}}
.bar-lbl{{margin-top:8px;font-size:12px;color:var(--soft)}}
.brief-row{{display:flex;gap:10px;font-size:13px;padding:5px 0;border-bottom:1px dashed var(--line)}}
.brief-row:last-child{{border-bottom:0}}
.bk{{font-family:var(--mono);font-size:10.5px;color:var(--lane);min-width:52px;padding-top:3px}}
.pending{{display:inline-block;border:1.5px dashed var(--muted);color:var(--muted);border-radius:10px;
  padding:8px 16px;font-family:var(--mono);font-size:12px}}
.cell-thumb-link{{display:flex;flex-direction:column;gap:8px;text-decoration:none;color:var(--soft);
  font-size:12px}}
.cell-thumb{{width:96px;border-radius:10px;border:2px solid var(--lane)}}
.reel-chip{{display:inline-block;border:2px solid var(--lane);border-radius:10px;padding:10px 14px;
  color:var(--ink);font-size:13px}}
.map-note{{margin-top:22px;font-size:12.5px;color:var(--muted)}}

/* ---- page 4 ---- */
.vault{{background:{w['vault']};border-radius:22px;padding:44px 40px;margin-block:auto;
  box-shadow:0 0 0 1px rgba(196,170,232,.25),0 0 52px rgba(205,180,228,.10);
  display:grid;grid-template-columns:1fr 1.35fr 1fr;gap:36px}}
.v-col{{text-align:center;padding:10px 18px}}
.v-col.kill{{border-inline:1px solid var(--line)}}
.v-ic{{display:inline-flex;width:64px;height:64px;border-radius:50%;align-items:center;
  justify-content:center;background:rgba(196,170,232,.08);border:1px solid var(--line);
  margin-bottom:14px}}
.kill-shield{{animation:killPulse 2.4s ease-in-out infinite;border-color:rgba(255,93,93,.4)}}
@keyframes killPulse{{0%,100%{{box-shadow:0 0 0 0 rgba(255,93,93,.28)}}50%{{box-shadow:0 0 0 16px rgba(255,93,93,0)}}}}
.v-t{{font-size:14px;color:var(--soft);margin-bottom:10px}}
.v-big{{font-size:56px;font-weight:800;line-height:1.15}}
.v-big.red{{color:{w['red']}}}
.v-sub{{font-size:13px;color:var(--soft);margin-top:4px}}
.v-p{{font-size:13.5px;color:var(--soft);line-height:1.9;margin-top:14px;text-align:center}}
.v-p.stop{{color:var(--ink);border:1px dashed {w['redDeep']};border-radius:12px;padding:10px 14px}}
.cap-note{{font-size:12px;color:var(--gold);margin-top:10px}}
.v-col .tag.ask{{display:inline-block;margin-top:10px}}

/* ---- page 5 ---- */
.studio{{display:grid;grid-template-columns:1fr 1fr;gap:36px;align-items:start;margin-block:auto;
  width:100%}}
.studio-half{{display:flex;flex-direction:column;align-items:center;gap:16px}}
.studio-half.placeholder{{justify-content:center;min-height:60vh;color:var(--soft);gap:18px}}
.poster{{max-height:66vh;max-width:100%;border-radius:16px;border:1px solid var(--line);
  box-shadow:0 20px 60px rgba(0,0,0,.45)}}
.phone{{width:min(320px,26vw);aspect-ratio:9/16;background:#000;border-radius:34px;
  border:3px solid #2A3550;padding:10px;box-shadow:0 20px 60px rgba(0,0,0,.5);position:relative}}
.phone::before{{content:"";position:absolute;top:14px;left:50%;transform:translateX(-50%);
  width:84px;height:16px;border-radius:999px;background:#10182B;z-index:2}}
.phone video{{width:100%;height:100%;object-fit:cover;border-radius:24px}}
.phone.empty{{display:flex;align-items:center;justify-content:center}}
figcaption{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;justify-content:center}}
.btn{{font-family:var(--ar);display:inline-block;border:1px solid var(--line);background:{w['surface']};
  color:var(--ink);padding:10px 20px;border-radius:12px;font-size:13.5px;text-decoration:none;
  cursor:pointer;transition:transform .12s,border-color .12s}}
.btn:hover{{transform:translateY(-2px);border-color:var(--gold)}}
.btn.ghost{{background:transparent}}
.btn.gold{{background:linear-gradient(135deg,{w['gold']},{w['goldDeep']});color:{w['bg']};
  border:0;font-weight:800}}
.btn[disabled]{{opacity:.45;cursor:not-allowed}}
.belongs{{font-family:var(--mono);font-size:11px;color:{w['bg']};background:var(--lane);
  border-radius:999px;padding:5px 14px;font-weight:700}}

/* ---- page 6 ---- */
.ledger-banner{{display:flex;gap:18px;align-items:center;background:{w['ledgerBg']};
  color:{w['ledgerInk']};border-radius:18px;padding:26px 30px;margin-bottom:26px}}
.lb-check{{width:44px;height:44px;border-radius:50%;background:{w['ledgerInk']};color:{w['ledgerBg']};
  display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;flex:none}}
.lb-t{{font-size:17px;font-weight:800}}
.lb-s{{font-size:13px;margin-top:4px}}
.ledger-banner .num{{color:{w['ledgerInk']}}} .ledger-banner .unit{{color:{w['ledgerInk']};opacity:.7}}
.audit-body{{display:none;margin-top:20px}}
.audit-body.open{{display:block}}
.audit-scroll{{max-height:56vh;overflow:auto;border:1px solid var(--line);border-radius:14px}}
table.audit{{width:100%;border-collapse:collapse;font-size:12.5px}}
table.audit th{{position:sticky;top:0;background:{w['raise']};text-align:start;padding:12px 14px;
  font-size:11px;color:var(--soft);letter-spacing:.06em}}
table.audit td{{padding:10px 14px;border-top:1px solid var(--line);vertical-align:top}}
.au-head{{font-size:11px;color:var(--muted);white-space:nowrap}}
.au-token{{color:var(--gold);font-size:11.5px}}
.au-src a{{color:{w['laneBlue']};text-decoration:none}}
.au-quote{{color:var(--soft)}}
.ok{{color:{w['green']};font-weight:800}} .warn{{color:{w['gold']};font-weight:800}}
.au-safe{{color:var(--muted);font-size:11.5px}}
.rem-badge{{font-size:10px;color:{w['gold']};border:1px solid {w['gold']};border-radius:999px;
  padding:1px 8px;margin-inline-start:6px}}


/* ---- unified-package pages ---- */
.chip{{font-family:var(--mono);font-size:11px;color:var(--soft);border:1px solid var(--line);
  border-radius:999px;padding:2px 10px;display:inline-block}}
.market-grid{{display:grid;grid-template-columns:1.1fr 1fr;gap:26px;margin-block:auto;width:100%}}
.comp-col{{display:flex;flex-direction:column;gap:14px}}
.comp-card{{padding:18px 22px;min-height:0}}
.swot-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;width:100%}}
.quad{{background:#1C1832;border:1px solid var(--line);border-radius:16px;padding:16px 18px;
  border-inline-start:4px solid var(--q)}}
.q-t{{font-weight:800;font-size:14px;margin-bottom:10px;color:var(--q)}}
.quad ul{{list-style:none;font-size:12.5px;color:var(--soft)}}
.quad li{{padding:5px 0;border-bottom:1px dashed var(--line)}}
.quad li:last-child{{border-bottom:0}}
.swot-lower{{display:grid;grid-template-columns:1fr 1fr;gap:26px;margin-top:22px;width:100%}}
.strat-d{{font-size:12px;color:var(--muted);margin-top:3px}}
.svc-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:26px}}
.svc-chip{{background:#1C1832;border:1px solid var(--line);border-radius:14px;padding:12px 16px;
  cursor:pointer;transition:transform .12s,border-color .12s;min-width:0}}
.svc-chip:hover{{transform:translateY(-2px);border-color:var(--gold)}}
.svc-chip.sel{{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold)}}
.svc-n{{font-size:13px;font-weight:700;overflow-wrap:anywhere;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;unicode-bidi:plaintext}}
.svc-p{{font-size:11px;color:var(--soft);margin-top:3px;white-space:nowrap}}
.svc-actions{{max-width:860px}}
.svc-row{{display:flex;gap:12px;align-items:center;margin-block:12px;flex-wrap:wrap}}
.svc-input{{flex:1;min-width:280px;font-family:var(--mono);font-size:13px;background:#100E1A;
  border:1px solid var(--line);border-radius:12px;padding:11px 14px;color:var(--ink)}}
.svc-sel{{font-size:12.5px;color:var(--gold)}}
.hitl-panel{{display:none;background:#252041;border:1px solid var(--line);border-radius:14px;
  padding:16px;margin-block:12px}}
.hitl-panel.open{{display:block}}
.hitl-brief{{white-space:pre-wrap;font-size:12.5px;color:var(--soft);max-height:34vh;
  overflow:auto;margin-block:10px;font-family:var(--ar)}}
.svc-log{{font-size:12px;color:var(--gold);min-height:18px}}
.oi-l{{display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--soft)}}
.oi{{min-width:150px;flex:0 1 170px}}
.owner-inputs{{margin-top:20px}}
.br-l{{padding:6px 14px;font-size:11.5px}}
.st-sel{{background:#100E1A;color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-family:var(--ar);font-size:13px}}
.studio-controls{{margin-top:18px}}
.quad-why{{color:#c9b8e6;font-size:12px;line-height:1.6;list-style:none;margin-inline-start:-18px}}
.readiness-badge{{background:#4a3a18;border:1px solid #7a5f22;color:#f0d69a;border-radius:10px;
  padding:8px 13px;font-size:12.5px;margin-bottom:12px}}
.evidence-notes{{margin-top:18px}}
.ev-notes li{{font-size:12.5px;color:var(--soft);line-height:1.7;margin-bottom:6px}}
.stale-chip{{background:#4a3a18;border:1px solid #7a5f22;color:#f0d69a;border-radius:10px;
  padding:7px 12px;font-size:12px;margin-bottom:10px;line-height:1.5}}
.svc-poster{{max-height:40vh;border-radius:12px;border:1px solid var(--line);margin-top:10px}}
.plan-tags{{justify-content:flex-start;margin-bottom:18px}}
.posture{{margin-top:26px;text-align:center}}
.posture .chip{{font-size:12.5px;color:var(--gold);border-color:var(--gold)}}
.move{{margin-top:12px;font-size:15px;color:var(--soft);max-width:760px;margin-inline:auto}}
.counts{{margin-top:14px}}
.cal-scroll{{max-height:62vh}}
table.cal td{{font-size:12.5px}}
.cal-topic{{max-width:420px}}
.honesty{{margin-top:22px}}

/* 1.7 brand-summary card — compact strip under the hero */
.brand-summary{{margin-top:24px;background:#1C1832;border:1px solid var(--line);
  border-radius:16px;padding:16px 22px;max-width:900px;margin-inline:auto;text-align:start}}
.bs-t{{font-size:12px;color:var(--muted);letter-spacing:.12em;margin-bottom:8px}}
.brand-summary p{{font-size:13.5px;color:var(--soft);line-height:1.8;margin-bottom:10px}}
.bs-chips{{display:flex;flex-wrap:wrap;gap:8px}}

/* 1.4 bidi (owner report): mixed Arabic/English runs resolve per-paragraph — no collisions */
.ns-text,.mi-t,.mi-q,.strat-d,.quad li,.hitl-brief,.svc-log,.cal-topic,.move,.brand-summary p{{unicode-bidi:plaintext}}
.fact-chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.counter-basis{{margin-top:8px}}
.counter-basis .chip{{color:var(--gold);border-color:var(--gold)}}
@media(max-width:1280px){{
  .page-inner{{padding:32px 92px 32px 28px}}
  .intel-grid{{grid-template-columns:repeat(3,1fr);gap:16px}} .intel{{min-height:0}}
  .lane-grid{{grid-template-columns:1fr 1fr}} .studio{{grid-template-columns:1fr}}
  .vault{{grid-template-columns:1fr}} .v-col.kill{{border-inline:0;border-block:1px solid var(--line)}}
  .north-star{{width:100%}}}}
@media(max-width:900px){{
  .intel-grid{{grid-template-columns:1fr}}
}}
@media(prefers-reduced-motion:reduce){{
  *{{animation:none!important;transition:none!important}}}}
</style>"""


def _js() -> str:
    return """<script>
(function(){
  var pages=Array.prototype.slice.call(document.querySelectorAll('.page'));
  var dots=Array.prototype.slice.call(document.querySelectorAll('.rail .dot'));
  var N=pages.length,cur=0;
  function go(n){n=Math.max(0,Math.min(N-1,n));if(n===cur&&pages[cur].classList.contains('on'))return;
    pages[cur].classList.remove('on');if(dots[cur])dots[cur].classList.remove('act');
    cur=n;pages[cur].classList.add('on');if(dots[cur])dots[cur].classList.add('act');
    var c=document.getElementById('pgno');if(c)c.textContent=(cur+1)+' / '+N;
    if(history.replaceState)history.replaceState(null,'','#p'+(cur+1));}
  dots.forEach(function(d,i){d.addEventListener('click',function(){go(i);});});
  document.addEventListener('click',function(e){
    var a=e.target.closest?e.target.closest('a[href^="#p"]'):null;if(!a)return;
    e.preventDefault();go(parseInt(a.getAttribute('href').slice(2),10)-1);
    if(a.hasAttribute('data-open-audit')){var t=document.getElementById('audit-body');
      if(t)t.classList.add('open');}});
  document.addEventListener('keydown',function(e){
    var tg=e.target.tagName;if(tg==='INPUT'||tg==='TEXTAREA'||tg==='VIDEO')return;
    var k=e.key;
    if(k==='ArrowDown'||k==='PageDown'||k===' '){e.preventDefault();go(cur+1);}
    else if(k==='ArrowUp'||k==='PageUp'){e.preventDefault();go(cur-1);}
    else if(k==='ArrowLeft'){e.preventDefault();go(cur+1);}      /* RTL: forward reads leftward */
    else if(k==='ArrowRight'){e.preventDefault();go(cur-1);}
    else if(k==='Home'){go(0);}else if(k==='End'){go(N-1);}
    else if(k>='1'&&k<='9'){go(parseInt(k,10)-1);}else if(k==='0'){go(9);}});
  /* wheel paging — hardened: an ACCUMULATED delta threshold (trackpad inertia / synthetic
     scrolls must not flip several pages), a 900ms cooldown, and a startup grace period. */
  var lastFlip=0,acc=0,born=Date.now();
  document.addEventListener('wheel',function(e){
    var now=Date.now();
    if(now-born<800)return;                          /* ignore load-time synthetic scrolls */
    var inner=pages[cur].querySelector('.page-inner');
    if(inner){var down=e.deltaY>0;
      var atTop=inner.scrollTop<=0;
      var atBot=inner.scrollTop+inner.clientHeight>=inner.scrollHeight-1;
      if((down&&!atBot)||(!down&&!atTop)){acc=0;return;}}  /* internal overflow scroll wins */
    if(now-lastFlip<900){acc=0;return;}
    acc+=e.deltaY;
    if(Math.abs(acc)<120)return;                     /* one deliberate notch, not drift */
    lastFlip=now;var dir=acc>0?1:-1;acc=0;go(cur+dir);
  },{passive:true});
  var ab=document.getElementById('audit-toggle');
  if(ab&&!ab.disabled)ab.addEventListener('click',function(){
    var t=document.getElementById('audit-body');if(t)t.classList.toggle('open');});
  Array.prototype.forEach.call(document.querySelectorAll('[data-copy]'),function(b){
    b.addEventListener('click',function(){
      var txt=b.getAttribute('data-copy');
      if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt);
      b.classList.add('copied');setTimeout(function(){b.classList.remove('copied');},1200);});});
  var xb=document.getElementById('bundle-btn');
  if(xb)xb.addEventListener('click',function(){
    var p=document.getElementById('bundle-panel');if(p)p.classList.toggle('open');});
  /* wire download hrefs + the cell thumbnail from the ONE embedded media copy */
  Array.prototype.forEach.call(document.querySelectorAll('[data-dl]'),function(a){
    var el=document.getElementById(a.getAttribute('data-dl'));
    if(el)a.href=el.currentSrc||el.src;});
  Array.prototype.forEach.call(document.querySelectorAll('[data-src-from]'),function(img){
    var el=document.getElementById(img.getAttribute('data-src-from'));
    if(el)img.src=el.currentSrc||el.src;});
  var h=location.hash.match(/^#p(\d+)$/);go(h?parseInt(h[1],10)-1:0);
})();
</script>"""


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------
def build_war_room_html(
    result_path: str, *,
    profile_path: Optional[str] = None,
    media_plan_path: Optional[str] = None,
    audit_path: Optional[str] = None,
    poster_path: Optional[str] = None,
    reel_path: Optional[str] = None,
    plan_path: Optional[str] = None,
    embed_media: bool = True,
    asset_base: Optional[str] = None,
    fetch_logo: bool = True,
    slug: str = "",
    out_dir: Optional[str] = None,
    serve_mode: bool = False,
    services: Optional[list] = None,
) -> str:
    """The full War Room document as a string. Deterministic over the on-disk artifacts; the only
    (best-effort) network touch is the brand-logo fetch, disabled via fetch_logo=False."""
    result = _load(result_path)
    prof_doc = _load(profile_path)
    profile = result.get("profile") or prof_doc.get("profile", prof_doc) or {}
    swot = result.get("swot") or {}
    mp = result.get("media_plan") or _load(media_plan_path) or {}
    from dashboard.owner_inputs import load_owner_inputs
    owner_inputs = load_owner_inputs(slug, out_dir=out_dir or "outputs")
    # Her saved budget takes effect NOW, not at the next re-analysis (deterministic, no spend).
    mp = _apply_saved_budget(mp, owner_inputs)
    v2 = _ensure_v2(profile, mp, swot, result.get("marketing_intel"),
                    ad_presence=result.get("ad_presence"),
                    competitor_intel=result.get("competitor_intel"),
                    owner_inputs=owner_inputs)
    audit = _load(audit_path)
    plan = _load(plan_path)
    out_dir = out_dir or str(Path(result_path).parent)

    # media sources: embedded data URIs (portable file) or served/relative URLs
    def _src(path: Optional[str], kind: str) -> Optional[str]:
        if not path or not Path(path).is_file():
            return None
        if embed_media:
            return _data_uri(path)
        if asset_base:
            return f"{asset_base}{kind}"
        # --no-embed CLI build: the file sits in outputs/, media in outputs/posters|reels/
        sub, ext = ("posters", "png") if kind == "poster" else ("reels", "mp4")
        return f"{sub}/{slug}_{kind}.{ext}"

    poster_src = _src(poster_path, "poster")
    reel_src = _src(reel_path, "reel")

    logo_uri = None
    logo_url = _fv(profile, "visual", "logo_url")
    if fetch_logo and logo_url:
        try:
            from reel.textlayer import _logo_data_uri
            logo_uri = _logo_data_uri(logo_url)
        except Exception:  # noqa: BLE001
            logo_uri = None

    name = _fv(profile, "name") or slug
    bundle = _bundle_state(out_dir, slug, name)
    generated_at = str(result.get("generated_at") or "")

    objs = (mp or {}).get("objectives") or []
    mp_obj = objs[0] if objs and isinstance(objs[0], dict) else {}
    tb = _plan_test_budget(mp)
    rc = v2.get("reality_check") or {}
    currency = _deck_currency(rc)

    titles = ["القيادة", "السوق", "SWOT", "الذكاء", "الخلايا", "الخزنة",
              "الخدمات", "الاستوديو", "التقويم", "الذمة"]
    dots = "".join(
        f'<button class="dot{" act" if i == 0 else ""}" aria-label="{_esc(t)}">'
        f'<span class="dlbl">{i + 1} · {_esc(t)}</span></button>'
        for i, t in enumerate(titles))
    rail = (f'<nav class="rail">{dots}'
            f'<span class="rail-hint">← التالي · السابق →</span></nav>'
            f'<div class="pgno num" id="pgno">1 / {len(titles)}</div>')

    counts = {"خدمات": len(services or []), "منافسون": len(result.get("competitors") or []),
              "عناصر التقويم": len((plan or {}).get("items") or []),
              "إشارات سوق": len(result.get("trends") or [])}
    pages = (
        _page_command(profile, v2, mp, bundle, slug=slug, logo_uri=logo_uri,
                      generated_at=generated_at, tows=result.get("tows") or {}, counts=counts,
                      maturity=result.get("brand_maturity"))
        + _page_market(result, slug=slug, out_dir=out_dir or "outputs")
        + _page_swot(result, profile=profile)
        + _page_intel(v2, result)
        + _page_cells(v2, has_poster=bool(poster_src), has_reel=bool(reel_src),
                      test_budget=tb if isinstance(tb, (int, float)) else None, currency=currency)
        + _page_vault(v2, serve_mode=serve_mode, owner_inputs=owner_inputs)
        + _page_services(services or [], slug=slug, serve_mode=serve_mode)
        + _page_studio(v2, poster_src=poster_src, reel_src=reel_src, slug=slug,
                       has_audit=bool(audit), status_note=_poster_status_note(slug, out_dir),
                       serve_mode=serve_mode,
                       poster_stale=_staleness_note(
                           Path(out_dir or "outputs") / "posters" / f"{slug}_poster.png",
                           Path(out_dir or "outputs") / f"{slug}_result.json"),
                       reel_stale=_staleness_note(
                           Path(out_dir or "outputs") / "reels" / f"{slug}_reel.mp4",
                           Path(out_dir or "outputs") / f"{slug}_result.json"))
        + _page_plan_calendar(mp, plan, profile)
        + _page_ledger(audit, result, mp, profile)
    )
    # mark page 1 active initially (JS re-syncs from the hash)
    pages = pages.replace('class="page" id="p1"', 'class="page on" id="p1"', 1)

    return (
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>غرفة العمليات · {_esc(name)}</title>{_css()}</head>"
        f'<body>{rail}<main class="deck">{pages}</main>{_js()}'
        f'{_services_js(slug) if serve_mode else ""}</body></html>'
    )


def build_war_room(slug: str, out_dir: str = "outputs", *, embed_media: bool = True,
                   fetch_logo: bool = True) -> Path:
    """Resolve every artifact path for `slug` and write outputs/<slug>_war_room.html."""
    from dashboard.run import paths
    P = paths(slug, out_dir)
    out = Path(out_dir)
    reel_p = P["reel"]
    if embed_media and reel_p.is_file() and reel_p.stat().st_size > 20 * 1024 * 1024:
        print(f"[war_room] الريل {reel_p.stat().st_size / 1e6:.0f}MB — الملف هيبقى كبير؛ "
              "فكري في --no-embed أو الرابط الحي /war_room", file=sys.stderr)
    try:
        from dashboard.products import products_for_slug
        services = products_for_slug(slug, out_dir=out_dir)
    except Exception:  # noqa: BLE001 — no scrape dir yet -> honest empty services page
        services = []
    html = build_war_room_html(
        str(P["result"]),
        profile_path=str(P["profile"]) if P["profile"].is_file() else None,
        media_plan_path=str(out / f"{slug}_media_plan.json"),
        audit_path=str(out / f"{slug}_plan.audit.json"),
        poster_path=str(P["poster"]) if P["poster"].is_file() else None,
        reel_path=str(reel_p) if reel_p.is_file() else None,
        plan_path=str(P["plan"]) if P["plan"].is_file() else None,
        embed_media=embed_media, fetch_logo=fetch_logo, slug=slug, out_dir=out_dir,
        serve_mode=False, services=services,
    )
    dst = out / f"{slug}_war_room.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html, encoding="utf-8")
    return dst


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build the War Room dashboard for a brand slug.")
    ap.add_argument("slug")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--no-embed", action="store_true",
                    help="reference posters/·reels/ relative paths instead of base64 embed")
    ap.add_argument("--no-logo", action="store_true", help="skip the network logo fetch")
    args = ap.parse_args()
    dst = build_war_room(args.slug, args.out, embed_media=not args.no_embed,
                         fetch_logo=not args.no_logo)
    print(f"[ok] war room -> {dst.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
