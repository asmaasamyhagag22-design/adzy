"""U1 Media Plan — the builder (the real U1 gate).

`build_campaign_objective(profile, caller)` deduces the ONE objective + destination a brand should
run, TOGETHER, from the brand's real evidence — then grounds the choice in the actual conversion
surfaces the scrape found (a WhatsApp number, a checkout, a phone, an address). The LLM reasons
about WHICH objective; the code supplies + verifies the EVIDENCE. So a deduced destination is only
"grounded" when a real signal backs it — and a brand with no conversion surface yields an honest,
ungrounded objective (flagged downstream for the advisor), never a fabricated store.

Offline-testable: pass a MockCaller. No caller / creds -> None (honest-degrade, like the other
builders). PD-4: this module never hits the network itself.
"""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field

from business_profile.schemas import Confidence, EvidenceItem

from .schemas import (CampaignObjective, Destination, EvidenceRef, FunnelStage, GeoMode,
                      GeoTargeting, MetaObjective, Persona)

logger = logging.getLogger(__name__)

_ECOM_CATEGORIES = {"ecommerce", "retail"}
_ECOM_URL_HINTS = ("cart", "checkout", "/product", "/products", "/shop", "/collections", "add-to-cart",
                   "order", "/menu", "/store", "/buy")
# FIX-1 (universal): this many priced offerings on the brand's OWN domain = an online catalog the
# brand transacts through, even when no URL matches a cart pattern and the category isn't ecom
# (a priced /menu, products priced on the homepage). >=3 guards against a lone incidental price.
_PRICED_CATALOG_MIN = 3

# FIX-2c (universal): order-intent OUTBOUND links on the brand's OWN pages = the brand transacts
# online even when fulfilment lives on an ordering platform (mcdonalds.eg -> "Order" -> mcdelivery;
# zooba -> instashop/#popup-order-now). We read only the brand's own page — never the third party.
_ORDER_ANCHOR_TOKENS = {"order", "shop", "buy", "delivery", "store", "menu", "menus",
                        "اطلب", "اشتري", "تسوق", "توصيل", "دليفري"}
_ORDER_HOST_TOKENS = ("order", "delivery", "shop", "store", "menu", "cart", "checkout")
_ORDER_PATH_RE = re.compile(r"(?:^|[/?#&_.\-])(order|orders|delivery|checkout|cart|buy)(?:$|[/?#&_.\-])")
_APP_STORE_HOSTS = ("play.google.com", "apps.apple.com", "itunes.apple.com", "appgallery.huawei.com")


# ---------------------------------------------------------------------
# tolerant profile access (handles the object profile, the serialized dict,
# and the {"profile": {...}} wrapper — same discipline as the other builders)
# ---------------------------------------------------------------------

def _root(profile: Any) -> Any:
    if isinstance(profile, dict) and "profile" in profile and isinstance(profile["profile"], dict):
        return profile["profile"]
    return profile


def _get(obj: Any, key: str, default: Any = None) -> Any:
    val = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if val is None else val


# P-29: a client deliverable must never carry a raw Python repr — an `AudienceType.MIXED` enum
# (str(enum) is the repr on 3.11), an `EvidencedField(value=...)` model repr, or an `<obj at 0x..>`.
# CS-05: also catch CONTAINER reprs. The first version of this scrubber was scalar-only, so a
# list-typed field (`service_areas: list[EvidencedField[str]]`) went straight through `str()` and
# shipped `"serves: [{'value': 'Cairo', 'evidence': [{'page_url': ...}]}]"` — a raw list-of-dicts,
# evidence internals and all, as a client-facing persona claim.
_REPR_RE = re.compile(
    r"<[\w.]+ object at 0x[0-9a-fA-F]+>"   # <Foo object at 0x7ff...>
    r"|\b[A-Z]\w+\(\w+="                   # EvidencedField(value=...)
    r"|^\[.*\]$"                           # ['Cairo', 'Giza']  / [{'value': ...}]
    r"|^\{.*\}$",                          # {'value': 'Cairo'}
    re.DOTALL,
)

