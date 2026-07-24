"""Prompt templates for LLM extraction groups.

The grounding rules are stated ONCE as GROUNDING_CONTRACT and shared via SYSTEM_PROMPT
across every group, instead of each group re-litigating them (nine copies that had
drifted — one call ending up stricter than another). SYSTEM_PROMPT is the shared system
message; each build_* function produces the user message (the pack + that group's
extraction fields) and assumes the contract is already in force — it does not repeat it.
"""
from __future__ import annotations

from typing import Optional

from .evidence_pack import EvidencePack


# The canonical grounding rules — stated once, injected everywhere (they were duplicated,
# and drifting, across nine prompts). Every extraction value is bound by this block.
GROUNDING_CONTRACT = """[GROUNDING CONTRACT — non-negotiable, applies to every value you emit]
1. Evidence-bound: every value must be supported by a block_id present EXACTLY in the supplied evidence. No support -> value = null (or omit the item). Never guess.
2. Verbatim quotes: every quote is a character-exact substring of ONE real block — including Arabic diacritics/spelling as printed. Never normalize, translate, or repair.
3. Invent nothing falsifiable: no number, price, date, ranking, award, certification, or superlative that isn't literally in the evidence. Honest-empty beats padded.
4. No fabricated ids: never emit a block_id you were not given.
5. Language integrity: reproduce the source language of each quote; do not switch scripts.

A complete extraction captures every well-supported value and omits the rest. Coverage of the real, evidenced facts is the goal; silence on the unsupported is not a failure but the rule.

Reject the generic: any claim that would be equally true of a random competitor ("high quality," "great service," "trusted") is noise unless the evidence pairs it with a concrete specific. Specificity is the filter."""


SYSTEM_PROMPT = (
    "You are a business profile extractor. You read structured text blocks scraped from a "
    "business's website and produce a strictly-evidenced profile.\n\n"
    + GROUNDING_CONTRACT
    + "\n\nOPERATING NOTES:\n"
    "- You may emit a value INFERRED from copy rather than directly stated, but only if you cite "
    "the blocks that support the inference (prefer directly-stated facts to inferred ones).\n"
    "- Keep quotes short (under 200 characters); truncate a longer block to its most relevant substring."
)


def _format_pack_for_prompt(pack: EvidencePack, max_chars: int = 30_000) -> str:
    """Render the pack as text the LLM reads from."""
    return pack.as_llm_text(max_chars=max_chars)


def _common_header(pack: EvidencePack) -> str:
    return (
        f"Source URL: {pack.source_url}\n"
        f"Languages detected: {', '.join(pack.languages) or 'unknown'}\n"
        f"Missing fields (what rules could not fill): {', '.join(pack.missing_fields) or 'none'}\n"
        f"\nTEXT BLOCKS (each line is one block in the format: [block_id] (metadata) \"text\"):\n"
    )


# ---------------------------------------------------------------------
# Identity group: tagline, description, category
# ---------------------------------------------------------------------

CATEGORY_VALUES = (
    "restaurant", "cafe", "clinic", "hospital", "retail", "ecommerce",
    "services_b2b", "services_b2c", "education", "government", "agency",
    "hospitality", "real_estate", "professional_services", "fitness",
    "beauty", "automotive", "nonprofit", "other",
)


def build_identity_prompt(pack: EvidencePack) -> str:
    return (
        _common_header(pack)
        + _format_pack_for_prompt(pack)
        + "\n\nExtract three identity fields (the GROUNDING CONTRACT in your system instructions governs every value):\n"
        + "1. tagline: the brand's own one-line self-description, copied VERBATIM if one exists "
        "(ideally a homepage h1/h2 above_fold); null if none is stated. Copy, don't compose.\n"
        + "2. description: 2-3 sentences you COMPOSE from evidenced facts — what the business does, "
        "who it serves, and where. Every claim inside it must trace to a block_id. Compose, but grounded.\n"
        + f"3. category: choose exactly ONE from this fixed list: {', '.join(CATEGORY_VALUES)}. This "
        "label routes downstream specialization, so choose the closest real fit; use 'other' only "
        "when no listed category genuinely applies, never as a shortcut for 'unsure'.\n"
        # process.md §8.11: `category` conflated two questions, so it flipped between them
        # (an online pharmacy and a jeweller both landed on 'ecommerce'). These two fields split
        # the axes: WHAT the business sells (free text — a list is always too small) vs HOW it
        # sells (a genuinely small closed set).
        + "4. market_category: WHAT this business sells, in the brand's OWN words as free text — "
        "NOT from a list. Be specific: 'handmade jewellery', 'liposomal supplements', "
        "'soft-drink beverages', 'laser skin clinic'. This is the market, and no fixed list can "
        "hold every market.\n"
        + "5. business_model: HOW it sells — choose exactly ONE: ecommerce (sells online and ships), "
        "local (walk-in premises), service (delivers a service, on-site or remote), hybrid (a real "
        "storefront AND a real online store), unknown (evidence insufficient — never a guess).\n"
        + "\nFor each field, return value, evidence (list of block_id+quote), confidence (high/medium/low/none), and a short reasoning string."
    )


