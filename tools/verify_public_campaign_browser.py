"""Verify public sources, review export, direction revision and restore from Chromium."""
import argparse
from base64 import b64decode
from hashlib import sha256
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
from time import monotonic, sleep
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def unpack(download, destination, state):
    from PIL import Image

    archive_bytes = download.path().read_bytes()
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        report = json.loads(archive.read("campaign.json"))
        assert report["run_id"] == state["run_id"] and report["approval_id"] == state["approval_id"]
        assert report["proposal"]["sha256"] == state["proposal"]["sha256"]
        bound = state["approved_artifacts"]
        assert bound["archive_sha256"] == sha256(archive_bytes).hexdigest()
        assert report["approved_artifacts"] == {key: value for key, value in bound.items() if key != "archive_sha256"}
        for label, height in (("portrait", 1350), ("square", 1080)):
            name = f"poster-{label}.png"
            image = archive.read(name)
            assert Image.open(io.BytesIO(image)).size == (1080, height)
            assert sha256(image).hexdigest() == report["poster_measurements"][label]["sha256"]
            assert sha256(image).hexdigest() == report["approved_artifacts"]["poster_sha256"][label]
            renderer = report["poster_measurements"][label]["renderer"]
            assert renderer["chromium"] and len(renderer["html_sha256"]) == 64
            (destination / name).write_bytes(image)
    (destination / "campaign.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    (destination / "campaign.zip").write_bytes(archive_bytes)
    return {label: item["sha256"] for label, item in report["poster_measurements"].items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("cases", nargs="*", type=Path)
    parser.add_argument("--planner-cases", type=Path)
    parser.add_argument("--keep-demo", action="store_true", help="Keep the disposable database after verification")
    parser.add_argument("--port", type=int)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--check-destination", action="store_true", help="Follow the campaign CTA in Chromium")
    parser.add_argument("--case-name", action="append", default=[])
    args = parser.parse_args()
    if bool(args.cases) == bool(args.planner_cases):
        parser.error("Supply case directories or --planner-cases")
    directory, evidence = args.directory.resolve(), args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "workflows.public_campaign_demo"]
    subprocess.run([*command, "init", "--directory", str(directory),
                    *(item for case in args.cases for item in ("--case", str(case.resolve())))],
                   cwd=ROOT, check=True)
    manifest = json.loads((directory / "demo.json").read_text())
    plans = json.loads(args.planner_cases.read_text()) if args.planner_cases else None
    cases = plans if plans is not None else {case.name: case for case in args.cases}
    if args.case_name:
        cases = {name: cases[name] for name in args.case_name}
    port = args.port
    if port is None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = None
    errors = []

    def stop():
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=25)

    def restart(log):
        nonlocal process
        stop()
        snapshot_args = ["--snapshot", str(args.snapshot.resolve())] if args.snapshot else []
        process = subprocess.Popen([*command, "serve", "--directory", str(directory), "--port", str(port), *snapshot_args],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        deadline = monotonic() + 40
        while monotonic() < deadline and process.poll() is None:
            try:
                with urlopen(origin + "/app", timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                sleep(.2)
        raise RuntimeError("Campaign server unavailable")

    try:
        from playwright.sync_api import expect, sync_playwright
        with (evidence / "server.txt").open("w") as log, sync_playwright() as playwright:
            started = monotonic()
            restart(log)
            results = {}
            with playwright.chromium.launch() as browser:
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(
                    (origin + "/", "data:", "blob:")) else route.abort())
                page.goto(origin + "/app")
                page.fill("#auth-email", manifest["email"])
                page.fill("#auth-password", "LocalCampaignReview42")
                page.click("#auth-submit-btn")
                if plans is None:
                    page.wait_for_function("() => window.Adzy && Adzy.brand")
                for name, case in cases.items():
                    before = monotonic()
                    target = evidence / name
                    target.mkdir(exist_ok=True)
                    rejected_copy = None
                    unregistered_copy = None
                    visited_destination = None
                    accepted_copy_fields = []
                    stale_copy_packet_rejected = False
                    if plans is None:
                        domain = manifest["brands"][name]
                        page.locator("#adzy-brand-select").select_option(domain)
                    page.get_by_role("button", name="Campaign", exact=True).click()
                    if plans is not None:
                        form = page.locator("#v2-plan-form")
                        form.locator("button").wait_for(state="visible")
                        expect(form.locator("button")).to_be_enabled()
                        if page.evaluate("() => Adzy.loadingBrand"):
                            page.wait_for_function("() => !Adzy.loadingBrand")
                        # Await Campaign's GET before filling values that open() hydrates.
                        page.evaluate("() => window.adzyNavigate('campaign')")
                        form.locator('[name="url"]').fill(case["url"])
                        form.locator('[name="objective"]').fill(case["objective"])
                        form.locator('[name="language"]').select_option(case["language"])
                        with page.expect_request(lambda r: r.url.endswith("/campaigns/plan")) as posted, \
                                page.expect_response(lambda r: r.url.endswith("/campaigns/plan")) as response:
                            form.locator('button').click()
                        assert response.value.ok, response.value.text()
                        sent = posted.value.post_data_json
                        assert all(sent[key] == case[key] for key in ("url", "objective", "language")), sent
                        generated = response.value.json()
                        domain = generated["domain_key"]
                        (target / "draft.json").write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n")
                        if not generated["proposal"]["brief"]:
                            expect(page.locator("#v2-plan-gap")).to_be_visible()
                            page.locator("#v2-result").screenshot(path=str(target / "blocked.png"))
                            results[name] = {"status": "BLOCKED", "reason": generated["proposal"]["planner"]["gap"],
                                             "public_pages": generated["proposal"]["planner"]["page_calls"],
                                             "seconds": round(monotonic() - before, 2)}
                            continue
                        if case.get("destination"):
                            assert generated["proposal"]["planner"]["options"][0]["destination"] == case["destination"]
                        editor = page.locator(f'#v2-brief-form[data-run="{generated["run_id"]}"]')
                        expect(editor).to_be_visible()
                        expect(editor.locator('button[type="submit"]')).to_be_enabled()
                        assert generated["reason"] == "BRIEF_REVIEW_REQUIRED"
                        editor.locator('[name="audience"]').fill(
                            "فرضية جمهور بعد مراجعة المسودة." if case["language"] == "ar"
                            else "Audience hypothesis after reviewing the draft.",
                        )
                        if case.get("tone"):
                            editor.locator('[name="tone"]').fill(case["tone"])
                        if case.get("copy_edit"):
                            editor.locator("#v2-copy-details summary").click()
                            if case.get("copy_receipt"):
                                from tools.experiment_campaign_copy import copy_packet

                                with page.expect_download() as draft_download:
                                    editor.locator("#v2-copy-draft").click()
                                exported_draft = target / "exported-draft.json"
                                draft_download.value.save_as(exported_draft)
                                assert json.loads(exported_draft.read_text())["proposal"] == generated["proposal"]
                                packet = copy_packet(exported_draft, ROOT / case["copy_receipt"])
                                stale_packet = {**packet, "run_id": "old-version"}
                                upload = editor.locator("#v2-copy-packet")

                                def upload_packet(payload):
                                    upload.set_input_files({"name": "suggestions.json", "mimeType": "application/json",
                                                            "buffer": json.dumps(payload).encode()})

                                upload_packet(stale_packet)
                                expect(editor.locator("#v2-copy-packet-status")).to_contain_text("تخص نسخة تانية")
                                stale_copy_packet_rejected = True
                            if case.get("rejected_headline"):
                                headline = editor.locator('[name="headline"]')
                                headline.fill(case["rejected_headline"])
                                with page.expect_request(lambda r: r.url.endswith("/brief")) as unsafe_request, \
                                        page.expect_response(lambda r: r.url.endswith("/brief")) as rejected:
                                    editor.locator('button[type="submit"]').click()
                                assert unsafe_request.value.post_data_json["copy_edit"]["headline"] == case["rejected_headline"]
                                rejected_copy = rejected.value.status
                                assert rejected_copy == 409, rejected_copy
                                assert "COPY_REQUIRES_EVIDENCE_REVIEW" in rejected.value.text()
                                expect(page.locator("#v2-status")).to_contain_text("الصياغة تحتاج مراجعة دليل")
                                expect(headline).to_have_value(case["rejected_headline"])
                                if generated["proposal"]["planner"].get("edition"):
                                    assert rejected.value.json()["detail"]["fields"] == ["headline"]
                                    expect(headline).to_have_attribute("aria-invalid", "true")
                                    for field in ("message", "cta", "caption"):
                                        expect(editor.locator(f'[name="{field}"]')).not_to_have_attribute("aria-invalid", "true")
                                current = page.evaluate("domain => window.api('GET', '/api/v2/campaigns/' + domain)", domain)
                                assert current["run_id"] == generated["run_id"] and not current["approved"]
                                (target / "rejected.json").write_text(json.dumps(rejected.value.json(), ensure_ascii=False, indent=2) + "\n")
                                editor.locator("#v2-copy-details").screenshot(path=str(target / "rejected.png"))
                                headline.fill(generated["proposal"]["brief"]["directions"][0]["headline"])
                            if case.get("unregistered_copy"):
                                message = editor.locator('[name="message"]')
                                message.fill(case["unregistered_copy"])
                                with page.expect_response(lambda r: r.url.endswith("/brief")) as response:
                                    editor.locator('button[type="submit"]').click()
                                unregistered_copy = response.value.json()
                                assert response.value.status == 409
                                assert unregistered_copy["detail"]["fields"] == ["message"]
                                expect(message).to_have_attribute("aria-invalid", "true")
                                expect(message).to_have_value(case["unregistered_copy"])
                                (target / "unregistered.json").write_text(
                                    json.dumps(unregistered_copy, ensure_ascii=False, indent=2) + "\n")
                            if case.get("copy_receipt"):
                                upload_packet(packet)
                                options = editor.locator("#v2-copy-options details")
                                expect(options).to_have_count(3)
                                choice = options.first
                                choice.locator("summary").click()
                                for field, value in case["copy_edit"].items():
                                    choice.locator(f'[data-copy-field="{field}"]').click()
                                    expect(editor.locator(f'[name="{field}"]')).to_have_value(value)
                            elif case.get("choose_reviewed_copy"):
                                for field, value in case["copy_edit"].items():
                                    editor.locator("#v2-reviewed-variants").get_by_role("button", name=value, exact=True).click()
                                    expect(editor.locator(f'[name="{field}"]')).to_have_value(value)
                            else:
                                for field, value in case["copy_edit"].items():
                                    editor.locator(f'[name="{field}"]').fill(value)
                        with page.expect_request(lambda r: r.url.endswith("/brief")) as review_request, \
                                page.expect_response(lambda r: r.url.endswith("/brief")) as review_response:
                            editor.locator('button[type="submit"]').click()
                        assert review_response.value.ok, review_response.value.text()
                        for field, value in case.get("copy_edit", {}).items():
                            sent_edit = review_request.value.post_data_json.get("copy_edit", {})
                            assert sent_edit.get(field) == value, (field, sent_edit, value)
                        expect(page.locator("#v2-approve")).to_be_visible()
                    api_path = "/api/v2/campaigns/" + domain

                    def saved():
                        return page.evaluate("path => window.api('GET', path)", api_path)

                    if plans is None:
                        if page.locator("#v2-package-import").get_attribute("open") is None:
                            page.locator("#v2-package-import summary").click()
                        page.locator("#v2-package-form input").set_input_files(str(case / "brief.json"))
                        with page.expect_response(lambda response: response.url.endswith(api_path + "/package")) as requested:
                            page.locator("#v2-package-form button").click()
                        assert requested.value.ok, requested.value.text()
                    initial = saved()
                    assert initial["proposal"]["status"] == "READY", initial["proposal"]["evidence"]
                    capture_kind = "publisher_snapshot" if args.snapshot else "public_http"
                    assert all(item["capture_kind"] == capture_kind for item in initial["proposal"]["evidence"])
                    if plans is not None:
                        assert initial["run_id"] != generated["run_id"]
                        assert initial["proposal"]["evidence"] == generated["proposal"]["evidence"]
                        assert initial["proposal"]["planner"]["input"] == generated["proposal"]["planner"]["input"]
                        for field, value in case.get("copy_edit", {}).items():
                            actual = initial["proposal"]["package"]["caption"] if field == "caption" else initial["proposal"]["package"]["copy"][field]
                            assert actual == value, (field, actual, value)
                            accepted_copy_fields.append(field)
                    if plans is not None and case.get("destination"):
                        cta = page.locator("#v2-package-card a")
                        expect(cta).to_have_attribute("href", case["destination"])
                        assert initial["proposal"]["package"]["source_url"] == case["destination"]
                        if args.check_destination:
                            with page.expect_popup() as opened:
                                cta.click()
                            destination = opened.value
                            destination.wait_for_load_state("domcontentloaded")
                            expect(destination).to_have_url(case["destination"], timeout=15000)
                            for name_on_page in case.get("destination_visible_text", []):
                                matches = destination.get_by_text(name_on_page, exact=True).filter(visible=True)
                                expect(matches.first).to_be_visible(timeout=15000)
                            visited_destination = {"url": destination.url,
                                                   "visible_text": case.get("destination_visible_text", [])}
                            destination.close()
                    def preview_and_approve(*, capture_mobile=False):
                        approve = page.locator("#v2-approve")
                        previews = {}
                        for size, alternate_text in (("portrait", "البوستر الطولي"), ("square", "البوستر المربّع")):
                            expect(approve).to_be_disabled()
                            with page.expect_response(lambda response: response.url.endswith("&size=" + size)) as preview:
                                page.locator(f"[data-v2-preview='{size}']").click()
                            assert preview.value.ok, preview.value.text()
                            poster = page.get_by_alt_text(alternate_text, exact=True)
                            expect(poster).to_be_visible()
                            url = poster.get_attribute("src")
                            assert url.startswith("data:image/png;base64,")
                            previews[size] = sha256(b64decode(url.split(",", 1)[1])).hexdigest()
                            assert previews[size] == preview.value.header_value("x-poster-sha256")
                            if capture_mobile:
                                page.set_viewport_size({"width": 390, "height": 844})
                                assert page.evaluate("document.documentElement.scrollWidth") <= 390
                                poster.screenshot(path=str(target / f"recommended-{size}-mobile.png"))
                                page.set_viewport_size({"width": 1440, "height": 1000})
                        with page.expect_response(lambda response: response.url.endswith(api_path + "/review")) as decision:
                            approve.click()
                        assert decision.value.ok, decision.value.text()
                        assert decision.value.request.post_data_json["artifact_sha256"] == previews
                        assert decision.value.json()["approved_artifacts"]["poster_sha256"] == previews
                        return previews

                    previews = preview_and_approve(capture_mobile=bool(initial["proposal"].get("planner", {}).get("edition")))

                    def download(dest):
                        dest.mkdir(exist_ok=True)
                        with page.expect_download(timeout=25000) as item:
                            page.click("#v2-download")
                        return unpack(item.value, dest, saved())

                    initial_hashes = download(target / "initial")
                    if plans is not None and case.get("tone"):
                        assert initial["proposal"]["brief"]["tone"] == case["tone"]
                    assert initial_hashes == previews, (initial_hashes, previews)
                    alternate = (initial["proposal"]["brief"]["selected"] + 1) % len(initial["proposal"]["brief"]["directions"])
                    page.locator(f"[data-v2-direction='{alternate}']").click()
                    expect(page.locator("#v2-approve")).to_be_visible()
                    revision = saved()
                    assert revision["run_id"] != initial["run_id"] and not revision["approved"]
                    assert revision["proposal"]["evidence"] == initial["proposal"]["evidence"]
                    assert revision["proposal"]["brief"]["selected"] == alternate
                    stale = page.evaluate(
                        """async url => {
                            const r = await fetch(url, {headers: window.authHeaders()});
                            return {status: r.status, body: await r.json()};
                        }""",
                        api_path + "/package/export?run_id=" + initial["run_id"],
                    )
                    assert stale == {"status": 409, "body": {"detail": {"code": "CAMPAIGN_CHANGED"}}}
                    if plans is not None and case.get("restore_recommended"):
                        alternate_destination = page.locator("#v2-package-card a").get_attribute("href")
                        if "alternate_destination" in case:
                            assert alternate_destination == case["alternate_destination"]
                        else:
                            assert alternate_destination != case["destination"]
                        alternative_previews = preview_and_approve()
                        alternative_hashes = download(target / "alternative")
                        assert alternative_hashes == alternative_previews
                        assert all(alternative_hashes[k] != initial_hashes[k] for k in alternative_hashes)
                        page.locator(f"[data-v2-direction='{initial["proposal"]["brief"]["selected"]}']").click()
                        expect(page.locator("#v2-approve")).to_be_visible()
                    final_previews = preview_and_approve()
                    hashes = download(target)
                    assert hashes == final_previews
                    if plans is not None and case.get("restore_recommended"):
                        assert hashes == initial_hashes
                    else:
                        assert all(hashes[k] != initial_hashes[k] for k in hashes)
                    final = saved()
                    restart(log)
                    page.reload()
                    expect(page.locator("#v2-download")).to_be_visible(timeout=20000)
                    recovered = saved()
                    assert recovered["run_id"] == final["run_id"] and recovered["approval_id"] == final["approval_id"]
                    assert recovered["proposal"]["sha256"] == final["proposal"]["sha256"]
                    assert download(target / "replay") == hashes
                    assert (target / "replay/campaign.zip").read_bytes() == (target / "campaign.zip").read_bytes()
                    page.set_viewport_size({"width": 390, "height": 844})
                    if final["proposal"].get("planner", {}).get("edition"):
                        for size in ("portrait", "square"):
                            with page.expect_response(lambda response: response.url.endswith("&size=" + size)) as preview:
                                page.locator(f"[data-v2-preview='{size}']").click()
                            assert preview.value.ok
                            png = page.get_by_alt_text("البوستر الطولي" if size == "portrait" else "البوستر المربّع", exact=True)
                            expect(png).to_be_visible()
                            assert sha256(b64decode(png.get_attribute("src").split(",", 1)[1])).hexdigest() == hashes[size]
                            png.screenshot(path=str(target / f"app-{size}-mobile.png"))
                    card = page.locator("#v2-package-card")
                    expect(card).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth") <= 390
                    card.screenshot(path=str(target / "mobile-preview.png"))
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    planner = final["proposal"].get("planner")
                    results[name] = {"status": "EXPORTED", "seconds": round(monotonic() - before, 2),
                                     "public_pages": planner["page_calls"] if planner else 2,
                                     "plan_run_id": generated["run_id"] if plans is not None else None,
                                     "reviewed_run_id": initial["run_id"], "direction_revision_run_id": revision["run_id"],
                                     "restored_run_id": final["run_id"], "restored_approval_id": final["approval_id"],
                                     "restored_zip_sha256": sha256((target / "campaign.zip").read_bytes()).hexdigest(),
                                     "visited_destination": visited_destination,
                                     "stale_export_http": stale["status"], "rejected_copy_http": rejected_copy,
                                     "unregistered_copy_response": unregistered_copy,
                                     "stale_copy_packet_rejected": stale_copy_packet_rejected,
                                     "accepted_copy_fields": accepted_copy_fields,
                                     "reviewed_png_sha256": initial_hashes,
                                     "restored_png_sha256": hashes}
            from sqlalchemy import func, select
            from db.models import Approval, Run, UsageRecord
            from workflows.demo import connect
            service, _ = connect(manifest)
            with service._uow_factory() as uow:
                usage = uow.session.scalar(select(func.count()).select_from(UsageRecord))
                runs = uow.session.scalar(select(func.count()).select_from(Run).where(Run.kind == "campaign"))
                decisions = uow.session.scalar(select(func.count()).select_from(Approval).where(
                    Approval.subject_kind == "campaign"))
            exported = sum(item["status"] == "EXPORTED" for item in results.values())
            restored = sum(bool(case.get("restore_recommended")) and results[name]["status"] == "EXPORTED"
                           for name, case in cases.items()) if plans is not None else 0
            assert (usage, runs, decisions) == (0, 2 * exported + restored + (len(cases) if plans is not None else 0),
                                               2 * exported + restored)
            assert not errors, errors
            report = {"cases": results, "elapsed_seconds": round(monotonic() - started, 2),
                      "campaign_runs": runs, "automated_review_actions": decisions,
                      "provider_usage_rows": usage, "browser_errors": errors}
            (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
    finally:
        stop()
        if not args.keep_demo:
            subprocess.run([*command, "cleanup", "--directory", str(directory)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
