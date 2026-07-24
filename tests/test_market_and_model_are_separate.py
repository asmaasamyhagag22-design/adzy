# -*- coding: utf-8 -*-
"""What a business SELLS and HOW it sells are two questions. One field cannot answer both.

MEASURED 2026-07-21 across every saved profile — the single `category` field flips between the two
axes depending on what a site emphasises:

    ecommerce  ->  an online PHARMACY, a JEWELLERY house, a SUPPLEMENTS brand, a BEAUTY brand
    Raw African -> `beauty` in some runs, `ecommerce` in others (same brand, same site)
    Coca-Cola   -> `services_b2c`   (it sells beverages)
    Vodafone    -> `services_b2c` twice, `services_b2b` once

Four unrelated markets under one label means "who are my competitors" can compare a pharmacy to a
jeweller. And both axes are genuinely needed downstream, by DIFFERENT consumers:

    MARKET drives  -> trend seeds, market_definition.query_seeds, peer relevance, positioning
    MODEL  drives  -> CATEGORY_TYPE_MAP (Places types), business_type routing, search radius

So whichever question the field answered, the other consumer silently got a wrong input.

Owner's rulings (2026-07-21): "ممنوع الحلول المؤقتة" — split the axes rather than patch the
classifier; and the MARKET is free evidenced text, not a closed list, because any closed list stays
too small for real markets (D-8). The MODEL stays a small closed enum because it really is finite.
Hermetic."""
from __future__ import annotations

from datetime import datetime, timezone

from business_profile.schemas import (BusinessModel, BusinessProfile, EvidencedField,
                                      ExtractionMeta, ProfileQuality)


def _profile() -> BusinessProfile:
    """A minimal valid profile. ProfileQuality carries a dozen required booleans that change as the
    schema grows, so they are filled from the model itself rather than re-listed here."""
    quality = ProfileQuality(**{n: False for n, f in ProfileQuality.model_fields.items()
                                if f.is_required()})
    return BusinessProfile(
        source_url="https://x.example/",
        extraction_meta=ExtractionMeta(manifest_path="x",
                                       extracted_at=datetime.now(timezone.utc)),
        quality=quality)


def test_the_model_axis_is_a_small_closed_set():
    values = {m.value for m in BusinessModel}
    assert {"ecommerce", "local", "service", "hybrid"} <= values
    assert len(values) <= 6, "how a business sells really is finite — keep it small"


def test_the_market_axis_is_free_text():
    p = _profile()
    p.market_category = EvidencedField(value="مجوهرات يدوية فاخرة")
    assert "مجوهرات" in str(p.market_category.value)   # no enum can hold every real market


def test_a_profile_can_hold_both_at_once():
    p = _profile()
    p.market_category = EvidencedField(value="liposomal supplements")
    p.business_model = BusinessModel.ECOMMERCE
    assert p.business_model is BusinessModel.ECOMMERCE
    assert "supplements" in str(p.market_category.value)


def test_both_fields_default_to_unknown_not_to_a_guess():
    p = _profile()
    assert p.business_model in (None, BusinessModel.UNKNOWN)
    assert not str(getattr(p.market_category, "value", "") or "").strip()


def test_the_legacy_category_still_exists_for_unmigrated_readers():
    # 12 modules and 200+ usages read `category`; it must not vanish underneath them mid-migration.
    assert "category" in BusinessProfile.model_fields
