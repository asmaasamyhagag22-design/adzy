"""Media Plan v2 assembler — §2 narrative + §3 test cells + §5 reality check + §6 guardrails +
§7 human-input seam, from a CLOSED universe of grounded inputs (profile, SWOT, the evidence-bound
marketing intel). Deterministic: no LLM decides money or strategy here; the model's only role
upstream is the evidence-bound extractor (marketing_intel), whose items already survived the moat."""
from __future__ import annotations

from typing import Any, List, Optional

from .reality_check import _fv, build_reality_check
from .schemas import MediaPlanV2, TestCell


def _swot_texts(swot: Any, key: str) -> List[str]:
    """Texts of one SWOT axis — accepts the dataclass or its serialized dict."""
    if swot is None:
        return []
    items = swot.get(key) if isinstance(swot, dict) else getattr(swot, key, None)
    out: List[str] = []
    for it in (items or []):
        t = it.get("text") if isinstance(it, dict) else getattr(it, "text", None)
        if t and str(t).strip():
            out.append(str(t).strip())
    return out


def _field_values(fields: Any) -> List[str]:
    """The plain values of a list of EvidencedField (or dicts) — every item already carries a
    verbatim quote (the moat ran in the extractor), so these are safe to surface."""
    out: List[str] = []
    for f in (fields or []):
        v = f.get("value") if isinstance(f, dict) else getattr(f, "value", None)
        if v and str(v).strip():
            out.append(str(v).strip())
    return out


def _clip(s: str, n: int = 80) -> str:
    """Clip an embedded quote for the narrative sentence — a verbatim PREFIX marked with an
    ellipsis (SWOT gap-details can run to whole English paragraphs and drown the Arabic sentence)."""
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _strategic_narrative(profile: Any, swot: Any, usps: List[str]) -> str:
    """§2 — 'why THIS campaign', deterministically from client STRENGTH × competitor GAP. No LLM."""
    name = _fv(profile, "name") or "the brand"
    strengths = _swot_texts(swot, "strengths") or usps
    gaps = _swot_texts(swot, "opportunities") + _swot_texts(swot, "weaknesses")
    edge = _clip(strengths[0]) if strengths else ""
    gap = _clip(gaps[0]) if gaps else ""
    if edge and gap:
        return (f"تقود الحملة بميزة {name} الحقيقية «{edge}» لأنها تعالج فجوة السوق «{gap}» — "
                "رسالة لا يقدر منافس أن يقولها بنفس المصداقية.")
    if edge:
        return f"تقود الحملة بميزة {name} «{edge}» كحجة تموضع أساسية مبنية على دليل."
    return (f"لا توجد بعد ميزة تنافسية مؤكَّدة بالدليل لـ{name} — الحملة تبدأ باختبار الجمهور "
            "والعرض قبل تثبيت رسالة تموضع (نصرّح بهذا بدل اختراع ميزة).")


def _test_cells(profile: Any, swot: Any, offers: List[str],
                maturity: Any = None) -> List[TestCell]:
    """§3 — 3 cells (audience / offer / competitor). Owner ruling (2026-07-20): a NASCENT brand
    (disclosed maturity verdict) gets a 4th AWARENESS cell and the shares renormalize to
    25/30/25/20 — established/unknown keeps the 3-cell 40/35/25 byte-identical."""
    audience = _fv(profile, "audience_type") or "الجمهور الأساسي المستنتَج من الموقع"
    offer = offers[0] if offers else ""
    strength = (_swot_texts(swot, "strengths") or [""])[0]
    cells = [
        TestCell(name="اختبار الجمهور", axis="audience", budget_share=0.40,
                 hypothesis=f"«{audience}» هو الجمهور الأعلى تحويلاً",
                 targeting_note="اهتمامات + ديموغرافيا الجمهور المستنتَج من الموقع",
                 grounded_in="persona (evidenced)",
                 funnel_stage="tofu", angle=f"وعي: نخاطب احتياج «{audience}»",
                 cta="تعرّف أكثر / تابعنا"),
        TestCell(name="اختبار العرض", axis="offer", budget_share=0.35,
                 hypothesis=(f"العرض «{offer}» يدفع أعلى معدل ليدز" if offer
                             else "لا يوجد عرض نشط مستخرَج بدليل — تُختبر رسالة القيمة الأساسية بدلاً منه"),
                 targeting_note="نفس الجمهور، إبداع مختلف يبرز العرض",
                 grounded_in=("marketing_intel offer (verbatim)" if offer else "value proposition"),
                 funnel_stage="bofu", angle=(f"تحويل: العرض «{offer}»" if offer else "تحويل: رسالة القيمة"),
                 cta="احجزي الآن" if offer else "تواصلي معنا"),
        TestCell(name="اختبار التموضع مقابل المنافس", axis="competitor", budget_share=0.25,
                 hypothesis=(f"التموضع على «{strength}» يتفوق على المنافسين" if strength
                             else "التموضع على أقوى ميزة مؤكَّدة يتفوق على المنافسين"),
                 targeting_note="جمهور مشابه للمنافسين + إبداع يبرز الفارق",
                 grounded_in="SWOT strength / competitive gap",
                 funnel_stage="mofu", angle=(f"تفاضل: «{strength}»" if strength else "تفاضل: أقوى ميزة"),
                 cta="قارِن / اكتشف الفرق"),
    ]
    if str(getattr(maturity, "level", "") or "") == "nascent":
        disclosure = str(getattr(maturity, "disclosure", "") or "براند ناشئ")
        cells[0] = cells[0].model_copy(update={"budget_share": 0.30})
        cells[1] = cells[1].model_copy(update={"budget_share": 0.25})
        cells[2] = cells[2].model_copy(update={"budget_share": 0.20})
        cells.insert(0, TestCell(
            name="اختبار الوعي", axis="awareness", budget_share=0.25,
            hypothesis="البراند لسه بيتعرّف — الوصول الواسع بمحتوى تعريفي يبني الطلب قبل التحويل",
            targeting_note="جمهور واسع داخل السوق المستهدف — تحسين على الوصول/المشاهدة",
            grounded_in=f"brand maturity signals: {disclosure[:120]}",
            funnel_stage="tofu", angle="تعريف: مين إحنا وليه نستاهل الانتباه",
            cta="تابعنا"))
    return cells


