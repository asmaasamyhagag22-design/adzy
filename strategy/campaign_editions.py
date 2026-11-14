"""Locally curated campaign directions, checked against freshly observed publisher fields."""
from hashlib import sha256
import json
import os
import re
import unicodedata
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit

from strategy.campaign_package import Brief, digest, proposal, public_soup, valid_source_url
from strategy.campaign_research import ResearchGap, same_source
from strategy.campaign_v2 import CampaignConflict
from strategy.campaign_release import release_metadata


def find_edition(request, intent):
    path = os.getenv("ADZY_CAMPAIGN_EDITIONS_FILE")
    if not path:
        return None
    parts = urlsplit(request.url)
    route = parts.path.rstrip("/")
    objective = "".join(char for char in unicodedata.normalize("NFKD", request.objective).casefold()
                        if not unicodedata.combining(char))
    objective_words = set(re.findall(r"[^\W_]+", objective))
    for edition in json.loads(Path(path).read_text(encoding="utf-8")):
        subjects = set(edition.get("subject_terms", []))
        if (not subjects or subjects & objective_words) and (
                valid_source_url(edition["entry"]) and same_source(request.url, edition["entry"])
                and route == urlsplit(edition["entry"]).path.rstrip("/")
                and not parts.query and intent == edition["intent"]
                and request.language == edition["language"]):
            return edition
    return None


def capture_edition_source(url, entry, spec, load, clock, trace, deadline, reason):
    visited = set()
    retry_available = True
    while url not in visited and len(trace) < 8 and monotonic() < deadline:
        if not valid_source_url(url) or not same_source(url, entry):
            break
        visited.add(url)
        step = {"page_url": url, "reason": reason, "outcome": "SOURCE_UNAVAILABLE"}
        trace.append(step)
        try:
            page = load(url, min(5.0, max(.05, deadline - monotonic())))
            if page.url != url:
                raise ResearchGap("SOURCE_URL_CHANGED")
            soup = public_soup(page.body)
            selector = {"title": "title", "description": 'meta[name="description"]',
                        "og:description": 'meta[property="og:description"]'}[spec["field"]]
            values = {
                " ".join((tag.get("content") if tag.name == "meta" else tag.get_text(" ", strip=True)).split())
                for tag in soup.select(selector) if tag.name != "meta" or tag.get("content")
            }
            titles = {" ".join(tag.get_text(" ", strip=True).split()) for tag in soup.select("title")}
            title = next(iter(titles)) if len(titles) == 1 else ""
            if spec.get("title") and titles != {spec["title"]}:
                step["observed_values"] = [title] if title else []
                raise ResearchGap("SOURCE_SUBJECT_CHANGED")
            if values != {spec["quote"]}:
                step["observed_values"] = sorted(values)[:3]
                raise ResearchGap("SOURCE_FIELD_CHANGED" if values else "SOURCE_FIELD_MISSING")
            step["outcome"] = "QUOTE_OBSERVED"
            visible_body = " ".join(soup.body.get_text(" ", strip=True).split()) if soup.body else ""
            return {
                "page_url": page.url, "capture_kind": page.source_kind, "field": spec["field"],
                "captured_at": page.captured_at or clock().isoformat(), "body_sha256": sha256(page.body).hexdigest(),
                "text_sha256": digest([spec["field"], spec["quote"], title, visible_body]),
                "quote": spec["quote"], "context": spec["quote"], "source_title": title,
                "context_extent": "CURATED_FIELD_ONLY",
                "scope": spec["scope"], "outcome": "QUOTE_OBSERVED",
            }
        except HTTPError as exc:
            step["http_status"] = exc.code
            locations = exc.headers.get_all("Location", ()) if exc.headers else ()
            exc.close()
            if exc.code in {301, 302, 303, 307, 308} and len(locations) == 1:
                step["outcome"] = "SOURCE_REDIRECT"
                try:
                    target = urljoin(url, locations[0])
                except ValueError:
                    target = ""
                if not valid_source_url(target) or not same_source(target, entry):
                    step["outcome"] = "SOURCE_REDIRECT_OUT_OF_SCOPE"
                    break
                step["redirect_to"] = target
                url, reason = target, "FOLLOW_SOURCE_REDIRECT"
                continue
        except ResearchGap as exc:
            step["outcome"] = str(exc)
        except OSError as exc:
            timeout = exc.reason if isinstance(exc, URLError) else exc
            if isinstance(timeout, TimeoutError):
                step["outcome"] = "SOURCE_TIMEOUT"
                if retry_available and deadline - monotonic() >= 1:
                    retry_available = False
                    visited.remove(url)
                    reason = "RETRY_SOURCE_TIMEOUT"
                    continue
        break
    return None


