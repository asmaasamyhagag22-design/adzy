"""Exercise fixed source-context scenarios at the reviewed-package boundary."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

from strategy.campaign_planner import PlanInput, build_plan, review_draft
from strategy.campaign_research import Page, ResearchGap

FIXTURES = Path(__file__).resolve().parents[1] / "docs/campaigns/transformation"


def context_cases():
    manifest = json.loads((FIXTURES / "context-manifest.json").read_bytes())
    expected = manifest["files"]
    actual = {name: sha256((FIXTURES / name).read_bytes()).hexdigest()
              for name in ("context-cases.json", "protocol.md")}
    if actual != expected:
        raise ValueError("Campaign context evaluation manifest mismatch")
    data = json.loads((FIXTURES / "context-cases.json").read_bytes())
    return manifest, data


def evaluate_context(review_input):
    manifest, data = context_cases()
    captured = datetime.fromisoformat(data["captured_at"])
    rows = []
    for case in data["cases"]:
        url = f"https://{case['id']}.example/features"

        def load(requested_url, timeout):
            if case["html"] is None or requested_url != url:
                raise ResearchGap("SOURCE_UNAVAILABLE")
            body = (f'<html><head><meta charset="utf-8"><title>Publisher catalogue</title></head>'
                    f'<body><main>{case["html"]}</main></body></html>').encode()
            return Page(url, body, "synthetic_fixture")

        request = PlanInput(url=url, objective=case["objective"], language=case["language"])
        with patch.dict("os.environ", {"ADZY_CAMPAIGN_EDITIONS_FILE": "", "ADZY_CAMPAIGN_RELEASE": "stable"}):
            draft = build_plan(request, "Publisher", load, lambda: captured)
            result = review_draft(draft, review_input(draft["brief"])) if draft["brief"] else draft
        package = result["package"]
        actual = package["factual_review"] if package else "BLOCKED"
        failures = []
        if actual != case["expected"]:
            failures.append("UNEXPECTED_PACKAGE_OUTCOME")
        if package and (result["status"] != "READY" or package["source_url"] != url):
            failures.append("WRONG_JOURNEY")
        if case.get("qualifier"):
            fact = result["evidence"][0] if result["evidence"] else {}
            anchors = fact.get("anchor", {}).get("qualifications", [])
            if not any(span["text"][span["start"]:span["end"]] == span["quote"] == case["qualifier"]
                       for span in anchors):
                failures.append("QUALIFICATION_NOT_ANCHORED")
            if package and (fact["quote"] in package["copy"].values()
                            or result["planner"].get("claims", [{}])[0].get("fields")):
                failures.append("UNQUALIFIED_CLAIM")
        rows.append({"id": case["id"], "expected": case["expected"], "observed": actual,
                     "journey": result["status"], "gap": result["planner"]["gap"],
                     "package": package, "evidence": result["evidence"], "failures": failures})
    return {"manifest": manifest, "results": rows,
            "blockers": [{"case": row["id"], "failures": row["failures"]} for row in rows if row["failures"]]}
