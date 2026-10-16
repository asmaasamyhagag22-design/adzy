"""F-10: the public scrape controls reach and constrain the real crawler.

All crawler tests use a synthetic in-memory page graph.  Playwright, page fetching, robots and
sitemap discovery are local fakes; the production crawl loop, frontier selection, depth tracking,
page-cap checks and delay calculation remain in the path under test.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.jobs.store import JobStore, serving_store
from api.routes import scrape as scrape_route
from api.routes.scrape import ScrapeRequest
from scraper import crawler
from scraper.config import (
    ECOMMERCE_BUDGET_SECONDS,
    ECOMMERCE_MAX_INTERNAL_PAGES,
    INTER_PAGE_DELAY_SECONDS,
    MAX_CRAWL_DEPTH,
)
from scraper.schemas import (
    ContactInfo,
    LinkCategory,
    LinkInventory,
    LinkRecord,
    PageRecord,
    ReadinessReport,
    RobotsRecord,
    SiteMetadata,
    VisualIdentity,
)
from scraper.sitemap import SitemapDiscoveryResult


class _Closable:
    def close(self) -> None:
        pass


class _Managed:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class _FetchResult:
    def __init__(self, url: str):
        self.ok = True
        self.final_url = url
        self.bytes_downloaded = 1
        self._page = _Closable()
        self.full_screenshot_bytes = b""
        self.html = ""
        self.rendered_text = f"Synthetic content for {url}"
        self.screenshot_failed = False


def _ready() -> ReadinessReport:
    return ReadinessReport(
        has_homepage=True,
        has_internal_pages=True,
        has_contact_signals=False,
        has_visual_signals=False,
        has_cta_candidates=False,
        has_metadata=False,
        ready_for_extraction=True,
    )


def _install_crawl_graph(monkeypatch, graph: dict[str, list[str]], *, robots_delay: float = 0.0):
    """Replace external I/O while leaving ``crawler.scrape``'s real controls in charge."""
    fetched: list[str] = []
    sleeps: list[float] = []

    root = next(iter(graph))
    robots = RobotsRecord(
        checked=True,
        allowed=True,
        robots_url=f"{root}robots.txt",
        crawl_delay_seconds=robots_delay,
    )
    monkeypatch.setattr(crawler, "load_robots", lambda _url: (object(), robots))
    monkeypatch.setattr(crawler, "can_fetch", lambda _rp, _url: True)
    monkeypatch.setattr(
        "scraper.sitemap.discover_sitemap_urls",
        lambda _url, _robots: SitemapDiscoveryResult(),
    )

    monkeypatch.setattr(crawler, "sync_playwright", lambda: _Managed(object()))
    monkeypatch.setattr(crawler, "_launch_chromium", lambda *_args, **_kwargs: _Closable())
    # `**_kwargs` so the double survives the context factory gaining the evidence-aligned
    # `locale=` argument; this fixture is about crawl budget, not about the browser fingerprint.
    monkeypatch.setattr(crawler, "make_browser_context",
                        lambda _browser, **_kwargs: _Closable())
    # The neutral representation probe is a real HTTP GET in production. This suite is hermetic
    # and its hosts do not resolve, so stub it: F-10 asserts page/depth/delay budgets, and a
    # probe that fails simply leaves the render locale on its declared fallback.
    monkeypatch.setattr(crawler, "probe_neutral_representation", lambda _url, **_kw: None)

    def fake_fetch(_context, url: str, **_kwargs):
        fetched.append(url)
        return _FetchResult(url)

    monkeypatch.setattr(crawler, "fetch_page", fake_fetch)
    monkeypatch.setattr(crawler, "extract_from_page", lambda *_args: {})
    monkeypatch.setattr(crawler, "build_visual_identity", lambda *_args: VisualIdentity())

    def process(result, _page, page_type, tier, _out_dir, _index):
        links = [
            LinkRecord(
                href=target,
                anchor_text="Synthetic destination",
                page_url=result.final_url,
                category=LinkCategory.INTERNAL,
            )
            for target in graph.get(result.final_url, [])
        ]
        page = PageRecord(
            url=result.final_url,
            final_url=result.final_url,
            fetched_at=datetime.now(timezone.utc),
            fetch_duration_ms=1,
            page_type=page_type,
            tier=tier,
            viewport_width=1280,
            viewport_height=720,
            has_meaningful_content=True,
        )
        return page, links, SiteMetadata(), [], []

    monkeypatch.setattr(crawler, "_process_fetched_page", process)
    monkeypatch.setattr(crawler, "extract_contact", lambda *_args, **_kwargs: ContactInfo())
    monkeypatch.setattr(crawler, "fill_addresses_from_schema_org", lambda *_args: None)
    monkeypatch.setattr(crawler, "build_inventory", lambda _links: LinkInventory())
    monkeypatch.setattr(crawler, "detect_languages", lambda *_args: [])
    monkeypatch.setattr(crawler, "compute_readiness", lambda _manifest: _ready())
    monkeypatch.setattr(crawler, "_write_manifest", lambda *_args: None)
    monkeypatch.setattr(crawler.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("scraper.extractors.testimonials.extract_testimonials", lambda _html: [])
    return fetched, sleeps


def test_scrape_request_defaults_and_supported_boundaries():
    request = ScrapeRequest(url="https://synthetic.invalid/")
    assert (request.max_pages, request.max_depth, request.delay) == (
        20,
        MAX_CRAWL_DEPTH,
        INTER_PAGE_DELAY_SECONDS,
    )

    zero = ScrapeRequest(
        url="https://synthetic.invalid/", max_pages=0, max_depth=0, delay=0.0,
    )
    assert (zero.max_pages, zero.max_depth, zero.delay) == (0, 0, 0.0)
    assert ScrapeRequest(
        url="https://synthetic.invalid/",
        max_pages=ECOMMERCE_MAX_INTERNAL_PAGES,
        max_depth=MAX_CRAWL_DEPTH,
        delay=ECOMMERCE_BUDGET_SECONDS,
    )
    assert ScrapeRequest(url="https://synthetic.invalid/", delay=0.125).delay == 0.125


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_pages", -1),
        ("max_pages", ECOMMERCE_MAX_INTERNAL_PAGES + 1),
        ("max_pages", 1.5),
        ("max_pages", None),
        ("max_depth", -1),
        ("max_depth", MAX_CRAWL_DEPTH + 1),
        ("max_depth", None),
        ("delay", -0.01),
        ("delay", ECOMMERCE_BUDGET_SECONDS + 0.01),
        ("delay", float("inf")),
        ("delay", None),
    ],
)
def test_scrape_request_rejects_values_outside_crawler_contract(field, value):
    with pytest.raises(ValidationError):
        ScrapeRequest(url="https://synthetic.invalid/", **{field: value})


