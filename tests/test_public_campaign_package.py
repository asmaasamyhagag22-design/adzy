"""Isolated faults on the campaign HTTP and compositor boundaries."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from strategy.campaign_package import Brief, Source, capture
from strategy.campaign_research import Page
from test_app_review_graph import application
from test_review_workflow import database


CASE = Path(__file__).resolve().parents[1] / "docs/campaigns/firefox/brief.json"


@pytest.fixture
def fake_campaign_renderer(tmp_path, monkeypatch):
    from poster import campaign_package

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))

    def render(package, size, _path):
        image = f"{size}:{package['copy']['headline']}".encode()
        return image, {"sha256": sha256(image).hexdigest()}

    monkeypatch.setattr(campaign_package, "render_poster", render)


@pytest.fixture
def campaign(application, fake_campaign_renderer):
    from api.routes.v2_campaigns import get_clock, get_package_loader

    client, headers, service, _, data, app = application
    spec = Brief.model_validate_json(CASE.read_text()).model_dump(mode="json")
    spec["source"]["url"] = data["url"] + "/feature"
    spec["peer_source"]["url"] = "https://peer.example.test/feature"
    control = {"calls": [], "time": datetime.now(timezone.utc)}
    pages = {src["url"]: f"<html><body><p>{src['quote']}</p></body></html>".encode()
             for src in (spec["source"], spec["peer_source"])}

    def load(url, timeout):
        control["calls"].append(url)
        if url not in pages:
            raise OSError("Simulated timeout")
        return Page(url, pages[url], "synthetic_fault_fixture")

    app.dependency_overrides[get_package_loader] = lambda: load
    app.dependency_overrides[get_clock] = lambda: lambda: control["time"]
    path = "/api/v2/campaigns/" + data["domain"]

    def request(method, suffix="", body=None, auth=None):
        return client.request(method, path + suffix, json=body, headers=headers if auth is None else auth)

    yield request, spec, pages, control, service
    del app.dependency_overrides[get_package_loader]
    del app.dependency_overrides[get_clock]


def create(request, spec, expected=None):
    response = request("POST", "/package", {"brief": spec, "expected_run_id": expected})
    assert response.status_code == 200, response.text
    return response.json()


def review_body(view):
    return {"run_id": view["run_id"], "decision": "approved"}


def preview_hashes(request, view, prefix=""):
    hashes = {}
    for size in ("portrait", "square"):
        response = request("GET", f"{prefix}/package/preview?size={size}&run_id={view['run_id']}")
        assert response.status_code == 200, response.text
        hashes[size] = sha256(response.content).hexdigest()
        assert hashes[size] == response.headers["X-Poster-SHA256"]
    return hashes


def approve(request, view, prefix="", *, hashes=None):
    hashes = hashes if hashes is not None else preview_hashes(request, view, prefix)
    return request("POST", prefix + "/review", {**review_body(view), "artifact_sha256": hashes})


@pytest.mark.parametrize("failure", ["source_changed", "source_unavailable"])
def test_evidence_change_hides_prior_export_and_requires_fresh_approval(campaign, failure):
    request, spec, pages, control, _ = campaign
    first = create(request, spec)
    assert approve(request, first).json()["approved"]
    prior = "/package/export?run_id=" + first["run_id"]
    before = len(control["calls"])
    replay = request("POST", "/package", {"brief": spec, "expected_run_id": None})
    assert replay.status_code == 409 and len(control["calls"]) == before
    source = spec["source"]["url"]
    if failure == "source_changed":
        pages[source] = b"<html><body><p>The publisher revised the feature.</p></body></html>"
    else:
        pages.pop(source)
    revised = request("POST", "/refresh", {"run_id": first["run_id"]}).json()
    assert revised["run_id"] != first["run_id"] and revised["reason"] == "EVIDENCE_GAP"
    assert revised["proposal"]["package"] is None
    assert not revised["approved"]
    assert revised["proposal"]["evidence"][0]["outcome"] == (
        "SOURCE_UNAVAILABLE" if failure == "source_unavailable" else "QUOTE_NOT_FOUND"
    )
    assert request("GET", prior).status_code == 409
    assert request("POST", "/review", review_body(revised)).status_code == 409


def test_expiry_and_concurrent_approval_invalidate_export(campaign, monkeypatch):
    from db.campaign_v2 import Campaigns

    request, spec, _, control, _ = campaign
    first = create(request, spec)
    hashes = preview_hashes(request, first)
    assert approve(request, first, hashes=hashes).json()["approved"]

    def reject_during_read(self, domain, view):
        reject = request("POST", "/review", {"run_id": view["run_id"], "decision": "rejected"})
        assert reject.status_code == 200 and not reject.json()["approved"]
        return b"should never be delivered"

    monkeypatch.setattr(Campaigns, "approved_archive", reject_during_read)
    path = "/package/export?run_id=" + first["run_id"]
    blocked = request("GET", path)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CURRENT_PACKAGE_APPROVAL_REQUIRED"
    assert approve(request, first, hashes=hashes).json()["approved"]

    def reapprove_during_read(self, domain, view):
        reject_during_read(self, domain, view)
        assert approve(request, first, hashes=hashes).json()["approval_id"] != view["approval_id"]
        return b"should never be delivered"

    monkeypatch.setattr(Campaigns, "approved_archive", reapprove_during_read)
    blocked = request("GET", path)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CURRENT_PACKAGE_APPROVAL_REQUIRED"
    control["time"] += timedelta(days=7)
    assert request("GET").json()["reason"] == "SOURCE_RECHECK_REQUIRED"
    assert approve(request, first, hashes=hashes).status_code == 409
    refreshed = request("POST", "/refresh", {"run_id": first["run_id"]}).json()
    assert refreshed["run_id"] != first["run_id"] and not refreshed["approved"]
    assert approve(request, refreshed).json()["approved"]


def test_foreign_owner_cannot_fetch_sources_or_render(campaign):
    from api.user_auth import issue_token

    request, spec, _, control, service = campaign
    first = create(request, spec)
    user = service.create_user(email=f"{uuid4().hex}@synthetic.invalid", hashed_password="disabled")
    headers = {"Authorization": "Bearer " + issue_token(str(user.id))}
    before = len(control["calls"])
    assert request("POST", "/package", {"brief": spec, "expected_run_id": first["run_id"]}, headers).status_code == 404
    assert request("GET", "/package/preview?size=square&run_id=" + first["run_id"], auth=headers).status_code == 404
    assert request("GET", "/package/export?run_id=" + first["run_id"], auth=headers).status_code == 404
    assert len(control["calls"]) == before


def test_hidden_and_ambiguous_quotes_are_rejected():
    source = Source(url="https://example.test/", quote="Free today", scope="Current offer scope")
    for html, expected in [
        ('<p style="display: none !important">Free today</p>', "QUOTE_NOT_FOUND"),
        ('<section hidden><p>Free today</p></section>', "QUOTE_NOT_FOUND"),
        ('<p>Free today</p><p>Free today for subscribers</p>', "QUOTE_CONTEXT_AMBIGUOUS"),
        ('<p style="color:red">Free today</p>', "QUOTE_OBSERVED"),
    ]:
        got = capture(source, lambda url, _: Page(url, html.encode()), lambda: datetime.now(timezone.utc))
        assert got["outcome"] == expected


def test_unusually_wide_copy_renders_at_square_size(tmp_path):
    import subprocess
    import sys

    # The broader suite installs a Playwright stub during collection. Use Chromium in a fresh process.
    script = """