_LIST_JOIN = "، "   # Arabic comma: these strings are read by Arabic-speaking clients


def _clean_value(v: Any) -> str:
    """Unwrap an EvidencedField / dict / Enum / LIST down to a clean human string for CLIENT-facing
    text. Returns '' (and warns) rather than leak a Python repr — CS-05: a scrubber that silently
    passes what it cannot clean IS the failure class, so the fallback drops loudly."""
    for _ in range(4):  # bounded unwrap of EvidencedField-shaped wrappers
        if isinstance(v, dict) and "value" in v:
            v = v.get("value")
        elif not isinstance(v, (str, int, float, bool, Enum)) and hasattr(v, "value"):
            v = getattr(v, "value")
        else:
            break
    if isinstance(v, Enum):
        v = v.value                      # AudienceType.MIXED -> "mixed", never the repr
    if v is None:
        return ""

    # CS-05: a list/tuple field (service_areas) — clean each element, keep the human values.
    if isinstance(v, (list, tuple, set)):
        parts = [_clean_value(item) for item in v]
        return _LIST_JOIN.join(p for p in parts if p)

    s = str(v).strip()
    if not s:
        return ""
    if _REPR_RE.search(s):
        # Loud on purpose: we could not reduce this to human text, so we drop it — but silence here
        # is how the leak shipped in the first place.
        logger.warning("media_plan: dropped an un-scrubbable value (Python repr leaked): %.120s", s)
        return ""
    return s


def _fv(obj: Any, key: str) -> str:
    """A scalar field value, unwrapping EvidencedField (object .value or {"value": ...})."""
    return _clean_value(_get(obj, key))


def _offerings(profile: Any) -> List[dict]:
    out = []
    for o in (_get(profile, "offerings", []) or []):
        name = o.get("name") if isinstance(o, dict) else getattr(o, "name", None)
        if isinstance(name, dict):                     # EvidencedField-shaped name
            name = name.get("value")
        name = getattr(name, "value", name)
        price = o.get("price_text") if isinstance(o, dict) else getattr(o, "price_text", None)
        url = o.get("page_url") if isinstance(o, dict) else getattr(o, "page_url", None)
        out.append({"name": (str(name).strip() if name else None),
                    "price_text": price, "page_url": url})
    return out


def _contact(profile: Any) -> Any:
    return _get(profile, "contact_channels")


def _same_site(url: str, src: str) -> bool:
    """True when `url` lives on the brand's own host (www-insensitive). An offering with NO
    page_url counts as on-site — it was scraped from the brand's own pages."""
    if not url:
        return True
    try:
        from urllib.parse import urlparse
        h_url, h_src = urlparse(url).netloc.lower(), urlparse(src).netloc.lower()
    except ValueError:
        return False
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return bool(h_src) and strip(h_url) == strip(h_src)


# ---------------------------------------------------------------------
# Conversion-surface signals — the grounding substrate for the destination
# ---------------------------------------------------------------------

def _ref(claim: str, url: str, quote: str, extractor: str) -> EvidenceRef:
    """A RESOLVED evidence ref — the signal is a real structural fact from the scrape, so it is
    grounded by construction (the code found it; the model did not assert it)."""
    return EvidenceRef(claim=claim, resolved=True,
                       evidence=[EvidenceItem(page_url=url or "", quote=(quote or "")[:200] or None,
                                              extractor=extractor)])


