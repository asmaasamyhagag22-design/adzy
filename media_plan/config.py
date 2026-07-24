"""U1 config — KPI targets + channel weights BY CATEGORY, always with a universal default
(INTERFACES §F: "config.py (KPI + channel weights CONFIG by category + default)"; D-8: config
keyed by the profile's universal category signal, never vertical logic in code).

Numbers here are STARTING POINTS for the advisor (industry-typical planning priors), not
promises — every one is overridable and the dashboard surfaces them as editable. The KPI
metric names align with `schemas._COST_PER_RESULT_METRICS` so the 3×CPL learning floor
(`CampaignObjective._learning_floor`) engages automatically when a target is set.
"""
from __future__ import annotations

from typing import Optional

from .schemas import KPITarget, MetaObjective

# ---------------------------------------------------------------------
# Channel weights: relative budget emphasis per channel, by category class.
# Universal default first; categories only OVERRIDE what differs.
# ---------------------------------------------------------------------

_DEFAULT_CHANNELS: dict[str, float] = {
    "facebook": 0.40, "instagram": 0.40, "tiktok": 0.10, "youtube": 0.10,
}

_CHANNELS_BY_CATEGORY: dict[str, dict[str, float]] = {
    # visual product verticals lean harder into IG/TikTok
    "ecommerce": {"facebook": 0.30, "instagram": 0.40, "tiktok": 0.20, "youtube": 0.10},
    "retail":    {"facebook": 0.30, "instagram": 0.40, "tiktok": 0.20, "youtube": 0.10},
    "beauty":    {"facebook": 0.25, "instagram": 0.45, "tiktok": 0.20, "youtube": 0.10},
    "restaurant": {"facebook": 0.35, "instagram": 0.40, "tiktok": 0.20, "youtube": 0.05},
    "cafe":      {"facebook": 0.35, "instagram": 0.40, "tiktok": 0.20, "youtube": 0.05},
    # trust/consideration verticals lean FB/YT
    "clinic":    {"facebook": 0.50, "instagram": 0.30, "tiktok": 0.05, "youtube": 0.15},
    "hospital":  {"facebook": 0.50, "instagram": 0.30, "tiktok": 0.05, "youtube": 0.15},
    "education": {"facebook": 0.45, "instagram": 0.25, "tiktok": 0.10, "youtube": 0.20},
    "government": {"facebook": 0.55, "instagram": 0.25, "tiktok": 0.05, "youtube": 0.15},
    "services_b2b": {"facebook": 0.45, "instagram": 0.20, "tiktok": 0.05, "youtube": 0.30},
}


def channel_weights(category: Optional[str]) -> dict[str, float]:
    """Channel emphasis for a category (universal default when unknown). Always sums to 1.0."""
    w = dict(_CHANNELS_BY_CATEGORY.get((category or "").strip().lower(), _DEFAULT_CHANNELS))
    total = sum(w.values()) or 1.0
    return {k: round(v / total, 4) for k, v in w.items()}


# ---------------------------------------------------------------------
# Posting cadence: how many content items PER WEEK a category realistically sustains (P-14).
# A flat 4/week over-posted low-frequency verticals (a clinic/law firm) and under-posted a
# high-velocity one (a restaurant/e-commerce feed). Universal default first; categories override.
# ---------------------------------------------------------------------

_DEFAULT_CADENCE = 4

_CADENCE_BY_CATEGORY: dict[str, int] = {
    # high-velocity, visual, promo-driven feeds
    "ecommerce": 6, "retail": 6, "beauty": 6, "restaurant": 7, "cafe": 7,
    # steady consideration verticals
    "education": 4, "services_b2b": 3,
    # trust verticals post less but heavier (quality > frequency)
    "clinic": 3, "hospital": 3, "government": 3, "legal": 2, "finance": 3,
}


def cadence_for(category: Optional[str]) -> int:
    """Content items PER WEEK for a category (universal default when unknown). A planning prior,
    overridable — the same D-8 shape as `channel_weights`: keyed on the universal category signal."""
    return _CADENCE_BY_CATEGORY.get((category or "").strip().lower(), _DEFAULT_CADENCE)


# ---------------------------------------------------------------------
# Media Plan v2 · §5 Reality Check inputs — capacity + profit margin, BY CATEGORY (D-8). Planning
# PRIORS, always overridable by a real human number (that is the whole point of §7). Numbers are
# industry-typical estimates, NOT scraped truth — the advisor surfaces them as editable.
# ---------------------------------------------------------------------

_DEFAULT_CAPACITY = 15          # max NEW LEADS/day a small operator can realistically service
_CAPACITY_BY_CATEGORY: dict[str, int] = {
    # appointment-bound service verticals — capacity is the real ceiling
    "clinic": 15, "hospital": 40, "dental": 12, "legal": 6, "salon": 20, "beauty": 20,
    "education": 30, "services_b2b": 8, "finance": 10, "real_estate": 12,
    # high-throughput verticals — capacity rarely binds, so a generous default
    "ecommerce": 200, "retail": 200, "restaurant": 120, "cafe": 120,
}

_DEFAULT_MARGIN = 0.30          # fraction of price that is profit (conservative universal default)
_MARGIN_BY_CATEGORY: dict[str, float] = {
    "clinic": 0.60, "hospital": 0.55, "dental": 0.60, "legal": 0.70, "beauty": 0.55,
    "salon": 0.55, "education": 0.50, "services_b2b": 0.45, "finance": 0.50, "real_estate": 0.40,
    "ecommerce": 0.30, "retail": 0.30, "restaurant": 0.20, "cafe": 0.25,
}

