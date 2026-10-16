"""F-12: one scrape asks robots.txt once, and that answer stays the policy.

The crawler used to load robots TWICE — once to satisfy `ScrapeManifest.robots` (a required
schema field) and once for real, the second overwriting the first. A restrictive policy read on
the first load could therefore be replaced by whatever the second load happened to see, and a
transient 429 reads as "robots unavailable -> assume allowed". A `Disallow: /` site became
crawlable because we asked twice.

Hermetic: the robots transport is a stateful fake, sitemap discovery is a spy that stops the run
before the browser, and Playwright is a tripwire. No socket is opened.
"""
from __future__ import annotations

import urllib.error

import pytest

import scraper.crawler as crawler
import scraper.robots as robots_mod
import scraper.sitemap as sitemap_mod


_SITE = "https://policy.public.invalid/"
_DISALLOW = b"User-agent: *\nDisallow: /\n"
_ALLOW = b"User-agent: *\nAllow: /\n"
_DISALLOW_WITH_DELAY = b"User-agent: *\nDisallow: /\nCrawl-delay: 7\n"


class _StopBeforeBrowser(BaseException):
    """Raised from the sitemap spy so a scrape ends deterministically after robots.

    Deliberately a `BaseException`: the crawler's sitemap step is wrapped in `except Exception`
    (a sitemap problem must never fail a scrape), so an ordinary exception would be swallowed and
    the run would continue into the browser.
    """