def _ordering_links(manifest: Any) -> List[dict]:
    """External links on the brand's own pages whose anchor or URL carries order/purchase intent.
    Anchor matches on WHOLE words (so 'workshop'/'borderline' never match), hosts on substring
    (mcdelivery.eg, instashop.com, elmenus.com), paths on delimited tokens ('#popup-order-now'
    matches, '/workshop.html' does not). App-store links are excluded — an app listing is an APP
    surface, not an online store."""
    links = _get(manifest, "links", {}) if manifest else {}
    out: List[dict] = []
    for e in (links.get("external") or []) if isinstance(links, dict) else []:
        if not isinstance(e, dict):
            continue
        href = str(e.get("href") or "").lower()
        if not href or any(h in href for h in _APP_STORE_HOSTS):
            continue
        anchor_words = set(re.findall(r"[\w؀-ۿ]+", str(e.get("anchor_text") or "").lower()))
        try:
            from urllib.parse import urlparse
            parsed = urlparse(href)
            host, path = parsed.netloc, (parsed.path or "") + "?" + (parsed.query or "") + "#" + (parsed.fragment or "")
        except ValueError:
            host, path = "", href
        if (anchor_words & _ORDER_ANCHOR_TOKENS
                or any(t in host for t in _ORDER_HOST_TOKENS)
                or _ORDER_PATH_RE.search(path)):
            out.append(e)
    return out


def conversion_signals(profile: Any, manifest: Any = None) -> List[Tuple[str, Destination, EvidenceRef]]:
    """Every REAL conversion surface the scrape found, each paired with the Destination it enables
    and the resolved evidence that proves it. This is the ground truth the objective is anchored to;
    the deduction may only pick a destination that appears here. `manifest` (optional, the scrape
    manifest dict) adds structural link evidence the profile itself doesn't carry."""
    p = _root(profile)
    src = _fv(p, "source_url") or _fv(p, "url") or _fv(p, "website") or ""
    out: List[Tuple[str, Destination, EvidenceRef]] = []

    cc = _contact(p)
    whatsapp = list(_get(cc, "whatsapp_numbers", []) or []) if cc is not None else []
    phones = list(_get(cc, "phones", []) or []) if cc is not None else []
    has_form = bool(_get(cc, "has_contact_form", False)) if cc is not None else False
    addresses = list(_get(cc, "physical_addresses", []) or []) if cc is not None else []

    if whatsapp:
        out.append(("whatsapp", Destination.WHATSAPP,
                    _ref("the brand takes contact/orders via WhatsApp", src, str(whatsapp[0]), "rule:whatsapp")))
    if phones:
        num = getattr(phones[0], "raw", None) or (phones[0].get("raw") if isinstance(phones[0], dict) else str(phones[0]))
        out.append(("phone", Destination.PHONE_CALL,
                    _ref("the brand publishes a phone line to call", src, str(num), "rule:tel_link")))
    if has_form:
        out.append(("contact_form", Destination.LEAD_FORM,
                    _ref("the site has a contact/booking form", src, "contact form", "rule:contact_form")))
    if addresses:
        out.append(("address", Destination.PHYSICAL_STORE,
                    _ref("the brand has a physical location", src, str(addresses[0]), "rule:address")))

    # online store: an ecommerce/retail category, OR a priced offering on a cart/order URL, OR
    # (FIX-1) a priced catalog — >= _PRICED_CATALOG_MIN priced offerings on the brand's own domain
    category = _fv(p, "category").lower()
    store_url, store_quote = "", ""
    priced_on_site, first_on_site = 0, None
    for off in _offerings(p):
        if not off.get("price_text"):
            continue
        url = (off.get("page_url") or "")
        if not store_url and (any(h in url.lower() for h in _ECOM_URL_HINTS) or category in _ECOM_CATEGORIES):
            store_url, store_quote = url or src, f"{off.get('name')} — {off.get('price_text')}"
        if _same_site(url, src):
            priced_on_site += 1
            if first_on_site is None:
                first_on_site = (url or src, f"{off.get('name')} — {off.get('price_text')}")
    if not store_url and priced_on_site >= _PRICED_CATALOG_MIN and first_on_site:
        store_url = first_on_site[0]
        store_quote = f"{priced_on_site} priced offerings on the brand's own site — e.g. {first_on_site[1]}"
    if store_url:
        out.append(("online_store", Destination.ONLINE_STORE,
                    _ref("the brand sells products through an online store", store_url,
                         store_quote or category, "rule:online_store")))
    elif category in _ECOM_CATEGORIES:
        # CS-06: the category CLASS alone is a HYPOTHESIS, not proof — no store URL, no priced
        # catalog, and no ordering link was found. Emit an UNRESOLVED ref (resolved=False) whose
        # "evidence" is the mere category name, so an objective built on it comes back is_grounded=
        # False and the advisor flags it to verify — never a fact whose evidence is its own claim.
        out.append(("online_store", Destination.ONLINE_STORE,
                    EvidenceRef(claim="the brand's category suggests it may sell products online",
                                resolved=False,
                                evidence=[EvidenceItem(page_url=src or "",
                                                       quote=(category or None),
                                                       extractor="rule:category_class")])))
    else:
        # FIX-2c: no on-site store — an order-intent outbound link on the brand's own page is
        # still structural proof the brand takes online orders (evidence = the brand's page + the
        # anchor/href we found there, not the third-party site)
        olinks = _ordering_links(manifest)
        if olinks:
            e = olinks[0]
            out.append(("online_ordering", Destination.ONLINE_STORE,
                        _ref("the brand takes online orders via an ordering link on its site",
                             str(e.get("page_url") or src),
                             f"{e.get('anchor_text') or 'order link'} -> {e.get('href')}",
                             "rule:ordering_link")))

    # the website itself always exists when we scraped one — a grounded baseline for TRAFFIC/AWARENESS
    if src:
        out.append(("website", Destination.WEBSITE,
                    _ref("the brand has a live website", src, src, "rule:source_url")))
    return out


