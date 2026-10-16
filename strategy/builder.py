"""Strategy builder — turn a BusinessProfile into a concrete N-day CONTENT CALENDAR.

An LLM (the same `Caller` the poster uses) plans a scheduled mix of posts/reels across
platforms, grounded in the brand's real persona + offerings and, optionally, CURRENT
trends (from `trends.top_trends`). A content PLAN is DESIGN/strategy, not a factual
claim, so paraphrase is fine (the two-truth-domains rule); the poster/reel still validate
any hard fact at render time.

Degrades gracefully: with no caller (or on error) it emits a deterministic plan that
cycles the brand's real offerings across the calendar — never empty, never fabricated.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# P-18: an unfilled template placeholder ("[Customer Name]", "[Industry]", "[Client Type]") must
# never reach a client's calendar. Matches bracketed tokens naming a to-be-filled slot.
_PLACEHOLDER_RE = re.compile(
    r"\[[^\]]*\b(name|customer|client|industry|brand|company|product|service|type|city|"
    r"location|region|audience|topic|x)\b[^\]]*\]", re.IGNORECASE)

# A resolved brand name that is really the "couldn't identify the brand" fallback — never shippable.
_FALLBACK_NAMES = {"the brand", "brand", "unknown business", "unknown", "your brand",
                   "your business", "your company", "n/a", "none"}


def _name_is_placeholder(name: str) -> bool:
    return (name or "").strip().lower() in _FALLBACK_NAMES or not (name or "").strip()

_DEFAULT_PLATFORMS = ["instagram", "tiktok", "linkedin"]   # last-resort only (see _platforms_for)
_CONTENT_TYPES = ["reel", "post", "story", "carousel"]

# Platforms a content calendar can actually schedule to (messaging apps / dev sites excluded).
_CONTENT_PLATFORMS = ("facebook", "instagram", "tiktok", "linkedin", "youtube")


def _platforms_for(profile: dict[str, Any]) -> list[str]:
    """Pick the calendar's platforms from the BRAND'S OWN accounts + the media plan's channel
    weights, instead of a hardcoded instagram/tiktok/linkedin trio (P-09).

    The old default produced ZERO Facebook for EVERY brand while every audited brand's largest
    page was Facebook (Egypt = 51.6M FB users), and forced a LinkedIn slot onto restaurants/
    retailers with no LinkedIn presence — all while the system's OWN media plan weighted Facebook
    highest. Now:
      1. take the platforms the brand actually maintains (scraped `social_presence`),
      2. order them by `media_plan.config.channel_weights(category)` (Facebook-heavy by design),
      3. if the brand shows no social accounts, fall back to the category's top weighted channels
         (which INCLUDE Facebook) — never the old trio.
    """
    present: list[str] = []
    seen: set[str] = set()
    for e in (profile.get("social_presence") or profile.get("social_links")
              or profile.get("social_profiles") or []):
        plat = (e.get("platform") if isinstance(e, dict) else None) or ""
        plat = str(plat).strip().lower()
        if plat in _CONTENT_PLATFORMS and plat not in seen:
            seen.add(plat)
            present.append(plat)
    try:
        from media_plan.config import channel_weights
        weights = channel_weights(str(_val(profile.get("category")) or ""))
    except Exception:  # noqa: BLE001
        weights = {}
    if present:
        present.sort(key=lambda p: weights.get(p, 0.0), reverse=True)
        return present
    top = [p for p, w in sorted(weights.items(), key=lambda kv: kv[1], reverse=True) if w > 0]
    return top[:4] or list(_DEFAULT_PLATFORMS)


# ---- structured LLM output -------------------------------------------------
class _PlanItem(BaseModel):
    day_offset: int                # 0 .. days-1
    platform: str
    content_type: str              # reel | post | story | carousel
    topic: str
    angle: str = ""
    hook: str = ""
    # P-15: the funnel STAGE this item serves (tofu=awareness | mofu=consideration | bofu=action)
    # + its call-to-action. A calendar without funnel intent is a list of posts, not a plan; the
    # objective biases the mix toward BOFU when the goal is leads/sales.
    funnel_stage: str = ""         # tofu | mofu | bofu
    cta: str = ""


class _PlanResponse(BaseModel):
    items: list[_PlanItem]


# ---- public calendar types -------------------------------------------------
@dataclass
class ContentItem:
    date: str                      # ISO YYYY-MM-DD
    platform: str
    content_type: str
    topic: str
    angle: str = ""
    hook: str = ""
    # P-15: funnel intent per item — the calendar is now a funnel plan, not a flat post list.
    funnel_stage: str = ""         # tofu | mofu | bofu
    cta: str = ""
    # Recording ONLY (additive): what the grounding gate blanked or replaced on THIS item, so the
    # audit trail shows the remediation rather than making removed copy silently vanish.
    remediation: list[dict] = field(default_factory=list)


@dataclass
class ContentCalendar:
    business_name: str
    start_date: str
    days: int
    items: list[ContentItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**{k: v for k, v in asdict(self).items() if k != "items"},
                "items": [asdict(i) for i in self.items]}

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _val(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def _profile_name(profile: dict[str, Any]) -> str:
    n = _val(profile.get("name")) or _val(profile.get("business_name"))
    return str(n) if n else "The brand"


# Language-matched generic themes for the LAST-RESORT fallback — an Arabic brand must not get
# hardcoded ENGLISH topics that then render as English poster headlines (the language lock).
_FILLER_EN = ["Brand highlight", "Customer story", "Behind the scenes", "Offer spotlight"]
_FILLER_AR = ["أبرز ما نقدّمه", "قصة عميل", "من وراء الكواليس", "عرض مميز"]


# CS-04 (2026-07-18): the country->dialect map moved to `poster/locale.py` (beside `country_of`,
# the country fact it depends on) so the poster copy path can reuse the SAME map instead of adding a
# fourth hardcoded table. Kept as a thin re-export here so this module's own caller (:234) and any
# importer of `strategy.builder._arabic_dialect_for` keep working unchanged.
from poster.locale import arabic_dialect_for as _arabic_dialect_for


def _cadence_for(category: str) -> int:
    """P-14: per-category posting cadence (items/week) from media_plan.config — the same config
    that owns channel_weights. Falls back to 4/week if the config import ever fails."""
    try:
        from media_plan.config import cadence_for
        return cadence_for(category)
    except Exception:  # noqa: BLE001
        return 4


def _brand_is_arabic(profile: dict[str, Any]) -> bool:
    """Whether the evidenced dominant content language is Arabic.

    Market country, audience geography, and incidental Arabic glyphs are deliberately excluded:
    each answers a different question and none may overwrite a measured content language.
    """
    from poster.locale import content_language_of

    return content_language_of(profile) == "ar"


def _profile_topics(profile: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    for o in (profile.get("offerings") or []):
        name = _val(o.get("name") if isinstance(o, dict) else o)
        if isinstance(name, str) and name.strip():
            topics.append(name.strip())
    for vp in (profile.get("value_propositions") or [])[:4]:
        # A value_proposition is an EvidencedField -> text under 'value' (NOT 'text').
        v = _val(vp)
        if isinstance(v, str) and v.strip():
            topics.append(v.strip())
    tag = _val(profile.get("tagline"))          # real brand copy — one more grounded topic
    if isinstance(tag, str) and tag.strip():
        topics.append(tag.strip())
    # Dedup, order-preserving.
    seen: set[str] = set()
    uniq = [t for t in topics if not (t in seen or seen.add(t))]
    if uniq:
        return uniq
    return _FILLER_AR if _brand_is_arabic(profile) else _FILLER_EN


def _coerce_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except Exception:
            pass
    return date.today()


def _profile_prices(profile: dict[str, Any]) -> list[str]:
    """P-10: the brand's REAL price points (offering name + price_text) — the calendar was blind to
    these (`_profile_topics` keeps names only), so it could never write a priced/BOFU offer. Dedup,
    order-preserving, capped. Only real scraped prices; never a fabricated one."""
    out: list[str] = []
    for o in (profile.get("offerings") or []):
        if not isinstance(o, dict):
            continue
        name = _val(o.get("name"))
        price = _val(o.get("price_text")) or _val(o.get("price"))
        price = str(price).strip() if price else ""
        if not price:
            continue
        out.append(f"{str(name).strip()}: {price}" if name and str(name).strip() else price)
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))][:6]


def _swot_angle(swot: Any) -> str:
    """P-10: the strategy angle from SWOT — strengths to AMPLIFY and opportunities to SEIZE (the two
    the calendar can act on). Accepts a dict or an object; items may be strings or {text}/{value}."""
    if not swot:
        return ""

    def _items(key: str) -> list[str]:
        v = swot.get(key) if isinstance(swot, dict) else getattr(swot, key, None)
        out: list[str] = []
        for it in (v or [])[:3]:
            if isinstance(it, str):
                t = it
            elif isinstance(it, dict):
                t = it.get("text") or it.get("value") or ""
            else:
                t = getattr(it, "text", None) or getattr(it, "value", None) or ""
            if t and str(t).strip():
                out.append(str(t).strip())
        return out

    parts: list[str] = []
    s, o = _items("strengths"), _items("opportunities")
    if s:
        parts.append("Strengths to amplify: " + "; ".join(s))
    if o:
        parts.append("Opportunities to seize: " + "; ".join(o))
    return "\n".join(parts)


def _objective_of(media_plan: Any) -> str:
    """The campaign objective from the media plan (dict or object). '' when absent."""
    if not media_plan:
        return ""
    obj = media_plan.get("objective") if isinstance(media_plan, dict) else getattr(media_plan, "objective", None)
    obj = _val(obj)
    return str(obj).strip() if obj else ""


def _persona_block(profile: dict[str, Any], *, swot: Any = None, media_plan: Any = None) -> str:
    lines = [f"Brand: {_profile_name(profile)}"]
    try:
        from poster.locale import country_of
        country = country_of(profile)
        if country:
            lines.append(f"Country (anchor imagery/culture/dialect here): {country}")
    except Exception:  # noqa: BLE001
        pass
    for key, label in [("category", "Category"), ("tagline", "Tagline"),
                       ("audience_type", "Audience"), ("tone_of_voice", "Tone")]:
        v = _val(profile.get(key))
        if isinstance(v, str) and v.strip():
            lines.append(f"{label}: {v.strip()}")
    offs = _profile_topics(profile)[:8]
    if offs:
        lines.append("Offerings / themes: " + "; ".join(offs))
    prices = _profile_prices(profile)
    if prices:
        lines.append("Real price points (ground offer/BOFU content; NEVER invent a price): "
                     + "; ".join(prices))
    obj = _objective_of(media_plan)
    if obj:
        lines.append(f"Campaign objective (bias the calendar toward it): {obj}")
    swot_line = _swot_angle(swot)
    if swot_line:
        lines.append("Strategic angle (from SWOT/TOWS):\n" + swot_line)
    return "\n".join(lines)


def _llm_plan(profile, caller, *, days, platforms, trends, target,
              swot=None, media_plan=None) -> list[_PlanItem]:
    persona = _persona_block(profile, swot=swot, media_plan=media_plan)
    trend_block = ""
    if trends:
        titles = [getattr(t, "title", None) or (t.get("title") if isinstance(t, dict) else None) for t in trends]
        titles = [t for t in titles if t][:6]
        if titles:
            trend_block = ("\nRIDE THESE CURRENT TRENDS where they fit the brand "
                           "(tie an item to one, don't force it):\n- " + "\n- ".join(titles))
    lang = (f"{_arabic_dialect_for(profile)} — write EVERY topic, angle and hook in Arabic, "
            "no Latin letters" if _brand_is_arabic(profile) else "English")
    # P-15: the funnel mix follows the campaign objective. Leads/sales objectives weight BOFU
    # heavier (more action content); awareness/engagement stay TOFU-led. A balanced default when
    # no objective is set. This shapes the PLAN, never fabricates a claim (two-truth-domains).
    obj = _objective_of(media_plan).lower()
    if any(k in obj for k in ("lead", "sale", "purchase", "conversion")):
        funnel_note = ("FUNNEL MIX — the objective is action-driven: weight the calendar toward "
                       "BOFU (~40% bofu action/offer posts, ~35% mofu consideration, ~25% tofu "
                       "awareness). Every BOFU item needs a concrete CTA tied to a REAL offering.")
    elif any(k in obj for k in ("aware", "reach", "engage")):
        funnel_note = ("FUNNEL MIX — the objective is top-of-funnel: weight toward TOFU (~50% "
                       "awareness, ~30% mofu, ~20% bofu), CTAs soft (follow/learn more).")
    else:
        funnel_note = ("FUNNEL MIX — no objective set: keep a balanced funnel (~34% tofu, ~33% "
                       "mofu, ~33% bofu) so the plan drives awareness AND action.")
    system = (
        "You are a senior social-media strategist. Produce a concrete, varied content "
        "calendar for the brand below — a realistic mix of formats, angles, and platforms. "
        "Ground every item in the brand's real persona/offerings; do NOT invent facts, "
        "prices, or claims. Vary content_type and angle so the feed isn't repetitive. "
        "Every item serves a FUNNEL STAGE (tofu=awareness, mofu=consideration, bofu=action) and "
        "carries a CTA that matches that stage — BOFU items call a concrete action on a REAL "
        "offering (book/order/enquire), TOFU items invite (follow/learn). " + funnel_note + " "
        "When current trends are supplied, use them to shape the TIMING and ANGLE of grounded "
        "content — never to invent a fact or claim the brand can't support. A trend is a reason "
        "to feature a REAL offering now, not a license to promise something new. Most of the "
        "calendar stays evergreen-grounded; trends flavor a SUBSET. "
        f"LANGUAGE: {lang}."
    )
    user = (
        f"{persona}\n{trend_block}\n\n"
        f"Plan {target} items EVENLY spread across a {days}-day window (use the FULL range of "
        f"day_offset 0..{days-1}, not just the first days). "
        f"Platforms to use: {', '.join(platforms)}. "
        f"content_type ∈ {{reel, post, story, carousel}}. funnel_stage ∈ {{tofu, mofu, bofu}}. "
        f"Each item: day_offset, platform, content_type, funnel_stage, topic, a one-line angle, "
        f"a scroll-stopping hook, and a stage-appropriate cta. Write the topic/angle/hook/cta "
        f"in {lang}."
    )
    resp, _usage = caller(system, user, _PlanResponse, group_name="content_strategy")
    return list(resp.items)


def _fallback_plan(profile, *, days, platforms, target) -> list[_PlanItem]:
    topics = _profile_topics(profile)
    items: list[_PlanItem] = []
    for i in range(target):
        # Spread items EVENLY across the whole window. The old `i * (days // target)` floored
        # the spacing to 1 whenever target > days/2, front-loading every item into the first
        # third and leaving the tail empty (MEASURED: a 30-day/17-item plan filled only days
        # 0-16). Interpolating over [0, days-1] uses the full window.
        off = round(i * (days - 1) / (target - 1)) if target > 1 else 0
        items.append(_PlanItem(
            day_offset=max(0, min(days - 1, off)),
            platform=platforms[i % len(platforms)],
            content_type=_CONTENT_TYPES[i % len(_CONTENT_TYPES)],
            topic=topics[i % len(topics)],
            angle="",
            hook="",
        ))
    return items


def _hook_is_grounded(text: str, ledger: Any) -> bool:
    """Evidence-Ledger predicate for one generated calendar copy field.

    True iff `text` carries NO unsourced falsifiable claim — a number/year, a
    superlative/ranking, a credential (award/certification/guarantee), or a free-offer
    claim that the brand's real evidence doesn't support. Pure paraphrase (no hard claim)
    always passes, so the gate is strict on fabrication without freezing creative copy.

    The strategy ledger is profile-only (every entry is brand-tier), so there is no
    web-snippet reputability dimension here — unlike `pick_angle`, which also ingests live
    research facts. OPT-IN: `build_strategy` only consults this when a ledger is supplied
    (the gate is wired in a separate step, after the gray-case FP/FN measurement).
    """
    return not any(not v.sourced for v in ledger.audit_text(text or ""))


def _blank_record(field_name: str, text: str, ledger: Any) -> dict:
    """RECORDING ONLY — describe a calendar field the gate blanked (it does NOT decide the
    blank; the caller already did). Captures the original text + the unsourced claim kinds
    so the per-item audit shows the fabrication was caught and handled."""
    uns = sorted({v.claim.kind for v in ledger.audit_text(text or "") if not v.sourced})
    return {"field": field_name, "original_text": text, "unsourced_claims": uns,
            "action": "blanked",
            "note": f"fabricated {field_name} removed; item runs on its sourced topic"}


def _topic_replacement_record(text: str, replacement: str, ledger: Any) -> dict:
    """Record a rejected generated topic and the existing grounded fallback that replaced it."""
    uns = sorted({v.claim.kind for v in ledger.audit_text(text or "") if not v.sourced})
    return {"field": "topic", "original_text": text, "unsourced_claims": uns,
            "action": "replaced", "replacement_text": replacement,
            "note": "fabricated topic removed; replaced with a grounded profile topic"}


def build_strategy(
    profile: dict[str, Any],
    caller: Optional[Any] = None,
    *,
    days: int = 30,
    platforms: Optional[list[str]] = None,
    trends: Optional[list] = None,
    start_date=None,
    cadence_per_week: Optional[int] = None,
    ledger: Any = None,
    swot: Any = None,
    media_plan: Any = None,
) -> ContentCalendar:
    """Build an N-day `ContentCalendar`. Uses the LLM when `caller` is given; otherwise
    (or on error) a deterministic plan over the brand's real offerings.

    When an Evidence `ledger` is supplied, every generated textual field passes the grounding
    gate: unsupported hook/angle/CTA copy is blanked and an unsupported topic is replaced by the
    existing profile-derived fallback because topic is also a downstream headline. Pure creative
    copy passes untouched. Without a ledger the behaviour is exactly as before."""
    platforms = platforms or _platforms_for(profile)   # P-09: brand's own channels, not a trio
    start = _coerce_date(start_date)
    # P-14: posting cadence is PER-CATEGORY (a clinic ≠ a restaurant ≠ a SaaS), not a flat 4/week.
    # An explicit `cadence_per_week` still wins; None resolves the category default from config.
    if cadence_per_week is None:
        cadence_per_week = _cadence_for(str(_val(profile.get("category")) or ""))
    target = max(1, round(days / 7 * cadence_per_week))

    # HARD STOP (P-18): never ship a deliverable for a brand we couldn't identify. A FALLBACK NAME
    # ("The brand" / "Unknown Business") means the scrape failed to identify the brand — marasim
    # shipped a calendar literally named "The brand". Return an EMPTY calendar so an advisor Gap
    # surfaces the real problem, instead of fabricating a generic plan under a placeholder name.
    # (A named-but-thin brand still builds — weaker content, but not a broken deliverable; unfilled
    # "[Customer Name]"-style placeholders are scrubbed per-item below regardless.)
    _name = _profile_name(profile)
    if _name_is_placeholder(_name):
        logger.warning("strategy: brand not identified (fallback name %r) -> empty calendar; "
                       "surface an advisor Gap instead of fabricating a plan", _name)
        return ContentCalendar(business_name=_name, start_date=start.isoformat(),
                               days=days, items=[])

    plan: Optional[list[_PlanItem]] = None
    if caller is not None:
        try:
            plan = _llm_plan(profile, caller, days=days, platforms=platforms,
                             trends=trends, target=target, swot=swot, media_plan=media_plan)
        except Exception:
            plan = None
    if not plan:
        plan = _fallback_plan(profile, days=days, platforms=platforms, target=target)

    # Existing deterministic topics are the honest replacement strategy when a generated TOPIC
    # itself carries an unsupported hard claim. Keep only candidates that pass this same Ledger;
    # with no Ledger this list is irrelevant and the backwards-compatible behaviour is unchanged.
    grounded_topic_fallbacks = [t for t in _profile_topics(profile)
                                if ledger is None or _hook_is_grounded(t, ledger)]

    items: list[ContentItem] = []
    for item_index, it in enumerate(plan):
        off = max(0, min(days - 1, int(getattr(it, "day_offset", 0))))
        hook, angle = str(it.hook or ""), str(it.angle or "")
        topic = str(it.topic or "")
        cta = str(getattr(it, "cta", "") or "")
        funnel_stage = str(getattr(it, "funnel_stage", "") or "").strip().lower()
        if funnel_stage not in ("tofu", "mofu", "bofu"):
            funnel_stage = ""                              # only the three valid stages survive
        # P-18: an item whose TOPIC still carries an unfilled placeholder is unusable — skip it.
        if _PLACEHOLDER_RE.search(topic):
            continue
        # Scrub unfilled placeholders from hook/angle/cta (the item keeps its grounded topic).
        if _PLACEHOLDER_RE.search(hook):
            hook = ""
        if _PLACEHOLDER_RE.search(angle):
            angle = ""
        if _PLACEHOLDER_RE.search(cta):
            cta = ""
        rem: list[dict] = []
        if ledger is not None:
            # Drop-to-grounded: every generated factual surface uses one contract. Hook, angle,
            # and CTA can be honestly omitted; topic is structurally central and becomes a poster
            # headline downstream, so a rejected topic uses the existing profile-derived fallback
            # plan rather than inventing a replacement or shipping an empty calendar concept.
            # (The `rem.append(...)` lines are RECORDING ONLY — the blank conditions and
            # their results are byte-for-byte unchanged; they only make the catch visible.)
            if topic and not _hook_is_grounded(topic, ledger):
                replacement = (grounded_topic_fallbacks[
                    item_index % len(grounded_topic_fallbacks)] if grounded_topic_fallbacks else "")
                rem.append(_topic_replacement_record(topic, replacement, ledger))
                topic = replacement
            if hook and not _hook_is_grounded(hook, ledger):
                rem.append(_blank_record("hook", hook, ledger))
                hook = ""
            if angle and not _hook_is_grounded(angle, ledger):
                rem.append(_blank_record("angle", angle, ledger))
                angle = ""
            if cta and not _hook_is_grounded(cta, ledger):
                rem.append(_blank_record("cta", cta, ledger))
                cta = ""
        items.append(ContentItem(
            date=(start + timedelta(days=off)).isoformat(),
            platform=str(it.platform), content_type=str(it.content_type),
            topic=topic, angle=angle, hook=hook,
            funnel_stage=funnel_stage, cta=cta, remediation=rem,
        ))
    items.sort(key=lambda c: (c.date, c.platform))
    return ContentCalendar(business_name=_profile_name(profile),
                           start_date=start.isoformat(), days=days, items=items)
