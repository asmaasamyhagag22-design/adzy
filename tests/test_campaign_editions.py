"""Check the published campaign boundary with local, labelled source faults."""
from html import escape
from pathlib import Path
from email.message import Message
from urllib.error import HTTPError
from uuid import uuid4
import json

import pytest

from strategy.campaign_research import Page
from test_app_review_graph import application
from test_public_campaign_package import approve, fake_campaign_renderer
from test_review_workflow import database, sdk_telemetry


@pytest.fixture
def edition(application, monkeypatch, tmp_path, fake_campaign_renderer):
    from api.routes.v2_campaigns import get_package_loader

    client, headers, _, _, _, app = application
    root = f"https://catalog-{uuid4().hex}.example.test"
    path = tmp_path / "editions.json"
    spec = json.loads(Path("docs/campaigns/buffalo/editions.json").read_text())[0]
    spec.update(entry=root, brand_name="Catalog fixture")
    spec.pop("observe_entry")
    bodies = {}
    for index, item in enumerate(spec["sources"]):
        item["url"] = f"{root}/catalog/{index}"
        title = item.get("title", item["quote"])
        bodies[item["url"]] = f'<title>{escape(title)}</title><meta property="og:description" content="{escape(item["quote"])}">'
    path.write_text(json.dumps([spec]))
    monkeypatch.setenv("ADZY_CAMPAIGN_EDITIONS_FILE", str(path))
    fault, calls = {}, []

    def load(url, timeout):
        calls.append(url)
        if isinstance(fault.get(url), TimeoutError):
            raise fault.pop(url)
        if url in fault:
            status, values = fault[url]
            headers = Message()
            for key, value in values.items():
                headers[key] = value
            raise HTTPError(url, status, "fixture", headers, None)
        if url not in bodies:
            raise OSError("No fixture for this source")
        return Page(url, bodies[url].encode(), "synthetic_fault_fixture")

    app.dependency_overrides[get_package_loader] = lambda: load

    def request(method, suffix, body=None):
        return client.request(method, "/api/v2/campaigns" + suffix, headers=headers, json=body)

    try:
        yield request, root, spec, bodies, fault, calls
    finally:
        app.dependency_overrides.pop(get_package_loader)


def plan(request, root, objective="تشجيع المستخدم على تصفّح المنيو واختيار برجر يناسبه"):
    result = request("POST", "/plan", {"url": root, "objective": objective, "language": "ar",
                                       "request_id": str(uuid4())})
    assert result.status_code == 200, result.text
    return result.json()


def test_field_level_review_and_binding(edition):
    request, root, spec, _, _, _ = edition
    draft = plan(request, root)
    path = "/" + draft["domain_key"]
    brief = draft["proposal"]["brief"]
    assert len(brief["directions"]) == 2
    copy = {key: brief["directions"][0][key] for key in ("headline", "message", "cta", "caption")}
    edit = {"run_id": draft["run_id"], "audience": brief["audience"], "tone": "مباشر وخفيف", "selected": 0}
    bad = request("POST", path + "/brief", {**edit, "copy_edit": {**copy, "headline": "كل البرجر بالهالبينو"}})
    assert bad.status_code == 409
    assert bad.json()["detail"]["fields"] == ["headline"]
    assert request("GET", path).json()["run_id"] == draft["run_id"]
    assert request("POST", path + "/brief", {**edit, "selected": 2}).status_code == 409
    text = spec["options"][0]["variants"]["message"][0]
    unregistered = request("POST", path + "/brief", {
        **edit, "copy_edit": {**copy, "message": "اختار بين أولد سكول وراستافاري على حسب مزاجك."},
    })
    assert unregistered.status_code == 409
    assert unregistered.json()["detail"]["fields"] == ["message"]
    accepted = request("POST", path + "/brief", {**edit, "copy_edit": {**copy, "message": text}}).json()
    package = accepted["proposal"]["package"]
    assert package["copy"]["message"] == text
    assert package["source_url"] == spec["sources"][0]["url"]
    assert package["claims"][1]["evidence"] == [1, 2]
    assert not accepted["approved"]
    assert request("POST", path + "/direction", {"run_id": accepted["run_id"], "selected": 2}).status_code == 409


