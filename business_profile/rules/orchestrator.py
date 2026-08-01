"""Rules orchestrator.

Runs every rule-based extractor and assembles a partial BusinessProfile.
Fields the LLM will fill later (tagline, description, audience, tone,
value_propositions, trust_signals, offerings, pricing_posture) are
left in their default `missing()` state.

Name resolution: schema.org wins if available; otherwise the metadata
fallback (og:site_name → title → h1).
"""
from __future__ import annotations

from datetime import datetime, timezone

from scraper.schemas import ScrapeManifest

from ..schemas import (
    BusinessProfile,
    EvidencedField,
    ExtractionMeta,
    ProfileQuality,
    SourceType,
)
from ..quality import compute_quality
from .from_contact import extract_contact
from .from_deep_search import (
    extract_description_from_deep_search,
    extract_name_from_deep_search,
    extract_social_from_deep_search,
)
from .from_links import extract_existing_ctas, extract_social_presence
from .from_metadata import extract_name_from_metadata
from .from_pricing import extract_pricing_visible, pricing_extraction_note
from .from_offerings import extract_restaurant_offerings
from .from_schema_org import (
    extract_category_from_schema_org,
    extract_hours_from_schema_org,
    extract_locations_from_schema_org,
    extract_name_from_schema_org,
)
from .from_visual import extract_visual


def _pick_better_name(schema_name, metadata_name):
    """schema.org name beats metadata when available."""
    if schema_name.value is not None:
        return schema_name
    return metadata_name


def apply_rules(
    manifest: ScrapeManifest, manifest_path: str = ""
) -> BusinessProfile:
    """Apply all rule extractors and return a partial profile.

    The profile's LLM-filled fields are left as missing(). The
    `extraction_meta.rule_extractors_used` list tracks which rules
    produced non-missing values for the audit trail.
    """
    used: list[str] = []

    # ---- Identity ----
    name_schema = extract_name_from_schema_org(manifest)
    name_meta = extract_name_from_metadata(manifest)
    name = _pick_better_name(name_schema, name_meta)
    if name.value:
        used.append("name:" + (name.evidence[0].extractor if name.evidence else "?"))

    category = extract_category_from_schema_org(manifest)
    if category.value:
        used.append("category:schema_org")

    # ---- Pricing ----
    pricing_visible = extract_pricing_visible(manifest)
    pricing_note = pricing_extraction_note(manifest)
    if pricing_visible.source_type != SourceType.MISSING:
        used.append("pricing_visible:currency_regex")
    if pricing_note:
        used.append("pricing_note:" + pricing_note)

    # ---- Offerings fallback ----
    # Narrow restaurant/café rule fallback. LLM can still replace this with
    # richer validated offerings, but an empty LLM response should not erase
    # directly evidenced cuisine/menu signals.
    offerings = extract_restaurant_offerings(manifest)
    if offerings:
        used.append(f"offerings:restaurant_rules({len(offerings)})")

    # ---- Geography ----
    locations = extract_locations_from_schema_org(manifest)
    if locations:
        used.append(f"locations:schema_org({len(locations)})")

    hours = extract_hours_from_schema_org(manifest)
    if hours:
        used.append("hours:schema_org")

    # ---- Contact / acquisition ----
    contact_channels = extract_contact(manifest)
    if any([contact_channels.phones, contact_channels.emails,
            contact_channels.whatsapp_numbers, contact_channels.has_contact_form]):
        used.append("contact:manifest_passthrough")

    existing_ctas = extract_existing_ctas(manifest)
    if existing_ctas:
        used.append(f"ctas:manifest({len(existing_ctas)})")

    social_presence = extract_social_presence(manifest)
    if social_presence:
        used.append(f"social:manifest({len(social_presence)})")

    # ---- Deep-search (SERP) fallback ----
    # When the direct scrape was blocked/empty (big brands behind Imperva/Akamai/Cloudflare),
    # scraper.deep_search recovered the brand's identity from web search. Fill ONLY the core fields
    # the first-party extractors couldn't — cited to the real SERP URLs, tagged rule:deep_search,
    # SECONDARY by construction. First-party always wins (we only fall back when it found nothing).
    description = EvidencedField.missing()   # rules don't set description otherwise; LLM fills it
    if manifest.deep_search and manifest.deep_search.used:
        if name.source_type == SourceType.MISSING:
            ds_name = extract_name_from_deep_search(manifest)
            if ds_name.value:
                name = ds_name
                used.append("name:deep_search")
        ds_desc = extract_description_from_deep_search(manifest)
        if ds_desc.value:
            description = ds_desc
            used.append("description:deep_search")
        if not social_presence:
            ds_social = extract_social_from_deep_search(manifest)
            if ds_social:
                social_presence = ds_social
                used.append(f"social:deep_search({len(ds_social)})")

    # ---- Visual ----
    visual = extract_visual(manifest)
    if visual.palette_hex or visual.logo_url or visual.primary_logo or visual.logo_candidates:
        used.append("visual:manifest_passthrough")

    # ---- Languages (pure passthrough) ----
    languages = [e.code for e in manifest.languages]

    # ---- Assemble ----
    profile = BusinessProfile(
        source_url=manifest.scrape_meta.normalized_url,
        final_url=manifest.scrape_meta.final_url,
        name=name,
        description=description,
        category=category,
        pricing_visible=pricing_visible,
        pricing_extraction_note=pricing_note,
        offerings=offerings,
        locations=locations,
        hours=hours,
        contact_channels=contact_channels,
        existing_ctas=existing_ctas,
        social_presence=social_presence,
        visual=visual,
        languages=languages,
        # The site's OWN declaration outranks the edition we crawled (see the field).
        declared_languages=list(getattr(manifest, "declared_languages", None) or []),
        extraction_meta=ExtractionMeta(
            manifest_path=manifest_path,
            extracted_at=datetime.now(timezone.utc),
            rule_extractors_used=used,
        ),
        quality=_PLACEHOLDER_QUALITY,  # overwritten on the next line
    )
    profile.quality = compute_quality(profile)
    return profile


# A throwaway quality used only during construction (Pydantic requires
# the field). It's immediately overwritten by compute_quality.
_PLACEHOLDER_QUALITY = ProfileQuality(
    has_name=False, has_category=False, has_offerings=False,
    has_audience=False, has_value_propositions=False, has_tone=False,
    has_contact=False, has_visual=False,
    fields_extracted=0, fields_inferred=0, fields_missing=0,
    ready_for_strategy=False,
)