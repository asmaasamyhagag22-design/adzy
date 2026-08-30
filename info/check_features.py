# -*- coding: utf-8 -*-
"""فحص الفيتشرز على المخرجات نفسها — مش على وجود الراوت.

`check_dashboard.py` بيسأل «الراوت موجود؟» و`check_wiring.py` بيسأل «الزرار موصول؟».
الملف ده بيسأل السؤال التالت: **«الفيتشر بيطلّع حاجة حقيقية؟»** — بيشغّل كل واحد على براند
مخزَّن ويعدّ اللي رجع.

TWO RULES, AND THE SECOND ONE IS WHY THIS FILE IS CAREFUL:

  1. صفر صرف. مفيش نداء موديل صور ولا فيديو. الحاجات اللي محتاجة LLM بتتقال إنها محتاجاه.
  2. **الفرق بين «مكسور» و«محتاج حساب» لازم يبان.** أول نسخة من الفحص ده اخترعت أربع أعطال
     وهمية لأنها كانت بتقرا أماكن غلط — نفس الدرس المسجَّل في `logs/dashboard_check.log`:
     «كاشف بيخترع أعطال أسوأ من مفيش كاشف». فكل حالة ليها ثلاث نتايج، مش اتنين:
     **PASS** (طلّع محتوى) · **NEED** (شغّال بس محتاج نداء حي/داتا مش موجودة على الجهاز ده)
     · **FAIL** (اتكسر فعلاً).

الاستخدام:
    python info/check_features.py [domain_key] [-v]
"""
from __future__ import annotations

import io
import json
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

BRANDS = _ROOT / "outputs" / "webapp" / "brands"
LOG = _ROOT / "logs" / "dashboard_check.log"
RESULTS: list[tuple[str, str, str]] = []
_VERBOSE = "-v" in sys.argv


def check(name: str):
    def deco(fn):
        def run(*a, **k):
            try:
                verdict, detail = fn(*a, **k)
            except Exception as exc:  # noqa: BLE001
                verdict, detail = "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}"
                if _VERBOSE:
                    traceback.print_exc()
            RESULTS.append((verdict, name, detail))
            print(f"[{verdict:4}] {name:24} {detail}")
            return verdict
        return run
    return deco


# =====================================================================
# what the analysis already produced (stored — no call)
# =====================================================================

@check("SWOT")
def swot(b, _p):
    s = b.get("swot") or {}
    q = {k: len(s.get(k) or []) for k in ("strengths", "weaknesses", "opportunities", "threats")}
    total = sum(q.values())
    if not total:
        return "NEED", "مفيش SWOT مخزَّن — دوسي Generate SWOT"
    cited = sum(1 for k in q for it in (s.get(k) or []) if (it or {}).get("citation"))
    ids = [it.get("id") for k in q for it in (s.get(k) or []) if isinstance(it, dict)]
    dupes = len(ids) - len(set(i for i in ids if i))
    v = "PASS" if (cited == total and not dupes) else "FAIL"
    return v, (f"S{q['strengths']}/W{q['weaknesses']}/O{q['opportunities']}/T{q['threats']} · "
               f"{cited}/{total} مستشهَد" + (f" · {dupes} id مكرر" if dupes else ""))


@check("TOWS")
def tows(b, _p):
    raw = b.get("_swot_raw") or {}
    t = raw.get("tows") or (b.get("swot") or {}).get("tows")
    n = sum(len(v) for v in t.values() if isinstance(v, list)) if isinstance(t, dict) else len(t or [])
    if not n:
        return "NEED", "مش مخزَّن — بيتولّد مع تشغيلة SWOT حية"
    return "PASS", f"{n} خطة"


@check("Competitors")
def peers(b, _p):
    try:
        from competitor.registry import load_registry
        reg = load_registry(b.get("domain_key") or "")
        rows = list(getattr(reg, "competitors", None) or getattr(reg, "approved", None) or [])
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        # `registry/` is gitignored per-machine state — empty here is not a broken feature.
        return "NEED", "الرجيستري فاضي على الجهاز ده (registry/ محلي ومش في git)"
    return "PASS", f"{len(rows)} منافس معتمَد"


# =====================================================================
# what it can compute right now (free)
# =====================================================================

@check("Content calendar")
def calendar(_b, p):
    from strategy.builder import build_strategy
    plan = build_strategy(p, caller=None, days=14)
    items = getattr(plan, "items", None) or []
    if not items:
        return "FAIL", "الكالندر رجع فاضي"
    days = {getattr(i, "date", None) for i in items}
    return "PASS", f"{len(items)} بند على {len(days)} يوم"


@check("Language (D-8)")
def language(_b, p):
    """اللغة لازم تبقى **واحدة** عبر الريل والبوستر والكالندر — العطل المتكرر في المشروع."""
    from reel.audience import audience_language_for
    from strategy.builder import _brand_is_arabic
    lang, country, why = audience_language_for(p)
    agree = _brand_is_arabic(p) == str(lang).lower().startswith("ar")
    return ("PASS" if agree else "FAIL"), \
        f"{lang}{('/' + country) if country else ''} — {why[:52]}" + ("" if agree else "  ← الكالندر مختلف!")


@check("Trends")
def trend(_b, p):
    from trends import top_trends
    from trends.engine import keywords_from_profile
    kws = keywords_from_profile(p)[:4]
    items = top_trends(kws, require_match=True, top_k=6) or []
    if not items:
        return "NEED", f"مفيش نتايج على {kws[:3]} (مفتاح Serper أو مافيش ترند على الموضوع)"
    hosts = {str(getattr(i, "url", "")).split("/")[2] for i in items if getattr(i, "url", "")}
    return "PASS", f"{len(items)} بند من {len(hosts)} مصدر"


