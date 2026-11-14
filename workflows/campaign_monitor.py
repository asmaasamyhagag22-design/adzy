"""Report Campaign stage rates and alerts from the app's structured logs."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from workflows.campaign_telemetry import LOG_PREFIX, MODES, STAGES, TIMEOUTS

WINDOW = timedelta(minutes=5)
MIN_LATENCY_SAMPLES = 20
SIZE_ALERT_MIN_RUNS = 3


def events(lines):
    for line in lines:
        if LOG_PREFIX not in line:
            continue
        row = json.loads(line.split(LOG_PREFIX, 1)[1])
        if (row.get("schema") == "adzy.campaign-event@1" and row["stage"] in STAGES
                and row["source_mode"] in MODES):
            yield row


def rate(rows, failed):
    count = sum(failed(row) for row in rows)
    return {"events": count, "total": len(rows), "rate": count / len(rows) if rows else None}


def report(rows, *, end):
    start = end - WINDOW
    groups = defaultdict(list)
    for row in rows:
        if start < datetime.fromisoformat(row["at"]) <= end:
            groups[row["source_mode"]].append(row)
    alerts = []
    metrics = {}
    for mode, batch in sorted(groups.items()):
        fetch = [row for row in batch if row["stage"] == "campaign.fetch"]
        verification = [row for row in batch if row["stage"] == "campaign.verify"]
        timings = defaultdict(list)
        for row in batch:
            timings[row["stage"]].append(row["elapsed_ms"])
        latency = {}
        for stage, values in sorted(timings.items()):
            values.sort()
            latency[stage] = {"n": len(values), "p95_ms": (
                values[ceil(.95 * len(values)) - 1] if len(values) >= MIN_LATENCY_SAMPLES else None
            )}
        metrics[mode] = {
            "fetch_failure": rate(fetch, lambda row: row["status"] == "ERROR"),
            "fetch_timeout": rate(fetch, lambda row: row["reason"] in TIMEOUTS),
            "verification_rejection": rate(verification, lambda row: row["reason"] in {
                "COPY_REQUIRES_EVIDENCE_REVIEW", "CLAIM_EXCEEDS_SOURCE",
            }),
            "latency": latency,
        }
        oversized = sorted({row["trace_id"] for row in fetch if row["reason"] == "PAGE_TOO_LARGE"})
        if len(oversized) >= SIZE_ALERT_MIN_RUNS:
            alerts.append({"code": "CAMPAIGN_REPEATED_PAGE_TOO_LARGE", "source_mode": mode,
                           "threshold": SIZE_ALERT_MIN_RUNS, "runs": len(oversized), "trace_ids": oversized})
    return {
        "schema": "adzy.campaign-monitor@1", "start": start.isoformat(), "end": end.isoformat(),
        "telemetry_state": "OBSERVED" if groups else "NO_EVENTS_IN_WINDOW",
        "minimum_latency_samples": MIN_LATENCY_SAMPLES, "metrics": metrics, "alerts": alerts,
    }


def deliver_alerts(alerts, receiver):
    target = urlsplit(receiver)
    if (target.scheme != "http" or target.hostname != "127.0.0.1" or target.username is not None
            or target.port is None or target.query or target.fragment):
        raise ValueError("alert receiver must be an explicit loopback HTTP URL")
    receipts = []
    with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
        for alert in alerts:
            identity = sha256(json.dumps(alert, sort_keys=True).encode()).hexdigest()
            response = client.post(receiver, json=alert, headers={"Idempotency-Key": identity})
            response.raise_for_status()
            receipts.append({"id": identity, "http_status": response.status_code})
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, nargs="+")
    parser.add_argument("--end", type=datetime.fromisoformat, help="ISO 8601 timestamp for a historical window")
    parser.add_argument("--local-receiver", help="Deliver alerts to a loopback HTTP receiver")
    args = parser.parse_args()
    end = args.end or datetime.now(timezone.utc)
    if end.utcoffset() is None:
        parser.error("--end must include a timezone")
    rows = []
    for path in args.log:
        with path.open(encoding="utf-8") as source:
            rows.extend(events(source))
    result = report(rows, end=end)
    if args.local_receiver:
        try:
            result["delivery"] = deliver_alerts(result["alerts"], args.local_receiver)
        except (ValueError, httpx.HTTPError) as exc:
            parser.exit(1, f"Alert delivery failed ({type(exc).__name__})\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