from pathlib import Path
import sys
from poster.campaign_package import render_poster
from strategy.campaign_package import Brief, compile_package

brief = Brief.model_validate_json(Path(sys.argv[1]).read_text())
package = compile_package(brief, "Renderer Fixture", [{"page_url": brief.source.url}])
package["copy"] = {"headline": "W" * 55, "message": "W" * 105, "cta": "W" * 30}
package["direction"] = "ltr"
image, report = render_poster(package, "square", Path(sys.argv[2]) / "square.png")
assert image.startswith(bytes.fromhex("89504e470d0a1a0a"))
assert report["geometry"][-1]["bottom"] <= 1020
assert report["renderer"]["chromium"] and len(report["renderer"]["html_sha256"]) == 64
"""
    subprocess.run([sys.executable, "-c", script, str(CASE), str(tmp_path)], check=True,
                   cwd=CASE.parents[3], timeout=20)


def test_comparison_frames_fit_the_short_square_motif(tmp_path):
    import subprocess
    import sys

    script = """
from pathlib import Path
import sys
from poster.campaign_package import render_poster

package = {
    "brand_name": "Museum", "language": "en", "direction": "ltr",
    "theme": "cobalt", "poster_layout": "source-campaign@2", "concept": "compare",
    "copy": {"headline": "Compare Artworks Side by Side",
             "message": "Use the Comparator to place artworks next to each other and zoom in on the smallest details.",
             "cta": "Read about the Comparator"},
}
for size in ("square", "portrait"):
    image, measurements = render_poster(package, size, Path(sys.argv[1]) / (size + ".png"))
    motif = next(box for box in measurements["geometry"] if box["field"] == "motif")
    assert image.startswith(bytes.fromhex("89504e470d0a1a0a"))
    assert not motif["overflow"]
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True,
                   cwd=CASE.parents[3], timeout=25)