# The share of one sale's PROFIT you are willing to pay to acquire one lead (kill-switch basis).
KILL_SWITCH_PROFIT_SHARE = 0.30
# Fallback max-CPL when NO price can be scraped (surfaced with an explicit disclosure, never silent).
DEFAULT_KILL_CPL = 150.0


def capacity_default(category: Optional[str]) -> int:
    """Max NEW leads/day a category can realistically service (the §5 capacity ceiling). A human
    number overrides this (§7); this is only the fallback."""
    return _CAPACITY_BY_CATEGORY.get((category or "").strip().lower(), _DEFAULT_CAPACITY)


def profit_margin(category: Optional[str]) -> float:
    """Profit margin (fraction of price) for a category — the multiplier in the kill-switch CPL."""
    return _MARGIN_BY_CATEGORY.get((category or "").strip().lower(), _DEFAULT_MARGIN)


def kill_switch_cpl(price: Optional[float], category: Optional[str],
                    margin: Optional[float] = None) -> tuple[float, str]:
    """The §5 max cost-per-lead: `price × profit_margin × 30%`. Returns (max_cpl, basis). With NO
    price the caller must disclose the DEFAULT_KILL_CPL fallback explicitly (owner's ruling — never
    silently invent a number). `margin` (0..1) = the OWNER'S real profit margin — it beats the
    category prior and flips basis to 'price_margin_owner' so the surface can say "من رقمك".
    basis is 'price_margin_owner' | 'price_margin' | 'default_kpi'."""
    if price and price > 0:
        if margin is not None and 0 < margin <= 1:
            return round(price * margin * KILL_SWITCH_PROFIT_SHARE, 2), "price_margin_owner"
        return round(price * profit_margin(category) * KILL_SWITCH_PROFIT_SHARE, 2), "price_margin"
    return DEFAULT_KILL_CPL, "default_kpi"


# ---------------------------------------------------------------------
# KPI planning priors: metric + typical window per objective, tuned per category class.
# target_value stays None unless the category prior sets one — an HONEST unknown engages
# no learning-floor math (the schema only enforces 3×CPL when a target exists).
# ---------------------------------------------------------------------

_KPI_METRIC_BY_OBJECTIVE: dict[MetaObjective, str] = {
    MetaObjective.SALES: "cost_per_purchase",
    MetaObjective.LEADS: "cost_per_lead",
    MetaObjective.TRAFFIC: "cost_per_click",
    MetaObjective.ENGAGEMENT: "cost_per_engagement",
    MetaObjective.AWARENESS: "cpm",
    MetaObjective.APP_PROMOTION: "cost_per_install",
}


def default_kpi(objective: MetaObjective, category: Optional[str] = None) -> KPITarget:
    """The planning KPI for an objective (metric + 7-day window; no invented target_value).

    P-06 (D-8): `unit` defaults to "" (no currency claim), NOT "EGP". The old literal denominated a
    Qatari/Saudi/UAE brand's cost target in Egyptian Pounds and leaked into the launch-bundle
    artifact. The currency should be set from the brand's evidenced country when a target_value is
    actually calibrated (target_value is None until then, so nothing renders a bare "EGP" today)."""
    return KPITarget(metric=_KPI_METRIC_BY_OBJECTIVE.get(objective, "cost_per_result"),
                     target_value=None, unit="", window_days=7)


# --- Funnel split by brand maturity (owner ruling 2026-07-20: automatic-by-evidence) ----------
# The same accessor shape as cadence_for: keyed lookup + universal default. A NASCENT brand
# splits budget across the funnel (awareness on top); anything else = single objective (no split).
#
# WHY 30/70 (a DISCLOSED PRIOR, not a derived fact — the owner asked and this is the father):
#   1. Evidence takes the majority: the conversion objective is the one anchored to a VERIFIED
#      surface (store/whatsapp/form); awareness rests on a maturity inference — weaker father,
#      smaller share.
#   2. Binet & Field's brand/activation research argues ~60/40 toward brand — but for LARGE
#      advertisers on multi-year horizons. A nascent small business lives on next month's cash
#      flow, so awareness gets a meaningful MINORITY (30%), never the majority.
#   3. It is a STARTING prior for the test cells, not a verdict — two weeks of real CPM/CPL
#      through the closed loop are what correct it (same philosophy as the kill-switch).
#   4. Owner-tunable without code: FUNNEL_TOFU_PCT (clamped 5–60% so a typo can't flip the plan).
_DEFAULT_FUNNEL_SPLIT = {"tofu": 0.3, "bofu": 0.7}
_SPLIT_BY_MATURITY = {
    "nascent": {"tofu": 0.3, "bofu": 0.7},
}


def funnel_split_for(maturity) -> dict:
    """Budget split {stage: share} for a maturity level. Env-tunable TOFU share
    (FUNNEL_TOFU_PCT, percent) for the owner's calibration without a code change."""
    import os
    split = dict(_SPLIT_BY_MATURITY.get((str(maturity) or "").strip().lower(),
                                        _DEFAULT_FUNNEL_SPLIT))
    try:
        pct = os.getenv("FUNNEL_TOFU_PCT")
        if pct:
            t = max(0.05, min(0.6, float(pct) / 100.0))
            split = {"tofu": t, "bofu": round(1.0 - t, 4)}
    except Exception:  # noqa: BLE001
        pass
    return split