# ---------------------------------------------------------------------
# The deduction (LLM) + assembly
# ---------------------------------------------------------------------

class _ObjectiveDeduction(BaseModel):
    """What the model returns: WHICH objective, to WHICH available destination, and why."""
    objective: MetaObjective
    destination: Destination
    funnel_stage: FunnelStage
    rationale: str = ""
    confidence: Confidence = Confidence.NONE
    alternatives: List[MetaObjective] = Field(default_factory=list)


_SYSTEM = (
    "You are a senior Meta media-buying strategist. Deduce the ONE campaign objective a brand should "
    "run FIRST, and the destination it drives to — TOGETHER, from the brand's real facts.\n"
    "HARD RULES:\n"
    "- Pick exactly one MetaObjective and one Destination.\n"
    "- The Destination MUST be one of the AVAILABLE conversion surfaces listed below — never invent a "
    "surface the brand doesn't have (no 'Sales / online store' if there is no store).\n"
    "- Match the objective to the surface: a real online store -> Sales; bookings/leads via "
    "WhatsApp/phone/form -> Leads; only a website and no conversion surface -> Traffic or Awareness.\n"
    "- rationale: ONE sentence, grounded in the facts + the chosen surface. Invent no numbers, "
    "claims, or guarantees.\n"
    "- funnel_stage: the stage this single objective serves (a one-objective plan is a complete "
    "single-stage funnel — usually BOFU for a conversion goal, TOFU for pure awareness)."
)


def _deduce_user(profile: Any, signals: List[Tuple[str, Destination, EvidenceRef]]) -> str:
    p = _root(profile)
    offers = [o["name"] for o in _offerings(p) if o.get("name")][:8]
    surfaces = "\n".join(f"- {name} -> Destination '{dest.value}'" for name, dest, _ in signals)
    return (
        f"Brand: {_fv(p, 'name')}\n"
        f"Category: {_fv(p, 'category') or 'unknown'}\n"
        f"What they do: {_fv(p, 'description')[:400]}\n"
        + (f"Offerings: {', '.join(offers)}\n" if offers else "")
        + f"AVAILABLE conversion surfaces (pick the Destination from THESE only):\n{surfaces}\n"
        + "\nReturn the deduced objective, destination, funnel_stage, rationale, confidence, alternatives."
    )