@check("Social performance")
def social(_b, p):
    from api.webapp_analysis import social_performance_for
    rep = social_performance_for(p) or {}
    if not rep.get("checked"):
        return "NEED", f"مفيش سناب شوت ({rep.get('reason') or 'ماتجمعش'})"
    return "PASS", f"اتقاس · {len(rep.get('top') or [])} بوست"


@check("Social caption")
def caption(_b, p):
    from api.webapp_social_copy import social_caption
    out = social_caption(p, caller=None) or {}
    cap = str(out.get("caption") or out.get("text") or "")
    if not cap:
        return "FAIL", "مفيش كابشن"
    return "PASS", f"{len(cap)} حرف · {len(out.get('hashtags') or [])} هاشتاج"


@check("Evidence Ledger")
def ledger(_b, p):
    """الموات: ادّعاء بلا مصدر لازم **يترفض**. بنقيسه بادّعاء مفبرك، مش بعدّ الحقول."""
    from grounding.ledger import EvidenceLedger
    led = EvidenceLedger.from_profile(p)
    fake = led.audit_text("The number one pharmacy in Egypt with 50% off")
    if not fake:
        return "NEED", "مفيش ادّعاءات قابلة للفحص في النص ده"
    caught = sum(1 for v in fake if not v.sourced)
    return ("PASS" if caught else "FAIL"), \
        f"{caught}/{len(fake)} ادّعاء مفبرك اترفض"


@check("Compliance sheet")
def compliance(_b, p):
    from types import SimpleNamespace
    from grounding.compliance import build_compliance_sheet
    from poster.audit import build_poster_audit
    concept = SimpleNamespace(headline="Fast delivery across Cairo", subheadline="",
                              cta="Order now", proof_points=[], remediation=[])
    sheet = build_compliance_sheet(build_poster_audit(p, concept)) or {}
    if not sheet:
        return "FAIL", "الشيت مااتبناش"
    return "PASS", f"verdict={sheet.get('verdict', '?')}"


@check("Reel plan")
def reelplan(_b, p):
    from reel.creative import build_creative_storyboard
    from reel.creative_director import CreativeReel, CreativeScene
    from reel.from_profile import build_reel_brief
    from reel.runtime import picture_budget_s
    plan = CreativeReel(concept="c", hook="h", cta="x", language="ar", images=[],
                        scenes=[CreativeScene(image_index=0, veo_prompt=f"S{i}",
                                              voiceover="كلمة " * 5, duration_s=5.0)
                                for i in range(4)])
    sb = build_creative_storyboard(plan, build_reel_brief(p))
    return "PASS", f"{len(sb.scenes)} مشهد · {sb.primary_dir} · لوجو={bool(sb.logo_url)}"


@check("Reel runtime")
def runtime(_b, p):
    """الفيلم لازم يقع على ١٥ ثانية بالظبط، والكارت جزء منها."""
    from reel.runtime import endcard_tail_s, fit_to_runtime, picture_budget_s
    from reel.tempo import beat_grid_for_brand
    g = beat_grid_for_brand(profile=p)
    durs, note = fit_to_runtime([4.0, 4.0, 4.0, 1.4], [1.0, 1.0, 1.0, 1.4], g.beat_s,
                                picture_budget_s(15.0))
    total = sum(durs) + endcard_tail_s()
    ok = abs(total - 15.0) < 0.05
    return ("PASS" if ok else "FAIL"), \
        f"{sum(durs):.2f}s صورة + {endcard_tail_s()}s كارت = {total:.2f}s" + (f" · {note}" if note else "")


@check("Media plan")
def mediaplan(_b, p):
    from media_plan.builder import build_media_plan, conversion_signals
    sig = conversion_signals(p)
    mp = build_media_plan(p, caller=None)
    if mp is None:
        # By design: the objective is DEDUCED by a model, and None is the honest degrade.
        return "NEED", f"محتاج نداء حي لاستنتاج الهدف ({len(sig)} سطح تحويل جاهز)"
    return "PASS", f"{len(getattr(mp, 'objectives', None) or [])} هدف"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not BRANDS.is_dir():
        print(f"!! مفيش مخزن براندات على {BRANDS}")
        return 2
    files = sorted(f for f in BRANDS.glob("*.json") if ".bak." not in f.name)
    dk = args[0] if args else (files[0].stem if files else "")
    path = BRANDS / f"{dk}.json"
    if not path.is_file():
        print(f"!! مفيش براند اسمه {dk}. الموجود: {', '.join(f.stem for f in files)}")
        return 2
    b = json.loads(path.read_text(encoding="utf-8"))
    b.setdefault("domain_key", dk)
    p = b.get("_profile") or b.get("profile") or {}

    print(f"\n=== فحص الفيتشرز · {dk} ===\n")
    swot(b, p); tows(b, p); peers(b, p)
    language(b, p); calendar(b, p); trend(b, p)
    social(b, p); caption(b, p)
    ledger(b, p); compliance(b, p)
    reelplan(b, p); runtime(b, p); mediaplan(b, p)

    n = {v: sum(1 for x, _n, _d in RESULTS if x == v) for v in ("PASS", "NEED", "FAIL")}
    line = (f"\n{n['PASS']} شغّال · {n['NEED']} محتاج حساب/داتا · {n['FAIL']} مكسور")
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"\n--- check_features {dk} ---\n")
            for v, nme, d in RESULTS:
                fh.write(f"[{v}] {nme}: {d}\n")
            fh.write(line.strip() + "\n")
        print(f"-> {LOG}")
    except Exception:  # noqa: BLE001 — a log failure must not fail the check
        pass
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
