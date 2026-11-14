"""Run the frozen Campaign release gate. Live mode produces a diagnostic only."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import Message
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from urllib.error import URLError
from uuid import uuid4

from workflows import demo

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs/campaigns/lifecycle"
EDITING_FIXTURES = ROOT / "docs/campaigns/editing"
COPY_FIELDS = ("headline", "message", "cta", "caption")


def case_set():
    from strategy.campaign_package import digest

    data = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    study = json.loads((EDITING_FIXTURES / "cases.json").read_text(encoding="utf-8"))
    cases = []
    for case in data["cases"]:
        extension = study["existing_cta_cases"].get(case["id"], {})
        cases.append({**case, **{key: value for key, value in extension.items() if key != "edits"},
                      "edits": case.get("edits", []) + extension.get("edits", [])})
    cases.extend(study["cases"])
    bindings = []
    for case in cases:
        manifest = (ROOT / case["snapshot"] / "manifest.json").read_bytes()
        binding = {"snapshot_id": sha256(manifest).hexdigest()}
        if case.get("editions"):
            binding["edition_file_id"] = sha256((ROOT / case["editions"]).read_bytes()).hexdigest()
        bindings.append(binding)
    return cases, bindings, digest({
        "labels": data, "editing_labels": study,
        "editing_protocol": sha256((EDITING_FIXTURES / "PROTOCOL.md").read_bytes()).hexdigest(),
        "snapshots": bindings,
        "protocol": sha256((FIXTURES / "PROTOCOL.md").read_bytes()).hexdigest(),
    })


@contextmanager
def case_source(case, *, live=False):
    from strategy.campaign_package import load_package_page
    from workflows.campaign_snapshot import open_snapshot

    if live:
        yield load_package_page, lambda: datetime.now(timezone.utc), "public_http"
        return
    load, clock = open_snapshot(ROOT / case["snapshot"])
    fault = case.get("simulated_transport")
    if fault is None:
        yield load, clock, "publisher_replay"
        return

    def open_response(url, **kwargs):
        if url != case["input"]["url"]:
            raise AssertionError("Unexpected URL in simulated transport")
        if fault == "socket_timeout":
            raise URLError(TimeoutError("simulated socket timeout"))
        if fault != "oversize_html":
            raise ValueError("Unknown simulated transport")
        reply = BytesIO(b"<html>" + b" " * 2_000_000 + b"</html>")
        reply.status = 200
        reply.headers = Message()
        reply.length = None
        reply.headers["Content-Type"] = "text/html"
        return reply

    def simulated(url, timeout):
        return load_package_page(url, timeout)

    simulated.campaign_source_mode = "simulated_transport"
    simulated.campaign_snapshot_id = load.campaign_snapshot_id
    with patch("strategy.campaign_research.open_campaign_url", open_response):
        yield simulated, clock, "simulated_transport"


def review_input(brief, edit=None):
    from strategy.campaign_planner import BriefReview

    selected = (edit or {}).get("selected", brief["selected"])
    copy = None
    if edit:
        copy = {key: brief["directions"][selected][key] for key in COPY_FIELDS}
        copy[edit["field"]] = edit["text"]
    return BriefReview(audience=brief["audience"], tone=brief["tone"], selected=selected, copy_edit=copy)


def measure(case, draft, *, trace):
    from strategy.campaign_package import digest
    from strategy.campaign_planner import review_draft
    from strategy.campaign_v2 import CampaignConflict

    expected = case["expected"]
    planner = draft["planner"]
    evidence = draft["evidence"]
    fetch = [step["outcome"] for step in planner["trace"]]
    if "fetch" in expected:
        expected_stop = (draft["status"] == "BLOCKED" and not evidence and draft["brief"] is None
                         and planner["gap"] == expected["gap"] and fetch == [expected["fetch"]])
    else:
        expected_stop = draft["status"] == "DRAFT" and planner["gap"] is None
    evidence_match = None
    if evidence and "subject" in expected:
        evidence_match = evidence[0]["scope"] == expected["subject"] and evidence[0]["quote"] == expected["quote"]
    if "proof_urls" in expected:
        evidence_match = ([item["page_url"] for item in evidence] == expected["proof_urls"]
                          and all(item["outcome"] == expected["evidence_source"] for item in evidence))

    review = None
    cta_match = None
    cta_copy_match = None
    writing_context_hash = None
    edits = []
    if draft["brief"]:
        brief = draft["brief"]
        review = review_draft(draft, review_input(brief), trace=trace)
        package = review["package"]
        choice = planner["options"][brief["selected"]]
        cta_match = (package["source_url"] == choice["destination"] == expected.get("destination")
                     and package["source_url"] == evidence[package["source_evidence_index"]]["page_url"]
                     and package["copy"]["cta"] == choice["cta"] and choice["action"] == expected.get("action"))
        if "cta" in case:
            release = planner["version"]["release"]
            expected_cta = case["cta"][release]
            actual_cta = package["copy"]["cta"]
            cta_copy_match = actual_cta == expected_cta and package["caption"].endswith(f"{actual_cta}.")
        # Only the selected CTA and its caption suffix are the treatment.
        index = brief["selected"]
        directions = list(brief["directions"])
        options = list(planner["options"])
        if "cta" in case:
            direction = directions[index]
            suffix = f"{direction['cta']}."
            directions[index] = {**direction, "cta": None, "caption": direction["caption"].removesuffix(suffix)}
            options[index] = {**options[index], "cta": None}
        writing_context_hash = digest({"brief": {**brief, "directions": directions}, "options": options})
        for edit in case.get("edits", []):
            row = {key: edit[key] for key in ("id", "field", "supported")}
            row["critical"] = edit.get("critical", False)
            try:
                updated = review_draft(draft, review_input(brief, edit), trace=trace)
            except CampaignConflict as exc:
                row.update(accepted=False, reason=str(exc))
            else:
                value = (updated["package"]["caption"] if edit["field"] == "caption"
                         else updated["package"]["copy"][edit["field"]])
                row.update(accepted=value == edit["text"] and updated["status"] == "READY",
                           observed=value, reason=None)
                if not row["accepted"]:
                    row["reason"] = "EDIT_OUTPUT_MISMATCH"
            if row["accepted"] and not row["supported"]:
                row["classification"] = "WRITING_EXCEEDS_EVIDENCE"
            elif not row["accepted"] and row["supported"]:
                row["classification"] = "SUPPORTED_COPY_REJECTED"
            else:
                row["classification"] = "EXPECTED"
            edits.append(row)

    failures = []
    source_failed = not any(step in {"SOURCE_READ", "QUOTE_OBSERVED"} for step in fetch)
    if not expected_stop:
        failures.append("SOURCE_FETCH_FAILURE" if source_failed else "UNEXPECTED_STOP")
    if evidence_match is False:
        failures.append("WRONG_EVIDENCE_SELECTION")
    if "destination" in expected and review and (cta_match is not True or review["status"] != "READY"):
        failures.append("CTA_OR_JOURNEY_MISMATCH")
    if cta_copy_match is False:
        failures.append("CTA_COPY_MISMATCH")
    if any(row["classification"] == "WRITING_EXCEEDS_EVIDENCE" or row["reason"] == "EDIT_OUTPUT_MISMATCH"
           or (not row["supported"] and row["reason"] != "COPY_REQUIRES_EVIDENCE_REVIEW") for row in edits):
        failures.append("UNSAFE_COPY_OR_GUARD_FAILURE")
    return {
        "fetch": fetch, "source_failed": source_failed,
        "stop": planner["stop"], "gap": planner["gap"], "evidence_match": evidence_match,
        "evidence": [{key: item[key] for key in ("scope", "quote", "page_url", "capture_kind", "body_sha256")}
                     for item in evidence],
        "cta_match": cta_match, "cta_copy_match": cta_copy_match,
        "writing_context_sha256": writing_context_hash,
        "journey": review["status"] if review else draft["status"],
        "cta": {
            "url": review["package"]["source_url"], "evidence_index": review["package"]["source_evidence_index"],
            "text": review["package"]["copy"]["cta"], "action": choice["action"],
        } if review else None,
        "edits": edits, "failures": failures, "page_calls": planner["page_calls"],
        "provider_calls": planner["provider_calls"], "cost_usd": planner["cost_usd"],
    }


def run_case(case, binding, dataset_id, release, telemetry, *, live=False):
    from urllib.parse import urlsplit

    from strategy.campaign_planner import PlanInput, build_plan
    from workflows.campaign_telemetry import CampaignTrace

    case_env = {"ADZY_CAMPAIGN_RELEASE": release, "ADZY_CAMPAIGN_EDITIONS_FILE": ""}
    if case.get("editions"):
        case_env["ADZY_CAMPAIGN_EDITIONS_FILE"] = str(ROOT / case["editions"])
    telemetry.correlate(uuid4().hex)
    started = monotonic()
    with patch.dict(os.environ, case_env), case_source(case, live=live) as (load, clock, mode):
        trace = CampaignTrace(telemetry, mode=mode)
        with trace.stage("campaign.eval") as outcome:
            request = PlanInput.model_validate(case["input"])
            draft = build_plan(request, urlsplit(request.url).hostname, load, clock, trace=trace)
            observed = measure(case, draft, trace=trace)
            failures = observed["failures"]
            if failures:
                reason = failures[0]
                if reason == "UNSAFE_COPY_OR_GUARD_FAILURE" and any(
                    edit["classification"] == "WRITING_EXCEEDS_EVIDENCE" for edit in observed["edits"]
                ):
                    reason = "WRITING_EXCEEDS_EVIDENCE"
                outcome.update(status="ERROR", reason=reason)
    return {
        "case_id": case["id"], "split": case["split"], "eval_set_id": dataset_id,
        "fixtures": binding if not live else None, "source_mode": mode,
        "simulated_transport": case.get("simulated_transport") if not live else None,
        "version": draft["planner"]["version"], "trace_id": trace.ref,
        "elapsed_ms": round((monotonic() - started) * 1000, 3), **observed,
    }


def release_decision(stable, candidate):
    blockers = []
    restored = []
    for left, right in zip(stable, candidate, strict=True):
        case_id = right["case_id"]
        assert left["case_id"] == case_id and left["fixtures"] == right["fixtures"]
        for row in (left, right):
            if row["failures"]:
                blockers.append({"case": case_id, "release": row["version"]["release"], "failures": row["failures"]})
            if row["provider_calls"] != 0 or row["cost_usd"] != 0:
                blockers.append({"case": case_id, "failures": ["PROVIDER_CALL"]})
        left_action, right_action = left["cta"], right["cta"]
        if left["cta_copy_match"] is True and right["cta_copy_match"] is True:
            left_action, right_action = (
                {key: value for key, value in action.items() if key != "text"}
                for action in (left_action, right_action)
            )
        if left_action != right_action or any(left[key] != right[key] for key in (
            "fetch", "evidence", "page_calls", "journey", "writing_context_sha256",
        )):
            blockers.append({"case": case_id, "failures": ["CHANGE_OUTSIDE_REVIEW_POLICY"]})
        before = {row["id"] for row in left["edits"] if row["classification"] == "SUPPORTED_COPY_REJECTED"}
        after = {row["id"] for row in right["edits"] if row["classification"] == "SUPPORTED_COPY_REJECTED"}
        if after - before:
            blockers.append({"case": case_id, "failures": ["NEW_SUPPORTED_COPY_REJECTION"]})
        restored.extend({"case": case_id, "edit": edit, "split": right["split"]} for edit in sorted(before - after))
    if not any(item["case"] == "osm-local-knowledge" and item["edit"] == "verbatim-heading" for item in restored):
        blockers.append({"failures": ["NO_HEADING_REVIEW_IMPROVEMENT"]})
    if not any(item["split"] == "generalization" and item["edit"] == "specific-cta" for item in restored):
        blockers.append({"failures": ["NO_GENERALIZATION_IMPROVEMENT"]})
    return {"decision": "KEEP_STABLE" if blockers else "ELIGIBLE_FOR_LOCAL_RELEASE",
            "blockers": blockers, "restored_edits": restored, "default_release": "stable"}


def span_receipt(exporter):
    return [{
        "name": span.name, "trace_id": f"{span.context.trace_id:032x}",
        "span_id": f"{span.context.span_id:016x}", "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
        "elapsed_ms": round((span.end_time - span.start_time) / 1_000_000, 3),
        "attributes": dict(span.attributes),
    } for span in exporter.get_finished_spans()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Public-source diagnostics, no release decision")
    parser.add_argument("--case", action="append", help="Live diagnostics only")
    args = parser.parse_args()
    if args.case and not args.live:
        parser.error("The release gate must run the complete replay set")

    demo.isolate(offline=not args.live)
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from strategy.campaign_release import code_commit
    from workflows.telemetry import Telemetry, create_client

    if code_commit() is None:
        parser.error("A Git checkout or ADZY_CAMPAIGN_BUILD_COMMIT is required")
    recorded_at = datetime.now(timezone.utc).isoformat()
    cases, bindings, dataset_id = case_set()
    if args.case and set(args.case) - {case["id"] for case in cases if not case.get("simulated_transport")}:
        parser.error("Select known publisher cases for live diagnostics")
    exporter = InMemorySpanExporter()
    client = create_client(public_key="pk-local-campaign-eval", secret_key="sk-local-campaign-eval",
                           base_url="http://127.0.0.1:9", span_exporter=exporter)
    telemetry = Telemetry(client)
    results = {release: [
        run_case(case, binding, dataset_id, release, telemetry, live=args.live)
        for case, binding in zip(cases, bindings, strict=True)
        if (not args.live or not case.get("simulated_transport")) and (not args.case or case["id"] in args.case)
    ] for release in (("stable",) if args.live else ("stable", "candidate"))}
    gate = None if args.live else release_decision(results["stable"], results["candidate"])
    context = None
    if gate is not None:
        from workflows.campaign_context_eval import evaluate_context
        context = evaluate_context(review_input)
        if context["blockers"]:
            gate["blockers"].extend(context["blockers"])
            gate["decision"] = "KEEP_STABLE"
    telemetry.flush()
    receipt = {"schema": "adzy.campaign-eval-result@1", "eval_set_id": dataset_id,
               "started_at": recorded_at, "mode": "public_http" if args.live else "offline_replay",
               "results": results, "context_regression": context, "gate": gate,
               "langfuse_spans": span_receipt(exporter)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gate:
        print(json.dumps(gate, ensure_ascii=False))
    return int(bool(gate and gate["blockers"]))


if __name__ == "__main__":
    raise SystemExit(main())