def _g(obj: Any, key: str, default: Any = None) -> Any:
    v = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if v is None else v


def _dominant_rival(ad_presence: Any) -> Optional[dict]:
    """The rival to counter: most ACTIVE ads; ties broken by the longest-running ad. None when no
    presence data (the legacy strength-cell then stands — no invented counter)."""
    best = None
    for ap in (ad_presence or []):
        row = {"page_name": str(_g(ap, "page_name") or _g(ap, "brand_ref") or ""),
               "brand_ref": str(_g(ap, "brand_ref") or ""),
               "active_count": int(_g(ap, "active_count", 0) or 0),
               "longest_days": int(_g(ap, "longest_days", 0) or 0),
               "winners_30d": int(_g(ap, "winners_30d", 0) or 0),
               "fatigue_groups": int(_g(ap, "fatigue_groups", 0) or 0),
               "top_format": str(_g(ap, "top_format", "") or ""),
               "top_cta": str(_g(ap, "top_cta", "") or "")}
        if best is None or (row["active_count"], row["longest_days"]) > \
                (best["active_count"], best["longest_days"]):
            best = row
    return best


def _rival_plays_price(rival_mi: Any) -> bool:
    """Does the rival's evidence-bound intel show a PRICE play (offers, or a price message angle)?"""
    if not rival_mi:
        return False
    offers = _g(rival_mi, "offers", []) or []
    if offers:
        return True
    for ang in (_g(rival_mi, "message_angles", []) or []):
        val = str(_g(ang, "value", "") or "").lower()
        if "price" in val or "سعر" in val or "خصم" in val or "discount" in val:
            return True
    return False


def _counter_cell(profile: Any, swot: Any, usps: List[str], rival: dict,
                  rival_mi: Any, base: TestCell) -> TestCell:
    """ش4 — the COUNTER-play cell. Deterministic rules: rival dominant format image→we bet Reel,
    video→we bet a premium static/carousel; rival plays price→we lead with OUR evidenced quality
    (never a discount war). Unknown format AND no price signal → the legacy cell stands unchanged."""
    fmt = rival.get("top_format", "")
    price_play = _rival_plays_price(rival_mi)
    if not fmt and not price_play:
        return base
    strength = usps[0] if usps else (_swot_texts(swot, "strengths") or [""])[0]
    parts: list[str] = []
    if price_play and strength:
        parts.append(f"بينما «{rival['page_name']}» يلعب على الخصم، نتموضع بجودتنا: "
                     f"«{strength}» — جمهور جودة مش جمهور سعر")
    if fmt == "image":
        parts.append("المنافس ثابت على إعلانات الصور — نراهن على Reel فيديو يكسر ملل السوق")
        angle = "تفاضل بالفورمات: Reel ضد بوستر المنافس"
    elif fmt == "video":
        parts.append("المنافس غارق في الفيديو — نراهن على تصميم ثابت/كاروسيل بريميوم يبرز في الفيد")
        angle = "تفاضل بالفورمات: ثابت/كاروسيل ضد فيديو المنافس"
    else:
        angle = base.angle
    if not parts and strength:
        parts.append(f"التموضع على «{strength}» يتفوق على المنافسين")
    basis = (f"منافس {rival['page_name']}: {rival['active_count']} إعلان نشط، "
             f"الأقدم شغّال {rival['longest_days']} يوم (Meta Ad Library)")
    return TestCell(name=base.name, axis="competitor", budget_share=base.budget_share,
                    hypothesis="؛ ".join(parts) or base.hypothesis,
                    targeting_note=base.targeting_note,
                    grounded_in="competitor ad presence (Meta Ad Library) + own evidenced strength",
                    funnel_stage=base.funnel_stage, angle=angle,
                    cta=base.cta, counter_basis=basis)