def test_route_captures_distinct_settings_for_two_nearby_jobs(monkeypatch):
    seen: dict[str, tuple[int, int, float]] = {}

    def capture_pipeline(job_id, _url, _store, **kwargs):
        seen[job_id] = (
            kwargs["max_pages"], kwargs["max_depth"], kwargs["delay"],
        )
        return None

    monkeypatch.setattr(scrape_route, "run_pipeline_job", capture_pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    monkeypatch.setattr(scrape_route, "is_safe_public_url", lambda _url: True)

    async def exercise():
        store = JobStore(ttl_seconds=60)
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(store=store)),
        )
        # The route creates its job through the provider now, and the legacy provider finds the
        # store the way production does — through the request-scoped binding the middleware makes.
        # Calling the handler directly skips that middleware, so the binding is made here.
        binding = serving_store(store)
        binding.__enter__()
        first, second, omitted = await asyncio.gather(
            scrape_route.scrape(
                ScrapeRequest(
                    url="https://first.invalid/", max_pages=1, max_depth=0, delay=0.25,
                ),
                request,
                user={"id": "owner"},
            ),
            scrape_route.scrape(
                ScrapeRequest(
                    url="https://second.invalid/", max_pages=7, max_depth=2, delay=3.5,
                ),
                request,
                user={"id": "owner"},
            ),
            scrape_route.scrape(
                ScrapeRequest(url="https://defaults.invalid/"),
                request,
                user={"id": "owner"},
            ),
        )
        for _ in range(100):
            if len(seen) == 3:
                break
            await asyncio.sleep(0.001)
        return first["job_id"], second["job_id"], omitted["job_id"]

    first_id, second_id, omitted_id = asyncio.run(exercise())
    assert seen[first_id] == (1, 0, 0.25)
    assert seen[second_id] == (7, 2, 3.5)
    assert seen[omitted_id] == (20, MAX_CRAWL_DEPTH, INTER_PAGE_DELAY_SECONDS)


def test_pipeline_passes_explicit_settings_to_scraper_boundary():
    from api.jobs.runner import run_pipeline_job

    seen = {}

    def scraper_boundary(_url, _output_root, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after observing the crawler boundary")

    async def exercise():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job,
            job.job_id,
            "https://synthetic.invalid/",
            store,
            skip_llm=True,
            persist_to_disk=False,
            scraper_fn=scraper_boundary,
            max_pages=6,
            max_depth=2,
            delay=0.75,
        )
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert seen == {"max_pages": 6, "max_depth": 2, "delay": 0.75}