@pytest.mark.parametrize("scenario", ["changed", "unreachable"])
def test_bound_source_fault_revises_and_withdraws_export(edition, scenario):
    request, root, spec, bodies, fault, _ = edition
    draft = plan(request, root)
    path = "/" + draft["domain_key"]
    brief = draft["proposal"]["brief"]
    ready = request("POST", path + "/brief", {
        "run_id": draft["run_id"], "audience": brief["audience"], "tone": brief["tone"], "selected": 0,
    }).json()
    assert approve(request, ready, path).json()["approved"]
    source = spec["sources"][2]["url"]
    if scenario == "changed":
        bodies[source] = bodies[source].replace(escape(spec["sources"][2]["quote"]), "Changed product description")
    else:
        fault[source] = (503, {})
    fresh = request("POST", path + "/refresh", {"run_id": ready["run_id"]}).json()
    assert fresh["reason"] == "EVIDENCE_GAP" and not fresh["approved"]
    assert fresh["run_id"] != ready["run_id"] and fresh["proposal"]["package"] is None
    expected = "SOURCE_FIELD_CHANGED" if scenario == "changed" else "SOURCE_UNAVAILABLE"
    assert fresh["proposal"]["planner"]["trace"][-1]["outcome"] == expected
    stale = request("GET", path + "/package/export?run_id=" + ready["run_id"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "CAMPAIGN_CHANGED"


def test_redirect_traces_in_scope_and_refuses_foreign_origin(edition):
    request, root, spec, bodies, fault, calls = edition
    source = spec["sources"][0]["url"]
    final = root + "/catalog"
    fault[source] = (308, {"Location": "/catalog"})
    bodies[final] = bodies[source]
    foreign = "https://foreign.example.test/"
    fault[spec["sources"][1]["url"]] = (302, {"Location": foreign})
    draft = plan(request, root)
    trace = draft["proposal"]["planner"]["trace"]
    assert trace[0]["redirect_to"] == final
    assert trace[1]["reason"] == "FOLLOW_SOURCE_REDIRECT" and trace[1]["outcome"] == "QUOTE_OBSERVED"
    assert trace[2]["outcome"] == "SOURCE_REDIRECT_OUT_OF_SCOPE"
    assert foreign not in calls and draft["reason"] == "EVIDENCE_GAP"


def test_campaign_observations_redact_publisher_and_copy(edition, monkeypatch):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from workflows.telemetry import Telemetry

    request, root, _, _, _, _ = edition
    exporter = InMemorySpanExporter()
    telemetry = sdk_telemetry(exporter)
    monkeypatch.setattr(Telemetry, "from_env", lambda: telemetry)
    draft = plan(request, root)
    brief = draft["proposal"]["brief"]
    direction = brief["directions"][0]
    body = {"run_id": draft["run_id"], "audience": brief["audience"], "tone": brief["tone"], "selected": 0,
            "copy_edit": {key: direction[key] for key in ("headline", "message", "cta", "caption")}}
    body["copy_edit"]["headline"] = "PRIVATE_EXPANDED_CLAIM"
    response = request("POST", "/" + draft["domain_key"] + "/brief", body)
    assert response.status_code == 409
    path = "/" + draft["domain_key"]
    body.pop("copy_edit")
    reviewed = request("POST", path + "/brief", body).json()
    approval = approve(request, reviewed, path)
    assert approval.json()["approved"]
    exported = request("GET", path + "/package/export?run_id=" + reviewed["run_id"])
    assert exported.status_code == 200 and exported.content.startswith(b"PK")
    telemetry.flush()
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "campaign.plan", "campaign.research", "campaign.fetch", "campaign.select_evidence",
        "campaign.write_copy", "campaign.persist", "campaign.review_copy", "campaign.verify",
        "campaign.approval", "campaign.export",
    }
    assert len({span.context.trace_id for span in spans}) == 1
    research = next(span for span in spans if span.name == "campaign.research")
    assert all(span.parent and span.parent.span_id == research.context.span_id
               for span in spans if span.name == "campaign.fetch")
    serialized = str([dict(span.attributes) for span in spans])
    assert root not in serialized and "PRIVATE_EXPANDED_CLAIM" not in serialized
    assert "NEEDS_REVIEW" in serialized and "COPY_REQUIRES_EVIDENCE_REVIEW" in serialized


def test_timeout_retries_once_inside_research_trace(edition):
    request, root, spec, _, fault, calls = edition
    source = spec["sources"][0]["url"]
    fault[source] = TimeoutError("transient fixture")
    draft = plan(request, root)
    trace = draft["proposal"]["planner"]["trace"]
    assert draft["proposal"]["status"] == "DRAFT"
    assert calls.count(source) == 2
    assert trace[0]["outcome"] == "SOURCE_TIMEOUT"
    assert trace[1]["reason"] == "RETRY_SOURCE_TIMEOUT" and trace[1]["outcome"] == "QUOTE_OBSERVED"


def test_catalog_scope_requires_reviewed_subject(edition):
    request, root, _, _, _, calls = edition
    draft = plan(request, root, objective="تشجيع الناس على تصفّح المنيو واختيار حلوى")
    assert "edition" not in draft["proposal"]["planner"]
    assert not draft["proposal"]["package"]
    assert calls == [root]