def _ground_destination(dest: Destination,
                        signals: List[Tuple[str, Destination, EvidenceRef]]) -> EvidenceRef:
    """The resolved evidence for the chosen destination — or, if the model picked a destination the
    brand has NO signal for, an UNRESOLVED ref (honest ungrounded output, flagged downstream). We
    never fabricate a signal that isn't in the scrape."""
    for _name, d, ref in signals:
        if d is dest:
            return ref
    return EvidenceRef(claim=f"targets destination '{dest.value}' (no matching conversion surface found)",
                       evidence=[], resolved=False)


def build_campaign_objective(profile: Any, caller: Any = None,
                             manifest: Any = None) -> Optional[CampaignObjective]:
    """Deduce the single grounded CampaignObjective for a brand. None on honest-degrade (no caller /
    creds, or a profile too thin to expose ANY surface). An objective whose destination lacks a real
    signal comes back with `is_grounded == False` — a valid honest output the advisor flags, not a
    fabricated one. `caller` is injectable for tests; defaults to default_caller(strong=True).
    `manifest` (optional scrape-manifest dict) adds link-level surfaces (see conversion_signals)."""
    if caller is None:
        from business_profile.llm import default_caller
        caller = default_caller(strong=True)
    if caller is None:
        logger.info("media_plan: no Gemini caller (creds/SDK); skipping objective deduction")
        return None

    signals = conversion_signals(profile, manifest=manifest)
    if not signals:
        logger.info("media_plan: no conversion surface / website found; too thin to deduce")
        return None

    try:
        dedux, _usage = caller(_SYSTEM, _deduce_user(profile, signals), _ObjectiveDeduction,
                               group_name="media_plan_objective")
    except Exception as e:  # noqa: BLE001 — a call failure must never crash the plan
        logger.warning("media_plan: objective deduction failed: %s", e)
        return None

    return CampaignObjective(
        objective=dedux.objective,
        destination=dedux.destination,
        funnel_stage=dedux.funnel_stage,
        rationale=(dedux.rationale or "").strip() or "(no rationale returned)",
        budget_allocation_pct=100.0,                     # single objective -> the whole budget
        evidence=[_ground_destination(dedux.destination, signals)],
        confidence=dedux.confidence,
        alternatives=list(dedux.alternatives)[:3],
    )


# ---------------------------------------------------------------------
# U1 ASSEMBLY (unblocked by the measured gate: 93% objective, 2026-07-11)
# ---------------------------------------------------------------------

def _field_ref(profile: Any, key: str, claim: str) -> Optional[EvidenceRef]:
    """An EvidenceRef built from a profile EvidencedField: value + its real evidence items.
    None when the field has no value (an honestly-unknown axis); resolved=False when the
    value exists but carries no evidence (asserted-but-ungrounded, the advisor flags it)."""
    p = _root(profile)
    v = _get(p, key)
    if isinstance(v, dict):
        ev = v.get("evidence") or []
    else:
        ev = list(getattr(v, "evidence", None) or [])
    value = _clean_value(v)   # P-29: enum/EvidencedField -> clean string, never a leaked repr
    if not value:
        return None
    items = []
    for e in ev[:3]:
        page = e.get("page_url") if isinstance(e, dict) else getattr(e, "page_url", "")
        quote = e.get("quote") if isinstance(e, dict) else getattr(e, "quote", None)
        items.append(EvidenceItem(page_url=str(page or ""), quote=(str(quote)[:200] if quote else None),
                                  extractor="llm"))
    return EvidenceRef(claim=f"{claim}: {value}"[:300], resolved=bool(items), evidence=items)


