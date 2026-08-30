"""U1 Media Plan — core schemas.

The Media Plan answers what the campaign defense actually asks: *what should this brand run, and
why?* Everything is grounded — a plan is only as trustworthy as its weakest evidence link.

Three design decisions the objective LIST forced (each a trap if left implicit):
1. A multi-objective list is NOT flat — it is a funnel with a budget split. So every
   `CampaignObjective` carries `funnel_stage` + `budget_allocation_pct`, and `MediaPlan` enforces
   the allocations SUM TO 100 (the silent-math-bug class this project fights).
2. Shared vs per-objective targeting lives at TWO levels: `MediaPlan.base_persona`/`base_geo` (who
   the brand talks to, generally) + each objective's optional `audience_refinement` (TOFU widens,
   BOFU narrows) — mirroring how a media buyer actually works, without repeating the persona N times.
3. A plan with ONE objective is fully valid, not an exception (a Nasr-City clinic = one LEADS
   objective, a complete single-stage funnel). The schema keeps the simple simple: `List[...]` of
   length 1 is natural, and its allocation auto-resolves to 100.

Rules (frozen, INTERFACES.md §F + the Prime Directives): PD-5 NEW models are STRICT
(`extra="forbid"`); PD-2 reuse `business_profile` `EvidenceItem`/`Confidence` (no fork); PD-4 no
network here (types the builder fills via the Caller / MockCaller); PD-8 `run_id` for traceability.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Reuse the profile's evidence primitives — one provenance vocabulary across the whole system.
from business_profile.schemas import Confidence, EvidenceItem

_ALLOCATION_TOLERANCE = 0.01   # float-sum slack for the "allocations == 100%" invariant

# The learning floor (budget for ~N conversions per ad set) only makes sense for a COST-PER-RESULT
# target. For reach / roas / cpc / ctr the target isn't a per-conversion cost, so `3 x target x n`
# is nonsense and must NOT gate the budget. The floor applies only to these metrics.
_COST_PER_RESULT_METRICS = frozenset({
    "cost_per_lead", "cpl", "cpa", "cost_per_acquisition",
    "cost_per_result", "cost_per_purchase", "cost_per_conversion",
})


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------

class MetaObjective(str, Enum):
    """The SIX Meta (Facebook / Instagram) campaign objectives under the current ODAX consolidation.

    The `.value` is the exact Meta Ads API objective string, so a CampaignObjective serializes
    straight into an Ads-Manager import (U6 launch bundle) with no translation layer. Every Meta
    campaign is exactly one of these — there is no seventh.
    """
    AWARENESS = "OUTCOME_AWARENESS"          # be seen/remembered by the right people (reach, brand lift)
    TRAFFIC = "OUTCOME_TRAFFIC"              # send people to a destination (site, WhatsApp, profile)
    ENGAGEMENT = "OUTCOME_ENGAGEMENT"        # messages, video views, post/page engagement
    LEADS = "OUTCOME_LEADS"                  # collect leads (instant forms, calls, sign-ups)
    APP_PROMOTION = "OUTCOME_APP_PROMOTION"  # app installs + in-app actions
    SALES = "OUTCOME_SALES"                  # purchases / conversions (needs a store or a pixel/CAPI)

    @property
    def label(self) -> str:
        """Human-facing name (for the plan UI / defense), not the API string."""
        return {
            "OUTCOME_AWARENESS": "Awareness", "OUTCOME_TRAFFIC": "Traffic",
            "OUTCOME_ENGAGEMENT": "Engagement", "OUTCOME_LEADS": "Leads",
            "OUTCOME_APP_PROMOTION": "App promotion", "OUTCOME_SALES": "Sales",
        }[self.value]


class FunnelStage(str, Enum):
    """Where an objective sits in the customer journey — so a multi-objective plan has an ordering
    and a distribution logic, not three objectives in a heap."""
    TOFU = "tofu"   # top: reach a cold, wide audience (awareness / traffic / engagement)
    MOFU = "mofu"   # middle: nurture the interested (engagement / traffic / leads)
    BOFU = "bofu"   # bottom: convert the ready, usually retargeted (leads / sales)


class Destination(str, Enum):
    """WHERE the objective drives the customer — the concrete conversion surface. The deduced
    objective is only actionable paired with a destination the brand ACTUALLY has (a Sales
    objective needs a store; a Leads objective needs a form/WhatsApp/phone)."""
    WEBSITE = "website"
    ONLINE_STORE = "online_store"        # a real cart/checkout (a precondition for a credible SALES objective)
    WHATSAPP = "whatsapp"
    MESSENGER = "messenger"
    INSTAGRAM_DM = "instagram_dm"
    LEAD_FORM = "lead_form"              # Meta instant form
    PHONE_CALL = "phone_call"
    APP = "app"
    PHYSICAL_STORE = "physical_store"    # foot traffic / store visits
    PROFILE = "profile"                  # grow the page/profile itself


class GeoMode(str, Enum):
    RADIUS = "radius"        # a circle around an address (local brick-and-mortar)
    CITIES = "cities"        # a named list of cities
    REGIONS = "regions"      # governorates / states
    NATIONAL = "national"    # the whole country (online-only / nationwide brands)


# ---------------------------------------------------------------------
# Evidence + targeting
# ---------------------------------------------------------------------

class EvidenceRef(BaseModel):
    """A resolution-wrapper: a deduction CLAIM plus the real evidence that supports it. `resolved`
    is set True only once the claim's evidence is checked against the Evidence Ledger (the builder
    does this) — an UNresolved ref is a hypothesis, not a fact, and must not drive a decision."""
    model_config = ConfigDict(extra="forbid")

    claim: str                                     # e.g. "the brand sells products through an online store"
    evidence: List[EvidenceItem] = Field(default_factory=list)
    resolved: bool = False                         # True once Ledger-verified

    @property
    def is_grounded(self) -> bool:
        return self.resolved and bool(self.evidence)


class KPITarget(BaseModel):
    """The primary metric this objective is judged on. Values are OPTIONAL — an honest plan states
    the metric it will optimise even before a numeric target is set (targets need historical data
    or a benchmark, which may be UNKNOWN at plan time)."""
    model_config = ConfigDict(extra="forbid")

    metric: str                                    # e.g. "cost_per_lead", "roas", "reach", "cpc"
    # A cost/target is positive by nature; a negative or zero target would silently DISABLE the
    # learning-floor gate (floor = 3 x target x n -> <=0 -> any budget passes). gt=0 shuts that door.
    target_value: Optional[float] = Field(default=None, gt=0.0)   # None = optimise, target TBD (honest-unknown)
    unit: str = ""                                 # "EGP", "%", "count", "ratio"
    window_days: Optional[int] = Field(default=None, gt=0)        # measurement window (>0); None = lifetime


class Persona(BaseModel):
    """WHO the brand talks to — and every axis is an `EvidenceRef`, never a free string, because a
    persona without evidence is a guess (the line between this system and a hallucinating generator).
    Any axis may be None (honestly unknown); whatever IS asserted must resolve to real evidence."""
    model_config = ConfigDict(extra="forbid")

    age_range: Optional[EvidenceRef] = None
    gender: Optional[EvidenceRef] = None
    interests: List[EvidenceRef] = Field(default_factory=list)
    income_level: Optional[EvidenceRef] = None
    location_summary: Optional[EvidenceRef] = None

    def _refs(self) -> List[EvidenceRef]:
        core = [r for r in (self.age_range, self.gender, self.income_level, self.location_summary) if r]
        return core + list(self.interests)

    @property
    def is_grounded(self) -> bool:
        """Grounded = at least one axis is asserted AND every asserted axis resolves to evidence.
        (An empty persona is not grounded; a persona with one ungrounded axis is not grounded.)"""
        refs = self._refs()
        return bool(refs) and all(r.is_grounded for r in refs)


class GeoTargeting(BaseModel):
    """WHERE the brand advertises. `address_ref` grounds the location in the brand's real address
    (from the profile), so a radius is anchored to a fact, not invented."""
    model_config = ConfigDict(extra="forbid")

    mode: GeoMode
    center_address: Optional[str] = None
    radius_km: Optional[float] = None
    cities: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)
    address_ref: Optional[EvidenceRef] = None      # the address's evidence, from the profile
    # CS-84 (root of P-51): the brand's country, from `country_of` evidence. Valid in EVERY mode —
    # a national plan, a radius, or a city list all need to name WHICH country (a bare "national" or
    # a lone "Cairo" is ambiguous). None when the evidence is silent (never a guess).
    country: Optional[str] = None

    @model_validator(mode="after")
    def _mode_consistency(self) -> "GeoTargeting":
        # Each mode asserts BOTH its own requirement AND that the other modes' fields are empty, so a
        # GeoTargeting can never carry mutually-contradictory targeting (e.g. a radius AND a city list).
        if self.mode is GeoMode.RADIUS:
            if not self.center_address or self.radius_km is None or self.radius_km <= 0:
                raise ValueError("radius geo needs a center_address and a positive radius_km")
            if self.cities or self.regions:
                raise ValueError("radius geo must not also set cities/regions")
        elif self.mode is GeoMode.CITIES:
            if not self.cities:
                raise ValueError("cities geo needs a non-empty cities list")
            if self.regions or self.radius_km is not None or self.center_address:
                raise ValueError("cities geo must not set regions/radius_km/center_address")
        elif self.mode is GeoMode.REGIONS:
            if not self.regions:
                raise ValueError("regions geo needs a non-empty regions list")
            if self.cities or self.radius_km is not None or self.center_address:
                raise ValueError("regions geo must not set cities/radius_km/center_address")
        elif self.mode is GeoMode.NATIONAL:
            if self.cities or self.regions or self.radius_km is not None or self.center_address:
                raise ValueError("national geo must not set cities/regions/radius_km/center_address")
        return self


# ---------------------------------------------------------------------
# Objective + plan
# ---------------------------------------------------------------------

class CampaignObjective(BaseModel):
    """The deduced objective for ONE funnel stage, with its destination, KPI, budget share, and
    grounded rationale. The defense reads `objective` + `rationale` and must trace WHY — so
    `rationale` is backed by `evidence` refs that resolve to the brand's real facts.
    """
    model_config = ConfigDict(extra="forbid")

    objective: MetaObjective
    destination: Destination
    funnel_stage: FunnelStage
    rationale: str                                 # one clear sentence: why THIS objective for THIS stage
    budget_allocation_pct: float = Field(ge=0.0, le=100.0)   # this objective's share of the plan budget
    num_ad_sets: int = Field(default=1, ge=1)      # ad sets under this objective (drives the learning floor)
    test_budget: Optional[float] = Field(default=None, gt=0.0)   # absolute testing budget (>0); None = TBD
    budget_note: str = ""                          # WHERE test_budget came from, or why it stayed unknown
    audience_refinement: Optional[str] = None      # refinement OVER MediaPlan.base_persona (TOFU widen / BOFU narrow)
    evidence: List[EvidenceRef] = Field(default_factory=list)
    kpi_target: Optional[KPITarget] = None
    confidence: Confidence = Confidence.NONE
    alternatives: List[MetaObjective] = Field(default_factory=list)   # considered-but-not-chosen, in order

    @model_validator(mode="after")
    def _learning_floor(self) -> "CampaignObjective":
        """test_budget must clear the LEARNING FLOOR: >= 3 x target_cost x num_ad_sets, so each ad
        set can buy the ~3 conversions a minimum read needs. Enforced ONLY when (a) both the budget
        and the cost target are known (honest-unknown skips it, never fabricates a pass) AND (b) the
        KPI metric is a per-conversion COST (the floor is meaningless for reach/roas/cpc/ctr).

        NOTE — this 3x is a BUDGET floor ("have I spent enough to judge this ad at all?"). It is NOT
        Meta's LEARNING-PHASE exit (~50 conversions/ad-set/7-days), which is a CONVERSION COUNT, not
        a budget, and lives as a U3 Decision-Engine guardrail (no kill/scale before ~50). See §T T-4.
        Putting 50 here would fabricate a huge budget; the two thresholds have two homes."""
        kpi = self.kpi_target
        tv = kpi.target_value if kpi else None
        is_cost = bool(kpi) and kpi.metric.strip().lower() in _COST_PER_RESULT_METRICS
        if self.test_budget is not None and tv is not None and is_cost:
            floor = 3.0 * tv * self.num_ad_sets
            if self.test_budget < floor:
                raise ValueError(
                    f"test_budget {self.test_budget} is below the learning floor {floor} "
                    f"(3 x target {tv} x {self.num_ad_sets} ad sets)")
        return self

    @property
    def is_grounded(self) -> bool:
        """True when the rationale is backed by at least one Ledger-resolved evidence ref."""
        return any(ref.is_grounded for ref in self.evidence)


class MediaPlan(BaseModel):
    """The container: one or more objectives in funnel order, sharing a base persona + geo, tied to
    a run for telemetry. A plan is grounded only when EVERY objective is grounded — it is as strong
    as its weakest link, never its strongest."""
    model_config = ConfigDict(extra="forbid")

    run_id: str                                    # PD-8 telemetry / traceability
    market_definition_ref: str                     # links to the competitor/ MarketDefinition (D-3)
    objectives: List[CampaignObjective] = Field(min_length=1)
    base_persona: Persona
    base_geo: GeoTargeting

    @model_validator(mode="after")
    def _budget_allocations_sum_to_100(self) -> "MediaPlan":
        # A single-objective plan takes the whole budget by definition — auto-resolve, don't force
        # the caller to type 100 for the trivial case.
        if len(self.objectives) == 1:
            self.objectives[0].budget_allocation_pct = 100.0
        total = sum(o.budget_allocation_pct for o in self.objectives)
        if abs(total - 100.0) > _ALLOCATION_TOLERANCE:
            raise ValueError(f"budget_allocation_pct across objectives must sum to 100, got {total}")
        return self

    @property
    def is_grounded(self) -> bool:
        """Weakest-link across EVERY evidence-bearing part — the objectives AND the base persona AND
        the base geo. A plan aimed at a guessed audience or an unfounded location is not grounded,
        no matter how solid its objectives (a plan is as strong as its weakest link, never its
        strongest). A RADIUS geo is anchored on a real address, so it must carry a grounded
        address_ref; for the other modes an address_ref is optional but, if asserted, must resolve."""
        if not all(o.is_grounded for o in self.objectives):
            return False
        if not self.base_persona.is_grounded:
            return False
        ref = self.base_geo.address_ref
        if self.base_geo.mode is GeoMode.RADIUS:
            return ref is not None and ref.is_grounded
        return ref is None or ref.is_grounded