def test_cached_bytes_survive_renderer_change_and_detect_corruption(monkeypatch, tmp_path):
    from poster import campaign_package

    run_id = str(uuid4())
    proposal_sha = "c" * 64
    view = {"run_id": run_id, "proposal": {"sha256": proposal_sha, "package": {}}}
    calls = []

    def fake_render(package, size, path):
        from hashlib import sha256
        calls.append(size)
        return b"first-render", {"sha256": sha256(b"first-render").hexdigest()}

    monkeypatch.setattr(campaign_package, "render_poster", fake_render)
    assert campaign_package.preview_poster(view, "square", tmp_path) == b"first-render"
    assert campaign_package.preview_poster(view, "square", tmp_path) == b"first-render"
    assert calls == ["square"]
    view.update(approval_id=None, profile_snapshot_id=str(uuid4()))
    expected = {size: sha256(b"first-render").hexdigest() for size in ("portrait", "square")}
    with pytest.raises(campaign_package.PosterLayoutError, match="POSTER_PREVIEWS_REQUIRED"):
        campaign_package.prepare_approval(view, tmp_path, expected)
    assert campaign_package.preview_poster(view, "portrait", tmp_path) == b"first-render"
    prepared = campaign_package.prepare_approval(view, tmp_path, expected)
    assert campaign_package.approved_preview(prepared.archive, "square") == b"first-render"
    assert calls == ["square", "portrait"]
    campaign_package.poster_path(view, "square", tmp_path).write_bytes(b"invalid")
    with pytest.raises(campaign_package.PosterLayoutError, match="POSTER_ARTIFACT_CORRUPT"):
        campaign_package.preview_poster(view, "square", tmp_path)
    assert calls == ["square", "portrait"]


def test_both_preview_hashes_are_required_and_bound_to_saved_zip(campaign):
    request, spec, _, _, _ = campaign
    draft = create(request, spec)
    before_previews = request("POST", "/review", review_body(draft))
    assert before_previews.status_code == 409
    assert before_previews.json()["detail"]["code"] == "POSTER_PREVIEWS_REQUIRED"
    invented_hashes = {"portrait": "0" * 64, "square": "1" * 64}
    missing = approve(request, draft, hashes=invented_hashes)
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "POSTER_PREVIEWS_REQUIRED"

    hashes = preview_hashes(request, draft)
    mismatch = approve(request, draft, hashes=invented_hashes)
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "POSTER_PREVIEW_CHANGED"
    approved = approve(request, draft, hashes=hashes).json()
    duplicate = approve(request, draft, hashes=hashes).json()
    assert duplicate["approval_id"] == approved["approval_id"]
    data = request("GET", "/package/export?run_id=" + draft["run_id"]).content
    with ZipFile(BytesIO(data)) as bundle:
        receipt = json.loads(bundle.read("campaign.json"))
        assert {size: sha256(bundle.read(f"poster-{size}.png")).hexdigest() for size in hashes} == hashes
    manifest = approved["approved_artifacts"]
    assert receipt["approval_id"] == approved["approval_id"]
    assert receipt["approved_artifacts"]["poster_sha256"] == manifest["poster_sha256"] == hashes
    assert manifest["archive_sha256"] == sha256(data).hexdigest()