def build_persona(profile: Any) -> Persona:
    """The base persona — ONLY axes the profile evidences; everything else honestly None."""
    interests = []
    r = _field_ref(profile, "audience_type", "the brand's audience")
    if r is not None:
        interests.append(r)
    # location: the evidenced service_areas field, else the brand's REAL physical address
    # (a structural fact -> resolved by construction), else honestly None — never a URL
    # dressed up as a location.
    loc = _field_ref(profile, "service_areas", "serves")
    if loc is None:
        p = _root(profile)
        cc = _contact(p)
        for a in (_get(cc, "physical_addresses", []) or []) if cc is not None else []:
            val = a.get("value") if isinstance(a, dict) else getattr(a, "value", a)
            val = str(val or "").strip()
            if val:
                loc = _ref(f"located at {val}"[:300], _fv(p, "source_url"), val, "rule:address")
                break
    return Persona(location_summary=loc, interests=interests)


def build_geo(profile: Any) -> GeoTargeting:
    """WHERE: a real physical address -> RADIUS anchored to it (category-class radius from
    the MarketDefinition config); else NATIONAL (mode-consistent, nothing invented)."""
    p = _root(profile)
    cc = _contact(p)
    addr = ""
    addr_ref: Optional[EvidenceRef] = None
    for a in (_get(cc, "physical_addresses", []) or []) if cc is not None else []:
        val = a.get("value") if isinstance(a, dict) else getattr(a, "value", a)
        val = str(val or "").strip()
        if val:
            addr = val
            ev = (a.get("evidence") if isinstance(a, dict) else getattr(a, "evidence", None)) or []
            items = [EvidenceItem(page_url=str((e.get("page_url") if isinstance(e, dict)
                                                else getattr(e, "page_url", "")) or ""),
                                  quote=addr[:200], extractor="rule:address")
                     for e in ev[:1]] or [EvidenceItem(page_url=_fv(p, "source_url"),
                                                       quote=addr[:200], extractor="rule:address")]
            addr_ref = EvidenceRef(claim=f"physical location: {addr}"[:300],
                                   resolved=True, evidence=items)
            break
    # CS-84: name the brand's country on EVERY geo (root of P-51). '' -> None (no guess).
    try:
        from poster.locale import country_of
        country = country_of(profile) or None
    except Exception:  # noqa: BLE001
        country = None
    if addr:
        from competitor.market_definition import build_market_definition
        md = build_market_definition(p)
        # CS-20: MarketDefinition sets radius_km per CATEGORY — None means national reach (e.g.
        # e-commerce). Respect that even when the brand has an HQ address; do NOT fabricate a 15km
        # radius around a warehouse. Only a category with a real local radius becomes RADIUS.
        if md.geo.radius_km is not None:
            return GeoTargeting(mode=GeoMode.RADIUS, center_address=addr,
                                radius_km=md.geo.radius_km, address_ref=addr_ref, country=country)
        return GeoTargeting(mode=GeoMode.NATIONAL, country=country)
    return GeoTargeting(mode=GeoMode.NATIONAL, country=country)