class _Response:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _RobotsTransport:
    """A robots endpoint whose answer CHANGES between observations.

    Each entry is either a body (served) or an exception (raised). `served` counts real HTTP
    attempts, which is what separates "one logical load that retried" from "two logical loads".
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.served = 0

    def __call__(self, request, *, timeout=None, context=None):
        index = min(self.served, len(self.responses) - 1)
        outcome = self.responses[index]
        self.served += 1
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)

    @property
    def unconsumed(self) -> int:
        return max(0, len(self.responses) - self.served)


def _too_many_requests() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(_SITE + "robots.txt", 429, "Too Many Requests", {}, None)


@pytest.fixture
def scrape_env(monkeypatch):
    """Wire the fakes and return a handle exposing every count the invariant needs."""
    state = {
        "logical_loads": 0,
        "sitemap_records": [],
        "browser_launched": False,
        "transport": None,
    }

    real_load_robots = robots_mod.load_robots

    def counting_load_robots(url, timeout=5.0):
        state["logical_loads"] += 1
        return real_load_robots(url, timeout)

    monkeypatch.setattr(crawler, "load_robots", counting_load_robots)

    def sitemap_spy(url, robots_record, *args, **kwargs):
        state["sitemap_records"].append(robots_record)
        raise _StopBeforeBrowser()

    monkeypatch.setattr(sitemap_mod, "discover_sitemap_urls", sitemap_spy)

    def tripwire(*_a, **_k):
        state["browser_launched"] = True
        raise AssertionError("the crawl reached the browser despite the robots policy")

    monkeypatch.setattr(crawler, "sync_playwright", tripwire)
    # A robots-disallowed scrape still writes its manifest; keep that on the real code path but
    # off the shared corpus by giving every scrape its own output root.
    return state


def _run(state, monkeypatch, tmp_path, *responses, name="run"):
    transport = _RobotsTransport(*responses)
    state["transport"] = transport
    monkeypatch.setattr(robots_mod, "open_public_url", transport)
    # `time.sleep` would only ever be reached by the retry policy; keep the suite fast without
    # changing that policy.
    monkeypatch.setattr(robots_mod.time, "sleep", lambda _s: None)
    return crawler.scrape(_SITE, str(tmp_path / name))


# ---------------------------------------------------------------------------
# A — Disallow observed first, a transient 429 waiting behind it
# ---------------------------------------------------------------------------

def test_a_a_transient_second_observation_cannot_unlock_a_disallowed_site(
        scrape_env, monkeypatch, tmp_path):
    manifest, _out = _run(scrape_env, monkeypatch, tmp_path,
                          _DISALLOW, _too_many_requests())

    assert scrape_env["logical_loads"] == 1, "robots policy was acquired more than once"
    assert scrape_env["transport"].served == 1, "a second robots response was requested"
    assert scrape_env["transport"].unconsumed == 1, "the 429 was fetched and could replace policy"
    assert manifest.robots.allowed is False, "the Disallow policy did not survive the scrape"
    assert scrape_env["browser_launched"] is False
    assert any(f.code.value == "ROBOTS_DISALLOWED" or "ROBOTS_DISALLOWED" in str(f.code)
               for f in manifest.failures), "the disallow was not recorded as the outcome"


def test_a_the_manifest_carries_the_same_record_the_decision_used(
        scrape_env, monkeypatch, tmp_path):
    manifest, _out = _run(scrape_env, monkeypatch, tmp_path,
                          _DISALLOW, _too_many_requests())
    assert manifest.robots.note is None or "unavailable" not in (manifest.robots.note or "")


# ---------------------------------------------------------------------------
# B — Allow observed first, a Disallow waiting behind it
# ---------------------------------------------------------------------------

def test_b_a_second_stricter_observation_does_not_replace_the_first_policy(
        scrape_env, monkeypatch, tmp_path):
    """Policy CONSISTENCY, not a preference for Allow: whichever answer the one load got, holds."""
    with pytest.raises(_StopBeforeBrowser):
        _run(scrape_env, monkeypatch, tmp_path, _ALLOW, _DISALLOW)

    assert scrape_env["logical_loads"] == 1
    assert scrape_env["transport"].served == 1
    assert scrape_env["transport"].unconsumed == 1
    assert len(scrape_env["sitemap_records"]) == 1
    assert scrape_env["sitemap_records"][0].allowed is True


# ---------------------------------------------------------------------------
# C — the single authoritative load fails: existing semantics preserved
# ---------------------------------------------------------------------------

def test_c_a_failing_authoritative_load_keeps_its_existing_assume_allowed_semantics(
        scrape_env, monkeypatch, tmp_path):
    """F-12 removes a duplicate acquisition. It does not re-decide what one failed load means."""
    with pytest.raises(_StopBeforeBrowser):
        _run(scrape_env, monkeypatch, tmp_path, _too_many_requests(), _ALLOW)

    assert scrape_env["logical_loads"] == 1
    record = scrape_env["sitemap_records"][0]
    assert record.allowed is True
    assert "unavailable" in (record.note or "").lower()


def test_c_a_5xx_still_retries_inside_the_one_logical_load(scrape_env, monkeypatch, tmp_path):
    """§3: retries inside one load are the existing policy and are NOT a second logical load."""
    server_error = urllib.error.HTTPError(_SITE + "robots.txt", 503, "Server Error", {}, None)
    manifest, _out = _run(scrape_env, monkeypatch, tmp_path, server_error, _DISALLOW)

    assert scrape_env["logical_loads"] == 1, "a retry was mistaken for a second policy load"
    assert scrape_env["transport"].served >= 2, "the existing retry policy stopped retrying"
    assert manifest.robots.allowed is False


# ---------------------------------------------------------------------------
# D — crawl-delay from the retained record reaches the crawler's own state
# ---------------------------------------------------------------------------

def test_d_the_retained_records_crawl_delay_is_the_one_the_crawler_reads(
        scrape_env, monkeypatch, tmp_path):
    """The polite-delay site reads `manifest.robots.crawl_delay_seconds`, so pin it there."""
    manifest, _out = _run(scrape_env, monkeypatch, tmp_path,
                          _DISALLOW_WITH_DELAY, _too_many_requests())

    assert scrape_env["logical_loads"] == 1
    assert manifest.robots.crawl_delay_seconds == 7.0


def test_d_the_crawl_delay_survives_into_the_sitemap_and_frontier_record(
        scrape_env, monkeypatch, tmp_path):
    allow_with_delay = b"User-agent: *\nAllow: /\nCrawl-delay: 3\n"
    with pytest.raises(_StopBeforeBrowser):
        _run(scrape_env, monkeypatch, tmp_path, allow_with_delay, _too_many_requests())

    assert scrape_env["sitemap_records"][0].crawl_delay_seconds == 3.0


# ---------------------------------------------------------------------------
# E — no cross-scrape caching
# ---------------------------------------------------------------------------

def test_e_two_scrapes_each_perform_exactly_one_load_and_share_nothing(
        scrape_env, monkeypatch, tmp_path):
    first, _ = _run(scrape_env, monkeypatch, tmp_path, _DISALLOW, name="first")
    assert scrape_env["logical_loads"] == 1
    assert first.robots.allowed is False

    # A SEPARATE execution must observe robots for itself. A cache keyed on the host would skip
    # this transport entirely and hand the second scrape the first one's `Disallow`.
    with pytest.raises(_StopBeforeBrowser):
        _run(scrape_env, monkeypatch, tmp_path, _ALLOW, _DISALLOW, name="second")

    assert scrape_env["logical_loads"] == 2, "the second scrape reused the first scrape's policy"
    assert scrape_env["transport"].served == 1, "the second scrape asked robots more than once"
    assert len(scrape_env["sitemap_records"]) == 1
    assert scrape_env["sitemap_records"][0].allowed is True, (
        "a cached policy leaked across scrape executions")


# ---------------------------------------------------------------------------
# F-01: the one load still goes through the guarded transport
# ---------------------------------------------------------------------------

def test_the_single_load_still_uses_the_guarded_transport(scrape_env, monkeypatch, tmp_path):
    """The fake replaces `open_public_url` itself, so being called at all is the proof."""
    _run(scrape_env, monkeypatch, tmp_path, _DISALLOW, _too_many_requests())
    assert scrape_env["transport"].served == 1, "robots bypassed the F-01 guarded transport"


# ---------------------------------------------------------------------------
# B2.1 added a pre-browser content request. A disallowed crawl must not make it.
# ---------------------------------------------------------------------------

def test_a_disallowed_crawl_fetches_no_page_content_to_determine_language(
        scrape_env, monkeypatch, tmp_path):
    """The representation bootstrap is a CONTENT request; robots governs it like any other.

    Determining "what language is this site" is not an exemption from `Disallow: /`.
    """
    probes: list[str] = []
    monkeypatch.setattr(crawler, "probe_neutral_representation",
                        lambda url, **_kw: probes.append(url))

    manifest, _out = _run(scrape_env, monkeypatch, tmp_path, _DISALLOW)

    assert manifest.robots.allowed is False
    assert probes == [], "a disallowed site was fetched anyway, to sniff its language"
    assert scrape_env["browser_launched"] is False
    assert scrape_env["logical_loads"] == 1


def test_an_allowed_crawl_makes_exactly_one_representation_probe(
        scrape_env, monkeypatch, tmp_path):
    """§17: one bounded probe per permitted scrape — not per page, retry, or hreflang variant."""
    probes: list[str] = []
    monkeypatch.setattr(crawler, "probe_neutral_representation",
                        lambda url, **_kw: probes.append(url))

    with pytest.raises(_StopBeforeBrowser):
        _run(scrape_env, monkeypatch, tmp_path, _ALLOW)

    assert len(probes) == 1, f"expected one representation probe, made {len(probes)}"
