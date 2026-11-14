"""Exercise Campaign release, operational alert and rollback over real local HTTP."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4
from zipfile import ZipFile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflows import demo
from workflows.campaign_eval import case_set, review_input
from workflows.campaign_monitor import events, report


class LocalCampaign:
    def __init__(self, root):
        self.root = root
        self.state = root / "state"
        self.command = [sys.executable, "-m", "workflows.public_campaign_demo"]
        self.log = root / "server.log"
        self.manifest = json.loads((self.state / "demo.json").read_text())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.port = port
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=40, trust_env=False)

    @contextmanager
    def serve(self, *, release, case):
        env = {**os.environ, "ADZY_CAMPAIGN_RELEASE": release,
               "ADZY_CAMPAIGN_EDITIONS_FILE": str(ROOT / case["editions"]) if case.get("editions") else ""}
        source = (["--simulate-case", case["id"]] if case.get("simulated_transport")
                  else ["--snapshot", str(ROOT / case["snapshot"])])
        with self.log.open("a", encoding="utf-8") as log:
            server = subprocess.Popen([*self.command, "serve", "--directory", str(self.state),
                                       "--port", str(self.port), *source],
                                      cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = monotonic() + 40
                while server.poll() is None and monotonic() < deadline:
                    try:
                        if self.client.get("/app", timeout=1).is_success:
                            break
                    except httpx.TransportError:
                        pass
                    sleep(.15)
                else:
                    raise RuntimeError(f"Campaign demo failed to start. See {self.log}")
                yield
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)

    def login(self):
        token = self.client.post("/api/auth/login", json={"email": self.manifest["email"],
                                 "password": "LocalCampaignReview42"}).raise_for_status().json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    def api(self, method, suffix, *, body=None, expected=200):
        response = self.client.request(method, "/api/v2/campaigns" + suffix, json=body)
        assert response.status_code == expected, (suffix, response.status_code, response.text[:1000])
        return response

    def plan(self, case):
        inputs = case["input"]
        current = self.api("GET", "/planning-state?" + str(httpx.QueryParams({"url": inputs["url"]}))).json()
        return self.api("POST", "/plan", body={**inputs, "expected_run_id": current["run_id"],
                                             "request_id": str(uuid4())}).json()

    def copy(self, draft, edit, expected=200):
        review = review_input(draft["proposal"]["brief"], edit)
        return self.api("POST", f"/{draft['domain_key']}/brief", body={
            **review.model_dump(mode="json"), "run_id": draft["run_id"],
        }, expected=expected).json()

    def approve(self, domain, draft):
        run_id = draft["run_id"]
        previews = {}
        for size in ("portrait", "square"):
            response = self.api("GET", f"/{domain}/package/preview?run_id={run_id}&size={size}")
            previews[size] = sha256(response.content).hexdigest()
            assert previews[size] == response.headers["X-Poster-SHA256"]
        approved = self.api("POST", f"/{domain}/review", body={
            "run_id": run_id, "decision": "approved", "artifact_sha256": previews,
        }).json()
        assert approved["approved"]
        assert approved["approved_artifacts"]["poster_sha256"] == previews
        return approved

    def export(self, domain, approved):
        prefix = f"/{domain}/package"
        run_id = approved["run_id"]
        preview = {size: self.api("GET", f"{prefix}/preview?run_id={run_id}&size={size}").content
                   for size in ("square", "portrait")}
        bundle = self.api("GET", f"{prefix}/export?run_id={run_id}").content
        assert approved["approved_artifacts"]["archive_sha256"] == sha256(bundle).hexdigest()
        with ZipFile(BytesIO(bundle)) as archive:
            saved = json.loads(archive.read("campaign.json"))
            assert saved["proposal"] == approved["proposal"]
            assert saved["approval_id"] == approved["approval_id"] and saved["run_id"] == run_id
            assert saved["profile_snapshot_id"] == approved["profile_snapshot_id"]
            for size, png in preview.items():
                assert archive.read(f"poster-{size}.png") == png
                assert saved["poster_measurements"][size]["sha256"] == sha256(png).hexdigest()
        return bundle


def audit_database(manifest, campaign):
    from sqlalchemy import func, select, text
    from db.models import Approval, Run, UsageRecord

    service, _ = demo.connect(manifest)
    with service._uow_factory() as uow:
        run = uow.session.get(Run, campaign["run_id"])
        decision = uow.session.get(Approval, campaign["approval_id"])
        assert run.output == campaign["proposal"]
        assert str(decision.subject_run_id) == campaign["run_id"] and decision.decision == "approved"
        assert str(decision.decided_by_user_id) == manifest["actor"]
        return {"migration": uow.session.scalar(text("select version_num from alembic_version")),
                "provider_usage_rows": uow.session.scalar(select(func.count()).select_from(UsageRecord))}


def exercise(app):
    cases = {case["id"]: case for case in case_set()[0]}
    osm = cases["osm-local-knowledge"]
    buffalo = cases["buffalo-menu"]
    nandos = cases["nandos-page-cap"]
    start = monotonic()

    with app.serve(release="stable", case=buffalo):
        app.login()
        draft = app.plan(buffalo)
        rejected = app.copy(draft, buffalo["edits"][2], expected=409)
        assert rejected["detail"]["code"] == "COPY_REQUIRES_EVIDENCE_REVIEW"
        reviewed = app.copy(draft, buffalo["edits"][0])
        app.api("GET", f"/{draft['domain_key']}/package/export?run_id={reviewed['run_id']}", expected=409)
        initial = app.approve(draft["domain_key"], reviewed)
        buffalo_zip = app.export(draft["domain_key"], initial)
        assert initial["proposal"]["package"]["source_url"] == buffalo["expected"]["destination"]

    with app.serve(release="candidate", case=osm):
        draft = app.plan(osm)
        assert draft["proposal"]["planner"]["version"]["release"] == "candidate"
        assert app.copy(draft, osm["edits"][1], expected=409)["detail"]["code"] == "COPY_REQUIRES_EVIDENCE_REVIEW"
        reviewed = app.copy(draft, osm["edits"][0])
        candidate = app.approve(draft["domain_key"], reviewed)
        assert candidate["proposal"]["package"]["copy"]["headline"] == osm["edits"][0]["text"]
        candidate_zip = app.export(draft["domain_key"], candidate)
    database_before = audit_database(app.manifest, candidate)

    with app.serve(release="candidate", case=nandos):
        blocked = []
        for _ in range(21):
            state = app.plan(nandos)
            planner = state["proposal"]["planner"]
            assert planner["source_mode"] == "simulated_transport"
            assert planner["trace"][0]["outcome"] == "PAGE_TOO_LARGE" and state["reason"] == "EVIDENCE_GAP"
            blocked.append(planner["trace_id"])

    with app.serve(release="candidate", case=cases["nandos-timeout"]):
        timeout_state = app.plan(cases["nandos-timeout"])
        assert timeout_state["proposal"]["planner"]["trace"][0]["outcome"] == "SOURCE_TIMEOUT"

    with app.serve(release="stable", case=osm):
        domain = draft["domain_key"]
        recovered = app.api("GET", f"/{domain}").json()
        for field in ("run_id", "approval_id", "profile_snapshot_id", "proposal"):
            assert recovered[field] == candidate[field]
        assert recovered["approved"] and app.export(domain, recovered) == candidate_zip
        database_after = audit_database(app.manifest, recovered)
        assert database_before == database_after
        fresh = app.plan(osm)
        version = fresh["proposal"]["planner"]["version"]
        assert version["release"] == "stable"
        assert app.copy(fresh, osm["edits"][0], expected=409)["detail"]["code"] == "COPY_REQUIRES_EVIDENCE_REVIEW"
        assert app.api("GET", f"/{domain}/package/export?run_id={recovered['run_id']}", expected=409).json()[
            "detail"]["code"] == "CAMPAIGN_CHANGED"
        assert audit_database(app.manifest, recovered) == database_before

    rows = list(events(app.log.read_text(encoding="utf-8").splitlines()))
    # Anchor the report to the injected incident so its 5 minute window is reproducible.
    anchor = datetime.fromisoformat(next(
        row["at"] for row in reversed(rows) if row["reason"] == "SOURCE_TIMEOUT"
    ))
    monitor = report(rows, end=anchor)
    assert len(monitor["alerts"]) == 1
    assert monitor["alerts"][0]["code"] == "CAMPAIGN_REPEATED_PAGE_TOO_LARGE"
    assert set(monitor["alerts"][0]["trace_ids"]) == set(blocked)
    assert monitor["metrics"]["simulated_transport"]["latency"]["campaign.fetch"]["p95_ms"] is not None

    def stages(state):
        planner = state["proposal"]["planner"]
        trace = [row for row in rows if row["trace_id"] == planner["trace_id"]]
        return {"run_id": state["run_id"], "profile_snapshot_id": state["profile_snapshot_id"],
                "approval_id": state["approval_id"], "proposal_sha256": state["proposal"]["sha256"],
                "snapshot_id": planner["snapshot_id"], "trace_id": planner["trace_id"],
                "stages": trace, "version": planner["version"]}

    buffalo_result = stages(initial)
    assert {row["stage"] for row in buffalo_result["stages"]} >= {
        "campaign.fetch", "campaign.research", "campaign.select_evidence", "campaign.write_copy",
        "campaign.verify", "campaign.approval", "campaign.export",
    }
    for name, bundle in (("buffalo", buffalo_zip), ("candidate", candidate_zip)):
        (app.root / f"{name}.zip").write_bytes(bundle)
    return {
        "schema": "adzy.campaign-lifecycle-demo@1",
        "elapsed_seconds": round(monotonic() - start, 3),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "buffalo": {**buffalo_result, "zip_sha256": sha256(buffalo_zip).hexdigest()},
        "candidate": {**stages(candidate), "zip_sha256": sha256(candidate_zip).hexdigest()},
        "simulated_source_failure": stages(state), "simulated_timeout": stages(timeout_state),
        "monitor": monitor,
        "rollback": {"fresh": stages(fresh), "prior_zip_byte_equal": True, "prior_proposal_equal": True,
                     "prior_approval_preserved": True, "same_domain_later_run_fences_prior_export": True,
                     "database_before": database_before, "database_after": database_after},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Promotion is conditional on the same gate that the CI workflow runs.
    gate_path = output / "release-gate.json"
    subprocess.run([sys.executable, "-m", "workflows.campaign_eval", "--output", str(gate_path)],
                   check=True, cwd=ROOT)
    gate = json.loads(gate_path.read_text())
    demo.isolate()
    command = [sys.executable, "-m", "workflows.public_campaign_demo"]
    state = output / "state"
    subprocess.run([*command, "init", "--directory", str(state)], check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    try:
        app = LocalCampaign(output)
        try:
            receipt = {**exercise(app), "eval_set_id": gate["eval_set_id"], "release_gate": gate["gate"]}
        finally:
            app.client.close()
        (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"result": str(output / "result.json"),
                          "alert": receipt["monitor"]["alerts"][0]["code"],
                          "rollback_verified": receipt["rollback"]["prior_zip_byte_equal"]}))
    finally:
        subprocess.run([*command, "cleanup", "--directory", str(state)], check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
