"""F-08: a stored creative must keep the QA disclosure of that exact image.

These tests stop at hermetic seams around paid generation, but exercise the public HTTP routes,
the real history/BrandStore persistence, job terminal state, and generated-asset serving.  They do
not infer continuity from helper calls or source strings.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import sys
import types
from types import SimpleNamespace

import pytest
from PIL import Image


# The API imports scraper modules which type-import Playwright.  F-08 never launches a browser.
_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")


class _Stub: ...


for _name in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _name, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from fastapi.testclient import TestClient  # noqa: E402

from api import webapp_images as images  # noqa: E402
from api import webapp_influencer as influencer  # noqa: E402
from api.brand_store import BrandStore, record_history  # noqa: E402
from api.job_access import user_job_principal
from api.jobs.store import serving_store  # noqa: E402
from api.schemas import JobStatus  # noqa: E402
from poster.vision_qa import (  # noqa: E402
    POSTER_QA_SOURCE,
    PosterQAVerdict,
    poster_acceptance_from_output,
    poster_attempt_identity,
    required_poster_qa_policy,
)


DOMAIN = "synthetic_qa_example"
DAY = "2026-09-03"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "f08-test-secret")
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)
    import api.user_auth as user_auth

    user_auth._SECRET_CACHE = None
    yield


@pytest.fixture
def client():
    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


def _account(client: TestClient, email: str) -> tuple[dict, str]:
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "password123", "full_name": email},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    return headers, user_id


def _seed_brand(owner_id: str, *, poster: dict | None = None) -> None:
    row = {"date": DAY, "topic": "Fabricated launch copy", "cta": "Discover"}
    if poster is not None:
        row["poster"] = poster
    BrandStore().save(
        {
            "domain_key": DOMAIN,
            "organization_name": "Synthetic QA Brand",
            "tagline": "Synthetic tagline",
            "_profile": {"name": {"value": "Synthetic QA Brand"}},
            "content_calendar": {"calendar": [row]},
        },
        owner_id=owner_id,
    )


def _png(identity: str) -> bytes:
    """A tiny valid, identity-distinct PNG; projection tests must model a possible render."""
    color = tuple(hashlib.sha256(identity.encode("utf-8")).digest()[:3])
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _creative(identity: str, status: str, note: str = "") -> dict:
    raw = _png(identity)
    qa = _authoritative_qa(identity, status, note)
    output = {
        "image_b64": base64.b64encode(raw).decode("ascii"),
        "filename": f"{DOMAIN}_post_{identity}.png",
        "headline": f"Headline {identity}",
        "subtext": "",
        "cta": "Discover",
        "is_preview": False,
        "degraded_reason": "",
        "trust_violation": False,
        "trust_note_ar": "",
        "qa_status": status,
        "qa_failed": status == "fail",
        "qa_note": note,
        "product": None,
        "product_note": "",
        "trend_used": None,
        "trend_note": "",
        "social_caption": {},
        "visual_qa": qa.model_dump(mode="json"),
    }
    decision = poster_acceptance_from_output(output)
    output["acceptance_decision"] = decision.model_dump(mode="json")
    output.update(images.acceptance_disclosure(decision))
    return output


def _authoritative_qa(identity: str, status: str, note: str = "") -> PosterQAVerdict:
    raw = _png(identity)
    policy = required_poster_qa_policy(
        arabic=False, logo_composited=False,
        expect_real_products=False, product_named=False)
    checked = status != "not_checked"
    overall = True if status == "pass" else False if status == "fail" else None
    return PosterQAVerdict(
        checked=checked, overall_pass=overall, critic_pass=overall,
        cta_clipped=status == "fail",
        text_lines=["Synthetic headline", "Discover"],
        score=8,
        reason=note or ("clean" if status == "pass" else "QA unavailable"),
        qa_source=POSTER_QA_SOURCE,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        policy_context=dict(policy.context),
        mandatory_checks_expected=list(policy.mandatory_checks),
        mandatory_checks_completed=(list(policy.mandatory_checks) if checked else []),
        non_applicable_checks=dict(policy.non_applicable_checks),
        attempt_identity=poster_attempt_identity(
            raw, attempt_id=f"legacy-{identity}", candidate_id=identity),
    )


def _patch_generation(monkeypatch, *outputs: dict) -> None:
    from api.routes import generate as generate_route

    queue = [dict(item) for item in outputs]

    def fake_generate(*args, **kwargs):
        assert queue, "unexpected extra image generation"
        return queue.pop(0)

    monkeypatch.setattr(generate_route.images, "generate_image", fake_generate)


def _assert_disclosure(value: dict, status: str, note: str = "") -> None:
    assert value["qa_status"] == status
    assert value["qa_failed"] is (status == "fail")
    assert value["qa_note"] == note


def _assert_poster_acceptance(value: dict, status: str) -> None:
    expected_status = {
        "pass": "QA_PASS",
        "fail": "QA_FAIL",
        "not_checked": "QA_UNVERIFIED",
    }[status]
    assert value["canonical_qa_status"] == expected_status
    assert value["qa_executed"] is (status != "not_checked")
    for field in ("qa_passed", "accepted", "publishable", "deliverable"):
        assert value[field] is (status == "pass")


@pytest.mark.parametrize(
    ("overall", "status", "note"),
    [(True, "pass", ""),
     (False, "fail", "Synthetic QA rejection"),
     (None, "not_checked", "Synthetic verifier unavailable")],
)
def test_real_generator_rederives_canonical_acceptance_from_authoritative_tri_state(
    monkeypatch, overall, status, note,
):
    verdict_reason = ("Synthetic QA rejection" if overall is False
                      else "Synthetic verifier unavailable" if overall is None else "clean")
    qa = _authoritative_qa(f"paid-image-{status}", status, verdict_reason)
    rendered = images.RealRender(
        png=_png(f"paid-image-{status}"),
        result=SimpleNamespace(
            qa=qa,
            trust_violation=False,
            trust_note_ar="",
        ),
    )
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    monkeypatch.setattr(images, "_render_real", lambda *args, **kwargs: rendered)
    monkeypatch.setattr(images, "_caption_for", lambda *args, **kwargs: {})

    output = images.generate_image(
        {"domain_key": DOMAIN, "organization_name": "Synthetic QA Brand"})

    assert base64.b64decode(output["image_b64"]) == rendered.png
    _assert_disclosure(output, status, note)
    _assert_poster_acceptance(output, status)


def test_failed_qa_from_unshipped_candidate_does_not_label_fallback_placeholder(
    monkeypatch,
):
    rejected_candidate = images.RealRender(
        png=None,
        result=SimpleNamespace(
            qa=SimpleNamespace(overall_pass=False, reason="belongs to unshipped candidate"),
            trust_violation=False,
            trust_note_ar="",
        ),
    )
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    monkeypatch.setattr(images, "_render_real", lambda *args, **kwargs: rejected_candidate)
    monkeypatch.setattr(images, "_caption_for", lambda *args, **kwargs: {})

    output = images.generate_image(
        {"domain_key": DOMAIN, "organization_name": "Synthetic QA Brand"})

    assert output["is_preview"] is True
    assert output["qa_status"] == "not_checked"
    assert output["qa_failed"] is False
    assert "No authoritative visual-QA result" in output["qa_note"]
    assert "belongs to unshipped candidate" not in output["qa_note"]


def test_failed_disclosure_and_reason_survive_history_for_same_creative(client, monkeypatch):
    owner, owner_id = _account(client, "f08-history-owner@test.local")
    stranger, _ = _account(client, "f08-history-stranger@test.local")
    _seed_brand(owner_id)
    failed = _creative("A", "fail", "Typography gate rejected synthetic image A")
    _patch_generation(monkeypatch, failed)

    immediate = client.post(
        "/api/generate", headers=owner,
        json={"domain_key": DOMAIN, "headline": "Synthetic"},
    )
    assert immediate.status_code == 200, immediate.text
    _assert_disclosure(immediate.json(), "fail", failed["qa_note"])
    _assert_poster_acceptance(immediate.json(), "fail")

    history = client.get("/api/history", headers=owner).json()["history"]
    assert len(history) == 1
    stored = history[0]
    assert (stored["filename"], stored["image_b64"]) == (
        immediate.json()["filename"], immediate.json()["image_b64"])
    _assert_disclosure(stored, "fail", failed["qa_note"])
    _assert_poster_acceptance(stored, "fail")
    assert client.get("/api/history", headers=stranger).json()["history"] == []


@pytest.mark.parametrize(
    ("status", "note"),
    [("pass", ""), ("not_checked", "QA service unavailable for synthetic image")],
)
def test_nonfailed_qa_states_survive_history_without_becoming_failure(
    client, monkeypatch, status, note,
):
    owner, owner_id = _account(client, f"f08-{status}@test.local")
    _seed_brand(owner_id)
    output = _creative(status, status, note)
    _patch_generation(monkeypatch, output)

    immediate = client.post("/api/generate", headers=owner, json={"domain_key": DOMAIN}).json()
    stored = client.get("/api/history", headers=owner).json()["history"][0]

    assert (stored["filename"], stored["image_b64"]) == (
        immediate["filename"], immediate["image_b64"])
    _assert_disclosure(stored, status, note)
    _assert_poster_acceptance(stored, status)


def test_history_does_not_cross_attach_newer_qa_to_older_image(client, monkeypatch):
    owner, owner_id = _account(client, "f08-two-history@test.local")
    _seed_brand(owner_id)
    image_a = _creative("A", "fail", "A-only reason")
    image_b = _creative("B", "pass")
    _patch_generation(monkeypatch, image_a, image_b)

    first = client.post("/api/generate", headers=owner, json={"domain_key": DOMAIN}).json()
    second = client.post("/api/generate", headers=owner, json={"domain_key": DOMAIN}).json()
    history = client.get("/api/history", headers=owner).json()["history"]

    assert [(item["filename"], item["image_b64"]) for item in history] == [
        (second["filename"], second["image_b64"]),
        (first["filename"], first["image_b64"]),
    ]
    _assert_disclosure(history[0], "pass")
    _assert_disclosure(history[1], "fail", "A-only reason")
    _assert_poster_acceptance(history[0], "pass")
    _assert_poster_acceptance(history[1], "fail")


def test_failed_history_metadata_write_cannot_return_generated_success(client, monkeypatch):
    from api.routes import generate as generate_route

    owner, owner_id = _account(client, "f08-history-write-fail@test.local")
    _seed_brand(owner_id)
    _patch_generation(monkeypatch, _creative("history-write-fail", "fail", "must persist"))

    # Injected at the provider: DB2G-B moved the gallery append behind `record_gallery_entry` so
    # the postgres backend can refuse it outright instead of writing history.json. The claim under
    # test is unchanged — a failed gallery write must not be reported as a generated success.
    from api import persistence

    def reject_history(*args, **kwargs):
        raise OSError("synthetic history write rejected")

    monkeypatch.setattr(persistence.LegacyPersistence, "record_gallery_entry", reject_history,
                        raising=True)
    persistence.reset_persistence()
    # The claim is unchanged and now stated more strongly. This used to assert that the raw OSError
    # escaped the route — true, but it meant the caller got an unhandled 500 carrying whatever the
    # storage layer said. The gallery cutover translates it: still NOT a success, and now a
    # sentence written for a human with nothing from the exception in it.
    response = client.post("/api/generate", headers=owner, json={"domain_key": DOMAIN})
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert "could not be saved to your gallery" in detail
    assert "synthetic history write rejected" not in detail
    assert "image_b64" not in response.text, "a failed gallery write still returned the image"


def test_calendar_disclosure_survives_brand_api_with_creative_identity_and_scope(
    client, monkeypatch,
):
    owner, owner_id = _account(client, "f08-calendar-owner@test.local")
    stranger, _ = _account(client, "f08-calendar-stranger@test.local")
    _seed_brand(owner_id)
    failed = _creative("calendar-A", "fail", "Calendar A failed text gate")
    _patch_generation(monkeypatch, failed)

    response = client.post(
        "/api/calendar/generate-image", headers=owner,
        json={"domain_key": DOMAIN, "date": DAY},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_disclosure(body, "fail", failed["qa_note"])
    _assert_poster_acceptance(body, "fail")
    assert body["filename"] == failed["filename"]
    assert client.get(body["image_url"]).content == base64.b64decode(failed["image_b64"])

    brand = client.get(f"/api/brands/{DOMAIN}", headers=owner).json()
    poster = brand["content_calendar"]["calendar"][0]["poster"]
    assert (poster["url"], poster["filename"]) == (body["image_url"], body["filename"])
    _assert_disclosure(poster, "fail", failed["qa_note"])
    _assert_poster_acceptance(poster, "fail")
    assert client.get(f"/api/brands/{DOMAIN}", headers=stranger).status_code == 404


def test_calendar_regeneration_keeps_each_image_bound_to_its_own_qa(client, monkeypatch):
    owner, owner_id = _account(client, "f08-calendar-regen@test.local")
    _seed_brand(owner_id)
    image_a = _creative("calendar-A", "fail", "A-only calendar reason")
    image_b = _creative("calendar-B", "not_checked", "B was not checked")
    _patch_generation(monkeypatch, image_a, image_b)

    a = client.post(
        "/api/calendar/generate-image", headers=owner,
        json={"domain_key": DOMAIN, "date": DAY},
    ).json()
    b = client.post(
        "/api/calendar/generate-image", headers=owner,
        json={"domain_key": DOMAIN, "date": DAY},
    ).json()

    assert a["image_url"] != b["image_url"]
    assert client.get(a["image_url"]).content == base64.b64decode(image_a["image_b64"])
    assert client.get(b["image_url"]).content == base64.b64decode(image_b["image_b64"])
    _assert_disclosure(a, "fail", image_a["qa_note"])
    _assert_disclosure(b, "not_checked", image_b["qa_note"])
    _assert_poster_acceptance(a, "fail")
    _assert_poster_acceptance(b, "not_checked")

    poster = client.get(f"/api/brands/{DOMAIN}", headers=owner).json()[
        "content_calendar"]["calendar"][0]["poster"]
    assert (poster["url"], poster["filename"]) == (b["image_url"], b["filename"])
    _assert_disclosure(poster, "not_checked", image_b["qa_note"])
    _assert_poster_acceptance(poster, "not_checked")


def test_failed_calendar_metadata_write_cannot_relabel_or_overwrite_old_creative(
    client, monkeypatch,
):
    from api.routes import generate as generate_route

    owner, owner_id = _account(client, "f08-calendar-persist-fail@test.local")
    old_image = _png("calendar-old")
    old_url = images.save_generated_png(old_image, f"{DOMAIN}_{DAY}")
    old_poster = {
        "url": old_url,
        "filename": "old.png",
        "qa_status": "pass",
        "qa_failed": False,
        "qa_note": "",
        "is_preview": False,
        "trust_violation": False,
    }
    _seed_brand(owner_id, poster=old_poster)
    _patch_generation(monkeypatch, _creative("calendar-new", "fail", "new-only reason"))

    real_store = BrandStore()

    # Injected at the persistence provider — DB2G-B routed the calendar write through it, so
    # patching BrandStore here would no longer intercept the path under test.
    from api import persistence

    monkeypatch.setattr(persistence.LegacyPersistence, "record_enrichment",
                        lambda self, **_kw: None, raising=True)
    persistence.reset_persistence()

    response = client.post(
        "/api/calendar/generate-image", headers=owner,
        json={"domain_key": DOMAIN, "date": DAY},
    )
    assert response.status_code == 404
    assert client.get(old_url).content == old_image
    assert real_store.get(DOMAIN, for_user=owner_id)["content_calendar"]["calendar"][0][
        "poster"] == old_poster


def test_calendar_qa_mutation_preserves_a_concurrent_unrelated_update(client, monkeypatch):
    from api.routes import generate as generate_route

    owner, owner_id = _account(client, "f08-calendar-concurrent@test.local")
    _seed_brand(owner_id)
    output = _creative("calendar-concurrent", "fail", "Concurrent QA reason")

    def generation_that_overlaps_another_write(*args, **kwargs):
        BrandStore().update(
            DOMAIN,
            {"marketing_strategy": {"concurrent_marker": "must survive"}},
            for_user=owner_id,
        )
        return dict(output)

    monkeypatch.setattr(generate_route.images, "generate_image", generation_that_overlaps_another_write)
    response = client.post(
        "/api/calendar/generate-image", headers=owner,
        json={"domain_key": DOMAIN, "date": DAY},
    )
    assert response.status_code == 200, response.text

    brand = client.get(f"/api/brands/{DOMAIN}", headers=owner).json()
    assert brand["marketing_strategy"]["concurrent_marker"] == "must survive"
    _assert_disclosure(
        brand["content_calendar"]["calendar"][0]["poster"],
        "fail", "Concurrent QA reason",
    )


def test_old_artifacts_remain_unknown_instead_of_receiving_a_fake_pass(client):
    owner, owner_id = _account(client, "f08-old-artifact@test.local")
    old_url = images.save_generated_png(_png("old-unknown-image"), "old_unknown")
    _seed_brand(owner_id, poster={"url": old_url, "is_preview": False})
    record_history(
        {"image_b64": base64.b64encode(_png("old-history")).decode("ascii"),
         "filename": "old-history.png", "domain_key": DOMAIN},
        owner_id=owner_id,
    )

    poster = client.get(f"/api/brands/{DOMAIN}", headers=owner).json()[
        "content_calendar"]["calendar"][0]["poster"]
    history = client.get("/api/history", headers=owner).json()["history"][0]
    assert "qa_status" not in poster and "qa_failed" not in poster
    assert "qa_status" not in history and "qa_failed" not in history


def test_influencer_poster_job_persists_qa_and_returns_same_state_only_to_owner(
    client, monkeypatch,
):
    from api import webapp_jobs
    import business_profile.llm as llm
    import poster.pipeline as poster_pipeline

    owner, owner_id = _account(client, "f08-influencer-owner@test.local")
    stranger, _ = _account(client, "f08-influencer-stranger@test.local")
    _seed_brand(owner_id)
    reason = "Influencer poster failed synthetic logo gate"
    pipeline_result = SimpleNamespace(
        image_base64=base64.b64encode(_png("synthetic-influencer-image")).decode("ascii"),
        qa=SimpleNamespace(overall_pass=False, reason=reason),
        trust_violation=False,
        trust_note_ar="",
    )
    monkeypatch.setattr(images, "paid_images_enabled", lambda: True)
    monkeypatch.setattr(influencer, "selected_face_bytes", lambda brand: (b"face", "image/png"))
    monkeypatch.setattr(llm, "default_caller", lambda **kwargs: object())
    monkeypatch.setattr(poster_pipeline, "generate_poster", lambda *args, **kwargs: pipeline_result)

    job_store = client.app.state.store
    job = job_store.create(owner_principal=user_job_principal(owner_id))
    job.domain_key = DOMAIN
    with serving_store(job_store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id,
            domain_key=DOMAIN,
            owner_id=owner_id,
            kind="influencer_poster",
            work=lambda brand: influencer.generate_influencer_poster(brand),
        ))
    assert job_store.get(job.job_id).status == JobStatus.SUCCEEDED

    result = client.get(f"/api/jobs/{job.job_id}/result", headers=owner)
    assert result.status_code == 200, result.text
    result_ai = result.json()["profile"]["ai_influencer"]
    stored_ai = client.get(f"/api/brands/{DOMAIN}", headers=owner).json()["ai_influencer"]
    assert result_ai["poster_url"] == stored_ai["poster_url"]
    _assert_disclosure(result_ai, "fail", reason)
    _assert_disclosure(stored_ai, "fail", reason)
    assert client.get(f"/api/jobs/{job.job_id}/result", headers=stranger).status_code == 404
    assert client.get(f"/api/brands/{DOMAIN}", headers=stranger).status_code == 404


def test_required_job_persistence_failure_cannot_report_success(client, monkeypatch):
    from api import webapp_jobs

    owner, owner_id = _account(client, "f08-required-write@test.local")
    _seed_brand(owner_id)
    job_store = client.app.state.store
    job = job_store.create(owner_principal=user_job_principal(owner_id))
    job.domain_key = DOMAIN
    from api import persistence

    monkeypatch.setattr(persistence.LegacyPersistence, "record_enrichment",
                        lambda self, **_kw: None, raising=True)
    persistence.reset_persistence()

    with serving_store(job_store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id,
            domain_key=DOMAIN,
            owner_id=owner_id,
            kind="influencer_poster",
            work=lambda brand: {"ai_influencer": {
                "poster_url": "/api/generated/synthetic.png",
                "qa_status": "fail", "qa_failed": True, "qa_note": "must persist",
            }},
        ))

    finished = job_store.get(job.job_id)
    assert finished.status == JobStatus.FAILED
    assert finished.result is None
    assert client.get(f"/api/jobs/{job.job_id}/result", headers=owner).status_code == 500
