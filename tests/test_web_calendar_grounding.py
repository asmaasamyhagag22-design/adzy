"""F-04: the real web calendar composition must ship only grounded hard claims.

Hermetic: the calendar model and media-plan builder are fakes; no network, render, or paid call.
The assertions are on ``generate_plan`` output and the BrandStore/API payload, not source text.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api import webapp_analysis as analysis
from api.brand_store import BrandStore


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))


def _ef(value: str, quote: str | None = None) -> dict:
    return {
        "value": value,
        "evidence": [{
            "block_id": "synthetic_1",
            "page_url": "https://synthetic.test/about",
            "quote": quote or value,
            "extractor": "llm",
        }],
        "confidence": "high",
        "source_type": "extracted",
    }


def _profile() -> dict:
    return {
        "source_url": "https://synthetic.test/",
        "name": _ef("Synthetic Studio"),
        "tagline": _ef("Ideas for everyday moments"),
        "description": _ef("We support 500 members every year"),
        "category": _ef("creative studio"),
        "languages": ["en"],
        "value_propositions": [_ef("Thoughtful creative guidance")],
        "offerings": [{"name": _ef("Creative workshops")}],
        "social_presence": [{"platform": "instagram", "url": "https://synthetic.test/social"}],
    }


def _brand() -> dict:
    return {
        "domain_key": "synthetic_test",
        "organization_name": "Synthetic Studio",
        "tagline": "Ideas for everyday moments",
        "key_messages": ["Thoughtful creative guidance"],
        "has_strategy": True,
        "_profile": _profile(),
    }


def _item(**overrides) -> dict:
    item = {
        "day_offset": 0,
        "platform": "instagram",
        "content_type": "post",
        "topic": "A brighter creative moment",
        "angle": "A warm invitation to explore",
        "hook": "Make today feel brighter",
        "funnel_stage": "tofu",
        "cta": "Discover more",
    }
    item.update(overrides)
    return item


def _wire_model(monkeypatch, items: list[dict]) -> None:
    def caller(_system, _user, schema, **kwargs):
        assert kwargs.get("group_name") == "content_strategy"
        return schema(items=items), {"total_tokens": 0}

    monkeypatch.setattr(analysis, "_caller", lambda strong: caller)
    monkeypatch.setattr("media_plan.builder.build_media_plan", lambda *args, **kwargs: None)
    # Language resolution is orthogonal to this gate and must not load another creative pipeline.
    monkeypatch.setattr("strategy.builder._brand_is_arabic", lambda profile: False)


def _generate(monkeypatch, *items: dict) -> dict:
    _wire_model(monkeypatch, list(items))
    return analysis.generate_plan(_brand(), planning_period="1_week")


def _entry(patch: dict) -> dict:
    return patch["content_calendar"]["calendar"][0]


def test_web_plan_removes_unsupported_factual_hook(monkeypatch):
    patch = _generate(monkeypatch, _item(hook="Save 77% with the only certified studio"))
    assert "77%" not in _entry(patch)["hook"]
    assert "certified" not in _entry(patch)["hook"].lower()


def test_web_plan_removes_unsupported_factual_cta(monkeypatch):
    patch = _generate(monkeypatch, _item(cta="Join 9000 customers today"))
    assert "9000" not in _entry(patch)["cta"]


def test_web_plan_gates_angle_before_projection(monkeypatch):
    captured = {}
    original = analysis._calendar_to_frontend

    def capture(cal):
        captured["calendar"] = cal
        return original(cal)

    monkeypatch.setattr(analysis, "_calendar_to_frontend", capture)
    _generate(monkeypatch, _item(angle="The #1 and only certified studio"))

    item = captured["calendar"].items[0]
    assert item.angle == ""
    assert any(r["field"] == "angle" for r in item.remediation)


def test_web_plan_replaces_unsupported_factual_topic(monkeypatch):
    patch = _generate(monkeypatch, _item(topic="The #1 studio serving 9000 customers"))
    topic = _entry(patch)["topic"]
    assert topic
    assert "#1" not in topic and "9000" not in topic


def test_distributed_claims_are_clean_in_complete_customer_payload(monkeypatch):
    captured = {}
    original = analysis._calendar_to_frontend

    def capture(cal):
        captured["calendar"] = cal
        return original(cal)

    monkeypatch.setattr(analysis, "_calendar_to_frontend", capture)
    patch = _generate(
        monkeypatch,
        _item(
            topic="#1 synthetic studio",
            hook="Save 77% today",
            angle="The only certified choice",
            cta="Join 9000 customers",
        ),
    )

    customer_payload = json.dumps(patch, ensure_ascii=False).lower()
    assert all(token not in customer_payload for token in ("#1", "77%", "9000", "certified"))
    item = captured["calendar"].items[0]
    assert item.angle == ""
    assert {r["field"] for r in item.remediation} == {"topic", "hook", "angle", "cta"}


def test_supported_factual_calendar_content_remains(monkeypatch):
    supported = "We support 500 members every year"
    patch = _generate(monkeypatch, _item(topic=supported, hook=supported))
    entry = _entry(patch)
    assert entry["topic"] == supported and entry["hook"] == supported


def test_non_factual_creative_calendar_copy_remains(monkeypatch):
    item = _item(
        topic="Small moments, brighter days",
        angle="A warm invitation to explore",
        hook="Make today feel lighter",
        cta="Discover more",
    )
    patch = _generate(monkeypatch, item)
    entry = _entry(patch)
    assert entry["topic"] == item["topic"]
    assert entry["hook"] == item["hook"] and entry["cta"] == item["cta"]


def test_calendar_stays_structurally_valid_after_remediation(monkeypatch):
    patch = _generate(
        monkeypatch,
        _item(topic="Best certified studio since 1999", hook="Only certified choice",
              angle="Save 88%", cta="Join 9000 customers"),
    )
    entry = _entry(patch)
    assert entry["topic"]
    assert entry["hook"] == "" and entry["cta"] == ""
    assert all(key in entry for key in ("week", "day", "date", "channel", "content_type",
                                        "topic", "hook", "cta", "funnel_stage"))
    assert patch["marketing_plan"]["campaigns"]


@pytest.mark.parametrize(
    ("field", "claim", "rejected_token"),
    [
        ("topic", "أفضل استوديو معتمد منذ ٢٠١٠", "٢٠١٠"),
        ("cta", "انضم إلى ٩٠٠٠ عميل اليوم", "٩٠٠٠"),
        ("hook", "The only استوديو معتمد يخدم 9000 عميل", "9000"),
        ("angle", "وفر ٨٨٪ مع الاستوديو الأفضل", "٨٨"),
    ],
)
def test_arabic_and_mixed_hard_claims_cannot_bypass_calendar_grounding(
        monkeypatch, field, claim, rejected_token):
    captured = {}
    original = analysis._calendar_to_frontend

    def capture(cal):
        captured["calendar"] = cal
        return original(cal)

    monkeypatch.setattr(analysis, "_calendar_to_frontend", capture)
    patch = _generate(monkeypatch, _item(**{field: claim}))

    assert rejected_token not in json.dumps(patch, ensure_ascii=False)
    assert rejected_token not in getattr(captured["calendar"].items[0], field)


def test_grounded_calendar_survives_job_persistence_and_brand_api_projection(monkeypatch):
    from api.jobs.store import JobStore, serving_store
    from api.routes.brands import get_brand
    from api.schemas import JobStatus
    from api.webapp_jobs import run_brand_job

    _wire_model(monkeypatch, [
        _item(topic="#1 synthetic studio", hook="Save 77% today",
              angle="The only certified choice", cta="Join 9000 customers"),
    ])
    BrandStore().save(_brand(), owner_id="owner")

    async def run_it():
        jobs = JobStore(ttl_seconds=60)
        with serving_store(jobs):
            job = jobs.create()
            job.domain_key = "synthetic_test"
            jobs.attach_loop(job.job_id, asyncio.get_running_loop())
            await run_brand_job(job_id=job.job_id, domain_key="synthetic_test",
                                work=analysis.generate_plan, owner_id="owner")
            return jobs.get(job.job_id).status

    assert asyncio.run(run_it()) == JobStatus.SUCCEEDED
    response = get_brand("synthetic_test", user={"id": "owner"})
    customer_payload = json.dumps({
        "content_calendar": response["content_calendar"],
        "marketing_plan": response["marketing_plan"],
    }, ensure_ascii=False).lower()
    assert all(token not in customer_payload for token in ("#1", "77%", "9000", "certified"))
