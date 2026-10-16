"""robots.txt checking.

We respect robots.txt. If a site disallows our user agent for a path,
we skip it and record ROBOTS_DISALLOWED in the manifest.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.robotparser
import urllib.request
from urllib.parse import urlparse, urlunparse

from .config import USER_AGENT
from .schemas import RobotsRecord
from .url_utils import open_public_url


def _robots_url_for(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "/robots.txt", "", "", ""))


def _fetch_robots_body(robots_url: str, timeout: float, *, retries: int = 2,
                       backoff: float = 0.5) -> str:
    """Fetch robots.txt, RETRYING transient failures (P-65) so a single timeout/5xx blip does not
    set assume-allowed and silently disable robots for the WHOLE host. A definitive 4xx (e.g. 404
    robots-absent) is NOT retried — it means "no rules", handled by the caller's assume-allowed."""
    last_exc: Exception = RuntimeError("robots fetch not attempted")
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
            with open_public_url(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt >= retries:   # 4xx is definitive; don't retry
                raise
            last_exc = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:  # transient — retry
            if attempt >= retries:
                raise
            last_exc = e
        time.sleep(backoff * (attempt + 1))
    raise last_exc


def load_robots(url: str, timeout: float = 5.0) -> tuple[urllib.robotparser.RobotFileParser, RobotsRecord]:
    """Fetch and parse robots.txt for the given URL's host.

    Returns (parser, record). If the file is missing or unreachable,
    we default to allowing crawling — this matches major-crawler
    behavior. The record makes the decision auditable.
    """
    robots_url = _robots_url_for(url)
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)

    record_args = {
        "checked": True,
        "allowed": True,
        "robots_url": robots_url,
        "crawl_delay_seconds": 0.0,
        "note": None,
    }

    try:
        # SSRF guard (P-60): don't urlopen robots.txt on a non-public host either — the crawl of
        # that host will be blocked at the fetcher anyway, so assume-allowed and skip the network.
        from .url_utils import is_safe_public_url
        if not is_safe_public_url(robots_url):
            setattr(rp, "_assume_allowed", True)
            record_args["allowed"] = True
            record_args["note"] = "robots.txt skipped (non-public URL)"
            return rp, RobotsRecord(**record_args)
        # RobotFileParser.read() uses urllib.request.urlopen with no timeout built in. We fetch
        # manually to enforce a timeout AND retry transient failures (P-65), then feed lines.
        body = _fetch_robots_body(robots_url, timeout)
        rp.parse(body.splitlines())

        # PR-1: pull Sitemap: directives out of the body. These are global
        # per the robots.txt spec (not tied to a user-agent block) so we
        # collect them once. Errors here must never block crawling.
        try:
            from .sitemap import extract_sitemap_urls_from_robots
            record_args["sitemap_urls"] = extract_sitemap_urls_from_robots(body, url)
        except Exception:
            record_args["sitemap_urls"] = []

        # crawl-delay is per-user-agent; the parser exposes it.
        cd = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*")
        if cd:
            try:
                record_args["crawl_delay_seconds"] = float(cd)
            except (TypeError, ValueError):
                pass

        # Test the input URL itself
        record_args["allowed"] = rp.can_fetch(USER_AGENT, url)
        if not record_args["allowed"]:
            record_args["note"] = "Input URL disallowed by robots.txt"
    except Exception as e:
    # Missing / unreachable robots.txt -> assume allowed.
    # IMPORTANT: a RobotFileParser that never parsed robots.txt may return
    # False from can_fetch() in some Python versions. Mark it explicitly so
    # subpage checks do not contradict the root robots record.
        setattr(rp, "_assume_allowed", True)
        record_args["allowed"] = True
        record_args["note"] = f"robots.txt unavailable ({type(e).__name__}); assuming allowed"
    return rp, RobotsRecord(**record_args)



def can_fetch(rp: urllib.robotparser.RobotFileParser, url: str) -> bool:
    if getattr(rp, "_assume_allowed", False):
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True