def build_edition(request, edition, load, clock, request_id, *, trace, release, snapshot_id):
    started = monotonic()
    deadline = started + 12
    steps = []
    with trace.stage("campaign.research") as outcome:
        # Entry diagnostics are independent of the publisher fields the copy depends on.
        if edition.get("observe_entry"):
            capture_edition_source(request.url, request.url, edition["observe_entry"], load, clock,
                                   steps, deadline, "BRAND_ENTRY")
        rows = [
            capture_edition_source(item["url"], request.url, item, load, clock, steps, deadline, "EDITORIAL_SOURCE")
            for item in edition["sources"]
        ]
        sources = [item for item in rows if item]
        missing = len(sources) != len(rows)
        outcome.update(status="BLOCKED" if missing else "OK",
                       reason="EDITORIAL_SOURCE_GAP" if missing else "DECISION_EVIDENCE_FOUND")
    with trace.stage("campaign.select_evidence") as outcome:
        if missing:
            outcome.update(status="BLOCKED", reason="EDITORIAL_SOURCE_GAP")
        else:
            outcome["evidence_hash"] = digest([(row["body_sha256"], row["scope"], row["quote"]) for row in sources])
    planner = {
        "policy": "editorial-campaign@1", "edition": edition["id"], "edition_sha256": digest(edition),
        "input": request.model_dump(mode="json"), "intent": edition["intent"], "question": edition["question"],
        "opportunity_hypothesis": edition["brief"]["assumption"], "trace": steps,
        "pages": sources, "stop": "EDITORIAL_SOURCE_GAP" if missing else "DECISION_EVIDENCE_FOUND",
        "gap": "EDITORIAL_SOURCE_GAP" if missing else None,
        "missing_question": edition["missing_question"] if missing else None,
        "page_calls": len(steps), "elapsed_ms": round((monotonic() - started) * 1000, 2),
        "provider_calls": 0, "cost_usd": 0, "brief_reviewed": False, "request_id": request_id,
    }
    planner.update(trace_id=trace.ref, source_mode=trace.mode, snapshot_id=snapshot_id,
                   version=release_metadata(release, planner["policy"], planner["edition_sha256"]))
    if missing:
        return proposal(None, edition["brand_name"], sources, planner=planner)
    with trace.stage("campaign.write_copy"):
        options = []
        for item, direction in zip(edition["options"], edition["brief"]["directions"], strict=True):
            refs = [index for claim in item["claims"] for index in claim["evidence"]]
            refs.extend(item["art"]["evidence"])
            refs.extend(subject["evidence_index"] for subject in item["art"]["subjects"]
                        if "evidence_index" in subject)
            refs.append(item["evidence_index"])
            if any(not isinstance(index, int) or not 0 <= index < len(rows) for index in refs):
                raise ValueError("Invalid edition evidence reference")
            destination = rows[item["evidence_index"]]["page_url"]
            options.append({
                **item, "destination": destination,
                "variants": {field: [direction[field], *values] for field, values in item.get("variants", {}).items()},
            })
        planner.update(options=options)
        source = sources[0]
        brief = Brief.model_validate({
            **edition["brief"], "objective": request.objective,
            "source": {"url": source["page_url"], "quote": source["quote"], "scope": source["scope"]},
            "summary": source["quote"], "language": request.language,
            "direction": "rtl" if request.language.startswith("ar") else "ltr",
        })
        return proposal(brief, edition["brand_name"], sources, planner=planner)


def edit_edition_copy(direction, edit, option):
    values = edit.model_dump()
    unsupported = [
        key for key, value in values.items()
        if value != getattr(direction, key) and value not in option.get("variants", {}).get(key, [])
    ]
    if unsupported:
        raise CampaignConflict("COPY_REQUIRES_EVIDENCE_REVIEW", {"fields": unsupported})
    return direction.model_copy(update={**values, "hook": edit.headline})