_GUARDRAILS = [
    "كون مغلق: الخطة تُبنى حصراً من مخرجات مسحوبة/مستخرَجة بدليل — لا معرفة خارجية ولا اختراع.",
    "منع الحشو: ممنوع «الأفضل/الأول/الرائد» إلا بدليل حرفي (نفس موات مستخرِج الذكاء التسويقي).",
    "فصل منطقي: تحليل → بناء → مراجعة؛ أرقام المال (الطاقة/Kill-Switch/Scale) قواعد config لا تخمين LLM.",
]


def build_media_plan_v2(
    profile: Any, *, media_plan: Any = None, swot: Any = None, marketing_intel: Any = None,
    capacity: Optional[int] = None, objective: Optional[str] = None,
    monthly_budget: Optional[float] = None, category: Optional[str] = None,
    ad_presence: Any = None, competitor_intel: Any = None, maturity: Any = None,
    margin: Optional[float] = None,
) -> MediaPlanV2:
    """Assemble the v2 plan from a closed universe of grounded inputs. `marketing_intel` is the
    evidence-bound extractor output (offers/USPs/pain-points already past the moat). `capacity`/
    `objective`/`monthly_budget` are the §7 human inputs — when absent the system SAYS it needs the
    number (open_inputs) instead of guessing. Deterministic; no caller, no spend."""
    def _mi_list(key: str) -> List[str]:
        if marketing_intel is None:
            return []
        lst = marketing_intel.get(key) if isinstance(marketing_intel, dict) \
            else getattr(marketing_intel, key, None)
        return _field_values(lst)

    offers, usps, pains = _mi_list("offers"), _mi_list("usps"), _mi_list("pain_points")

    rc = build_reality_check(profile, capacity=capacity, category=category, margin=margin)

    open_inputs: List[str] = []
    if capacity is None:
        open_inputs.append("طاقة الاستيعاب الفعلية (عملاء/يوم) — مستخدَم الافتراضي مؤقتاً")
    if not objective and not (media_plan and (_fv(media_plan, "objective"))):
        open_inputs.append("هدف الحملة (ليدز/مبيعات/وعي) — لازم لتوجيه المزيج والقمع")
    if monthly_budget is None:
        open_inputs.append("الميزانية الشهرية — لتقسيم خلايا الاختبار بأرقام مطلقة")

    # ش4 — the counter-play: with real ad-presence facts, cell 3 attacks the dominant rival's
    # format/angle; the rationale carries the longevity evidence WITHOUT inventing a rival CPL.
    cells = _test_cells(profile, swot, offers, maturity=maturity)
    rationale: List[str] = []
    rival = _dominant_rival(ad_presence)
    if rival:
        rival_mi = None
        if isinstance(competitor_intel, dict):
            rival_mi = competitor_intel.get(rival.get("brand_ref")) \
                or competitor_intel.get(rival.get("page_name"))
        for i, c in enumerate(cells):
            if c.axis == "competitor":
                cells[i] = _counter_cell(profile, swot, usps, rival, rival_mi, c)
        note = (f"منافس {rival['page_name']} يشغّل نفس الإعلان {rival['longest_days']} يوم — "
                "إشارة قابلية ربح للفئة؛ لا نملك CPL المنافس ولن نخترعه — "
                "أدخلي رقمك الحقيقي ليُعايَر زر الطوارئ")
        rationale.append(note)
        rc.notes.append(note)

    return MediaPlanV2(
        brand_ref=_fv(profile, "name"),
        strategic_narrative=_strategic_narrative(profile, swot, usps),
        test_cells=cells,
        reality_check=rc,
        offers=offers, usps=usps, pain_points=pains,
        guardrails=list(_GUARDRAILS),
        open_inputs=open_inputs,
        competitor_rationale=rationale,
    )
