"""F-03: authentic citations must support the values attached to them.

Hermetic regression coverage for validator -> merger -> EvidenceLedger.  The
evidence pack and every model response are synthetic; no caller or network is
used.
"""
from __future__ import annotations

from datetime import datetime, timezone

from business_profile.llm.evidence_pack import EvidenceBlock, EvidencePack
from business_profile.llm.extractor import GroupResult, LLMExtractionResult
from business_profile.llm.responses import (
    IdentityResponse,
    OfferingItem,
    OfferingsResponse,
    PositioningResponse,
    StringListItem,
)
from business_profile.llm.validator import RejectionCode, validate_llm_extraction
from business_profile.merger import merge_profile
from business_profile.schemas import (
    BusinessProfile,
    Confidence,
    ExtractionMeta,
    LLMEvidenceRef,
    ProfileQuality,
)
from grounding import EvidenceLedger


_URL = "https://synthetic.invalid/"


def _pack() -> EvidencePack:
    blocks = [
        EvidenceBlock(
            block_id="offer_1",
            page_url=_URL + "packages",
            page_type="pricing",
            section="main",
            tag="p",
            above_fold=True,
            text="Synthetic consultation. Packages start at USD 500.",
        ),
        EvidenceBlock(
            block_id="customers_1",
            page_url=_URL + "about",
            page_type="about",
            section="main",
            tag="p",
            above_fold=False,
            text="We serve 500 customers every year.",
        ),
        EvidenceBlock(
            block_id="history_1",
            page_url=_URL + "history",
            page_type="about",
            section="main",
            tag="p",
            above_fold=False,
            text="Established in 1983.",
        ),
    ]
    return EvidencePack(
        source_url=_URL,
        languages=["en"],
        blocks=blocks,
        block_count=len(blocks),
        block_ids=[b.block_id for b in blocks],
    )


def _result(*, identity=None, offerings=None, positioning=None) -> LLMExtractionResult:
    def group(name, response):
        return GroupResult(group=name, response=response) if response is not None \
            else GroupResult(group=name, error="not_provided")

    return LLMExtractionResult(
        identity=group("identity", identity),
        offerings=group("offerings", offerings),
        positioning=group("positioning", positioning),
        trust=GroupResult(group="trust", error="not_provided"),
    )


def _ref(block_id: str, quote: str) -> LLMEvidenceRef:
    return LLMEvidenceRef(block_id=block_id, quote=quote)


def _rules_profile() -> BusinessProfile:
    return BusinessProfile(
        source_url=_URL,
        extraction_meta=ExtractionMeta(
            manifest_path="synthetic", extracted_at=datetime.now(timezone.utc),
        ),
        quality=ProfileQuality(
            has_name=False,
            has_category=False,
            has_offerings=False,
            has_audience=False,
            has_value_propositions=False,
            has_tone=False,
            has_contact=False,
            has_visual=False,
            fields_extracted=0,
            fields_inferred=0,
            fields_missing=20,
            ready_for_strategy=False,
        ),
    )


def test_authentic_usd_500_quote_rejects_usd_9999_price():
    response = OfferingsResponse(offerings=[OfferingItem(
        name="Synthetic consultation",
        price_text="USD 9999",
        evidence=[_ref("offer_1", "Packages start at USD 500.")],
        confidence=Confidence.HIGH,
    )])

    payload = validate_llm_extraction(_result(offerings=response), _pack())

    assert payload.offerings is not None
    assert payload.offerings.offerings == []
    assert any(
        record.code == RejectionCode.CLAIM_NOT_SUPPORTED
        for record in payload.diagnostics.rejections_by_group["offerings"]
    )


def test_same_number_with_a_different_currency_is_not_supported():
    response = OfferingsResponse(offerings=[OfferingItem(
        name="Synthetic consultation",
        price_text="EUR 500",
        evidence=[_ref("offer_1", "Packages start at USD 500.")],
        confidence=Confidence.HIGH,
    )])

    payload = validate_llm_extraction(_result(offerings=response), _pack())

    assert payload.offerings is not None
    assert payload.offerings.offerings == []
    assert any(
        record.code == RejectionCode.CLAIM_NOT_SUPPORTED
        for record in payload.diagnostics.rejections_by_group["offerings"]
    )


