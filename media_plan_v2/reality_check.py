"""§5 Reality Check + Kill-Switch — fully deterministic (owner's ruling: money numbers are NEVER
LLM-guessed). Capacity = a human number OR the vertical default; kill-switch CPL = scraped price ×
profit margin × 30% (or a DISCLOSED default when no price); scale/fatigue = the decision_engine's
hardcoded industry rules."""
from __future__ import annotations

import re
from typing import Any, Optional

from decision_engine.schemas import CampaignPolicy
from media_plan.config import capacity_default, kill_switch_cpl

from .schemas import KillSwitch, RealityCheck

# Country -> the currency word used in the human-facing disclosure (kept small + evidence-keyed).
_CURRENCY = {
    "Egypt": "جنيه", "Saudi Arabia": "ريال", "the UAE": "درهم", "Qatar": "ريال قطري",
    "Kuwait": "دينار", "Jordan": "دينار", "Morocco": "درهم مغربي", "Bahrain": "دينار",
    "Oman": "ريال عماني", "Lebanon": "ليرة",
}


def _fv(obj: Any, key: str) -> str:
    v = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    if isinstance(v, dict) and "value" in v:
        v = v.get("value")
    v = getattr(v, "value", v)
    return str(v).strip() if v else ""


def _parse_price_number(raw: Any) -> Optional[float]:
    """The first price NUMBER in a price_text, separator-aware. MEASURED failure (NTI, 2026-07-19):
    'الحد الأقصى للتمويل 65.000 جنية' uses the Arabic thousands-DOT — a naive float() read it as
    65.0 and the kill-switch became nonsense. A dot/comma group of exactly 3 digits repeating from
    a 1-3 digit head is a THOUSANDS separator (65.000 -> 65000, 1,500 -> 1500); a trailing 1-2
    digit fraction stays a decimal (799.99). None when no number."""
    s = str(raw or "")
    m = re.search(r"\d[\d.,]*", s)
    if not m:
        return None
    tok = m.group(0).rstrip(".,")
    try:
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", tok):        # pure thousands-grouped integer
            return float(re.sub(r"[.,]", "", tok))
        # mixed '1,234.56' style: commas group thousands, the final dot is the decimal
        val = float(tok.replace(",", ""))
        return val if val > 0 else None
    except ValueError:
        return None


def _scraped_price(profile: Any) -> Optional[float]:
    """A REPRESENTATIVE real price across the offerings' price_texts — the MEDIAN, so one outlier
    (a financing cap, a bulk price) can't hijack the kill-switch. None when nothing priced — the
    caller then discloses the default. Never invents a number."""
    p = profile.get("profile", profile) if isinstance(profile, dict) and "profile" in profile else profile
    vals: list[float] = []
    for o in (p.get("offerings") if isinstance(p, dict) else getattr(p, "offerings", None)) or []:
        raw = o.get("price_text") if isinstance(o, dict) else getattr(o, "price_text", None)
        raw = raw.get("value") if isinstance(raw, dict) else raw
        v = _parse_price_number(raw)
        if v:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _policy_defaults() -> tuple[float, float, float]:
    """The scale/fatigue thresholds — read from CampaignPolicy's FIELD DEFAULTS so there is ONE
    source of the industry rules (the decision engine), never a second hardcoded copy that drifts."""
    f = CampaignPolicy.model_fields
    return (f["scale_step_max_pct"].default, f["fatigue_frequency"].default,
            f["fatigue_ctr_drop_pct"].default)


def build_reality_check(profile: Any, *, capacity: Optional[int] = None,
                        category: Optional[str] = None,
                        margin: Optional[float] = None) -> RealityCheck:
    """Assemble §5. `capacity` is the human number (§7) — when None the vertical default is used and
    noted as an assumption. Deterministic; no caller, no spend."""
    cat = category or _fv(profile, "category")
    notes: list[str] = []

    if capacity is not None and int(capacity) > 0:
        max_daily_leads, cap_src = int(capacity), "human"
    else:
        max_daily_leads, cap_src = capacity_default(cat), "config_default"
        notes.append(f"طاقة الاستيعاب {max_daily_leads} عميل/يوم افتراضية للمجال — عدّليها بالرقم الحقيقي")

    country = ""
    try:
        from poster.locale import country_of
        country = country_of(profile)
    except Exception:  # noqa: BLE001
        country = ""
    currency = _CURRENCY.get(country, "")

    price = _scraped_price(profile)
    max_cpl, basis = kill_switch_cpl(price, cat, margin=margin)
    if basis == "price_margin_owner":
        notes.append(f"هامش الربح {margin*100:.0f}% من رقمك ✓ — زر الطوارئ محسوب عليه")
    ks = KillSwitch(max_cpl=max_cpl, currency=currency, basis=basis)
    if basis == "default_kpi":
        # owner's exact disclosure shape — the default is stated, never silently applied.
        # D-8: NEVER fall back to "جنيه". When the brand's country can't be resolved from its own
        # evidence, a currency is unknown — and an Egyptian unit on a Qatari (or any) brand is a
        # confident wrong number, the exact failure this project treats as unsourced.
        unit = currency or "بعملتك المحلية"
        ks.disclosure = f"الحد الأقصى للتكلفة {max_cpl:.0f} {unit} (افتراضي لعدم توفر الأسعار)"
        notes.append(ks.disclosure)

    scale_pct, freq, ctr_drop = _policy_defaults()
    scale_rule = (f"وسّعي الميزانية بحد أقصى {scale_pct:.0f}% في اليوم بعد ما تستقر التكلفة "
                  "(قاعدة صناعية، مش تخمين)")
    fatigue_rule = (f"لو التكرار > {freq:.1f} و CTR نزل > {ctr_drop:.0f}% → غيّري الإبداع (إجهاد)")

    return RealityCheck(
        max_daily_leads=max_daily_leads,
        capacity_source=cap_src,
        daily_spend_ceiling=round(max_daily_leads * max_cpl, 2),
        kill_switch=ks,
        scale_rule=scale_rule,
        fatigue_rule=fatigue_rule,
        notes=notes,
    )