# ---------------------------------------------------------------------
# Offerings group: offerings[], pricing_posture
# ---------------------------------------------------------------------

PRICING_POSTURE_VALUES = ("budget", "mid", "premium", "unknown")


# Universal CATALOG SHAPES — the offerings prompt's per-business guidance. REPLACES the old 18
# vertical keys (process.md rule 5: config keyed by a universal signal + a universal default,
# never vertical names as logic). The category enum maps to ONE of four shapes; the prompt only
# ever sees shape rules, and an unknown / None / unmapped category resolves to 'default' BY
# CONSTRUCTION — so the documented fail-open None-key bug is impossible. The hard-won specifics
# (DEPARTMENTS-FIRST, SKU != offering, breadth-over-depth, named-menu discipline) are preserved.
_CATALOG_SHAPES: dict[str, str] = {
    "broad_catalog": (
        "This is a PRODUCT STORE / MARKETPLACE (an ecommerce brand or retail chain). A store "
        "sells many individual items, so the offerings must summarize WHAT THE STORE SELLS, not "
        "what happened to sit on the crawled promo pages. Offerings include: product categories / "
        "departments; collections by name; best-selling or signature lines when the page calls "
        "them out; bundles and subscriptions; store-level services (delivery / shipping) when "
        "described; and branches. Only assert a product benefit the page explicitly states.\n"
        "HARD RULES:\n"
        "  0. SIZE CHECK FIRST (a boutique's products ARE its offerings): when the evidence "
        "shows a BOUTIQUE catalog — roughly 40 or fewer distinct products — the offerings are "
        "the INDIVIDUAL products by their exact names, each with its price_text VERBATIM when "
        "a price appears in the evidence; do NOT collapse them into departments. Apply rules "
        "1-4 below ONLY to a large store/marketplace (hundreds of SKUs, many departments).\n"
        "  1. DEPARTMENTS FIRST (large stores): prefer the store's OWN department/category "
        "names from its navigation/collections evidence (e.g. الأدوية / العناية بالمرأة / "
        "الأجهزة الطبية) — they define the store.\n"
        "  2. A name carrying a pack size, weight, count or flavor variant "
        "(8 كبسولات, 454 جم, 500mg, 'decaf caramel') is a SKU — NEVER an offering. Return its "
        "FAMILY or DEPARTMENT once instead.\n"
        "  3. BREADTH over depth: the list must COVER the range of what the store sells; ONE "
        "product family may NEVER occupy more than 2 entries.\n"
        "  4. At most 2-3 individual flagship products, and only when the page explicitly presents "
        "them as signature items."
    ),
    "named_menu": (
        "This business presents a MENU of specifically-named items (a restaurant or cafe). "
        "Offerings are the SPECIFIC named items the page actually lists:\n"
        "  - named signature dishes / menu items — and for cafes coffee, drinks, pastries, "
        "breakfast items (verbatim from the menu)\n"
        "  - a named menu SECTION, ONLY when no individual items are listed under it\n"
        "  - service modes the brand explicitly states (delivery, takeaway, catering, dine-in) — "
        "only when a section mentions it\n"
        "  - branches / locations when listed\n"
        "Do NOT emit a bare cuisine adjective, the word 'menu', or the brand name as an offering."
    ),
    "programs": (
        "This organization delivers NAMED SERVICES, PROGRAMS or SPECIALTIES (not a product "
        "catalog). Offerings are the specific named lines the page lists — capture the specific "
        "name, not the bare category. Look in services / treatments / courses / programs / "
        "practice-area / booking pages first, homepage second. Depending on the business they "
        "take the form of:\n"
        "  - Clinics & hospitals: medical services, treatments, procedures, specialties "
        "(cardiology, dermatology…), consultations, diagnostics, specialty departments, and named "
        "care packages / programs.\n"
        "  - Education & training: courses (specific titles), programs, diplomas, degrees, "
        "certificates, tracks, academies, specializations, workshops, bootcamps, and applied-"
        "service lines.\n"
        "  - Professional / B2B / agency: service lines (consulting, audit, implementation, 'SEO', "
        "'brand identity'), practice areas, packages / retainers, named expertise, industry "
        "specializations.\n"
        "  - Fitness: classes, memberships, personal training, programs, named amenities.\n"
        "  - Hospitality: room types, packages, experiences, dining options, amenities.\n"
        "  - Real estate: property listings by type; services (rental, sales, management).\n"
        "  - Automotive: vehicle categories; services (sales, leasing, maintenance); named brands.\n"
        "  - Nonprofit / NGO & government: programs, initiatives, named campaigns, services to "
        "beneficiaries, public services, departments.\n"
        "Prefer SPECIFIC named offerings; if only broad categories exist capture at most 1-2; if "
        "nothing concrete is listed return an EMPTY list — honest-empty over padding."
    ),
    "default": (
        "Offerings are anything this organization provides to its audience: services, products, "
        "courses, programs, menu items, experiences, memberships, locations of service. Pick the "
        "most prominent set. Each must cite block_id evidence.\n"
        "IF THE EVIDENCE SHOWS A PRODUCT STORE / MARKETPLACE (many individual products with "
        "prices), apply these HARD RULES — the offerings must summarize WHAT THE STORE SELLS, not "
        "what happened to sit on the crawled promo pages (a giant pharmacy must NOT be summarized "
        "as 'coffee capsules'):\n"
        "  0. SIZE CHECK FIRST (a boutique's products ARE its offerings): when the evidence "
        "shows a BOUTIQUE catalog — roughly 40 or fewer distinct products — the offerings are "
        "the INDIVIDUAL products by their exact names, each with its price_text VERBATIM when "
        "a price appears in the evidence; do NOT collapse them into departments. Apply rules "
        "1-4 below ONLY to a large store/marketplace (hundreds of SKUs, many departments).\n"
        "  1. DEPARTMENTS FIRST (large stores): prefer the store's OWN department/category "
        "names from its navigation/collections evidence (e.g. الأدوية / العناية بالمرأة / "
        "الأجهزة الطبية) — they define the store.\n"
        "  2. A name carrying a pack size, weight, count or flavor variant "
        "(8 كبسولات, 454 جم, 500mg, 'decaf caramel') is a SKU — NEVER an offering. Return its "
        "FAMILY or DEPARTMENT once instead.\n"
        "  3. BREADTH over depth: the list must COVER the range of what the store sells; ONE "
        "product family may NEVER occupy more than 2 entries.\n"
        "  4. At most 2-3 individual flagship products, and only when explicitly presented as "
        "signature items."
    ),
}