def test_authentic_number_quote_rejects_unrelated_string_list_number():
    response = PositioningResponse(value_propositions=[StringListItem(
        value="Chosen by 500 customers",
        evidence=[_ref("offer_1", "Packages start at USD 500.")],
        confidence=Confidence.HIGH,
    )])

    payload = validate_llm_extraction(_result(positioning=response), _pack())

    assert payload.positioning is not None
    assert payload.positioning.value_propositions == []
    assert any(
        record.code == RejectionCode.CLAIM_NOT_SUPPORTED
        for record in payload.diagnostics.rejections_by_group["positioning"]
    )


def test_genuinely_supported_offering_and_list_value_still_pass():
    offerings = OfferingsResponse(offerings=[OfferingItem(
        name="Synthetic consultation",
        price_text="USD 500",
        evidence=[_ref("offer_1", "Synthetic consultation. Packages start at USD 500.")],
        confidence=Confidence.HIGH,
    )])
    positioning = PositioningResponse(value_propositions=[StringListItem(
        value="Serving 500 customers every year",
        evidence=[_ref("customers_1", "We serve 500 customers every year.")],
        confidence=Confidence.HIGH,
    )])

    payload = validate_llm_extraction(
        _result(offerings=offerings, positioning=positioning), _pack(),
    )

    assert payload.offerings.offerings[0].price_text == "USD 500"
    assert payload.positioning.value_propositions[0].value == "Serving 500 customers every year"


def test_fabricated_quote_still_fails_citation_authenticity_gate():
    response = OfferingsResponse(offerings=[OfferingItem(
        name="Synthetic consultation",
        evidence=[_ref("offer_1", "A fabricated quotation absent from the block")],
    )])

    payload = validate_llm_extraction(_result(offerings=response), _pack())

    assert payload.offerings.offerings == []
    assert any(
        record.code == RejectionCode.QUOTE_NOT_IN_BLOCK
        for record in payload.diagnostics.rejections_by_group["offerings"]
    )


def test_existing_scalar_semantic_validation_is_unchanged():
    supported = IdentityResponse(
        description_value="Serving since 1983",
        description_evidence=[_ref("history_1", "Established in 1983.")],
        description_confidence=Confidence.HIGH,
    )
    unsupported = IdentityResponse(
        description_value="Serving since 1965",
        description_evidence=[_ref("history_1", "Established in 1983.")],
        description_confidence=Confidence.HIGH,
    )

    kept = validate_llm_extraction(_result(identity=supported), _pack())
    dropped = validate_llm_extraction(_result(identity=unsupported), _pack())

    assert kept.identity.description.value == "Serving since 1983"
    assert dropped.identity.description.value is None


def test_rejected_values_never_reenter_as_merged_profile_ledger_evidence():
    offerings = OfferingsResponse(offerings=[OfferingItem(
        name="Synthetic consultation",
        price_text="USD 9999",
        evidence=[_ref("offer_1", "Packages start at USD 500.")],
        confidence=Confidence.HIGH,
    )])
    positioning = PositioningResponse(value_propositions=[StringListItem(
        value="Chosen by 500 customers",
        evidence=[_ref("offer_1", "Packages start at USD 500.")],
        confidence=Confidence.HIGH,
    )])
    result = _result(offerings=offerings, positioning=positioning)
    payload = validate_llm_extraction(result, _pack())
    final = merge_profile(_rules_profile(), payload, result)
    serialized = final.model_dump(mode="json")
    ledger = EvidenceLedger.from_profile(serialized)

    observed = {
        "offering_entered_profile": bool(final.offerings),
        "list_value_entered_profile": bool(final.value_propositions),
        "bad_price_became_evidence": ledger.resolve_claim("Packages start at USD 9999") is not None,
        "bad_count_became_evidence": ledger.resolve_claim("Chosen by 500 customers") is not None,
    }
    assert observed == {
        "offering_entered_profile": False,
        "list_value_entered_profile": False,
        "bad_price_became_evidence": False,
        "bad_count_became_evidence": False,
    }
