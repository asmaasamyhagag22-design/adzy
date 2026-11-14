"""Campaign planner behavior through the authenticated application boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from strategy.campaign_planner import anchored_claim
from strategy.campaign_research import Page
from test_app_review_graph import application
from test_public_campaign_package import approve, fake_campaign_renderer
from test_review_workflow import database


BASE = "/api/v2/campaigns"
ROOT = "https://planned.example.test/"
QUOTE = "This feature blocks known trackers while you browse the web."


@pytest.fixture
def planner(application, fake_campaign_renderer):
    from api.routes.v2_campaigns import get_package_loader

    client, headers, _, _, _, app = application
    root = f"https://planned-{uuid4().hex[:12]}.example.test/"
    feature = root + "tracker-protection"
    control = {"calls": [], "hook": None, "root": root, "feature": feature}
    pages = {
        root: '<h1>Example</h1><a href="/tracker-protection">Tracker protection</a>',
        feature: f"<h1>Tracker protection</h1><p>{QUOTE}</p>",
    }

    def load(url, timeout):
        control["calls"].append(url)
        if control["hook"]:
            control["hook"](url)
        if url not in pages:
            raise OSError("unavailable fixture source")
        body = f"<html><body><main>{pages[url]}</main></body></html>".encode()
        return Page(url, body, "synthetic_fault_fixture")

    app.dependency_overrides[get_package_loader] = lambda: load

    def request(method, path, body=None):
        if body and body.get("url") == ROOT:
            body = {**body, "url": root}
        return client.request(method, BASE + path, headers=headers, json=body)

    yield request, control, pages
    del app.dependency_overrides[get_package_loader]


def input_body(url=ROOT, objective="Increase visits to the tracker protection explanation", expected=None):
    return {"url": url, "objective": objective, "language": "en",
            "request_id": str(uuid4()), "expected_run_id": expected}


def edit(request, draft, **updates):
    body = {"run_id": draft["run_id"], "audience": "Reviewers interested in tracking protection.",
            "tone": "Concise and calm", "selected": draft["proposal"]["brief"]["selected"], **updates}
    return request("POST", f"/{draft['domain_key']}/brief", body)


def test_start_without_profile_is_versioned_replay_safe_and_blocks_claim_expansion(planner):
    request, control, _ = planner
    body = input_body()
    response = request("POST", "/plan", body)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["reason"] == "BRIEF_REVIEW_REQUIRED" and not draft["proposal"]["package"]
    assert draft["proposal"]["evidence"][0]["quote"] == QUOTE
    assert draft["proposal"]["planner"]["trace"][0]["gap_after"] == "FEATURE_EVIDENCE"
    assert draft["proposal"]["planner"]["trace"][1]["gap_after"] is None
    assert request("POST", "/plan", body).json()["run_id"] == draft["run_id"]
    assert control["calls"] == [control["root"], control["feature"]]
    prefix = "/" + draft["domain_key"]
    assert request("POST", prefix + "/review", {"run_id": draft["run_id"], "decision": "approved"}).status_code == 409
    assert anchored_claim(draft["proposal"], "brief.summary", QUOTE)
    assert not anchored_claim(draft["proposal"], "package.copy.message", "This feature blocks all trackers.")
    assert edit(request, draft, message="This feature blocks all trackers.").status_code == 422
    accepted = edit(request, draft).json()
    assert accepted["run_id"] != draft["run_id"] and accepted["proposal"]["status"] == "READY"
    assert accepted["proposal"]["evidence"] == draft["proposal"]["evidence"]
    assert accepted["proposal"]["planner"]["destination_evidence_index"] == 0
    assert accepted["proposal"]["package"]["source_url"] == control["feature"]
    assert request("POST", "/plan", {**body, "expected_run_id": accepted["run_id"]}).status_code == 409
    assert control["calls"] == [control["root"], control["feature"]]


def test_target_changes_action_or_preserves_missing_requirement(planner):
    request, control, pages = planner
    pages[control["root"]] += '<a href="/download/options/">Download options</a>'
    pages[control["root"] + "download/options/"] = '<h1>Download options</h1>'
    first = request("POST", "/plan", input_body()).json()
    second = request("POST", "/plan", input_body(
        objective="Increase downloads for people interested in tracker protection", expected=first["run_id"],
    )).json()
    assert first["proposal"]["planner"]["options"][0]["action"] == "READ_FEATURE"
    assert second["proposal"]["planner"]["gap"] == "ACTION_DESTINATION"
    assert second["proposal"]["evidence"]
    assert second["proposal"]["package"] is None


def test_source_refresh_invalidates_approved_package(planner):
    request, control, pages = planner
    draft = request("POST", "/plan", input_body()).json()
    ready = edit(request, draft).json()
    path = "/" + draft["domain_key"]
    assert approve(request, ready, path).json()["approved"]
    pages[control["feature"]] = "<h1>Notice</h1><p>The publisher removed this page content.</p>"
    refreshed = request("POST", path + "/refresh", {"run_id": ready["run_id"]}).json()
    assert refreshed["run_id"] != ready["run_id"] and refreshed["reason"] == "EVIDENCE_GAP"
    assert request("GET", path + "/package/export?run_id=" + ready["run_id"]).status_code == 409


def test_late_research_cannot_replace_newer_campaign(planner):
    request, control, _ = planner
    draft = request("POST", "/plan", input_body()).json()
    entered, proceed = Event(), Event()

    def wait_at_source(url):
        if url == control["root"]:
            entered.set()
            assert proceed.wait(10)

    control["hook"] = wait_at_source
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(request, "POST", "/plan", input_body(expected=draft["run_id"]))
        assert entered.wait(10)
        try:
            newer = edit(request, draft)
            assert newer.status_code == 200, newer.text
        finally:
            proceed.set()
        response = pending.result(timeout=15)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "CAMPAIGN_CHANGED"
    assert request("GET", "/" + draft["domain_key"]).json()["run_id"] == newer.json()["run_id"]


def test_commercial_promise_abstains_with_specific_question(planner):
    request, _, _ = planner
    draft = request("POST", "/plan", input_body(objective="Promote a discount for tracker protection")).json()
    assert draft["reason"] == "EVIDENCE_GAP"
    assert draft["proposal"]["planner"]["gap"] == "COMMERCIAL_TERMS"
    assert "validity" in draft["proposal"]["planner"]["missing_question"]



def test_contradictory_feature_evidence_requires_review(planner):
    request, control, pages = planner
    pages[control["feature"]] += "<p>Tracker protection is no longer available in this product.</p>"
    response = request("POST", "/plan", input_body())
    assert response.status_code == 200, response.text
    result = response.json()["proposal"]
    assert result["status"] == "BLOCKED" and result["planner"]["gap"] == "SOURCE_CONFLICT"
    assert result["planner"]["conflicts"][0]["page_url"] == control["feature"]



def test_exact_topic_beats_repeated_collection_context():
    from strategy.campaign_planner import best_fact, inspect, words
    from datetime import datetime, timezone

    url = "https://gallery.example.test/collection"
    terms = words("Increase visits to the Collection Online Comparator explanation")
    body = b"""<html><body><main><h1>About Collection Online</h1>
      <p>The Online Collection brings together research and images.</p>
      <p>The Online Collection covers works from many periods.</p>
      <p>The Online Collection includes documents and research from the museum.</p>
      <p>Use the Comparator to place two artworks side by side.</p>
      </main></body></html>"""
    page = inspect(Page(url, body), lambda: datetime.now(timezone.utc), terms)
    assert best_fact([page], terms)["quote"] == "Use the Comparator to place two artworks side by side."


def test_named_item_does_not_inherit_a_variant_description(planner):
    request, control, pages = planner
    regular = "Fresh, 100% beef patties hot off the grill, layered with strips of crispy Applewood smoked bacon and placed on a soft, toasted sesame seed bun."
    smaller = "A fresh, 100% beef patty hot off the grill with strips of crispy Applewood smoked bacon on top."
    pages[control["root"]] = (
        "<h1>What comes in the Bacon Burger</h1>"
        f"<section><div><h3>Bacon Burger</h3></div><p>{regular}</p></section>"
        f"<section><h3>Little Bacon Burger</h3><p>{smaller}</p></section>"
    )
    objective = "Help people discover what comes in the Bacon Burger"
    draft = request("POST", "/plan", input_body(objective=objective)).json()
    evidence = draft["proposal"]["evidence"][0]
    assert (evidence["label"], evidence["quote"]) == ("Bacon Burger", regular)
    ready = edit(request, draft).json()
    assert ready["proposal"]["package"]["copy"]["headline"] == "Bacon Burger"
    assert ready["proposal"]["package"]["source_url"] == evidence["page_url"]

    pages[control["root"]] = (
        "<section><h3>Bacon Burger</h3></section>"
        "<aside><p>Customers often ask if bacon is available in every dish on this menu.</p></aside>"
        "<p>Bacon Burger and other products are discussed in this footer note.</p>"
        f"<section><h3>Little Bacon Burger</h3><p>{smaller}</p></section>"
    )
    result = request("POST", "/plan", input_body(objective=objective, expected=ready["run_id"])).json()
    assert result["proposal"]["planner"]["gap"] == "FEATURE_EVIDENCE"
    assert result["proposal"]["package"] is None

    pages[control["root"]] = (
        "<h3>Bacon Burger</h3><p>Our Little Bacon Burger uses one flame-grilled patty and a mustard sauce.</p>"
        "<h3>Little Bacon Burger</h3>"
    )
    result = request("POST", "/plan", input_body(objective=objective, expected=result["run_id"])).json()
    assert result["proposal"]["planner"]["gap"] == "FEATURE_EVIDENCE"
    assert result["proposal"]["package"] is None

    pages[control["root"]] = (
        '<h3>Bacon Burger</h3><section aria-label="Little Bacon Burger">'
        "<p>Our Little Bacon Burger uses one flame-grilled patty and a mustard sauce.</p></section>"
    )
    result = request("POST", "/plan", input_body(objective=objective, expected=result["run_id"])).json()
    assert result["proposal"]["planner"]["gap"] == "FEATURE_EVIDENCE"
    assert result["proposal"]["package"] is None


def test_new_brief_during_export_fences_old_approval(planner, monkeypatch):
    from db.campaign_v2 import Campaigns

    request, _, _ = planner
    draft = request("POST", "/plan", input_body()).json()
    ready = edit(request, draft).json()
    prefix = "/" + draft["domain_key"]
    assert approve(request, ready, prefix).json()["approved"]

    def replace_while_reading(self, domain, view):
        changed = edit(request, {**ready, "domain_key": draft["domain_key"]}, selected=1)
        assert changed.status_code == 200 and not changed.json()["approved"]
        return b"previous version bytes"

    monkeypatch.setattr(Campaigns, "approved_archive", replace_while_reading)
    blocked = request("GET", prefix + "/package/export?run_id=" + ready["run_id"])
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "CAMPAIGN_CHANGED"
    current = request("GET", prefix).json()
    assert current["run_id"] != ready["run_id"] and not current["approved"]



def test_download_revision_binds_the_selected_destination(planner):
    request, control, pages = planner
    root = control["root"]
    pages[root] += '<a href="/download/options/">Download options</a>'
    pages[root + "download/options/"] = '<title>Download options</title><h1>Download options</h1>'
    response = request("POST", "/plan", input_body(
        objective="Increase downloads for people interested in tracker protection",
    ))
    assert response.status_code == 200, response.text
    draft = response.json()
    selected = edit(request, draft).json()
    assert selected["proposal"]["package"]["source_url"] == root + "download/options/"
    assert selected["proposal"]["planner"]["destination_evidence_index"] == 1
    alternative = edit(request, {**selected, "domain_key": draft["domain_key"]}, selected=0).json()
    assert alternative["proposal"]["planner"]["destination_evidence_index"] == 0
    assert alternative["proposal"]["package"]["source_url"] == control["feature"]