# Which universal catalog shape each business category uses. Unknown / None / unmapped -> 'default'
# (see _shape_for). beauty & other map to 'default' deliberately: default's store-detection adapts
# to a cosmetics BRAND (departments/collections) vs a salon (named services), a duality no single
# vertical shape captures.
_CATEGORY_TO_SHAPE: dict[str, str] = {
    "ecommerce": "broad_catalog", "retail": "broad_catalog",
    "restaurant": "named_menu", "cafe": "named_menu",
    "clinic": "programs", "hospital": "programs",
    "education": "programs", "government": "programs", "agency": "programs",
    "services_b2b": "programs", "services_b2c": "programs",
    "professional_services": "programs", "fitness": "programs",
    "hospitality": "programs", "real_estate": "programs", "automotive": "programs",
    "nonprofit": "programs",
    "beauty": "default", "other": "default",
}


# Universal blacklist of claims that must not be inferred — only accepted
# when the cited quote LITERALLY contains the claim token. The validator
# enforces this; the prompt warns the LLM in advance to reduce wasted
# generations.
UNSUBSTANTIATED_CLAIM_TOKENS = (
    # Dietary / religious
    "halal", "kosher", "vegan", "vegetarian", "gluten-free", "gluten free",
    "sugar-free", "sugar free", "keto", "paleo",
    # Health
    "organic", "healthy", "nutritious", "low-calorie", "low calorie",
    "all-natural", "all natural",
    # Certification
    "certified", "iso", "iso-9001", "iso 9001", "haccp", "usda",
    "fda-approved", "fda approved", "medical-grade", "medical grade",
    # Medical efficacy (skincare/beauty caution)
    "clinically proven", "scientifically proven", "dermatologist-approved",
    "dermatologist approved", "hypoallergenic",
)


