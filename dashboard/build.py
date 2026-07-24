"""Build the Baseera brand-analysis dashboard as ONE self-contained HTML page.

Self-contained by design: inline CSS, inline SVG, base64 images, a system-font stack (elegant
serif display + clean sans body) — NO external CDN or web fonts — so it opens anywhere and can
be shared/published as-is. Reads whatever artifacts exist and degrades gracefully for the rest.
"""
from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# --- Baseera palette (from the design system) ------------------------------------------------
_C = {
    "bg": "#FAF5F1", "surface": "#FFFFFF", "surface2": "#FBF6F2", "ink": "#2A1A1F",
    "inkSoft": "#5C4A52", "muted": "#9B8A92", "line": "rgba(168,69,107,0.12)",
    "blush50": "#FBF0F3", "blush100": "#F6E0E7", "blush200": "#ECCBD6", "blush400": "#C97E96",
    "blush500": "#B85C7A", "blush600": "#9C4564", "blush700": "#7E3450",
    "sand": "#B89873", "sand100": "#F0E4D3", "sage": "#5E9479", "sage100": "#D9E8DF",
    "sage700": "#3F6E55", "teal": "#3F8F8C", "teal100": "#CFE5E2", "gold": "#C68A3C",
    "gold100": "#F5E6C8", "gold700": "#8A5E20", "ok": "#3F8F6B", "warn": "#C68A3C", "bad": "#B54848",
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _safe_href(url: Any) -> str:
    """CS-16/23: html.escape does NOT stop scheme-based XSS. A competitor/SERP URL is attacker-
    influenceable (it comes from a discovered site / a SERP result), so an `href` must only carry a
    safe scheme. Returns an escaped http(s)/mailto/tel/relative URL, else '#'."""
    u = str(url or "").strip()
    if not u:
        return "#"
    low = u.lower().lstrip()
    # explicit scheme -> must be on the allow-list; scheme-relative (//) and dangerous schemes drop
    if ":" in low.split("/", 1)[0]:
        if not low.startswith(("http://", "https://", "mailto:", "tel:")):
            return "#"
    elif low.startswith("//"):
        return "#"
    return _esc(u)


def _plain(v: Any) -> str:
    """CS-17: render a plain value, never a Python repr. Unwraps enums (`.value`) and evidenced
    fields, and drops anything that still looks like a `ClassName(...)`/`Enum.MEMBER` repr."""
    v = _val(v)
    val = getattr(v, "value", v)           # Enum / EvidencedField -> its value
    s = str(val if val is not None else "").strip()
    if _REPR_LEAK.match(s):                # e.g. "AudienceType.MIXED", "EvidencedField(value=..."
        return ""
    return s


_REPR_LEAK = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*[.(]")


def _val(x: Any) -> Any:
    """Unwrap an EvidencedField-shaped {'value': ...} (possibly nested)."""
    if isinstance(x, dict) and "value" in x:
        return _val(x["value"])
    return x


def _load(path: Optional[str]) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _data_uri(path: Optional[str]) -> Optional[str]:
    if not path or not Path(path).is_file():
        return None
    try:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


# --- small view helpers ----------------------------------------------------------------------
def _kpi(icon: str, value: str, label: str, sub: str, accent: str) -> str:
    return f"""<div class="kpi" style="--a:{accent}">
      <div class="kpi-top"><span class="kpi-ic">{icon}</span></div>
      <div class="kpi-v">{_esc(value)}</div>
      <div class="kpi-l">{_esc(label)}</div>
      <div class="kpi-s">{_esc(sub)}</div>
    </div>"""


_STRENGTH_BADGE = {
    "validated": ("ok", "Validated"), "directional_not_validated": ("warn", "Directional"),
    "internally_supported": ("warn", "Internal"),
}


_CITE_L = {
    "value_propositions": "قيمك المعلنة · your value props",
    "trust_signals": "شارات الثقة · your trust badges",
    "your profile": "ملفك · your profile",
    "your scraped site vs peers": "موقعك مقابل المنافسين · your site vs peers",
    "readiness audit": "فحص جاهزية الموقع · site-readiness check",
    "own-brand reviews": "آراء عملائك · your customers' reviews",
    "competitor reviews": "آراء عملاء المنافسين · competitors' reviews",
}


def _clean_citation(c: str) -> str:
    """RUBRIC G — turn an internal citation token into a client-safe label. snake_case field
    names, dotted paths, and raw URLs become plain words or a source domain."""
    s = str(c or "").strip()
    if s in _CITE_L:
        return _CITE_L[s]
    if s.startswith(("http://", "https://")):
        dom = re.sub(r"^https?://(www\.)?", "", s).split("/")[0]
        return f"مصدر خارجي · web: {dom}"
    if re.fullmatch(r"[a-z_]+(\.[a-z_]+)*(=\w+)?", s):   # bare field path / flag -> drop the jargon
        return _CITE_L.get(s.split(".")[0], s.split(".")[0].replace("_", " "))
    return s


def _swot_item(it: dict) -> str:
    text = _esc(it.get("text") or it.get("evidence") or "")
    cites = it.get("citation") or []
    cite_html = " · ".join(_esc(_clean_citation(c)) for c in cites) if cites else "—"
    cls, lab = _STRENGTH_BADGE.get(str(it.get("claim_strength") or ""), ("neutral", str(it.get("claim_strength") or "")))
    badge = f'<span class="cs cs-{cls}">{_esc(lab)}</span>' if lab else ""
    return f"""<li class="swot-item"><div class="swot-t">{text} {badge}</div>
      <div class="swot-c">↳ {cite_html}</div></li>"""


def _swot_quad(title: str, icon: str, items: list, kind: str) -> str:
    body = "".join(_swot_item(it) for it in (items or [])) or '<li class="swot-empty">—</li>'
    return f"""<div class="quad quad-{kind}">
      <div class="quad-h"><span class="quad-ic">{icon}</span>{_esc(title)}
        <span class="quad-n">{len(items or [])}</span></div>
      <ul class="swot-list">{body}</ul></div>"""


def _tier_reasons(breakdown: dict) -> str:
    """T-TIER — WHY this competitor matched, in plain bilingual chips (surfaces the already-
    computed PeerFitBreakdown sub-scores). A high sub-score => a real reason the owner reads:
    same field / same price band / similar size / nearby. Empty for the web-search path
    (all sub-scores null) — that honesty is handled by the caller's fallback reason."""
    if not isinstance(breakdown, dict):
        return ""
    labels = {
        "sub_vertical": ("نفس المجال", "same field"),
        "audience_tier": ("نفس الشريحة السعرية", "same price band"),
        "size_similarity": ("حجم مماثل", "similar size"),
        "proximity": ("قريب منك", "nearby"),
        "language": ("نفس اللغة", "same language"),
    }
    chips = []
    for k, (ar, en) in labels.items():
        v = breakdown.get(k)
        if isinstance(v, (int, float)) and v >= 0.6:      # a genuine match on this dimension
            chips.append(f'<span class="cmr">{_esc(ar)} · {_esc(en)}</span>')
    return f'<div class="comp-reasons">{"".join(chips)}</div>' if chips else ""


def _competitor_card(comp: dict) -> str:
    sel = comp.get("selection") or {}
    cand = comp.get("candidate") or {}
    name = _esc(sel.get("name") or cand.get("name") or "—")
    site = sel.get("website") or cand.get("website") or ""
    # CS-16/23: drop the link ENTIRELY for an unsafe scheme (don't even show it as text) — a
    # javascript:/data: "URL" is never a real competitor site.
    _safe_site = _safe_href(site)
    site_h = (f'<a href="{_safe_site}" class="comp-site">{_esc(site.replace("https://", "").strip("/"))}</a>'
              if site and _safe_site != "#" else "")
    # CS-09/100: `peer_fit_score` is a real weighted fit ONLY when `basis == "weighted"` (Places
    # data). For a web peer it's 1/SERP-rank (competitor/models.py) — NOT a measured fit — so
    # rendering it as "N% · peer fit" misleads. Show the honest rank instead.
    fit = sel.get("peer_fit_score")
    basis = str(sel.get("basis") or "").strip()
    if basis == "search_rank":
        rank = None
        try:
            rank = round(1.0 / float(fit)) if fit else None
        except (TypeError, ZeroDivisionError, ValueError):
            rank = None
        fit_val = f"#{rank}" if rank else "—"
        fit_label = "نتيجة بحث · search result"
    else:
        fit_pct = f"{round(float(fit) * 100)}%" if isinstance(fit, (int, float)) else "—"
        fit_val = fit_pct
        fit_label = "تطابق · peer fit"
    reasons = _tier_reasons(sel.get("breakdown") or {})
    # WHY matched: prefer the plain tier chips (Places path); else the honest search reason.
    why = "" if reasons else _esc(sel.get("why_selected") or "")
    tags = []
    if comp.get("is_local"):
        tags.append('<span class="tag tag-sage">محلي · local</span>')
    if comp.get("has_scrapable_site"):
        tags.append('<span class="tag tag-teal">موقع · site</span>')
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    return f"""<div class="comp">
      <div class="comp-logo">{_esc(initials)}</div>
      <div class="comp-body"><div class="comp-name">{name} {"".join(tags)}</div>
        {site_h}{reasons}<div class="comp-why">{why}</div></div>
      <div class="comp-fit"><div class="comp-fit-v">{fit_val}</div><div class="comp-fit-l">{fit_label}</div></div>
    </div>"""


_PLAT_ICON = {"instagram": "◎", "tiktok": "♪", "linkedin": "in", "facebook": "f", "youtube": "▶"}


def _seasons_ok_for(prof: dict) -> bool:
    """P-02 (D-8): the marketing-season calendar (`trends/seasons.py`) is Egypt-only — the single
    human-ratified table (§4 RULED). Show seasons ONLY for an Egyptian brand; for any other country
    the ratified calendar is EMPTY, so we omit the season UI rather than ship Egypt's seasons (Gulf
    summer INVERTS Egypt's, so 'plan for the summer peak' is exactly backwards there)."""
    try:
        from poster.locale import country_of
        return country_of(prof or {}) == "Egypt"
    except Exception:  # noqa: BLE001
        return False


def _calendar_row(it: dict, season_ok: bool = False) -> str:
    plat = str(it.get("platform") or "")
    ctype = str(it.get("content_type") or "").replace("_", " ")   # static_post -> static post
    topic = _esc(it.get("topic") or "")
    hook = _esc(it.get("hook") or it.get("angle") or "")
    raw_date = str(it.get("date") or "")
    try:                                              # 2026-07-05 -> Sun 05 Jul (raw on failure)
        date = _esc(datetime.strptime(raw_date, "%Y-%m-%d").strftime("%a %d %b"))
    except Exception:  # noqa: BLE001
        date = _esc(raw_date)
    ic = _PLAT_ICON.get(plat, "•")
    hook_html = f'<div class="cal-hook">“{hook}”</div>' if hook else ""   # no empty quotes
    # ROUND-2 A — per-item trend/season attribution: when the post date falls in an Egyptian
    # marketing season, say so ("هذا المحتوى قرب رمضان"). Deterministic from the date.
    season_tag = ""
    if season_ok:                                     # P-02: Egyptian brands only
        try:
            from trends.seasons import season_for_date
            sea = season_for_date(raw_date)
            if sea:
                season_tag = (f'<span class="cal-season">🗓️ قرب {_esc(sea["ar"])} · '
                              f'near {_esc(sea["en"])}</span>')
        except Exception:  # noqa: BLE001
            season_tag = ""
    return f"""<div class="cal-row">
      <div class="cal-date">{date}</div>
      <div class="cal-plat"><span class="cal-ic">{ic}</span>{_esc(plat)} · {_esc(ctype)}</div>
      <div class="cal-body"><div class="cal-topic">{topic}{season_tag}</div>{hook_html}</div>
    </div>"""


_OBJ_L = {"OUTCOME_SALES": "مبيعات · Sales",
          "OUTCOME_LEADS": "عملاء محتملون · Leads",
          "OUTCOME_TRAFFIC": "زيارات · Traffic",
          "OUTCOME_AWARENESS": "وعي · Awareness",
          "OUTCOME_ENGAGEMENT": "تفاعل · Engagement",
          "OUTCOME_APP_PROMOTION": "تطبيق · App"}
_DEST_L = {"online_store": "المتجر الإلكتروني", "lead_form": "نموذج تواصل",
           "whatsapp": "واتساب", "phone_call": "مكالمة هاتفية", "messenger": "ماسنجر",
           "app_store": "متجر التطبيقات", "website": "الموقع"}
_FUN_L = {"tofu": "وعي · TOFU", "mofu": "تفكير · MOFU", "bofu": "تحويل · BOFU"}


_POSTURE = {
    "leverage": ("وضعك التنافسي قوي — هاجم بنقاط قوتك",
                 "Strong position — press your advantages"),
    "defense": ("وضع دفاعي — حصّن نفسك ضد التهديدات",
                "Defensive — shield against the threats"),
    "improvement": ("تحتاج تطوير — عالج نقاط ضعفك أولاً",
                    "Needs work — fix the weaknesses first"),
    "contingency": ("وضع حسّاس — تحرّك بحذر وخطوة بخطوة",
                    "Contingency — move carefully, step by step"),
    "balanced": ("وضع متوازن — نمِّ قوتك واحتَرِس",
                 "Balanced — grow strengths, watch threats"),
}
_HORIZON = {"now": ("ابدأ الآن", "start now"), "next": ("الخطوة التالية", "next"),
            "later": ("لاحقاً", "later")}

# The TOWS priority-action `action` is stored as "{Type}: {swot text}" — a LABEL, not an
# instruction (and often truncated with …). For the first-screen "recommended move" that reads as
# a bare description ("A one-stop digital store…", "No online booking path…"). Reframe the type as
# an imperative verb so it reads as a MOVE, and recover the full body from the rationale.
_ACTION_VERB = {
    "leverage": "وظّف نقطة قوتك", "fix": "عالِج", "address": "عالِج",
    "defend": "حصِّن ضد", "exploit": "اغتنم الفرصة",
}


def _imperative_move(action: dict) -> str:
    """The rank-1 priority action rendered as an actual INSTRUCTION, not a raw strength/weakness.
    Reframes the "{Type}:" label into an imperative verb and recovers the full, un-truncated body
    from the action's rationale when the stored action text was clipped (…)."""
    raw = str(action.get("action") or "").strip()
    m = re.match(r"^(Leverage|Fix|Address|Defend|Exploit)\s*:\s*(.*)$", raw, re.I)
    if not m:
        return raw
    body = m.group(2).strip()
    if body.endswith("…") or body.endswith("..."):
        # "Use your strength — {full body} — to pursue the opportunity: …" -> recover {full body}
        rm = re.search(r"—\s*(.+?)\s*—", str(action.get("rationale") or ""))
        if rm and rm.group(1).strip():
            body = rm.group(1).strip()
    verb = _ACTION_VERB.get(m.group(1).lower(), "")
    return f"{verb}: {body}" if verb else body


def _one_sentence(text: str) -> str:
    """The first clean sentence of a description — the 'what they do' line."""
    t = str(text or "").strip().replace("\n", " ")
    for sep in (". ", "؟ ", "! ", "؛ "):
        if sep in t:
            t = t.split(sep)[0]
            break
    return t[:200]


def _executive_summary(name: str, category: str, prof: dict, swot: dict, tows: dict,
                       n_competitors: int, mp_obj: dict) -> str:
    """RUBRIC A — the first-screen story: in 10 seconds, WHO the brand is, its MARKET POSITION,
    and THE one recommended move. Pure composition from already-computed intelligence."""
    desc = _one_sentence(_val(prof.get("description")))
    strengths = swot.get("strengths") or []
    weaknesses = swot.get("weaknesses") or []
    posture = str(tows.get("posture") or "").lower()
    pos_ar, pos_en = _POSTURE.get(posture, ("", ""))
    # THE one move: the rank-1 priority action, its "Leverage:/Fix:" prefix stripped to a verb.
    actions = tows.get("priority_actions") or []
    move, hz = "", ""
    if actions:
        a0 = actions[0]
        move = _imperative_move(a0)
        hz = str(a0.get("horizon") or "now").lower()
    obj_ar = _OBJ_L.get(str(mp_obj.get("objective") or ""), "").split(" · ")[0]

    who = f"{_esc(name)}"
    if category:
        who += f' <span class="ex-cat">{_esc(category)}</span>'
    pos_line = (f'<div class="ex-pos"><b>{_esc(pos_ar)}</b><span>{_esc(pos_en)}</span></div>'
                if pos_ar else "")
    stat = (f'<span class="ex-stat"><b>{len(strengths)}</b> نقطة قوة · strengths</span>'
            f'<span class="ex-stat"><b>{len(weaknesses)}</b> نقطة ضعف · weaknesses</span>'
            f'<span class="ex-stat"><b>{n_competitors}</b> منافس · competitors</span>')
    move_html = ""
    if move:
        hz_ar, hz_en = _HORIZON.get(hz, ("ابدأ الآن", "start now"))
        obj_line = (f' — والهدف: {_esc(obj_ar)}' if obj_ar else "")
        move_html = (f'<div class="ex-move"><div class="ex-move-h">✦ الخطوة المُوصى بها الآن '
                     f'· Your recommended first move</div>'
                     f'<div class="ex-move-b">{_esc(move)}{obj_line}</div>'
                     f'<span class="ex-move-hz">{_esc(hz_ar)} · {_esc(hz_en)}</span></div>')
    return f"""<div class="sec ex"><div class="ex-card">
      <div class="ex-eyebrow">الخلاصة التنفيذية · Executive summary</div>
      <div class="ex-who">{who}</div>
      {f'<p class="ex-desc">{_esc(desc)}</p>' if desc else ''}
      {pos_line}
      <div class="ex-stats">{stat}</div>
      {move_html}
    </div></div>"""


# Plain-language, bilingual explanations for the media-plan terms (the "cards I don't
# understand" fix). Every marketing term becomes a sentence a non-marketer reads.
_METRIC_L = {
    "cost_per_purchase": ("تكلفة البيعة الواحدة", "Cost per sale"),
    "cost_per_lead": ("تكلفة العميل المحتمل", "Cost per enquiry"),
    "cost_per_click": ("تكلفة النقرة", "Cost per click"),
    "cpm": ("تكلفة الألف ظهور", "Cost per 1,000 views"),
    "cost_per_engagement": ("تكلفة التفاعل", "Cost per interaction"),
    "cost_per_install": ("تكلفة تحميل التطبيق", "Cost per app install"),
    "cost_per_result": ("تكلفة النتيجة", "Cost per result"),
}
_FUNNEL_DOES = {
    "tofu": ("نعرّف البراند لناس جديدة لسه ماتعرفوش",
             "Introduce the brand to new people who don't know you yet"),
    "mofu": ("نقنع الناس المهتمة تفكّر فيك جدياً",
             "Convince interested people to seriously consider you"),
    "bofu": ("نحوّل الناس المستعدة للشراء لعملاء فعليين",
             "Convert ready-to-buy people into actual customers"),
}
_OBJ_GOAL = {
    "OUTCOME_SALES": ("بيع منتجاتك أونلاين", "sell your products online"),
    "OUTCOME_LEADS": ("جمع بيانات عملاء مهتمّين للتواصل معاهم",
                      "collect details of interested customers to follow up"),
    "OUTCOME_TRAFFIC": ("جلب زوّار لموقعك", "bring visitors to your site"),
    "OUTCOME_AWARENESS": ("تعريف أكبر عدد بالبراند", "make the most people aware of the brand"),
    "OUTCOME_ENGAGEMENT": ("زيادة تفاعل الناس مع محتواك", "grow people's interaction with your content"),
    "OUTCOME_APP_PROMOTION": ("زيادة تحميلات تطبيقك", "grow installs of your app"),
}
_CHANNEL_L = {"facebook": "فيسبوك · Facebook", "instagram": "إنستجرام · Instagram",
              "tiktok": "تيك توك · TikTok", "youtube": "يوتيوب · YouTube"}


def _tip(ar: str, en: str, label: str) -> str:
    """A term with a (?) native-tooltip explaining it in both languages (education layer)."""
    return (f'{_esc(label)}<span class="tip" title="{_esc(ar)}  ·  {_esc(en)}">؟</span>')


def _explained_plan(mp: dict, category: str) -> str:
    """RUBRIC I/B — the media plan a non-marketer reads and can explain back. Every number is
    evidence-linked or honestly marked; no fabricated targets, ever. Pure composition +
    surfacing the already-computed channel split (media_plan.config.channel_weights)."""
    obj = (mp.get("objectives") or [{}])[0] if mp else {}
    if not obj.get("objective"):
        return ""
    o = str(obj.get("objective") or "")
    dest = str(obj.get("destination") or "")
    fun = str(obj.get("funnel_stage") or "").lower()
    kpi = obj.get("kpi_target") or {}
    geo = mp.get("base_geo") or {}
    conf = str(obj.get("confidence") or "").lower()

    # 1) OBJECTIVE as a plain sentence + why + the evidence receipt inline
    goal_ar, goal_en = _OBJ_GOAL.get(o, (_OBJ_L.get(o, o), o))
    dest_ar = _DEST_L.get(dest, dest)
    obj_sentence = (f'هدف حملتك: <b>{_esc(goal_ar)}</b> عن طريق {_esc(dest_ar)}.'
                    f'<span class="pl-en">Your campaign goal: {_esc(goal_en)} via {_esc(dest_ar)}.</span>')
    quote = ""
    for e in (obj.get("evidence") or []):
        for ev in (e.get("evidence") or []):
            if ev.get("quote"):
                quote = str(ev["quote"]); break
        if quote:
            break
    receipt = (f'<div class="pl-receipt">✓ الدليل من موقعك · Proof from your own site: '
               f'«{_esc(quote[:140])}»</div>' if quote
               else '<div class="pl-receipt pl-warn">⚠ للمراجعة — لم نجد دليلاً مباشراً '
                    'على الوجهة · needs review: no direct evidence for this destination</div>')
    conf_line = ""
    if conf:
        # P-28: this is the MODEL's own self-grade, not a measured confidence. Label it as such so
        # the client doesn't read a self-assessment as an evidenced probability.
        cl = {"high": ("تقدير الموديل: ثقة عالية", "model self-estimate: high"),
              "medium": ("تقدير الموديل: ثقة متوسطة", "model self-estimate: medium"),
              "low": ("تقدير الموديل: مبدئي", "model self-estimate: tentative")}.get(conf, ("", ""))
        if cl[0]:
            conf_line = f'<span class="pl-conf">◆ {_esc(cl[0])} · {_esc(cl[1])}</span>'

    # 2) FUNNEL stage — what it does, in words
    fdo_ar, fdo_en = _FUNNEL_DOES.get(fun, ("", ""))
    funnel_html = (f'<div class="pl-block"><div class="pl-h">{_tip("مرحلة رحلة العميل التي تركّز عليها الحملة", "the customer-journey stage this campaign targets", "مرحلة الحملة · Campaign stage")}</div>'
                   f'<div class="pl-funnel"><span class="pl-fstage on">{_esc(_FUN_L.get(fun, fun))}</span></div>'
                   f'<p class="pl-p">{_esc(fdo_ar)}. <span class="pl-en">{_esc(fdo_en)}.</span></p></div>'
                   if fdo_ar else "")

    # 3) BUDGET SPLIT — surface the category-tuned channel weights (was computed, never shown)
    weights = {}
    try:
        from media_plan.config import channel_weights
        weights = channel_weights(category)
    except Exception:  # noqa: BLE001
        weights = {}
    split_rows = ""
    for ch, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        pct = round(w * 100)
        if pct <= 0:
            continue
        split_rows += (f'<div class="pl-bar-row"><span class="pl-bar-lab">{_CHANNEL_L.get(ch, ch)}</span>'
                       f'<span class="pl-bar"><span class="pl-bar-fill" style="width:{pct}%"></span></span>'
                       f'<span class="pl-bar-pct">{pct}%</span></div>')
    budget_html = (f'<div class="pl-block"><div class="pl-h">{_tip("كيف توزّع ميزانيتك على المنصّات — مبنيّ على مجالك", "how to split your budget across platforms — tuned to your industry", "توزيع الميزانية على المنصّات · Where to put your budget")}</div>'
                   f'<div class="pl-split">{split_rows}</div>'
                   f'<p class="pl-note">النِّسَب مبنيّة على أفضل الممارسات لمجال «{_esc(category or "عام")}» — عدّليها حسب أداءك الفعلي. '
                   f'<span class="pl-en">Splits are best-practice for your industry — tune to real performance.</span></p></div>'
                   if split_rows else "")

    # 4) AUDIENCE — persona axes as human sentences, each evidence-tagged or honest-assumption
    persona = mp.get("base_persona") or {}
    aud_lines = []
    for axis, lab in (("age_range", "العمر · Age"), ("gender", "النوع · Gender"),
                      ("income_level", "الدخل · Income")):
        v = persona.get(axis)
        raw = (v.get("value") or v.get("claim")) if isinstance(v, dict) else v
        val = _plain(raw)                              # CS-17: never leak an enum/repr to the client
        if val:
            aud_lines.append((f"{lab}: {val}", isinstance(v, dict) and bool(v.get("evidence"))))
    for itx in (persona.get("interests") or [])[:4]:
        c = itx.get("claim") if isinstance(itx, dict) else str(itx)
        if c:
            grounded = isinstance(itx, dict) and bool(itx.get("evidence"))
            aud_lines.append((str(c).replace("the brand's audience:", "الجمهور:"), grounded))
    aud_rows = "".join(
        f'<li>{_esc(t)} <span class="pl-tag {"ev" if g else "as"}">'
        f'{"بالأدلة · evidenced" if g else "افتراض · assumption"}</span></li>' for t, g in aud_lines)
    audience_html = (f'<div class="pl-block"><div class="pl-h">{_tip("الناس اللي هنوجّه لهم الإعلانات", "the people we aim the ads at", "جمهورك المستهدف · Who to target")}</div>'
                     f'<ul class="pl-aud">{aud_rows}</ul></div>' if aud_rows
                     else f'<div class="pl-block"><div class="pl-h">جمهورك المستهدف · Who to target</div>'
                          '<p class="pl-p pl-warn">لم نستخلص شريحة مؤكدة بعد — تُبنى من بيانات الحملة الأولى. '
                          '<span class="pl-en">No evidenced audience yet — built from first-campaign data.</span></p></div>')

    # 5) KPI — the number we watch, with an HONEST calibrate-later line (never a fabricated target)
    metric = str(kpi.get("metric") or "")
    m_ar, m_en = _METRIC_L.get(metric, (metric, metric))
    tgt = kpi.get("target_value")
    win = kpi.get("window_days") or 7
    if tgt:
        # P-06 (D-8): no "EGP" fallback — an empty/unknown unit renders the number alone rather
        # than denominating a non-Egyptian brand's target in Egyptian Pounds.
        _unit = str(kpi.get("unit") or "").strip()
        kpi_val = f'<b>{_esc(str(tgt))}{(" " + _esc(_unit)) if _unit else ""}</b>'
    else:
        kpi_val = ('<span class="pl-cal">يُعاير بعد أول أسبوعين تشغيل · calibrated after the '
                   'first two weeks</span>')
    kpi_html = (f'<div class="pl-block"><div class="pl-h">{_tip("الرقم اللي هنقيس بيه نجاح الحملة", "the number we judge the campaign by", "مؤشر النجاح · The number we watch")}</div>'
                f'<p class="pl-p"><b>{_esc(m_ar)} · {_esc(m_en)}</b> — {kpi_val}<br>'
                f'<span class="pl-note">نقيسه على أول {win} أيام. لن نخترع رقماً مستهدفاً قبل رؤية بياناتك الحقيقية. '
                f'<span class="pl-en">Measured over the first {win} days; we never invent a target before your real data exists.</span></span></p></div>')

    # 6) KILL-SWITCH / learning floor — plain words
    kill_html = ('<div class="pl-block"><div class="pl-h">متى توقف إعلاناً · When to stop an ad</div>'
                 '<p class="pl-p">قبل ما تحكم على أي إعلان، سيبه ياخد ٣ نتائج على الأقل (حوالي أول أسبوع) — '
                 'الأرقام قبل كده مش دقيقة. لو بعدها التكلفة أعلى من المتوقّع بكتير، أوقفه وحوّل ميزانيته للإعلان الشغّال. '
                 '<span class="pl-en">Give each ad at least 3 results (~the first week) before judging — earlier '
                 'numbers are noise. If cost stays far above expectation after that, pause it and move its budget '
                 'to what works.</span></p></div>')

    # 7) WHAT HAPPENS NEXT — 3 numbered steps
    next_html = ('<div class="pl-block"><div class="pl-h">الخطوات التالية · What happens next</div>'
                 '<ol class="pl-steps"><li>راجعي الخطة واعتمديها · Review &amp; approve this plan</li>'
                 '<li>جهّزي البوستر والريل (جاهزين في الاستوديو) · Prepare the poster &amp; reel (ready in the studio)</li>'
                 '<li>أطلقي بميزانية اختبار صغيرة وراقبي أول أسبوع · Launch with a small test budget and watch week one</li></ol></div>')

    # P-03 (D-8): NATIONAL was hardcoded "كل مصر" ("all of Egypt") — shown to a Qatari/Saudi brand
    # in its own plan. Use a country-NEUTRAL label. (When GeoBlock gains a country field, P-51, this
    # can name the brand's actual country; today the geo block carries no country to name.)
    geo_txt = {"national": "على المستوى الوطني · nationwide",
               "radius": "نطاق محلي · local radius"}.get(
        str(geo.get("mode") or ""), str(geo.get("mode") or ""))
    if geo.get("center_address"):
        geo_txt += f" — {geo.get('center_address')} ({geo.get('radius_km')} كم)"
    rationale = _esc(obj.get("rationale") or "")

    return f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>خطة الإعلان — مشروحة · Your ad plan, explained</h2>
      <span class="cnt">مبنية على أدلة من موقعك · grounded in your own site</span></div>
      <div class="card pl">
        <div class="pl-obj">{obj_sentence}{conf_line}{receipt}
          {f'<p class="pl-why">{rationale}</p>' if rationale else ''}
          <div class="pl-geo">📍 المنطقة · Area: {_esc(geo_txt)}</div></div>
        <div class="pl-grid">{funnel_html}{budget_html}{audience_html}{kpi_html}</div>
        {kill_html}{next_html}
      </div></div>"""


def _brand_assets(prof: dict) -> str:
    """ROUND-2 D / capability census — surface the real selling lines the census found dropped:
    offerings, value propositions, and (the credibility spine) trust signals. Rendered as the
    actual content, not just KPI counts."""
    def _texts(key: str, limit: int) -> list:
        out = []
        for x in (prof.get(key) or [])[:limit]:
            v = x.get("value") if isinstance(x, dict) else x
            v = v.get("value") if isinstance(v, dict) else v
            name = x.get("name") if isinstance(x, dict) and x.get("name") else None
            name = name.get("value") if isinstance(name, dict) else name
            t = str(name or v or "").strip()
            if t:
                out.append(t)
        return out

    offerings = _texts("offerings", 12)
    vprops = _texts("value_propositions", 6)
    trust = _texts("trust_signals", 8)
    if not (offerings or vprops or trust):
        return ""
    blocks = ""
    if trust:
        rows = "".join(f'<li>🏅 {_esc(t)}</li>' for t in trust)
        blocks += (f'<div class="ba-block"><div class="ba-h">شارات الثقة والاعتمادات · '
                   f'Trust &amp; accreditations</div><ul class="ba-list">{rows}</ul></div>')
    if vprops:
        rows = "".join(f'<li>◆ {_esc(t)}</li>' for t in vprops)
        blocks += (f'<div class="ba-block"><div class="ba-h">لماذا يختارك العملاء · '
                   f'Why customers choose you</div><ul class="ba-list">{rows}</ul></div>')
    if offerings:
        chips = "".join(f'<span class="ba-chip">{_esc(t)}</span>' for t in offerings)
        blocks += (f'<div class="ba-block"><div class="ba-h">ما تقدّمه · What you offer '
                   f'<span class="ba-n">{len(offerings)}</span></div><div class="ba-chips">{chips}</div></div>')
    return f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>ما وجدناه في موقعك · What we found on your site</h2>
      <span class="cnt">مستخرج بالكامل من موقعك · extracted from your own site</span></div>
      <div class="card ba">{blocks}</div></div>"""


def _capability_census(comp: dict, has_plan: bool, has_creative: bool, run_cost: dict = None) -> str:
    """ROUND-2 D — every system capability has a visible home: what this analysis includes, the
    per-run cost (when known), and a discreet logs link. Never a red state; honest checkmarks."""
    swot = comp.get("swot") or {}
    n_sw = sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats"))
    items = [
        ("ملف البراند · Brand profile", bool(comp.get("profile"))),
        ("تحليل SWOT · SWOT analysis", n_sw > 0),
        ("المنافسون · Competitors", int(comp.get("competitor_count") or 0) > 0),
        ("خطة الإعلان · Ad plan", has_plan),
        ("الإبداع · Creative", has_creative),
        ("سجل التشغيل الكامل · Full run log", True),
    ]
    checks = "".join(
        f'<span class="cc-item">{"✓" if ok else "○"} {_esc(lab)}</span>' for lab, ok in items)
    cost_html = ""
    if run_cost and run_cost.get("cost_usd"):
        cost_html = (f'<span class="cc-cost">تكلفة هذا التحليل · this analysis cost '
                     f'≈ ${run_cost["cost_usd"]:.2f} · {run_cost.get("llm_calls", 0)} نداء ذكاء</span>')
    return (f'<div class="sec"><div class="cc">'
            f'<div class="cc-items">{checks}</div>{cost_html}'
            f'<div class="cc-log">📄 كل خطوة موثّقة في سجل التشغيل · every step is recorded in the run log</div>'
            f'</div></div>')


def _market_pulse(comp: dict, generated_at: str = "") -> str:
    """ROUND-2 A / Market Pulse — surface the live trends (computed, folded only into SWOT
    before) as their own section + the upcoming Egyptian marketing-season window. Surfacing +
    the one allowed new compute (the season calendar). Renders only when there is something."""
    trends = [t for t in (comp.get("trends") or []) if isinstance(t, dict) and t.get("title")][:8]
    today = (generated_at or comp.get("generated_at") or "")[:10]
    season = None
    # P-02 (D-8): only surface the upcoming-season panel for an Egyptian brand (the one ratified
    # calendar). A Gulf/other brand gets the live-trends chips but NOT Egypt's season window.
    if today and _seasons_ok_for(comp.get("profile") or {}):
        try:
            from trends.seasons import upcoming_season
            season = upcoming_season(today, within_days=120)
        except Exception:  # noqa: BLE001
            season = None
    if not trends and not season:
        return ""
    season_html = ""
    if season:
        approx = " (تقريبي · approx)" if season.get("approximate") else ""
        when = ("مستمر الآن · active now" if season.get("active")
                else f"بعد {season['days_until']} يوم · in {season['days_until']} days")
        season_html = (f'<div class="mpz"><div class="mpz-h">🗓️ الموسم القادم · Upcoming season</div>'
                       f'<div class="mpz-b"><b>{_esc(season["ar"])} · {_esc(season["en"])}</b>{_esc(approx)}'
                       f'<span class="mpz-when">{_esc(when)}</span></div>'
                       f'<div class="mpz-note">جهّزي محتواك قبلها · plan content ahead of it '
                       f'({_esc(season["start"])} → {_esc(season["end"])})</div></div>')
    chips = "".join(
        f'<a class="mp-chip" href="{_safe_href(t.get("url"))}" target="_blank" rel="noopener">'
        f'{_esc(str(t.get("title"))[:70])}</a>' for t in trends)
    # P-27: the source is a rank-only SERP pull (score = search rank, no timestamps / time-series),
    # so DON'T call it "live" or "trending" — that overstates a signal we cannot measure. Frame it
    # honestly as recent coverage in the field, a source of inspiration.
    chips_html = (f'<div class="mpz"><div class="mpz-h">📰 تغطية حديثة في مجالك · Recent coverage in your field</div>'
                  f'<div class="mp-chips">{chips}</div>'
                  f'<div class="mpz-note">موضوعات على صلة بمجالك — مصدر إلهام لمحتواك · '
                  f'topics relevant to your field (search results, not a time-ranked trend feed)</div></div>' if chips else "")
    return f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>نبض السوق · Market pulse</h2><span class="cnt">اتجاهات ومواسم · trends &amp; seasons</span></div>
      <div class="card mp">{season_html}{chips_html}</div></div>"""


def _registry_panel(comp: dict) -> str:
    """T-REGISTRY — the stable competitor baseline + this run's diff. Disappeared peers are
    flagged (not deleted); newcomers are shown as PENDING owner approval (HITL for identity),
    never silently added. Renders only when a diff with real change exists."""
    diff = comp.get("registry_diff") or {}
    present = diff.get("still_present") or []
    gone = diff.get("disappeared") or []
    pending = diff.get("new_pending") or []
    if not (gone or pending):
        return ""   # first run / no drift -> nothing to reconcile
    rows = ""
    for e in present:
        rows += (f'<div class="rg-row"><span class="rg-badge rg-ok">✓ ثابت · present</span>'
                 f'<b>{_esc(e.get("name") or e.get("key"))}</b></div>')
    for e in gone:
        rows += (f'<div class="rg-row"><span class="rg-badge rg-gone">⚠ اختفى · disappeared</span>'
                 f'<b>{_esc(e.get("name") or e.get("key"))}</b>'
                 f'<span class="rg-note">ما ظهرش في آخر فحص — محفوظ للمراجعة · kept for your review</span></div>')
    for e in pending:
        rows += (f'<div class="rg-row"><span class="rg-badge rg-new">＋ جديد · new</span>'
                 f'<b>{_esc(e.get("name") or e.get("key"))}</b>'
                 f'<span class="rg-note">بانتظار موافقتك لإضافته · pending your approval</span></div>')
    return f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>سجل المنافسين — ثابت لا يتغيّر بلا إذنك · Competitor registry — stable, changes only with your approval</h2>
      <span class="cnt">{len(present)} ثابت · {len(gone)} اختفى · {len(pending)} جديد</span></div>
      <div class="card rg"><p class="rg-intro">قائمة منافسيك مثبّتة من أول تحليل — أي تغيير يُعرض عليك، لا يُطبَّق تلقائياً.<span class="pl-en">Your competitor set is locked from the first analysis — any change is shown for your decision, never applied silently.</span></p>{rows}</div></div>"""


def _honesty_surface(comp: dict, mp: dict, prof: dict, n_voice: int) -> str:
    """RUBRIC F — the "what we don't know yet" panel. Every unknown is DISPLAYED honestly,
    never hidden; each gap names the unit/action that would fill it (the advisor-gap channel).
    Pure detection from the real artifacts — no fabrication, no red banners."""
    gaps: list[tuple[str, str]] = []   # (arabic line, english line)
    swot = comp.get("swot") or {}
    mode = str(swot.get("mode") or "")
    obj = (mp.get("objectives") or [{}])[0] if mp else {}

    if mode == "standalone" or int(comp.get("competitor_count") or 0) == 0:
        gaps.append(("لم نؤكّد منافسين مباشرين بعد — التحليل التنافسي مبنيّ على موقعك وحده. "
                     "نحتاج تأكيد قائمة المنافسين لإكمال مقارنة السوق.",
                     "No confirmed direct competitors yet — the analysis stands on your own site. "
                     "Confirming the competitor list completes the market comparison."))
    if n_voice == 0:
        gaps.append(("لم نجمع آراء عملاء بعد — صوت العميل يُضاف بعد ربط تقييمات Google Maps للبراند.",
                     "No customer reviews collected yet — customer voice is added once the "
                     "brand's Google-Maps reviews are connected."))
    # ad presence (ads_intel) — A8: CONDITIONAL now. This gap shows ONLY when there is no saved
    # Meta Ad Library snapshot for the run (ads_available=False). Once the owner runs the (free)
    # pull and a snapshot lands, `ad_presence_for_refs` feeds the SWOT threats and this gap drops.
    if not comp.get("ads_available"):
        gaps.append(("إعلانات المنافسين الجارية غير متاحة بعد — تحتاج تأكيد هوية حساب Meta الإعلاني "
                     "(خطوة مجانية لمرة واحدة).",
                     "Competitors' running ads are not available yet — needs the one-time (free) "
                     "Meta ad-account identity confirmation."))
    kpi = obj.get("kpi_target") or {}
    if obj.get("objective") and not kpi.get("target_value"):
        gaps.append(("الرقم المستهدف للتكلفة لم يُحدَّد بعد — لا نخترع رقماً؛ يُعاير من بيانات "
                     "أول أسبوعين من التشغيل الفعلي.",
                     "The cost target isn't set yet — we never invent one; it calibrates from "
                     "your first two weeks of real spend."))
    # ungrounded persona axes -> honest assumption disclosure
    persona = mp.get("base_persona") or {}
    if persona and not any(
            isinstance(persona.get(a), dict) and persona.get(a, {}).get("evidence")
            for a in ("age_range", "gender", "income_level")):
        gaps.append(("عمر ودخل الجمهور غير مؤكّدين من الموقع — التقديرات مبدئية وتُحسم من أداء الحملة.",
                     "Audience age &amp; income aren't confirmed from the site — estimates are "
                     "provisional and settle from campaign performance."))
    if not gaps:
        return ""
    rows = "".join(
        f'<div class="hs-row"><span class="hs-dot">•</span><div><div class="hs-ar">{_esc(ar)}</div>'
        f'<div class="hs-en">{_esc(en)}</div></div></div>' for ar, en in gaps)
    return f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
      <h2>ما لا نعرفه بعد · What we don't know yet</h2>
      <span class="cnt">الأمانة جزء من التقرير · honesty is part of the report</span></div>
      <div class="card hs"><p class="hs-intro">هذه النقاط غير مؤكدة بعد — نعرضها بصراحة بدل إخفائها، وكل واحدة تُغلق بخطوة واضحة.<span class="pl-en">These are still open — shown openly rather than hidden, each with a clear next step.</span></p>{rows}</div></div>"""


def _css() -> str:
    c = _C
    return f"""<style>
    :root{{color-scheme:light;}}
    .bsr *{{margin:0;padding:0;box-sizing:border-box;}}
    .bsr{{
      --serif:'Fraunces','Sakkal Majalla','Traditional Arabic',Georgia,'Times New Roman',serif;
      --sans:'Segoe UI','Inter',Tahoma,-apple-system,BlinkMacSystemFont,system-ui,'Noto Sans Arabic',sans-serif;
      font-family:var(--sans);color:{c['ink']};background:{c['bg']};
      background-image:
        radial-gradient(circle at 8% 4%,rgba(236,203,214,.35) 0%,transparent 26%),
        radial-gradient(circle at 94% 8%,rgba(240,228,211,.30) 0%,transparent 28%),
        radial-gradient(circle at 96% 96%,rgba(221,168,185,.22) 0%,transparent 30%),
        radial-gradient(circle at 4% 98%,rgba(246,224,231,.36) 0%,transparent 28%);
      -webkit-font-smoothing:antialiased;line-height:1.5;padding:26px clamp(14px,4vw,44px) 60px;
    }}
    .bsr a{{color:{c['blush600']};text-decoration:none;}}
    .wrap{{max-width:1180px;margin:0 auto;}}
    .serif{{font-family:var(--serif);}}
    /* topbar */
    .top{{display:flex;align-items:center;justify-content:space-between;gap:14px;
      background:rgba(255,255,255,.72);border:1px solid {c['line']};border-radius:16px;
      padding:14px 22px;margin-bottom:20px;box-shadow:0 2px 8px rgba(168,69,107,.05);flex-wrap:wrap;}}
    .brand{{display:flex;align-items:center;gap:12px;}}
    .brand-badge{{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;color:#fff;
      font-family:var(--serif);font-weight:600;font-size:20px;
      background:linear-gradient(135deg,{c['blush500']},{c['blush700']});box-shadow:0 5px 14px rgba(184,92,122,.34);}}
    .brand-name{{font-family:var(--serif);font-weight:600;font-size:20px;letter-spacing:-.2px;}}
    .brand-sub{{font-size:10.5px;color:{c['muted']};letter-spacing:1.6px;text-transform:uppercase;font-weight:700;margin-top:2px;}}
    .tagpill{{display:inline-flex;align-items:center;gap:7px;background:{c['blush50']};border:1px solid {c['blush200']};
      color:{c['blush600']};border-radius:999px;padding:6px 14px;font-size:11px;font-weight:700;letter-spacing:1.1px;text-transform:uppercase;}}
    .dot{{width:7px;height:7px;border-radius:50%;background:{c['blush500']};}}
    /* hero */
    .hero{{position:relative;overflow:hidden;background:{c['surface']};border:1px solid {c['line']};
      border-radius:22px;padding:32px 34px;margin-bottom:20px;box-shadow:0 6px 20px rgba(168,69,107,.07);}}
    .hero::before{{content:'';position:absolute;inset:0;pointer-events:none;
      background:radial-gradient(circle at 90% 6%,rgba(221,168,185,.18) 0%,transparent 42%),
                 radial-gradient(circle at 4% 96%,rgba(240,228,211,.24) 0%,transparent 48%);}}
    .hero-in{{position:relative;z-index:1;}}
    .hero h1{{font-family:var(--serif);font-weight:600;font-size:clamp(26px,4vw,40px);line-height:1.1;letter-spacing:-.5px;}}
    .hero h1 em{{font-style:italic;color:{c['blush600']};}}
    .hero p{{color:{c['inkSoft']};font-size:14.5px;margin-top:8px;max-width:640px;}}
    /* kpis */
    .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:22px;}}
    .kpi{{position:relative;overflow:hidden;background:{c['surface2']};border:1px solid {c['line']};
      border-radius:14px;padding:16px;--a:{c['blush500']};}}
    .kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--a);}}
    .kpi-ic{{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;font-size:17px;
      background:color-mix(in srgb,var(--a) 14%,#fff);color:var(--a);}}
    .kpi-v{{font-family:var(--serif);font-weight:600;font-size:30px;line-height:1;margin-top:12px;color:{c['ink']};}}
    .kpi-l{{font-size:12.5px;color:{c['inkSoft']};font-weight:600;margin-top:6px;}}
    .kpi-s{{font-size:10.5px;color:{c['muted']};margin-top:2px;}}
    /* section */
    .sec{{margin-top:26px;}}
    .sec-h{{display:flex;align-items:center;gap:10px;margin-bottom:14px;}}
    .sec-h h2{{font-family:var(--serif);font-weight:600;font-size:21px;letter-spacing:-.3px;}}
    .sec-h .bar{{width:5px;height:22px;border-radius:3px;background:linear-gradient(135deg,{c['blush500']},{c['blush700']});}}
    .sec-h .cnt{{font-size:11.5px;color:{c['muted']};font-weight:600;margin-inline-start:auto;}}
    .card{{background:{c['surface']};border:1px solid {c['line']};border-radius:16px;padding:20px;
      box-shadow:0 2px 8px rgba(168,69,107,.05);}}
    /* SWOT */
    .swot{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
    .quad{{border-radius:16px;padding:18px;border:1px solid {c['line']};background:{c['surface']};}}
    .quad-s{{background:linear-gradient(180deg,{c['sage100']}55,#fff);}}
    .quad-w{{background:linear-gradient(180deg,#F6E0E755,#fff);}}
    .quad-o{{background:linear-gradient(180deg,{c['gold100']}55,#fff);}}
    .quad-t{{background:linear-gradient(180deg,#FDECEC,#fff);}}
    .quad-h{{display:flex;align-items:center;gap:9px;font-family:var(--serif);font-weight:600;font-size:15px;margin-bottom:12px;}}
    .quad-ic{{font-size:17px;}} .quad-n{{margin-inline-start:auto;font-size:12px;color:{c['muted']};font-family:var(--sans);
      background:#fff;border:1px solid {c['line']};border-radius:999px;padding:1px 9px;font-weight:700;}}
    .swot-list{{list-style:none;display:flex;flex-direction:column;gap:10px;}}
    .swot-item{{border-bottom:1px dashed {c['line']};padding-bottom:10px;}}
    .swot-item:last-child{{border:0;padding-bottom:0;}}
    .swot-t{{font-size:13.5px;font-weight:600;color:{c['ink']};line-height:1.45;}}
    .swot-c{{font-size:11px;color:{c['muted']};margin-top:4px;font-family:var(--sans);}}
    .swot-empty{{color:{c['muted']};font-size:13px;list-style:none;}}
    .cs{{font-size:9px;font-weight:800;padding:2px 7px;border-radius:5px;letter-spacing:.4px;text-transform:uppercase;vertical-align:middle;}}
    .cs-ok{{background:rgba(63,143,107,.14);color:{c['ok']};}}
    .cs-warn{{background:rgba(198,138,60,.16);color:{c['warn']};}}
    .cs-neutral{{background:{c['blush50']};color:{c['blush600']};}}
    /* competitors */
    .comps{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;}}
    .comp{{display:flex;align-items:center;gap:13px;background:{c['surface']};border:1px solid {c['line']};
      border-radius:14px;padding:15px;}}
    .comp-logo{{width:42px;height:42px;border-radius:11px;display:grid;place-items:center;font-weight:800;font-size:14px;
      background:{c['blush100']};color:{c['blush700']};flex-shrink:0;font-family:var(--sans);}}
    .comp-body{{flex:1;min-width:0;}} .comp-name{{font-weight:700;font-size:14px;}}
    .comp-site{{font-size:11.5px;color:{c['teal']};font-weight:600;}}
    .comp-why{{font-size:11px;color:{c['muted']};margin-top:3px;line-height:1.4;}}
    .comp-fit{{text-align:center;flex-shrink:0;}}
    .comp-fit-v{{font-family:var(--serif);font-weight:600;font-size:20px;color:{c['blush600']};}}
    .comp-fit-l{{font-size:9.5px;color:{c['muted']};text-transform:uppercase;letter-spacing:.5px;}}
    .tag{{font-size:9px;font-weight:700;padding:1px 7px;border-radius:5px;text-transform:uppercase;letter-spacing:.4px;vertical-align:middle;}}
    .tag-sage{{background:{c['sage100']};color:{c['sage700']};}} .tag-teal{{background:{c['teal100']};color:{c['teal']};}}
    /* tows */
    .tows-row{{display:flex;gap:12px;padding:11px 0;border-bottom:1px dashed {c['line']};}}
    .tows-row:last-child{{border:0;}}
    .tows-k{{font-family:var(--sans);font-weight:800;font-size:10px;letter-spacing:.6px;color:#fff;
      border-radius:6px;padding:3px 9px;height:fit-content;white-space:nowrap;}}
    .tows-so{{background:{c['sage']};}} .tows-st{{background:{c['teal']};}}
    .tows-wo{{background:{c['gold']};}} .tows-wt{{background:{c['blush600']};}}
    .tows-txt{{font-size:13px;color:{c['ink']};line-height:1.5;}}
    .tows-desc{{font-size:11.5px;color:{c['inkSoft']};margin-top:3px;font-weight:400;line-height:1.5;}}
    .posture{{background:{c['blush50']};border:1px solid {c['blush200']};border-radius:12px;padding:13px 16px;
      margin-top:14px;font-size:13.5px;color:{c['blush700']};font-weight:600;}}
    /* calendar */
    .cal-row{{display:grid;grid-template-columns:96px 150px 1fr;gap:12px;align-items:start;padding:12px 0;border-bottom:1px dashed {c['line']};}}
    .cal-row:last-child{{border:0;}}
    .cal-date{{font-weight:700;font-size:12px;color:{c['blush600']};font-family:var(--sans);}}
    .cal-plat{{font-size:11.5px;color:{c['inkSoft']};font-weight:600;display:flex;align-items:center;gap:6px;}}
    .cal-ic{{width:22px;height:22px;border-radius:6px;display:grid;place-items:center;background:{c['blush50']};color:{c['blush600']};font-size:11px;}}
    .cal-topic{{font-weight:700;font-size:13.5px;}} .cal-hook{{font-size:12px;color:{c['inkSoft']};margin-top:2px;font-style:italic;}}
    /* creative */
    .creative{{display:grid;grid-template-columns:1fr 1.2fr;gap:16px;align-items:start;}}
    .media-col{{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start;}}
    .media{{flex:1 1 190px;min-width:0;}}
    .creative img,.creative video{{width:100%;border-radius:14px;border:1px solid {c['line']};
      display:block;background:#0b0b11;}}
    .reel-vid{{aspect-ratio:9/16;max-height:560px;object-fit:contain;}}
    .creative .cap{{font-size:11.5px;color:{c['muted']};margin-top:8px;text-align:center;}}
    .foot{{text-align:center;color:{c['muted']};font-size:11.5px;margin-top:34px;}}
    .foot b{{color:{c['blush600']};}}
    .voice-row{{display:flex;gap:10px;padding:10px 4px;border-bottom:1px solid {c['line']};align-items:flex-start;}}
    .voice-row:last-child{{border-bottom:none;}}
    .voice-k{{flex:0 0 auto;font-size:16px;line-height:1.2;padding:4px 8px;border-radius:10px;}}
    .voice-s{{background:{c['sage100']};}}
    .voice-w{{background:{c['gold100']};}}
    .voice-row b{{font-size:13.5px;color:{c['ink']};}}
    .voice-q{{font-size:12px;color:{c['inkSoft']};margin-top:4px;line-height:1.55;}}
    .mp-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px 18px;}}
    .mp-l{{display:block;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:{c['muted']};margin-bottom:2px;}}
    .mp-grid b{{font-size:13.5px;color:{c['ink']};}}
    .mp-ok{{font-size:12px;color:{c['ok']};font-weight:700;}}
    .mp-warn{{font-size:12px;color:{c['warn']};font-weight:700;}}
    .mp-r{{font-size:12.5px;color:{c['inkSoft']};margin-top:12px;line-height:1.6;}}
    .act-row{{display:flex;gap:12px;padding:10px 4px;border-bottom:1px solid {c['line']};align-items:flex-start;}}
    .act-row:last-child{{border-bottom:none;}}
    .act-n{{flex:0 0 26px;height:26px;border-radius:50%;background:{c['blush100']};color:{c['blush700']};
      font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center;}}
    .act-row b{{font-size:13.5px;color:{c['ink']};}}
    .act-h{{font-size:12px;color:{c['muted']};margin-top:2px;}}
    /* RUBRIC A — executive summary */
    .ex{{margin-top:4px;}}
    .ex-card{{background:linear-gradient(135deg,{c['surface']},{c['blush50']});
      border:1px solid {c['line']};border-radius:18px;padding:26px 30px;
      box-shadow:0 6px 22px rgba(168,69,107,.07);}}
    .ex-eyebrow{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
      color:{c['blush600']};font-weight:700;margin-bottom:10px;}}
    .ex-who{{font-family:var(--serif);font-size:26px;font-weight:600;color:{c['ink']};
      line-height:1.2;}}
    .ex-cat{{font-family:var(--sans);font-size:13px;font-weight:600;color:{c['blush700']};
      background:{c['blush100']};padding:3px 11px;border-radius:20px;margin-inline-start:10px;
      vertical-align:middle;white-space:nowrap;}}
    .ex-desc{{font-size:14px;color:{c['inkSoft']};margin-top:10px;line-height:1.65;max-width:64ch;}}
    .ex-pos{{margin-top:14px;display:flex;flex-direction:column;gap:1px;}}
    .ex-pos b{{font-size:16px;color:{c['sage700']};}}
    .ex-pos span{{font-size:12px;color:{c['muted']};}}
    .ex-stats{{display:flex;flex-wrap:wrap;gap:10px 20px;margin-top:14px;}}
    .ex-stat{{font-size:12.5px;color:{c['inkSoft']};}}
    .ex-stat b{{font-size:17px;color:{c['ink']};font-family:var(--serif);margin-inline-end:4px;}}
    .ex-move{{margin-top:18px;padding:14px 18px;background:{c['ink']};border-radius:14px;color:#fff;}}
    .ex-move-h{{font-size:11px;letter-spacing:.06em;color:{c['blush200']};font-weight:700;text-transform:uppercase;}}
    .ex-move-b{{font-size:16px;font-weight:600;margin-top:6px;line-height:1.4;}}
    .ex-move-hz{{display:inline-block;margin-top:9px;font-size:11.5px;font-weight:700;
      background:{c['sage']};color:#fff;padding:3px 12px;border-radius:20px;}}
    /* RUBRIC I/B — the explained plan */
    .pl-en{{display:block;font-size:11.5px;color:{c['muted']};margin-top:2px;font-style:italic;}}
    .tip{{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
      margin-inline-start:6px;border-radius:50%;background:{c['blush100']};color:{c['blush700']};
      font-size:10px;font-weight:700;cursor:help;vertical-align:middle;}}
    .pl-obj{{background:{c['blush50']};border:1px solid {c['line']};border-radius:14px;
      padding:16px 18px;margin-bottom:16px;font-size:15px;line-height:1.6;color:{c['ink']};}}
    .pl-conf{{display:inline-block;margin-top:8px;font-size:12px;color:{c['sage700']};font-weight:700;}}
    .pl-receipt{{margin-top:10px;font-size:12.5px;color:{c['sage700']};background:{c['sage100']};
      border-radius:9px;padding:7px 11px;line-height:1.5;}}
    .pl-receipt.pl-warn{{color:{c['gold700']};background:{c['gold100']};}}
    .pl-why{{font-size:13px;color:{c['inkSoft']};margin-top:10px;line-height:1.6;}}
    .pl-geo{{margin-top:10px;font-size:12.5px;color:{c['inkSoft']};font-weight:600;}}
    .pl-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
    .pl-block{{background:{c['surface2']};border:1px solid {c['line']};border-radius:12px;padding:14px 16px;}}
    .pl-h{{font-size:13px;font-weight:700;color:{c['blush700']};margin-bottom:8px;}}
    .pl-p{{font-size:13px;color:{c['ink']};line-height:1.6;}}
    .pl-note{{font-size:11.5px;color:{c['muted']};line-height:1.5;}}
    .pl-warn{{color:{c['gold700']};}}
    .pl-fstage{{display:inline-block;font-size:13px;font-weight:700;padding:4px 14px;border-radius:20px;
      background:{c['ink']};color:#fff;}}
    .pl-split{{display:flex;flex-direction:column;gap:7px;margin-bottom:8px;}}
    .pl-bar-row{{display:flex;align-items:center;gap:9px;font-size:12px;}}
    .pl-bar-lab{{flex:0 0 118px;color:{c['inkSoft']};}}
    .pl-bar{{flex:1;height:9px;background:{c['blush100']};border-radius:6px;overflow:hidden;}}
    .pl-bar-fill{{display:block;height:100%;background:linear-gradient(90deg,{c['blush500']},{c['blush700']});}}
    .pl-bar-pct{{flex:0 0 34px;text-align:end;font-weight:700;color:{c['ink']};}}
    .pl-aud{{list-style:none;display:flex;flex-direction:column;gap:7px;}}
    .pl-aud li{{font-size:12.5px;color:{c['ink']};line-height:1.5;}}
    .pl-tag{{display:inline-block;font-size:10px;font-weight:700;padding:1px 8px;border-radius:12px;margin-inline-start:6px;}}
    .pl-tag.ev{{background:{c['sage100']};color:{c['sage700']};}}
    .pl-tag.as{{background:{c['gold100']};color:{c['gold700']};}}
    .pl-cal{{font-weight:700;color:{c['gold700']};font-size:12.5px;}}
    .pl-steps{{margin:0;padding-inline-start:20px;font-size:13px;color:{c['ink']};line-height:1.9;}}
    @media (max-width:760px){{.pl-grid{{grid-template-columns:1fr;}}}}
    /* RUBRIC F — honesty surface */
    .hs-intro{{font-size:13px;color:{c['inkSoft']};line-height:1.6;margin-bottom:12px;}}
    .hs-row{{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid {c['line']};align-items:flex-start;}}
    .hs-row:last-child{{border-bottom:none;}}
    .hs-dot{{color:{c['gold']};font-size:18px;line-height:1.2;flex:0 0 auto;}}
    /* RUBRIC H — the reading arc, now a STICKY JUMP NAV (real dashboard, not a long scroll) */
    .arc{{display:flex;flex-wrap:wrap;align-items:center;gap:6px 4px;margin:6px 2px 20px;
      padding:11px 16px;background:rgba(255,255,255,.86);backdrop-filter:blur(8px);
      border:1px solid {c['line']};border-radius:14px;
      position:sticky;top:8px;z-index:50;box-shadow:0 4px 14px rgba(168,69,107,.08);}}
    .arc-step{{font-size:11.5px;color:{c['inkSoft']};white-space:nowrap;text-decoration:none;
      padding:3px 7px;border-radius:8px;transition:background .12s,color .12s;}}
    .arc-step:hover,.arc-step:focus{{background:{c['blush50']};color:{c['blush700']};}}
    .arc-step b{{display:inline-flex;width:17px;height:17px;border-radius:50%;background:{c['blush100']};
      color:{c['blush700']};font-size:10px;align-items:center;justify-content:center;margin-inline-end:4px;}}
    .arc-sep{{color:{c['muted']};font-size:12px;margin:0 3px;}}
    /* anchored section groups: keep the heading clear of the sticky nav when jumped to */
    section[id]{{scroll-margin-top:64px;}}
    /* RUBRIC D — creative tied to strategy */
    .tie{{margin-top:8px;padding:9px 11px;background:{c['blush50']};border-radius:10px;}}
    .tie-h{{font-size:10.5px;font-weight:700;letter-spacing:.04em;color:{c['blush700']};text-transform:uppercase;margin-bottom:6px;}}
    .tie-chips{{display:flex;flex-wrap:wrap;gap:5px;}}
    .tie-chip{{font-size:11px;font-weight:600;color:{c['inkSoft']};background:{c['surface']};
      border:1px solid {c['line']};padding:2px 9px;border-radius:12px;}}
    .tie-chip.tie-strat{{color:{c['blush700']};background:{c['blush100']};border-color:transparent;}}
    .hs-ar{{font-size:13px;color:{c['ink']};line-height:1.55;}}
    .hs-en{{font-size:11.5px;color:{c['muted']};font-style:italic;margin-top:2px;line-height:1.5;}}
    /* T-REGISTRY — competitor registry diff */
    .rg-intro{{font-size:12.5px;color:{c['inkSoft']};line-height:1.6;margin-bottom:12px;}}
    .rg-row{{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid {c['line']};flex-wrap:wrap;}}
    .rg-row:last-child{{border-bottom:none;}}
    .rg-row b{{font-size:13px;color:{c['ink']};}}
    .rg-badge{{font-size:10.5px;font-weight:700;padding:2px 9px;border-radius:12px;white-space:nowrap;}}
    .rg-ok{{background:{c['sage100']};color:{c['sage700']};}}
    .rg-gone{{background:{c['gold100']};color:{c['gold700']};}}
    .rg-new{{background:{c['blush100']};color:{c['blush700']};}}
    .rg-note{{font-size:11px;color:{c['muted']};}}
    /* T-TIER — why a competitor matched */
    .comp-reasons{{display:flex;flex-wrap:wrap;gap:5px;margin:5px 0;}}
    .cmr{{font-size:10.5px;font-weight:600;color:{c['sage700']};background:{c['sage100']};
      padding:1px 8px;border-radius:11px;}}
    /* ROUND-2 A — Market Pulse */
    .mpz{{margin-bottom:14px;}}
    .mpz:last-child{{margin-bottom:0;}}
    .mpz-h{{font-size:12.5px;font-weight:700;color:{c['blush700']};margin-bottom:8px;}}
    .mpz-b{{font-size:14px;color:{c['ink']};}}
    .mpz-when{{display:inline-block;margin-inline-start:8px;font-size:11px;font-weight:700;
      background:{c['gold100']};color:{c['gold700']};padding:1px 9px;border-radius:11px;}}
    .mpz-note{{font-size:11.5px;color:{c['muted']};margin-top:5px;line-height:1.5;}}
    .mp-chips{{display:flex;flex-wrap:wrap;gap:7px;}}
    .mp-chip{{font-size:11.5px;color:{c['inkSoft']};background:{c['surface2']};border:1px solid {c['line']};
      padding:4px 11px;border-radius:14px;text-decoration:none;max-width:100%;}}
    .mp-chip:hover{{border-color:{c['blush400']};}}
    .cal-season{{display:inline-block;margin-inline-start:8px;font-size:10.5px;font-weight:600;
      color:{c['gold700']};background:{c['gold100']};padding:1px 8px;border-radius:11px;}}
    /* ROUND-2 D — brand assets + capability census */
    .ba-block{{margin-bottom:16px;}} .ba-block:last-child{{margin-bottom:0;}}
    .ba-h{{font-size:13px;font-weight:700;color:{c['blush700']};margin-bottom:8px;}}
    .ba-n{{font-size:11px;color:{c['muted']};font-weight:600;}}
    .ba-list{{list-style:none;display:flex;flex-direction:column;gap:6px;}}
    .ba-list li{{font-size:13px;color:{c['ink']};line-height:1.55;}}
    .ba-chips{{display:flex;flex-wrap:wrap;gap:6px;}}
    .ba-chip{{font-size:12px;color:{c['inkSoft']};background:{c['surface2']};border:1px solid {c['line']};
      padding:3px 11px;border-radius:13px;}}
    .cc{{background:{c['surface']};border:1px solid {c['line']};border-radius:14px;padding:14px 18px;}}
    .cc-items{{display:flex;flex-wrap:wrap;gap:7px 16px;}}
    .cc-item{{font-size:11.5px;color:{c['sage700']};font-weight:600;}}
    .cc-cost{{display:block;margin-top:9px;font-size:12px;color:{c['inkSoft']};font-weight:600;}}
    .cc-log{{margin-top:9px;font-size:11px;color:{c['muted']};}}
    /* RUBRIC C — strategy narrative in plain words */
    .cs-legend{{display:flex;flex-wrap:wrap;gap:8px 18px;margin-bottom:12px;font-size:11.5px;color:{c['inkSoft']};}}
    .cs-legend b{{margin-inline-end:5px;}}
    .tows-plain{{display:inline-block;font-size:11px;font-weight:700;color:{c['blush700']};
      background:{c['blush100']};padding:1px 9px;border-radius:12px;margin-bottom:5px;}}
    @media (max-width:760px){{.mp-grid{{grid-template-columns:1fr 1fr;}}}}
    @media (max-width:760px){{.swot,.creative{{grid-template-columns:1fr;}}.cal-row{{grid-template-columns:1fr;gap:3px;}}}}
    </style>"""


def build_dashboard_html(
    competitor_path: Optional[str] = None,
    *,
    profile_path: Optional[str] = None,
    plan_path: Optional[str] = None,
    poster_path: Optional[str] = None,
    reel_path: Optional[str] = None,
    media_plan_path: Optional[str] = None,
    include_media_plan: bool = True,
    standalone: bool = True,
    generated_at: Optional[str] = None,
) -> str:
    """Render the dashboard to an HTML string (the interactive studio embeds this body directly)."""
    comp = _load(competitor_path)
    profile = _load(profile_path)
    prof = profile.get("profile", profile) if profile else (comp.get("profile") or {})
    plan = _load(plan_path)

    name = _val(prof.get("name")) or plan.get("business_name") or comp.get("subject_url") or "Brand"
    category = _val(prof.get("category")) or comp.get("subject_category") or ""
    tagline = _val(prof.get("tagline")) or ""
    tone = _val(prof.get("tone_of_voice")) or ""
    offerings = prof.get("offerings") or []
    vprops = prof.get("value_propositions") or []
    swot = comp.get("swot") or {}
    competitors = comp.get("competitors") or []
    tows = comp.get("tows") or {}
    items = plan.get("items") or []

    n_swot = sum(len(swot.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats"))
    badge_letter = (name.strip()[:1] or "B").upper()

    # KPIs
    kpis = "".join([
        _kpi("◆", str(len(offerings)), "Offerings", "extracted & grounded", _C["blush500"]),
        _kpi("⚑", str(comp.get("competitor_count") or len(competitors)), "Competitors", "cited peers", _C["teal"]),
        _kpi("✳", str(n_swot), "SWOT items", "every one cited", _C["gold"]),
        _kpi("♥", str(len(vprops)), "Value props", "from the brand", _C["sage"]),
        _kpi("▤", str(len(items)), "Calendar posts", (f"{plan.get('days')}-day plan" if plan.get("days") else "content plan"), _C["blush700"]),
    ])

    swot_html = ""
    if n_swot:
        # RUBRIC C — a plain-words legend for the claim-strength badges (was bare jargon).
        legend = ('<div class="cs-legend">'
                  '<span><b class="cs cs-ok">مؤكَّد</b> بأكثر من منافس · confirmed vs several peers</span>'
                  '<span><b class="cs cs-warn">مبدئي</b> إشارة من مصدر واحد · one-source signal</span>'
                  '<span><b class="cs cs-warn">داخلي</b> من موقعك وحده · from your site alone</span></div>')
        swot_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>نقاط القوة والضعف والفرص والتهديدات · Strengths, weaknesses, opportunities, threats</h2>
          <span class="cnt">كل سطر له مصدر · every line has a source</span></div>
          {legend}
          <div class="swot">
            {_swot_quad('Strengths', '💪', swot.get('strengths'), 's')}
            {_swot_quad('Weaknesses', '⚠', swot.get('weaknesses'), 'w')}
            {_swot_quad('Opportunities', '✦', swot.get('opportunities'), 'o')}
            {_swot_quad('Threats', '⚡', swot.get('threats'), 't')}
          </div></div>"""

    # CUSTOMER VOICE (owner directive 2026-07-12 — "the review work must SHOW"): the ABSA
    # own-brand themes already live INSIDE the SWOT quadrants; this section makes them
    # unmissable for a client demo — each theme with its verbatim «quotes» as the receipts.
    voice_items = []
    for quad in ("strengths", "weaknesses"):
        for it in (swot.get(quad) or []):
            if "customer voice" in str(it.get("evidence") or ""):
                voice_items.append((quad, it))
    voice_html = ""
    if voice_items:
        rows = ""
        for quad, it in voice_items[:8]:
            icon, klass = ("💬", "s") if quad == "strengths" else ("⚠", "w")
            quotes = "".join(f'<div class="voice-q">{_esc(c)}</div>'
                             for c in (it.get("citation") or [])[:3])
            rows += (f'<div class="voice-row"><span class="voice-k voice-{klass}">{icon}</span>'
                     f'<div><b>{_esc(it.get("text") or "")}</b>{quotes}</div></div>')
        voice_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>Customer voice — صوت العملاء</h2><span class="cnt">{len(voice_items)} grounded themes · verbatim quotes</span></div>
          <div class="card">{rows}</div></div>"""

    # MEDIA PLAN (U1) — auto-discovered next to the result file, so the exported dashboard
    # carries the buying plan without any caller changes. Bilingual labels, never raw enums
    # (owner 2026-07-12: the plan section read as a raw dump). include_media_plan=False
    # suppresses it (the STUDIO embeds this report and has its own richer Arabic card —
    # the duplication was part of the mess).
    mp_html = ""
    mp_path = media_plan_path
    if include_media_plan and not mp_path and competitor_path \
            and str(competitor_path).endswith("_result.json"):
        cand = Path(str(competitor_path).replace("_result.json", "_media_plan.json"))
        mp_path = str(cand) if cand.is_file() else None
    mp = _load(mp_path) if include_media_plan else {}
    obj = (mp.get("objectives") or [{}])[0] if mp else {}
    # RUBRIC I/B — the media plan rebuilt as a NARRATED plan a non-marketer can explain back
    # (owner: "cards I don't even understand"). Replaces the raw enum grid.
    mp_html = _explained_plan(mp, category) if obj.get("objective") else ""

    # TOWS PRIORITY ACTIONS — already inside result.json, silently dropped before
    # (owner: everything must show). rank IS a real sequence, so numbering carries meaning.
    # RUBRIC A — the first-screen story (composed once the objective is known).
    exec_html = _executive_summary(name, category, prof, swot, tows,
                                   int(comp.get("competitor_count") or len(competitors)), obj)
    # RUBRIC F — the honesty surface ("what we don't know yet").
    honesty_html = _honesty_surface(comp, mp, prof, len(voice_items))
    # T-REGISTRY — the stable competitor baseline + this run's diff (HITL for identity).
    registry_html = _registry_panel(comp)
    # ROUND-2 A — Market Pulse (live trends + upcoming Egyptian season).
    pulse_html = _market_pulse(comp, generated_at or comp.get("generated_at") or "")
    # ROUND-2 D — capability census: the dropped selling lines (census strip is built below,
    # once poster/reel presence is known).
    assets_html = _brand_assets(prof)
    # RUBRIC H — the "start here" arc, now a STICKY JUMP NAV (owner 2026-07-18: a real dashboard,
    # not one long scroll). Each step is an anchor link into its section group; the bar sticks to
    # the top so it's always reachable. CSS-only (the standalone export forbids <script>).
    _arc = [("السوق", "Market", "sec-market"), ("الاستراتيجية", "Strategy", "sec-strategy"),
            ("الخطة", "Plan", "sec-plan"), ("الإبداع", "Creative", "sec-creative"),
            ("التقويم", "Calendar", "sec-calendar"), ("ما لا نعرفه", "Gaps", "sec-gaps")]
    arc_html = ('<nav class="arc" aria-label="sections">' + '<span class="arc-sep">→</span>'.join(
        f'<a class="arc-step" href="#{sid}"><b>{i}</b> {_esc(a)} · {_esc(e)}</a>'
        for i, (a, e, sid) in enumerate(_arc, 1)) + '</nav>')

    actions = (tows.get("priority_actions") or [])[:6]
    actions_html = ""
    if actions:
        rows = ""
        for a in actions:
            extra = f" — {_esc(a.get('rationale') or '')}" if a.get("rationale") else ""
            rows += (f'<div class="act-row"><span class="act-n">{int(a.get("rank") or 0)}</span>'
                     f'<div><b>{_esc(a.get("action") or "")}</b>'
                     f'<div class="act-h">{_esc(a.get("horizon") or "")}{extra[:170]}</div>'
                     f'</div></div>')
        actions_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>Priority actions — أولويات التنفيذ</h2><span class="cnt">{len(actions)} ranked moves</span></div>
          <div class="card">{rows}</div></div>"""

    # SCRAPER QA STRIP — the same summary the studio shows, now on the export (the analysis
    # names its own coverage). Best-effort; silently absent without a scrape dir.
    qa_html = ""
    try:
        if competitor_path and str(competitor_path).endswith("_result.json"):
            _slug = Path(competitor_path).name[: -len("_result.json")]
            from dashboard.products import scrape_qa_for_slug
            qa = scrape_qa_for_slug(_slug)
            if qa:
                note = _esc((qa.get("key_notes") or [""])[0])
                note_html = f'<p class="mp-r">{note}</p>' if note else ''
                qa_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
                  <h2>Data coverage — تغطية البيانات</h2><span class="cnt">{'✓ ready' if qa.get('ready') else '⚠ partial'}</span></div>
                  <div class="card"><div class="mp-grid">
                    <div><span class="mp-l">Pages</span><b>{qa.get('pages_succeeded')}/{qa.get('pages_attempted')}</b></div>
                    <div><span class="mp-l">Products</span><b>{qa.get('products')}</b></div>
                    <div><span class="mp-l">Images</span><b>{qa.get('images')}</b></div>
                    <div><span class="mp-l">Text blocks</span><b>{qa.get('text_blocks')}</b></div>
                    <div><span class="mp-l">Duration</span><b>{int((qa.get('duration_ms') or 0)/1000)}s</b></div>
                    <div><span class="mp-l">Failures</span><b>{qa.get('failures')}</b></div>
                  </div>{note_html}</div></div>"""
    except Exception:  # noqa: BLE001 — the export never breaks on a missing scrape
        qa_html = ""

    comp_html = ""
    if competitors:
        comp_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>Competitors — discovered & scored</h2><span class="cnt">{len(competitors)} peers</span></div>
          <div class="comps">{''.join(_competitor_card(x) for x in competitors)}</div></div>"""

    tows_html = ""
    strategies = tows.get("strategies") or []
    if strategies or tows.get("posture"):
        _tk = {"SO": "tows-so", "ST": "tows-st", "WO": "tows-wo", "WT": "tows-wt"}
        # RUBRIC C — translate the two-letter TOWS codes into a plain "what to do" phrase.
        _tk_lab = {"SO": ("هاجِم", "attack"), "ST": ("دافِع", "defend"),
                   "WO": ("طوّر", "improve"), "WT": ("احترِس", "protect")}
        rows = ""
        for s in strategies[:8]:
            kind = str(s.get("type") or s.get("kind") or "SO").upper()[:2]
            title = _esc(s.get("title") or s.get("text") or s.get("strategy") or "")
            desc = _esc(s.get("description") or "")
            desc_h = f'<div class="tows-desc">{desc}</div>' if desc else ""
            ka, ke = _tk_lab.get(kind, ("", ""))
            klab = f'<span class="tows-plain">{_esc(ka)} · {_esc(ke)}</span>' if ka else ""
            rows += (f'<div class="tows-row"><span class="tows-k {_tk.get(kind, "tows-so")}">{_esc(kind)}</span>'
                     f'<div class="tows-txt">{klab}<b>{title}</b>{desc_h}</div></div>')
        posture = str(tows.get("posture") or "").lower()
        pa, pe = _POSTURE.get(posture, ("", ""))
        posture_html = (f'<div class="posture">◎ الوضع الاستراتيجي · Your strategic stance: '
                        f'<b>{_esc(pa)}</b> — {_esc(pe)}</div>' if pa else "")
        tows_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>الاستراتيجية — ماذا تفعل ولماذا · Strategy — what to do &amp; why</h2>
          <span class="cnt">{len(strategies)} تحرّكات مبنية على تحليلك · moves from your analysis</span></div>
          <div class="card">{rows or '<div class="swot-empty">—</div>'}{posture_html}</div></div>"""

    cal_html = ""
    if items:
        cal_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>Content calendar</h2><span class="cnt">{len(items)} posts · grounding-gated hooks</span></div>
          <div class="card">{''.join(_calendar_row(it, season_ok=_seasons_ok_for(prof)) for it in items)}</div></div>"""

    poster_uri = _data_uri(poster_path)
    reel_uri = _data_uri(reel_path)
    # CS-08: the moat's headline guarantee ("every hard claim traces to real evidence") must be
    # backed by the poster's AUDIT sidecar, not by the poster file merely existing. 0/10 shipped
    # posters carried a sidecar, yet the sentence printed for all of them. Now it prints only when
    # the evidence-ledger gate actually ran on THIS poster (sidecar present + gate_active).
    audit_ok = False
    if poster_path:
        try:
            from pathlib import Path as _P
            sc = _P(poster_path).with_suffix(".audit.json")
            if sc.is_file():
                _a = json.loads(sc.read_text(encoding="utf-8"))
                cov = _a.get("coverage") or {}
                unver = int(cov.get("unverified") or cov.get("claims_unverified") or 0)
                audit_ok = bool(_a.get("gate_active")) and unver == 0
        except Exception:  # noqa: BLE001
            audit_ok = False
    creative_html = ""
    if poster_uri or reel_uri:
        # RUBRIC D — every creative is LABELLED with the strategy line, audience and funnel
        # stage it serves (no orphan creatives). Pulled from the same plan the deliverable shows.
        strat = ""
        if (tows.get("priority_actions") or []):
            strat = re.sub(r"^(Leverage|Fix|Address|Defend|Exploit)\s*:\s*", "",
                           str(tows["priority_actions"][0].get("action") or "")).strip()
        elif (tows.get("strategies") or []):
            strat = str(tows["strategies"][0].get("title") or "")
        fun = str(obj.get("funnel_stage") or "").lower()
        aud = ""
        for itx in ((mp.get("base_persona") or {}).get("interests") or [])[:1]:
            aud = str(itx.get("claim") if isinstance(itx, dict) else itx).replace(
                "the brand's audience:", "").strip()

        def _tie(kind_ar: str) -> str:
            chips = []
            if strat:
                chips.append(f'<span class="tie-chip tie-strat">🎯 {_esc(strat[:60])}</span>')
            if fun and fun in _FUN_L:
                chips.append(f'<span class="tie-chip">{_esc(_FUN_L[fun])}</span>')
            if aud:
                chips.append(f'<span class="tie-chip">👥 {_esc(aud[:40])}</span>')
            return (f'<div class="tie"><div class="tie-h">{kind_ar} يخدم استراتيجيتك · serves your strategy</div>'
                    f'<div class="tie-chips">{"".join(chips)}</div></div>' if chips else "")

        media = ""
        if poster_uri:
            media += (f'<div class="media"><img src="{poster_uri}" alt="poster">'
                      f'<div class="cap">بوستر · Poster</div>{_tie("البوستر · The poster")}</div>')
        if reel_uri:
            media += (f'<div class="media"><video class="reel-vid" src="{reel_uri}" controls playsinline '
                      f'preload="metadata"></video>'
                      f'<div class="cap">ريل · Reel</div>{_tie("الريل · The reel")}</div>')
        # CS-08: the strong guarantee only when an audit sidecar actually backs it; otherwise an
        # honest neutral caption (the asset predates / lacks the evidence-gate trace).
        if audit_ok:
            claim_block = (
                '<div class="swot-t" style="font-size:15px">كل ادعاء يرجع لدليل حقيقي · Every hard claim traces to real evidence.</div>'
                f'<p style="font-size:13px;color:{_C["inkSoft"]};margin-top:8px;line-height:1.6">'
                'العناوين وسطور الإثبات تمرّ ببوابة الأدلة قبل الرندر؛ اللوجو أصل حقيقي مركّب بدقة (لا يُعاد رسمه). '
                'الريل يحكي نفس القصة المؤسَّسة بصوت متصل وكارت نهاية بلوجو البراند.</p>')
        else:
            claim_block = (
                '<div class="swot-t" style="font-size:15px">تصاميم مربوطة باستراتيجيتك · Creatives tied to your strategy.</div>'
                f'<p style="font-size:13px;color:{_C["inkSoft"]};margin-top:8px;line-height:1.6">'
                'كل تصميم يخدم هدفاً من خطتك. (سجلّ تدقيق الأدلة لهذا الأصل غير مرفق — الضمان الكامل يظهر '
                'حين يُرفق سجلّ التدقيق.)</p>')
        creative_html = f"""<div class="sec"><div class="sec-h"><span class="bar"></span>
          <h2>الإبداع — مربوط باستراتيجيتك · Creative — tied to your strategy</h2>
          <span class="cnt">كل تصميم يخدم هدفاً · every asset serves a goal</span></div>
          <div class="card creative">
            <div class="media-col">{media}</div>
            <div>{claim_block}</div>
          </div></div>"""

    # ROUND-2 D — the capability-census strip (now poster/reel presence is known).
    census_html = _capability_census(comp, has_plan=bool(items),
                                     has_creative=bool(poster_uri or reel_uri))

    gen = generated_at or datetime.now().strftime("%Y-%m-%d")
    body = f"""<div class="bsr"><div class="wrap">
      <div class="top">
        <div class="brand"><div class="brand-badge">{_esc(badge_letter)}</div>
          <div><div class="brand-name">Baseera</div><div class="brand-sub">Marketing Intelligence</div></div></div>
        {('<div class="tagpill"><span class="dot"></span> Provable Brand Safety</div>' if audit_ok else '<div class="tagpill"><span class="dot"></span> Evidence-Gated Output</div>')}
      </div>
      <div class="hero"><div class="hero-in">
        <h1 class="serif">{_esc(name)} — <em>a complete campaign, from one URL</em></h1>
        <p>{_esc(tagline or 'Grounded brand intelligence: profile, cited SWOT, real competitors, a content calendar and agency-grade creative — every claim traceable to a source.')}
        {(' · <b>' + _esc(category) + '</b>') if category else ''}{(' · tone: ' + _esc(tone)) if tone else ''}</p>
        <div class="kpis">{kpis}</div>
      </div></div>
      {exec_html}{arc_html}
      <section id="sec-market">{pulse_html}{assets_html}{swot_html}{comp_html}{registry_html}{voice_html}</section>
      <section id="sec-strategy">{tows_html}{actions_html}</section>
      <section id="sec-plan">{mp_html}</section>
      <section id="sec-creative">{creative_html}</section>
      <section id="sec-calendar">{cal_html}</section>
      <section id="sec-gaps">{honesty_html}{census_html}{qa_html}</section>
      <div class="foot">Generated by <b>Baseera</b> · {_esc(gen)} · claims are checked against the Evidence Ledger — what fails verification is excluded.</div>
    </div></div>"""

    content = _css() + body
    if standalone:
        # RUBRIC G+ — the deliverable is Arabic-primary: RTL root + Arabic-capable font stack.
        # Latin runs render LTR via the browser's bidi algorithm; layout mirrors via the
        # logical CSS properties used throughout.
        content = (f"<!doctype html><html lang=\"ar\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
                   f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                   f"<title>Baseera · {_esc(name)}</title></head><body>{content}</body></html>")
    return content


def build_dashboard(
    competitor_path: Optional[str] = None,
    *,
    profile_path: Optional[str] = None,
    plan_path: Optional[str] = None,
    poster_path: Optional[str] = None,
    reel_path: Optional[str] = None,
    media_plan_path: Optional[str] = None,
    out_path: str = "outputs/dashboard.html",
    standalone: bool = True,
    generated_at: Optional[str] = None,
) -> Path:
    """Build the dashboard and write it to out_path (returns the Path)."""
    content = build_dashboard_html(
        competitor_path, profile_path=profile_path, plan_path=plan_path,
        poster_path=poster_path, reel_path=reel_path, media_plan_path=media_plan_path,
        standalone=standalone, generated_at=generated_at,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out