def test_pipeline_omission_leaves_direct_scraper_defaults_authoritative():
    from api.jobs.runner import run_pipeline_job

    seen = None

    def scraper_boundary(_url, _output_root, **kwargs):
        nonlocal seen
        seen = kwargs
        raise RuntimeError("stop after observing omitted crawler options")

    async def exercise():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job,
            job.job_id,
            "https://synthetic.invalid/",
            store,
            skip_llm=True,
            persist_to_disk=False,
            scraper_fn=scraper_boundary,
        )
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert seen == {}, "runner must not replace crawler/CLI defaults with API defaults"


def test_max_pages_caps_internal_pages_in_real_crawl_loop(tmp_path, monkeypatch):
    root = "https://synthetic.invalid/"
    graph = {
        root: [
            f"{root}services",
            f"{root}contact",
            f"{root}about",
        ],
    }
    fetched, _sleeps = _install_crawl_graph(monkeypatch, graph)

    manifest, _out = crawler.scrape(
        root, str(tmp_path), max_pages=1, max_depth=MAX_CRAWL_DEPTH, delay=0.0,
    )

    assert fetched[0] == root
    assert len(fetched) == 2, "homepage plus exactly one allowed internal page"
    assert manifest.scrape_meta.pages_attempted == 2

    fetched.clear()
    crawler.scrape(
        root, str(tmp_path), max_pages=3, max_depth=MAX_CRAWL_DEPTH, delay=0.0,
    )
    assert len(fetched) == 4, "homepage plus the three available internal pages"


def test_explicit_page_cap_cannot_be_upgraded_by_store_detection(tmp_path, monkeypatch):
    root = "https://synthetic.invalid/"
    graph = {
        root: [
            f"{root}products/item-{index}"
            for index in range(ECOMMERCE_MAX_INTERNAL_PAGES)
        ],
    }
    fetched, _sleeps = _install_crawl_graph(monkeypatch, graph)

    crawler.scrape(root, str(tmp_path), max_pages=1, max_depth=1, delay=0.0)

    assert len(fetched) == 2, "adaptive store detection must not override the request ceiling"


@pytest.mark.parametrize(
    ("max_depth", "expected_urls"),
    [
        (0, ["https://synthetic.invalid/"]),
        (1, ["https://synthetic.invalid/", "https://synthetic.invalid/services"]),
        (
            2,
            [
                "https://synthetic.invalid/",
                "https://synthetic.invalid/services",
                "https://synthetic.invalid/services/detail",
            ],
        ),
    ],
)
def test_max_depth_controls_root_depth1_depth2_graph(
    tmp_path, monkeypatch, max_depth, expected_urls,
):
    root = "https://synthetic.invalid/"
    graph = {
        root: [f"{root}services"],
        f"{root}services": [f"{root}services/detail"],
        f"{root}services/detail": [],
    }
    fetched, _sleeps = _install_crawl_graph(monkeypatch, graph)

    crawler.scrape(root, str(tmp_path), max_pages=10, max_depth=max_depth, delay=0.0)

    assert fetched == expected_urls


@pytest.mark.parametrize(
    ("requested_delay", "robots_delay", "expected"),
    [(0.25, 0.0, 0.25), (2.75, 0.0, 2.75), (0.25, 1.5, 1.5)],
)
def test_delay_is_consumed_without_real_sleep_and_robots_remains_a_floor(
    tmp_path, monkeypatch, requested_delay, robots_delay, expected,
):
    root = "https://synthetic.invalid/"
    graph = {root: [f"{root}services"], f"{root}services": []}
    _fetched, sleeps = _install_crawl_graph(
        monkeypatch, graph, robots_delay=robots_delay,
    )

    crawler.scrape(
        root, str(tmp_path), max_pages=1, max_depth=1, delay=requested_delay,
    )

    assert sleeps == [expected]


def test_direct_crawler_omission_preserves_config_defaults(tmp_path, monkeypatch):
    root = "https://synthetic.invalid/"
    graph = {
        root: [f"{root}services"],
        f"{root}services": [f"{root}services/detail"],
        f"{root}services/detail": [],
    }
    fetched, sleeps = _install_crawl_graph(monkeypatch, graph)

    crawler.scrape(root, str(tmp_path))

    assert fetched == [root, f"{root}services", f"{root}services/detail"]
    assert sleeps == [INTER_PAGE_DELAY_SECONDS, INTER_PAGE_DELAY_SECONDS]