def _shape_for(rules_category: Optional[str]) -> str:
    """Map a business category (or None / unknown) to a universal catalog SHAPE. Unknown, None,
    empty, and unmapped categories all resolve to 'default' — there is no fail-open None-key path
    (the old _category_key / _generic trap)."""
    if not rules_category:
        return "default"
    return _CATEGORY_TO_SHAPE.get(rules_category.lower().strip(), "default")


# Category-aware offering cap. MEASURED A/B (benchmark/measure_offering_cap_ab.py,
# cap 12 vs 30 across telecom/pharmacy/education): a higher cap surfaces MANY MORE
# real, distinct offerings on multi-SERVICE giants (te.eg 12->30, all real WE
# services) and NEVER pads thin sites (almentor returned 9 even at 30 — the
# "honest-empty/honest-stop" rule holds). The ONE downside was ECOMMERCE drifting
# toward individual SKUs (the ecommerce guidance wants collection-level), so
# ecommerce/retail stay at the conservative 12. Safe regardless: the validator drops
# any offering whose evidence doesn't ground, so a higher cap can only ADD real
# offerings, never hallucinations.
_OFFERINGS_CAP_DEFAULT = 30
_OFFERINGS_CAP_ECOMMERCE = 12
_ECOMMERCE_CATS = {"ecommerce", "retail"}


def offerings_cap_for(rules_category: Optional[str]) -> int:
    """How many offerings the model may return for this category (measured policy)."""
    if rules_category and rules_category.lower().strip() in _ECOMMERCE_CATS:
        return _OFFERINGS_CAP_ECOMMERCE
    return _OFFERINGS_CAP_DEFAULT


def build_offerings_prompt(
    pack: EvidencePack,
    rules_category: Optional[str] = None,
    max_offerings: int = 12,
) -> str:
    """Build the universal offerings prompt.

    The prompt is single — only the catalog-SHAPE guidance block is swapped based on
    rules_category (via _shape_for -> _CATALOG_SHAPES). Pass the rules-derived category
    (schema.org or None); None / unknown resolves to the 'default' shape.

    max_offerings caps how many offerings the model returns (default 12). The
    "honest-empty over padding" rule below still applies, so a higher cap surfaces more
    REAL offerings on multi-service sites without forcing filler on thin ones.

    The GROUNDING CONTRACT (shared SYSTEM_PROMPT) already forbids invented dietary /
    health / certification claims; the validator enforces it via UNSUBSTANTIATED_CLAIM_TOKENS,
    so this prompt no longer re-litigates that ban in prose.
    """
    guidance = _CATALOG_SHAPES[_shape_for(rules_category)]

    return (
        _common_header(pack)
        + _format_pack_for_prompt(pack)
        + "\n\nWHAT THIS ORGANIZATION OFFERS TO ITS AUDIENCE\n"
        + "============================================\n"
        + "Extract the concrete things this business offers — named products, services, programs, "
        "packages — each with name (short, verbatim or close to verbatim), short_description "
        "(one sentence when available), price_text (verbatim if shown; null otherwise), and "
        "evidence (the blocks that support its existence). Prefer SPECIFIC named offerings over "
        "generic ones; if only broad categories exist, capture at most 1-2 of them; if nothing "
        "concrete is listed, return an EMPTY list — honest-empty is better than padding. NEVER "
        "emit the business's OWN NAME as an offering, and never emit vague filler ('menu', 'our "
        "services', 'diverse menu', 'special menu', 'products', 'offerings') as an offering name.\n\n"
        + "Then apply the structural selection rules for THIS catalog shape:\n"
        + guidance
        + "\n\nLook in services/products/menu/pricing/booking/courses/programs/branches pages "
        "first; homepage second. Skip blog-post titles and generic mentions.\n"
        + f"- Cap: at most {max_offerings} offerings — pick the most prominent.\n\n"
        + f"PRICING POSTURE: choose one of {', '.join(PRICING_POSTURE_VALUES)}.\n"
        + "  - 'premium' = explicit positioning language "
        "('luxury', 'exclusive', 'world-class') OR high visible prices.\n"
        + "  - 'budget' = explicit affordability language "
        "('affordable', 'best value') OR low prices.\n"
        + "  - 'mid' = neutral positioning, mid-range prices.\n"
        + "  - 'unknown' = no clear signal.\n"
        + "  Cite the specific blocks justifying the posture."
    )


