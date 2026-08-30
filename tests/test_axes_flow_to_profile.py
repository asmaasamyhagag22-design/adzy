# -*- coding: utf-8 -*-
"""The two new axes must survive validation and the merge into the profile.

Step 2 wiring (process.md §8.11). The prompt asks for `market_category` and `business_model`; this
pins the rest of the path so a consumer that switches to the right axis actually finds a value:
  - ValidatedIdentity carries both,
  - the merger writes both onto the BusinessProfile,
  - an unknown model string is dropped, never crashes.
Hermetic."""
from __future__ import annotations

from business_profile.schemas import BusinessModel
from business_profile.llm.validator import ValidatedIdentity, ValidatedScalarField
from business_profile.schemas import Confidence


def _sf(value):
    return ValidatedScalarField(value=value, evidence=[], confidence=Confidence.NONE, inferred=True)


def test_validated_identity_holds_both_axes():
    vi = ValidatedIdentity(tagline=_sf(None), description=_sf(None), category=_sf(None),
                           market_category=_sf("handmade jewellery"),
                           business_model=_sf(BusinessModel.ECOMMERCE))
    assert "jewellery" in str(vi.market_category.value)
    assert vi.business_model.value is BusinessModel.ECOMMERCE


def test_the_merge_writes_both_axes_onto_the_profile():
    from business_profile.merger import _merge_identity_axes

    class _P:
        market_category = None
        business_model = BusinessModel.UNKNOWN

    class _Field:
        def __init__(self, val):
            self.value = val
            self.evidence = [1]

    p = _P()
    _merge_identity_axes(p, market=_Field("liposomal supplements"),
                         model=_Field(BusinessModel.ECOMMERCE))
    assert "supplements" in str(p.market_category.value)
    assert p.business_model is BusinessModel.ECOMMERCE


def test_the_merge_leaves_a_missing_axis_untouched():
    from business_profile.merger import _merge_identity_axes

    class _P:
        market_category = "already"
        business_model = BusinessModel.LOCAL

    p = _P()
    _merge_identity_axes(p, market=None, model=None)      # must not raise, must not overwrite
    assert p.business_model is BusinessModel.LOCAL


def test_an_unknown_model_string_coerces_to_none():
    from business_profile.llm.validator import _coerce_enum
    assert _coerce_enum("totally-made-up", BusinessModel) is None
    assert _coerce_enum("ecommerce", BusinessModel) is BusinessModel.ECOMMERCE
