"""The LOCAL interactive studio (dashboard/server.py): SSE framing, slug guard, the landing page,
and the full analyze -> studio -> generate -> asset flow over a real socket with the pipeline FAKED
(hermetic - no network, no LLM, no Veo).

Origin: owner wants to sit IN the dashboard and DRIVE it — analyze fast, then press "Generate
poster" / "Generate reel" on demand and regenerate. This locks the wiring without ever running the
real (paid) generation.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard import server as srv


def test_sse_frames_are_well_formed():
    frame = srv.sse("stage", {"msg": "line\nwith newline"}).decode("utf-8")
    assert frame.startswith("event: stage\n") and frame.endswith("\n\n")
    body = frame.split("data: ", 1)[1].rstrip("\n")
    assert json.loads(body)["msg"] == "line\nwith newline"


def test_slug_guard_rejects_traversal():
    assert srv._ok_slug("brand_example")
    assert not srv._ok_slug("../secret")
    assert not srv._ok_slug("a/b")
    assert not srv._ok_slug("")


def test_idn_url_slug_is_ascii_and_accepted_by_the_server():
    # str.isalnum() is True for é/ü/日, so an IDN brand once analyzed fine then 400'd at /studio.
    # _slug must emit only what the server's ASCII guard accepts.
    from dashboard.run import _slug
    for url in ["https://café.com", "https://münchen.de", "https://日本.jp", "https://naïve.io"]:
        s = _slug(url)
        assert s.isascii(), (url, s)
        assert srv._ok_slug(s), (url, s)          # analyze -> studio round-trips, no 400


def test_landing_page_wires_analyze_over_sse():
    # The front door wears the SAME lavender War-Room identity as /dashboard (owner: one design).
    html = srv._landing_html()
    assert 'id="url"' in html and 'id="go"' in html                 # URL box + Analyze
    assert "Baseera" in html and "#CDB4E4" in html                  # brand + lavender identity
    assert "#B85C7A" not in html and 'dir="rtl"' in html            # the old blush theme is gone
    assert "/analyze?url=" in html and "EventSource" in html        # SSE to analyze
    assert "/dashboard?slug=" in html                               # hands off to the UNIFIED deck


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """A real server on an ephemeral port with analyze/generate FAKED (no network/LLM/Veo)."""
    def fake_analyze(url, *, out_dir="outputs", on_progress=None):
        on_progress("stage_start", "Scrape + Profile + Competitors + SWOT", "  -> Scrape ...")
        on_progress("stage_ok", "Scrape + Profile + Competitors + SWOT", "    [OK] Scrape  (1s)")
        slug = srv._run_mod._slug(url)
        P = srv._run_mod.paths(slug, out_dir)
        P["out"].mkdir(parents=True, exist_ok=True)
        P["result"].write_text(json.dumps({
            "subject_url": url, "subject_category": "ecommerce", "competitor_count": 0,
            "swot": {"mode": "standalone",
                     "strengths": [{"text": "Social links: 5", "citation": ["your scraped site"]}],
                     "weaknesses": [], "opportunities": [], "threats": []},
            "competitors": [], "tows": {},
            "profile": {"name": {"value": "Test Brand"}, "category": {"value": "ecommerce"}},
        }), encoding="utf-8")
        P["profile"].write_text(json.dumps(
            {"name": {"value": "Test Brand"}, "category": {"value": "ecommerce"}}), encoding="utf-8")
        return slug

    captured: dict = {}

    def fake_poster(slug, *, out_dir="outputs", on_progress=None, product_name=None,
                    product_image=None, **kw):          # use_hitl / future flags ride through
        captured["product_name"] = product_name
        captured["product_image"] = product_image
        captured.update(kw)
        on_progress("stage_start", "Poster (one-shot)", "  -> Poster ...")
        P = srv._run_mod.paths(slug, out_dir)
        P["poster"].parent.mkdir(parents=True, exist_ok=True)
        P["poster"].write_bytes(b"\x89PNG\r\n\x1a\nFAKE-POSTER")
        on_progress("stage_ok", "Poster (one-shot)", "    [OK] Poster  (2s)")
        return P["poster"]

    monkeypatch.setattr("dashboard.run.analyze", fake_analyze)
    monkeypatch.setattr("dashboard.run.generate_poster", fake_poster)
    srv._Handler._captured = captured   # expose for the product-threading test
    srv._Handler.out_dir = str(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _host, port = httpd.server_address
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def test_analyze_streams_stages_then_slug(live):
    _s, body = _get(live + "/analyze?url=https://brand.example/")
    stream = body.decode("utf-8")
    assert "event: stage" in stream and "[OK] Scrape" in stream
    assert "event: done" in stream
    done = [l for l in stream.splitlines() if l.startswith("data:")][-1]
    assert json.loads(done[len("data: "):])["slug"] == "brand_example"


def test_studio_renders_report_plus_creative_studio(live):
    _get(live + "/analyze?url=https://brand.example/")                 # seed the artifacts
    _s, body = _get(live + "/studio?slug=brand_example")
    h = body.decode("utf-8")
    assert "Test Brand" in h                                           # the report renders
    assert "Creative Studio" in h                                      # the interactive block
    assert "gen('poster')" in h and "gen('reel')" in h                 # both generate buttons
    assert "Generate poster" in h and "Generate reel" in h            # empty -> "Generate"
    # BOTH heavy creatives are opt-in — the report shows instantly, nothing auto-runs, so the owner
    # never lands on a wait they didn't ask for.
    assert "AUTO={poster:false,reel:false}" in h


def test_studio_has_paste_a_product_url_input(live):
    _get(live + "/analyze?url=https://brand.example/")
    _s, body = _get(live + "/studio?slug=brand_example")
    h = body.decode("utf-8")
    assert 'id="purl"' in h and "addByUrl()" in h                      # the paste-a-link UI + handler
    assert "/product_by_url?" in h                                     # JS calls the new route


def test_dashboard_route_serves_the_unified_war_room(live):
    # /dashboard IS the unified paged package now; /classic keeps the legacy single-scroll report.
    _get(live + "/analyze?url=https://brand.example/")
    _s, body = _get(live + "/dashboard?slug=brand_example")
    h = body.decode("utf-8")
    assert 'id="p10"' in h and 'id="p7"' in h                     # the 10-page deck incl. services
    assert 'id="svc-poster"' in h and "/hitl/preview" in h        # served services page interactive
    _s2, body2 = _get(live + "/classic?slug=brand_example")
    h2 = body2.decode("utf-8")
    assert "bsr" in h2 and 'id="p1"' not in h2                    # the old light report, not paged


def test_war_room_route_serves_the_paged_dashboard(live):
    # /war_room?slug= builds + serves the paged War Room; served media rides /asset (no base64).
    _get(live + "/analyze?url=https://brand.example/")                 # seed the artifacts
    _s, body = _get(live + "/war_room?slug=brand_example")
    h = body.decode("utf-8")
    assert _s == 200
    assert 'id="p1"' in h and 'id="p6"' in h and 'dir="rtl"' in h      # the 6-page deck
    assert "Awaiting Meta Pull" in h                                   # honest radar (no Meta pull)
    # bad slug -> 400; unknown brand -> 404
    for path, want in (("/war_room?slug=../x", 400), ("/war_room?slug=nobrand", 404)):
        try:
            s, _ = _get(live + path)
        except urllib.error.HTTPError as e:
            s = e.code
        assert s == want, path


def test_product_by_url_scrapes_and_returns_the_product(live, monkeypatch):
    from scraper.product_page import ProductInfo
    monkeypatch.setattr("scraper.product_page.scrape_product_page",
                        lambda url: ProductInfo(name="Rosemary Mist", image="https://x/i.jpg",
                                                url=url, price="250", currency="EGP"))
    monkeypatch.setattr("scraper.url_utils.is_safe_public_url", lambda u: True)
    _s, body = _get(live + "/product_by_url?slug=brand_example&url=https://shop.example/products/x")
    d = json.loads(body)
    assert d["ok"] is True
    assert d["product"]["name"] == "Rosemary Mist" and d["product"]["image"] == "https://x/i.jpg"


def test_product_by_url_rejects_non_public_url(live, monkeypatch):
    monkeypatch.setattr("scraper.url_utils.is_safe_public_url", lambda u: False)
    _s, body = _get(live + "/product_by_url?slug=brand_example&url=http://localhost/x")
    d = json.loads(body)
    assert d["ok"] is False and "public" in d["error"].lower()


def test_product_by_url_reports_when_no_product_found(live, monkeypatch):
    monkeypatch.setattr("scraper.product_page.scrape_product_page", lambda url: None)
    monkeypatch.setattr("scraper.url_utils.is_safe_public_url", lambda u: True)
    _s, body = _get(live + "/product_by_url?slug=brand_example&url=https://shop.example/about")
    d = json.loads(body)
    assert d["ok"] is False and "no product" in d["error"].lower()


def test_studio_has_scraper_quality_panel(live):
    _get(live + "/analyze?url=https://brand.example/")
    _s, body = _get(live + "/studio?slug=brand_example")
    h = body.decode("utf-8")
    assert 'id="scrape-qa"' in h and "loadScrapeQA()" in h                 # the QA panel + its loader
    assert "/scrape_qa?" in h                                              # JS calls the new route


def test_scrape_qa_route_returns_the_summary(live, monkeypatch):
    fake = {"pages_succeeded": 5, "pages_attempted": 6, "products": 3, "images": 4,
            "text_blocks": 120, "duration_ms": 42000, "budget_exceeded": False,
            "ready": True, "major_missing": [], "failures": 0, "pages": [], "key_notes": []}
    monkeypatch.setattr("dashboard.products.scrape_qa_for_slug", lambda slug: fake)
    _s, body = _get(live + "/scrape_qa?slug=brand_example")
    d = json.loads(body)
    assert d["qa"]["ready"] is True and d["qa"]["pages_succeeded"] == 5


def test_scrape_qa_route_null_when_no_scrape(live, monkeypatch):
    monkeypatch.setattr("dashboard.products.scrape_qa_for_slug", lambda slug: None)
    _s, body = _get(live + "/scrape_qa?slug=brand_example")
    assert json.loads(body)["qa"] is None


def test_studio_does_not_rerun_existing_assets(live):
    _get(live + "/analyze?url=https://brand.example/")
    _get(live + "/generate/poster?slug=brand_example")                 # poster now exists (fake)
    _s, body = _get(live + "/studio?slug=brand_example")
    h = body.decode("utf-8")
    # poster exists -> don't auto-rerun it (shows Regenerate); reel is opt-in -> never auto-run
    assert "AUTO={poster:false,reel:false}" in h
    assert "Regenerate" in h


def test_picked_product_is_threaded_into_generation(live):
    # the studio picker passes the chosen product; the server must forward it to generate_*
    _get(live + "/analyze?url=https://brand.example/")
    _get(live + "/generate/poster?slug=brand_example&pname=Hair%20Growth&pimg=https://b.example/p.jpg")
    cap = srv._Handler._captured
    assert cap["product_name"] == "Hair Growth"
    assert cap["product_image"] == "https://b.example/p.jpg"


def test_product_args_builds_cli_flags():
    from dashboard.run import _product_args
    assert _product_args(None, None) == []
    assert _product_args("Hair Growth", "https://b/p.jpg") == [
        "--product-name", "Hair Growth", "--product-image", "https://b/p.jpg"]


def test_generate_poster_then_serve_the_asset(live):
    _get(live + "/analyze?url=https://brand.example/")
    _s, body = _get(live + "/generate/poster?slug=brand_example")
    stream = body.decode("utf-8")
    assert "event: done" in stream and "/asset?slug=brand_example&kind=poster" in stream
    status, data = _get(live + "/asset?slug=brand_example&kind=poster")
    assert status == 200 and data.startswith(b"\x89PNG")


def test_concurrent_generate_runs_only_one_subprocess(live, monkeypatch):
    # a refresh / second tab must NOT spawn a duplicate generation racing on the same file
    import time as _t
    calls = {"n": 0}
    lock = threading.Lock()

    def slow_poster(slug, *, out_dir="outputs", on_progress=None, product_name=None,
                    product_image=None, **kw):
        with lock:
            calls["n"] += 1
        on_progress("stage_start", "Poster (one-shot)", "  -> Poster ...")
        _t.sleep(1.5)                                   # hold the in-flight window open
        P = srv._run_mod.paths(slug, out_dir)
        P["poster"].parent.mkdir(parents=True, exist_ok=True)
        P["poster"].write_bytes(b"\x89PNGX")
        return P["poster"]

    monkeypatch.setattr("dashboard.run.generate_poster", slow_poster)
    _get(live + "/analyze?url=https://brand.example/")   # seed the slug
    bodies: list = []

    def hit():
        try:
            bodies.append(_get(live + "/generate/poster?slug=brand_example", timeout=20)[1].decode())
        except Exception as e:                           # noqa: BLE001
            bodies.append(str(e))

    ts = [threading.Thread(target=hit) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert calls["n"] == 1                               # only ONE real generation ran
    assert any("already generating" in b for b in bodies)  # the duplicate was rejected cleanly


def test_analyze_429_shows_honest_rate_limit_banner(live, monkeypatch):
    # a 429 failure must NOT tell the user "may block scraping / be unreachable" (misleading —
    # the site is fine, just throttling). The banner must name the rate limit + suggest waiting.
    def rl_analyze(url, *, out_dir="outputs", on_progress=None):
        on_progress("stage_fail", "Scrape",
                    "    [X] rate-limited (HTTP 429) — the site asked us to slow down")
        return None                                      # scrape yielded nothing

    monkeypatch.setattr("dashboard.run.analyze", rl_analyze)
    _s, body = _get(live + "/analyze?url=https://topshoes.store/")
    stream = body.decode("utf-8")
    assert "event: failed" in stream
    assert "rate-limiting us (HTTP 429)" in stream and "Wait about a minute" in stream
    assert "may block scraping" not in stream           # the misleading banner is gone


def test_analyze_non_429_keeps_generic_banner(live, monkeypatch):
    def dead_analyze(url, *, out_dir="outputs", on_progress=None):
        on_progress("stage_fail", "Scrape", "    [X] EMPTY_RENDERED_DOM")
        return None

    monkeypatch.setattr("dashboard.run.analyze", dead_analyze)
    _s, body = _get(live + "/analyze?url=https://dead.example/")
    assert "may block scraping or be unreachable" in body.decode("utf-8")


def test_concurrent_analyze_runs_only_one_scrape(live, monkeypatch):
    # A double-click / SSE reconnect must NOT spawn two concurrent crawls of the same host —
    # two crawls hammering the site is exactly what trips Cloudflare's 429 (MEASURED:
    # topshoes.store serves 200 sequentially but 429s our double-fire).
    import time as _t
    calls = {"n": 0}
    lock = threading.Lock()

    def slow_analyze(url, *, out_dir="outputs", on_progress=None):
        with lock:
            calls["n"] += 1
        on_progress("stage_start", "Scrape", "  -> Scrape ...")
        _t.sleep(1.5)                                    # hold the in-flight window open
        slug = srv._run_mod._slug(url)
        P = srv._run_mod.paths(slug, out_dir)
        P["out"].mkdir(parents=True, exist_ok=True)
        P["result"].write_text("{}", encoding="utf-8")
        P["profile"].write_text("{}", encoding="utf-8")
        return slug

    monkeypatch.setattr("dashboard.run.analyze", slow_analyze)
    bodies: list = []

    def hit():
        try:
            bodies.append(_get(live + "/analyze?url=https://topshoes.store/", timeout=20)[1].decode())
        except Exception as e:                           # noqa: BLE001
            bodies.append(str(e))

    ts = [threading.Thread(target=hit) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert calls["n"] == 1                               # only ONE real scrape ran
    assert any("already being analyzed" in b for b in bodies)   # the duplicate rejected cleanly


def test_asset_being_regenerated_returns_503(live, monkeypatch):
    import pathlib
    _get(live + "/analyze?url=https://brand.example/")
    _get(live + "/generate/poster?slug=brand_example")   # poster now exists (fake)
    orig = pathlib.Path.read_bytes

    def boom(self):
        if self.name.endswith("_poster.png"):
            raise PermissionError("locked by ffmpeg")
        return orig(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", boom)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(live + "/asset?slug=brand_example&kind=poster")
    assert e.value.code == 503                           # clean 503, not an unhandled 500


def test_strategy_tie_surfaces_the_why(tmp_path):
    # RUBRIC D: the WHY (strategy line + funnel + audience) is built for the LIVE studio, not only
    # the export. Prefixes ("Leverage:", "the brand's audience:") are stripped like the export tie.
    (tmp_path / "brand_media_plan.json").write_text(json.dumps({
        "objectives": [{"funnel_stage": "bofu"}],
        "base_persona": {"interests": [{"claim": "the brand's audience: busy new parents"}]},
    }), encoding="utf-8")
    (tmp_path / "brand_result.json").write_text(json.dumps({
        "tows": {"priority_actions": [
            {"action": "Leverage: free trial to win price-sensitive leads"}]},
    }), encoding="utf-8")
    tie = srv._strategy_tie("brand", str(tmp_path))
    assert "serves your strategy" in tie
    assert "free trial to win price-sensitive leads" in tie      # strat, prefix stripped
    assert "تحويل · BOFU" in tie                                 # funnel label
    assert "busy new parents" in tie                             # audience, prefix stripped
    assert "tie-chip tie-strat" in tie


def test_strategy_tie_empty_without_media_plan(tmp_path):
    # No media plan yet (analyze not re-run) -> nothing to tie -> render nothing (no empty box).
    assert srv._strategy_tie("nope", str(tmp_path)) == ""


def test_studio_shows_strategy_tie_beside_the_creative(live, tmp_path):
    # The live Creative Studio must carry the tie chips (the fake analyze doesn't write U1, so
    # seed a media plan + tows, then confirm /studio renders the WHY beside the on-demand assets).
    _get(live + "/analyze?url=https://brand.example/")
    (tmp_path / "brand_example_media_plan.json").write_text(json.dumps({
        "objectives": [{"funnel_stage": "tofu"}],
        "base_persona": {"interests": [{"claim": "eco-conscious shoppers"}]},
    }), encoding="utf-8")
    _s, body = _get(live + "/studio?slug=brand_example")
    h = body.decode("utf-8")
    assert "serves your strategy" in h and "وعي · TOFU" in h
    assert "eco-conscious shoppers" in h
    assert "Creative Studio" in h                                 # tie sits inside the studio card


def test_ask_route_returns_grounded_answer(live, monkeypatch):
    # /ask wires the archive Q&A: guard slug, call answer_question, return its answer+citations.
    from dashboard.archive_qa import ArchiveAnswer
    _get(live + "/analyze?url=https://brand.example/")
    monkeypatch.setattr(srv, "answer_question", lambda slug, q, **kw: ArchiveAnswer(
        answer="Accredited by the Supreme Council.", grounded=True,
        citations=[{"n": 1, "label": "Trust signal", "url": "https://b.example/about"}],
        corpus_size=42))
    _s, body = _get(live + "/ask?slug=brand_example&q=" + "what%20accreditation")
    d = json.loads(body)
    assert d["grounded"] is True and "Supreme Council" in d["answer"]
    assert d["citations"][0]["label"] == "Trust signal" and d["corpus_size"] == 42


def test_ask_route_guards_bad_slug(live):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(live + "/ask?slug=../etc&q=hi")
    assert e.value.code == 400


def test_ask_route_survives_answer_failure(live, monkeypatch):
    # a raising answerer must not 500 the route — it returns a clean, ungrounded payload.
    def boom(slug, q, **kw):
        raise RuntimeError("model down")
    monkeypatch.setattr(srv, "answer_question", boom)
    _get(live + "/analyze?url=https://brand.example/")
    _s, body = _get(live + "/ask?slug=brand_example&q=hi")
    d = json.loads(body)
    assert d["grounded"] is False and d["answer"] == "" and d["note"]


def test_studio_has_ask_archive_panel(live):
    _get(live + "/analyze?url=https://brand.example/")
    _s, body = _get(live + "/studio?slug=brand_example")
    h = body.decode("utf-8")
    assert "Ask the archive" in h and 'id="ask-q"' in h                # the panel + input
    assert "askArchive()" in h and "/ask?slug=" in h                    # handler + route call
    assert "اسأل الأرشيف" in h                                          # bilingual heading
    assert "test(c.url)" in h                                           # citation href scheme-guard (no javascript: XSS)


def test_bad_slug_and_missing_analysis_are_guarded(live):
    with pytest.raises(urllib.error.HTTPError) as e1:
        _get(live + "/studio?slug=../etc")
    assert e1.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e2:
        _get(live + "/asset?slug=brand_example&kind=poster")           # never generated
    assert e2.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e3:
        _get(live + "/studio?slug=neveranalyzed")
    assert e3.value.code == 404