def test_approval_survives_cache_loss_and_rejects_corrupt_saved_bytes(campaign, monkeypatch):
    from shutil import rmtree
    from uuid import UUID

    from api.user_auth import webapp_dir
    from db.models import Approval

    request, spec, _, _, service = campaign
    draft = create(request, spec)
    hashes = preview_hashes(request, draft)
    approval = approve(request, draft, hashes=hashes).json()
    export = "/package/export?run_id=" + draft["run_id"]
    initial = request("GET", export)
    assert initial.status_code == 200

    rmtree(webapp_dir() / "campaign-posters")

    def unexpected_render(*args, **kwargs):
        pytest.fail("Approved bytes must never be rendered again")

    monkeypatch.setattr("poster.campaign_package.render_poster", unexpected_render)
    assert preview_hashes(request, draft) == hashes
    restored = request("GET", export)
    assert restored.content == initial.content
    assert approve(request, draft, hashes=hashes).json()["approval_id"] == approval["approval_id"]

    with service._uow_factory() as uow:
        row = uow.session.get(Approval, UUID(approval["approval_id"]))
        row.artifact_archive = initial.content[:-1] + bytes([initial.content[-1] ^ 1])
        uow.commit()
    for endpoint in (export, "/package/preview?size=square&run_id=" + draft["run_id"]):
        blocked = request("GET", endpoint)
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "APPROVED_ARTIFACT_CORRUPT"


def test_historical_approval_needs_fresh_preview_and_keeps_the_original_record(campaign):
    from uuid import UUID

    from db.models import Approval, Run

    request, spec, _, _, service = campaign
    draft = create(request, spec)
    run_id = UUID(draft["run_id"])
    with service._uow_factory() as uow:
        run = uow.session.get(Run, run_id)
        original = uow.approvals.create_live(
            brand_id=run.brand_id, run_id=run_id, actor_id=run.created_by_user_id, subject_kind="campaign",
        )
        uow.commit()
    status = request("GET").json()
    assert not status["approved"] and status["artifact_review_required"]
    assert status["decision"] == "approved"
    assert request("GET", "/package/export?run_id=" + draft["run_id"]).status_code == 409
    current = approve(request, draft).json()
    assert current["approved"] and current["approval_id"] != str(original.id)
    with service._uow_factory() as uow:
        assert uow.session.get(Approval, original.id).artifact_manifest is None


def test_rejection_during_new_approval_preparation_is_not_overwritten(campaign, monkeypatch):
    from poster import campaign_package

    request, spec, _, _, _ = campaign
    draft = create(request, spec)
    hashes = preview_hashes(request, draft)
    prepare = campaign_package.prepare_approval

    def reject_before_commit(*args):
        candidate = prepare(*args)
        response = request("POST", "/review", {"run_id": draft["run_id"], "decision": "rejected"})
        assert response.status_code == 200
        return candidate

    monkeypatch.setattr(campaign_package, "prepare_approval", reject_before_commit)
    late = approve(request, draft, hashes=hashes)
    assert late.status_code == 409 and late.json()["detail"]["code"] == "CAMPAIGN_REVIEW_CHANGED"
    assert request("GET").json()["decision"] == "rejected"


def test_scene_export_rejects_bytes_that_differ_from_the_brief(monkeypatch, tmp_path):
    from hashlib import sha256

    from poster import campaign_scene
    from poster.campaign_package import PosterLayoutError, render_poster

    root = CASE.parents[3]
    edition = json.loads((root / "docs/campaigns/buffalo/portfolio/edition.json").read_text())[0]
    art = edition["options"][0]["art"]
    reviewed = campaign_scene.scene_bytes(art)
    digest = sha256(reviewed).hexdigest()
    alternate = campaign_scene.scene_bytes(edition["options"][1]["art"])
    assert reviewed != alternate
    (tmp_path / f"{digest}.jpg").write_bytes(alternate)
    monkeypatch.setattr(campaign_scene, "SCENES", tmp_path)

    def unexpected_render(*args, **kwargs):
        pytest.fail("Replacement artwork reached the renderer")

    monkeypatch.setattr("poster.campaign_package.render_html_to_png", unexpected_render)
    package = {
        "poster_layout": "source-campaign@5",
        "copy": {"headline": "Compare",
                 "message": "Two choices from the catalog", "cta": "See the menu"},
        "brand_name": "Catalog example", "language": "en", "direction": "ltr",
        "source_url": "https://example.test/menu", "art": art,
    }
    destination = tmp_path / "poster.png"
    with pytest.raises(PosterLayoutError, match="POSTER_SCENE_UNAVAILABLE"):
        render_poster(package, "square", destination)
    assert not destination.exists()
