"""Executable regressions for the two confirmed fail-open Poster boundaries.

This file intentionally uses only pre-existing public seams so its RED result demonstrates the
old behavior directly, independently of the new typed acceptance contract.
"""
from __future__ import annotations

import base64
import io
import sys
import types
from types import SimpleNamespace
from uuid import UUID

import pytest
from PIL import Image


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(out, format="PNG")
    return out.getvalue()


def test_canonical_web_boundary_rejects_an_unexecuted_visual_qa(monkeypatch):
    from api import webapp_images as images

    contract = SimpleNamespace(
        preflight=SimpleNamespace(status="READY"),
        strategy_version_id="strategy-1", creative_contract_id="contract-1",
        objective_id="objective-1", decision_id="decision-1",
        profile_snapshot_id="profile-1", consumer_facing_disclosures=[],
        target="audience", primary_message="message", proof_points=[],
    )
    pipeline_result = SimpleNamespace(
        qa=SimpleNamespace(overall_pass=None, checked=False, reason="QA unavailable"),
        trust_violation=False, trust_note_ar="",
        strategy_verification={"overall_pass": True},
        brief=SimpleNamespace(headline="H", subheadline="", cta_text="Go"),
        claim_ids_used=[], creative_contract={}, poster_strategy_brief={},
        repair_history=[],
    )
    monkeypatch.setattr(images, "paid_images_enabled", lambda: True)
    monkeypatch.setattr(
        images, "_render_real",
        lambda *args, **kwargs: images.RealRender(png=_png(), result=pipeline_result))
    monkeypatch.setattr(images, "_caption_for", lambda *args, **kwargs: {})
    monkeypatch.setattr(images, "product_from_link", lambda _url: (None, ""))

    with pytest.raises(RuntimeError, match="visual QA|accepted"):
        images.generate_image(
            {"domain_key": "frozen", "_profile": {"name": {"value": "Frozen"}}},
            creative_contract=contract,
            generation_run_id="11111111-1111-4111-8111-111111111111",
            generation_attempt_id="22222222-2222-4222-8222-222222222222",
        )


def test_postgres_persistence_does_not_force_unverified_qa_to_true(monkeypatch, tmp_path):
    from api import gallery_store
    from api.persistence import PostgresPersistence

    captured = {}
    stored = SimpleNamespace(
        storage_key="frozen/asset.png", mime="image/png", byte_size=len(_png()),
        checksum="a" * 64)

    class Service:
        def finalize_creative(self, data):
            captured["finalization"] = data
            return SimpleNamespace(
                run=SimpleNamespace(id=data.run_id),
                attempt=SimpleNamespace(id=data.attempt_id),
                asset=SimpleNamespace(id=UUID("33333333-3333-4333-8333-333333333333")),
            )

        def record_gallery_entry(self, **kwargs):
            captured["gallery"] = kwargs

    monkeypatch.setattr(gallery_store, "store", lambda payload: stored)
    fake_services = types.ModuleType("db.services")
    fake_services.CreativeFinalization = lambda **values: SimpleNamespace(**values)
    fake_services.VerifiedAssetData = lambda **values: SimpleNamespace(**values)
    monkeypatch.setitem(sys.modules, "db.services", fake_services)
    provider = PostgresPersistence(Service())
    monkeypatch.setattr(
        provider, "_brand_row",
        lambda **kwargs: SimpleNamespace(id=UUID("44444444-4444-4444-8444-444444444444")))
    payload = _png()
    provider.record_gallery_entry(entry={
        "image_b64": base64.b64encode(payload).decode("ascii"),
        "filename": "frozen.png", "domain_key": "frozen", "canonical": True,
        "generation_run_id": "11111111-1111-4111-8111-111111111111",
        "generation_attempt_id": "22222222-2222-4222-8222-222222222222",
        "qa_status": "not_checked", "qa_failed": False, "qa_note": "QA unavailable",
    }, user_id="55555555-5555-4555-8555-555555555555")

    finalization = captured["finalization"]
    assert finalization.qa_passed is False
    assert finalization.select_attempt is False
