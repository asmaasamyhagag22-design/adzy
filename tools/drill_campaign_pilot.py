"""Measure the local Campaign profile and restore its state in fresh PostgreSQL containers."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from math import ceil
import os
from pathlib import Path
from secrets import token_hex
import socket
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[1]
PG_IMAGE = "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
DB = "adzy_campaign_pilot"
EMAIL = "operator@adzy.invalid"
SAMPLES = 20
FAULT_SERVER = """
import sys
from pathlib import Path
from workflows.campaign_pilot import configure, create_app
state = Path(sys.argv[1])
configure(state, offline=True)
import uvicorn
from api.routes.v2_campaigns import get_package_loader
from workflows.campaign_eval import case_set, case_source
app = create_app(state)
case = next(case for case in case_set()[0] if case['id'] == 'nandos-page-cap')
with case_source(case) as (loader, _, _):
    app.dependency_overrides[get_package_loader] = lambda: loader
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), access_log=False)
"""


def docker(*args, **kwargs):
    return subprocess.run(("docker", *args), check=True, **kwargs)


@contextmanager
def postgres(password):
    name = "adzy-campaign-drill-" + uuid4().hex[:12]
    docker("run", "--rm", "--detach", "--pull=never", "--name", name,
           "--label", "adzy.campaign-drill=true", "--publish", "127.0.0.1::5432",
           "--env", f"POSTGRES_DB={DB}", "--env", "POSTGRES_PASSWORD", PG_IMAGE,
           env={**os.environ, "POSTGRES_PASSWORD": password}, stdout=subprocess.DEVNULL)
    try:
        for _ in range(60):
            ready = subprocess.run(("docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres",
                                    "-d", DB), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            sleep(.25)
        else:
            raise RuntimeError("Disposable PostgreSQL did not start")
        port = docker("port", name, "5432", capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1]
        yield name, f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/{DB}"
    finally:
        docker("stop", name, stdout=subprocess.DEVNULL)


def migrate(checkout, env):
    subprocess.run((sys.executable, "-m", "alembic", "upgrade", "head"), cwd=checkout, env=env, check=True)


@contextmanager
def serving(checkout, env, state, logs, *, previous=False, expect_start=True, inject_fault=False):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if previous:
        command = [sys.executable, "-c", (
            "from workflows.demo import isolate; isolate(); from uvicorn import run; "
            f"run('api.main:app', host='127.0.0.1', port={port}, access_log=False)"
        )]
    elif inject_fault:
        command = [sys.executable, "-c", FAULT_SERVER, str(state), str(port)]
    else:
        command = [sys.executable, "-m", "workflows.campaign_pilot", "serve", "--state", str(state),
                   "--port", str(port), "--snapshot", str(checkout / "docs/demo/campaign-snapshots/buffalo")]
    with logs.open("w", encoding="utf-8") as stream, httpx.Client(
        base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False,
    ) as client:
        process = subprocess.Popen(command, cwd=checkout, env=env, stdout=stream, stderr=subprocess.STDOUT)
        try:
            deadline = monotonic() + 30
            while process.poll() is None and monotonic() < deadline:
                try:
                    endpoint = "/api/health" if previous else "/api/ready"
                    if client.get(endpoint, timeout=1).is_success:
                        break
                except httpx.TransportError:
                    pass
                sleep(.1)
            else:
                if expect_start or process.poll() in (None, 0):
                    raise RuntimeError(f"Campaign startup did not meet expectation. See {logs.name}")
                assert "this build expects" in logs.read_text(encoding="utf-8")
                yield None, process.pid
                return
            assert expect_start, "Application became ready against an incompatible schema"
            yield client, process.pid
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def dump(container, destination):
    with destination.open("xb") as archive:
        destination.chmod(0o600)
        docker("exec", container, "pg_dump", "--username=postgres", "--format=custom", DB, stdout=archive)


def restore(container, archive):
    with archive.open("rb") as source:
        docker("exec", "-i", container, "pg_restore", "--username=postgres", "--exit-on-error",
               "--single-transaction", f"--dbname={DB}", stdin=source)


def authenticate(client, password):
    session = client.post("/api/auth/login", json={"email": EMAIL, "password": password}).raise_for_status().json()
    client.headers["Authorization"] = "Bearer " + session["access_token"]


def timing(samples):
    ordered = sorted(samples)
    return {"n": len(ordered), **{f"p{quantile}_ms": round(ordered[ceil(quantile * len(ordered) / 100) - 1] * 1000, 3)
                                  for quantile in (50, 95, 100)}}


def exercise(client, pid):
    base = "/api/v2/campaigns"
    case = json.loads((ROOT / "docs/campaigns/buffalo/portfolio/case.json").read_text())["buffalo"]
    inputs = {key: case[key] for key in ("url", "objective", "language")}
    assert client.get("/api/health", headers={"host": "other.invalid"}).status_code == 400
    assert client.post("/api/auth/signup", json={}).status_code == 404
    assert client.post("/api/generate", json={}).status_code == 404

    def oversized():
        yield b"a" * 16384
        yield b"b" * 16384
        yield b"c"

    assert client.post("/api/auth/login", content=oversized()).status_code == 413
    assert client.post(base + "/plan", json=inputs, headers={"Authorization": ""}).status_code == 401
    run_id = None
    planning = []
    for _ in range(SAMPLES):
        started = monotonic()
        view = client.post(base + "/plan", json={**inputs, "expected_run_id": run_id,
                                                 "request_id": str(uuid4())}).raise_for_status().json()
        planning.append(monotonic() - started)
        run_id = view["run_id"]
    assert view["proposal"]["planner"]["source_mode"] == "publisher_replay"
    sample = client.get(base + "/planning-state", params={"url": case["url"]}).raise_for_status().json()
    assert sample["run_id"] == run_id

    def burst(_):
        return client.post(base + "/plan", json={**inputs, "expected_run_id": run_id,
                                                  "request_id": str(uuid4())}).status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = Counter(executor.map(burst, range(8)))
    assert statuses[200] == 1 and set(statuses) <= {200, 409, 503}, statuses
    path = base + "/" + view["domain_key"]
    latest = client.get(path).raise_for_status().json()
    brief = latest["proposal"]["brief"]
    request = {key: brief[key] for key in ("audience", "tone", "selected")}
    request["run_id"] = latest["run_id"]
    reviewed = client.post(path + "/brief", json=request).raise_for_status().json()
    run_id = reviewed["run_id"]
    hashes = {}
    for size in ("portrait", "square"):
        preview = client.get(path + "/package/preview", params={"run_id": run_id, "size": size}).raise_for_status()
        hashes[size] = sha256(preview.content).hexdigest()
        assert preview.headers["X-Poster-SHA256"] == hashes[size]
    boundary = {"run_id": run_id, "decision": "approved"}
    assert client.post(path + "/review", json=boundary).status_code == 409
    approved = client.post(path + "/review", json={**boundary, "artifact_sha256": hashes}).raise_for_status().json()
    assert approved["approved"]
    route = path + f"/package/export?run_id={run_id}"
    downloads = []
    for _ in range(SAMPLES):
        started = monotonic()
        archive = client.get(route).raise_for_status().content
        downloads.append(monotonic() - started)
        assert sha256(archive).hexdigest() == approved["approved_artifacts"]["archive_sha256"]
    process = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    memory = {line.split(":")[0]: int(line.split()[1]) for line in process.splitlines()
              if line.startswith(("VmRSS:", "VmHWM:"))}
    planning_stats, download_stats = timing(planning), timing(downloads)
    assert planning_stats["p95_ms"] <= 2000 and download_stats["p95_ms"] <= 1000
    return route, archive, {"snapshot_planning": planning_stats, "approved_export": download_stats,
                            "burst_status_counts": dict(statuses), "server_memory_kib": memory,
                            "approved_zip_sha256": sha256(archive).hexdigest(), "approved_zip_bytes": len(archive)}


def exercise_alert(output, state, env, password):
    # Substitute only the publisher transport, then report from the server's emitted events.
    cases = json.loads((ROOT / "docs/campaigns/lifecycle/cases.json").read_text())["cases"]
    inputs = next(case["input"] for case in cases if case["id"] == "nandos-page-cap")
    log = output / "simulated-source-failure.log"
    with serving(ROOT, env, state, log, inject_fault=True) as (client, _):
        authenticate(client, password)
        run_id = None
        for _ in range(3):
            view = client.post("/api/v2/campaigns/plan", json={
                **inputs, "expected_run_id": run_id, "request_id": str(uuid4()),
            }).raise_for_status().json()
            assert view["proposal"]["planner"]["trace"][0]["outcome"] == "PAGE_TOO_LARGE"
            run_id = view["run_id"]

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.headers["Idempotency-Key"], json.loads(body)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", 0), Receiver) as receiver:
        thread = Thread(target=receiver.serve_forever)
        thread.start()
        try:
            receiver_url = f"http://127.0.0.1:{receiver.server_port}/alerts"
            invoked = subprocess.run((sys.executable, "-m", "workflows.campaign_monitor", str(log),
                                      "--local-receiver", receiver_url), cwd=ROOT, check=True, capture_output=True)
            monitor = json.loads(invoked.stdout)
        finally:
            receiver.shutdown()
            thread.join()
    assert len(received) == 1 and monitor["delivery"][0] == {"id": received[0][0], "http_status": 204}
    assert received[0][1]["code"] == "CAMPAIGN_REPEATED_PAGE_TOO_LARGE"
    assert received[0][1]["runs"] == 3
    return {"source_mode": "simulated_transport", "incoming_distinct_failures": 3,
            "alert_code": received[0][1]["code"], "receiver_http": 204}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous-checkout", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    previous = args.previous_checkout.resolve() if args.previous_checkout else None
    password = token_hex(32)
    settings = {**os.environ, "BASEERA_AUTH_SECRET": token_hex(32), "PERSISTENCE_BACKEND": "postgres",
                "BASEERA_NO_SPEND": "1", "ADZY_LANGFUSE_ENABLED": "0", "LANGSMITH_TRACING": "false",
                "ADZY_CAMPAIGN_RELEASE": "stable",
                "ADZY_CAMPAIGN_EDITIONS_FILE": str(ROOT / "docs/campaigns/buffalo/portfolio/edition.json")}
    original = output / "pre-upgrade.dump"
    verified = output / "approved.dump"
    report = {"profile": "single-worker loopback", "postgres_image": PG_IMAGE}
    with postgres(password) as (container, dsn):
        state = output / "initial-state"
        env = {**settings, "BASEERA_DATABASE_URL": dsn, "BASEERA_WEBAPP_DIR": str(state)}
        migrate(previous or ROOT, env)
        seed = "from api.persistence import get_persistence; import sys; get_persistence().create_user(email=sys.argv[1], password=sys.stdin.readline().strip())"
        subprocess.run((sys.executable, "-c", seed, EMAIL), input=password + "\n", text=True,
                       cwd=previous or ROOT, env=env, check=True)
        if previous:
            with serving(previous, env, state, output / "previous.log", previous=True) as (client, _):
                authenticate(client, password)
            dump(container, original)
            with serving(ROOT, env, state, output / "wrong-schema-candidate.log", expect_start=False):
                pass
            migrate(ROOT, env)
            with serving(previous, env, state, output / "wrong-schema-old.log", previous=True, expect_start=False):
                pass
            report["upgrade"] = "candidate_migration_succeeded_and_both_crossed_schema_pairs_refused_startup"
        with serving(ROOT, env, state, output / "candidate.log") as (client, pid):
            authenticate(client, password)
            route, archive, metrics = exercise(client, pid)
        dump(container, verified)
        report.update(metrics)
        report["alert"] = exercise_alert(output, state, env, password)
    with postgres(password) as (container, dsn):
        state = output / "restored-state"
        env = {**settings, "BASEERA_DATABASE_URL": dsn, "BASEERA_WEBAPP_DIR": str(state)}
        restore(container, verified)
        started = monotonic()
        with serving(ROOT, env, state, output / "restored.log") as (client, _):
            authenticate(client, password)
            assert client.get(route).raise_for_status().content == archive
            assert not (state / "campaign-posters").exists()
        report["restore"] = {"exact_zip": True, "draft_cache_recovered": False,
                             "restart_and_read_seconds": round(monotonic() - started, 3)}
    if previous:
        with postgres(password) as (container, dsn):
            state = output / "rollback-state"
            env = {**settings, "BASEERA_DATABASE_URL": dsn, "BASEERA_WEBAPP_DIR": str(state)}
            restore(container, original)
            with serving(previous, env, state, output / "rollback.log", previous=True) as (client, _):
                authenticate(client, password)
                assert client.get("/api/brands").raise_for_status().json()["brands"] == []
            report["rollback"] = "prior_binary_and_preupgrade_database_restored_in_new_container"
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