def _apply_owner_budget(objectives: list, monthly_budget: Any) -> list:
    """Spend the owner's OWN monthly number across the objectives she already has.

    Owner (2026-07-20): "حساب التكلفة بكتب مش بيحسب غير الديفلت بس" — she typed a budget in the
    vault and every surface still said "مطلوب رقمك", because `test_budget` was never set anywhere.

    Nothing is invented here: the month's budget is split by the funnel allocation the plan itself
    computed, so the parts sum to HER number. An objective whose share cannot buy a readable test
    (below the learning floor — 3 x target cost x ad sets, enforced by CampaignObjective) keeps
    test_budget=None and SAYS why, instead of shipping a number that can't be judged or raising.
    """
    try:
        budget = float(monthly_budget)
    except (TypeError, ValueError):
        return objectives
    if budget <= 0:
        return objectives

    out = []
    for o in objectives:
        share = budget * float(o.budget_allocation_pct or 0.0) / 100.0
        if share <= 0:
            out.append(o)
            continue
        note = (f"ميزانيتك الشهرية ({budget:,.0f}) × حصة هذا الهدف "
                f"{o.budget_allocation_pct:g}٪ = {share:,.0f}")
        try:
            # model_validate, NOT model_copy: copy does not re-run validators, so a share below
            # the learning floor would slip through silently — exactly what the floor exists to stop.
            out.append(type(o).model_validate(
                {**o.model_dump(), "test_budget": share, "budget_note": note}))
        except Exception:      # the learning-floor validator refused it — say so, don't crash
            kpi = getattr(o, "kpi_target", None)
            tv = getattr(kpi, "target_value", None)
            floor = 3.0 * float(tv) * int(o.num_ad_sets or 1) if tv else None
            out.append(o.model_copy(update={
                "test_budget": None,
                "budget_note": (f"حصة هذا الهدف من ميزانيتك ({share:,.0f}) تحت أرضية التعلّم"
                                + (f" ({floor:,.0f} = 3 × التكلفة المستهدفة {float(tv):,.0f} × "
                                   f"{int(o.num_ad_sets or 1)} مجموعة إعلانية)" if floor else "")
                                + " — الاختبار مش هيقرا نتيجة، ارفعي الميزانية أو قللي المجموعات"),
            }))
    return out


def build_media_plan(profile: Any, caller: Any = None, manifest: Any = None,
                     run_id: str = "", maturity: Any = None,
                     monthly_budget: Any = None) -> Optional["MediaPlan"]:
    """U1's final artifact: ONE grounded MediaPlan. Default = the deduced single objective (with
    its category KPI prior). Owner ruling (2026-07-20, automatic-by-evidence): a NASCENT brand
    (media_plan.maturity verdict, ≥2 disclosed signals) gets a FUNNEL plan instead — a TOFU
    Awareness objective (rationale = the maturity disclosure, kpi=cpm with NO invented target)
    on top of the deduced conversion objective, split per config.funnel_split_for. Established/
    unknown maturity keeps today's single-objective plan byte-identical.
    None on honest-degrade (no objective could be grounded)."""
    from media_plan.schemas import MediaPlan
    obj = build_campaign_objective(profile, caller=caller, manifest=manifest)
    if obj is None:
        return None
    from media_plan.config import default_kpi
    p = _root(profile)
    obj = obj.model_copy(update={"kpi_target": default_kpi(obj.objective, _fv(p, "category"))})

    objectives = [obj]
    level = str(getattr(maturity, "level", "") or "")
    if level == "nascent" and obj.objective != MetaObjective.AWARENESS:
        from media_plan.config import funnel_split_for
        from media_plan.schemas import Destination, FunnelStage, KPITarget
        split = funnel_split_for(level)
        tofu_pct = float(split.get("tofu", 0.3)) * 100.0
        awareness = CampaignObjective(
            objective=MetaObjective.AWARENESS,
            destination=Destination.WEBSITE,      # the always-existing grounded surface (:264)
            funnel_stage=FunnelStage.TOFU,
            rationale=str(getattr(maturity, "disclosure", "") or "براند ناشئ — بناء وعي أولاً"),
            budget_allocation_pct=round(tofu_pct, 1),
            kpi_target=KPITarget(metric="cpm", target_value=None, unit=""),
            confidence=obj.confidence,
        )
        objectives = [awareness,
                      obj.model_copy(update={
                          "budget_allocation_pct": round(100.0 - tofu_pct, 1)})]

    objectives = _apply_owner_budget(objectives, monthly_budget)

    from competitor.market_definition import build_market_definition
    md = build_market_definition(p)
    rid = run_id
    if not rid:
        import os
        rid = os.environ.get("TELEMETRY_RUN_ID") or f"u1_{uuid_hex()}"
    return MediaPlan(
        run_id=rid,
        market_definition_ref=md.brand_ref or _fv(p, "source_url") or "unknown",
        objectives=objectives,
        base_persona=build_persona(p),
        base_geo=build_geo(p),
    )


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex[:10]