# ---------------------------------------------------------------------
# Positioning group: audience_type, audience_signals[], value_propositions[], tone_of_voice
# ---------------------------------------------------------------------

AUDIENCE_TYPE_VALUES = ("B2C", "B2B", "B2G", "mixed", "unknown")
TONE_VALUES = (
    "formal", "professional", "friendly", "casual",
    "playful", "luxury", "clinical", "inspirational", "unknown",
)


def build_positioning_prompt(pack: EvidencePack) -> str:
    return (
        _common_header(pack)
        + _format_pack_for_prompt(pack)
        + "\n\nEXTRACT:\n"
        + f"1. audience_type: one of {', '.join(AUDIENCE_TYPE_VALUES)}.\n"
        + "   - 'B2C' = direct-to-consumer signals (individual customers, personal pronouns, retail prices)\n"
        + "   - 'B2B' = business buyer signals (enterprise, corporate, partnerships, RFQs)\n"
        + "   - 'B2G' = government clients (ministries, public sector, tenders)\n"
        + "   - 'mixed' = clear signals for two or more\n"
        + "   - 'unknown' = no clear signal\n"
        + "2. audience_signals: short phrases (3-8 words) that describe the audience the copy is speaking to. Examples: 'young professionals in Cairo', 'pregnant mothers', 'enterprise IT teams'. Cap at 5 items, most specific first. Each item must cite the blocks it came from.\n"
        + "3. value_propositions: the distinctive promises the business makes — what differentiates them. Each is a short phrase (5-15 words). Skip generic claims like 'high quality' unless paired with specifics. Cap at 5 items. Each must cite evidence.\n"
        + f"4. tone_of_voice: one of {', '.join(TONE_VALUES)}. Based on word choice across multiple blocks (formality, jargon, warmth, imagery). Cite 2-3 representative blocks.\n"
        + "5. other_unique_insights: a CATCH-ALL for a genuinely UNIQUE competitive edge or "
        "operational detail that does NOT fit the fields above — e.g. a stated differentiator "
        "('the only X in the city'), an unusual guarantee/policy, a notable scale fact, a "
        "distinctive process. STRICT: each must be a CONCRETE FACT stated on the page (cite "
        "block_id + verbatim quote), NOT your opinion or a generic strength. If nothing "
        "genuinely unique stands out, return an EMPTY list. Cap at 3 items."
    )


# ---------------------------------------------------------------------
# Trust group: trust_signals[], service_areas[]
# ---------------------------------------------------------------------

def build_trust_prompt(pack: EvidencePack) -> str:
    return (
        _common_header(pack)
        + _format_pack_for_prompt(pack)
        + "\n\nEXTRACT:\n"
        + "1. trust_signals: concrete proof points that build credibility. Examples:\n"
        + "   - certifications ('ISO 9001 certified', 'Board-certified')\n"
        + "   - awards ('Best Restaurant 2023')\n"
        + "   - notable clients or partners ('Trusted by Aramco, Vodafone')\n"
        + "   - tenure ('15 years in business', 'founded in 2008')\n"
        + "   - team credentials ('Harvard-trained surgeons')\n"
        + "   - testimonials (only when a real quote with attribution is present)\n"
        + "   Each is one short phrase. Cap at 8 items.\n"
        + "2. service_areas: geographic regions the business explicitly serves. Cities, districts, countries. Example: ['Cairo', 'Giza', 'Alexandria']. Skip mailing addresses — only places they say they SERVE. Cap at 10 items."
    )
