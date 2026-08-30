"""
Rate limiter for Instagram scraping.

This exists because Instagram's anonymous-access throttling in 2026 sits
around 1-2 requests / 30s, far stricter than a couple years ago. Every
provider MUST route requests through this before hitting the network -
no provider should implement its own sleep() calls.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class RateLimitExceededError(Exception):
    """Raised when max_retries on a 429 is exhausted."""


@dataclass
class RateLimiterConfig:
    min_delay_seconds: float = 20.0
    max_delay_seconds: float = 35.0
    jitter: bool = True
    max_requests_per_window: int = 3
    window_seconds: int = 60
    backoff_initial_wait: float = 60.0
    backoff_multiplier: float = 2.0
    backoff_max_wait: float = 1800.0
    backoff_max_retries: int = 5

    @classmethod
    def from_dict(cls, d: dict) -> "RateLimiterConfig":
        backoff = d.get("backoff_on_429", {})
        return cls(
            min_delay_seconds=d.get("min_delay_seconds", 20.0),
            max_delay_seconds=d.get("max_delay_seconds", 35.0),
            jitter=d.get("jitter", True),
            max_requests_per_window=d.get("max_requests_per_window", 3),
            window_seconds=d.get("window_seconds", 60),
            backoff_initial_wait=backoff.get("initial_wait_seconds", 60.0),
            backoff_multiplier=backoff.get("multiplier", 2.0),
            backoff_max_wait=backoff.get("max_wait_seconds", 1800.0),
            backoff_max_retries=backoff.get("max_retries", 5),
        )


@dataclass
class RateLimiter:
    """
    Sliding-window rate limiter with jittered delays and exponential backoff
    on 429s. Designed to be called before every outbound request.
    """
    config: RateLimiterConfig
    _request_times: list[float] = field(default_factory=list)
    _consecutive_429s: int = 0

    def wait_before_request(self) -> None:
        """Blocks until it's safe to make the next request."""
        now = time.monotonic()
        window_start = now - self.config.window_seconds
        self._request_times = [t for t in self._request_times if t >= window_start]

        if len(self._request_times) >= self.config.max_requests_per_window:
            oldest = self._request_times[0]
            sleep_for = (oldest + self.config.window_seconds) - now
            if sleep_for > 0:
                logger.debug("Window limit reached, sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)

        delay = self.config.min_delay_seconds
        if self.config.jitter:
            delay = random.uniform(self.config.min_delay_seconds, self.config.max_delay_seconds)

        if self._request_times:
            elapsed_since_last = now - self._request_times[-1]
            remaining = delay - elapsed_since_last
            if remaining > 0:
                time.sleep(remaining)

        self._request_times.append(time.monotonic())

    def handle_429(self) -> float:
        """
        Call when a 429 is received. Sleeps according to exponential backoff
        and returns the amount waited. Raises RateLimitExceededError if
        max_retries is exhausted so the caller can checkpoint and stop
        gracefully rather than looping forever.
        """
        if self._consecutive_429s >= self.config.backoff_max_retries:
            raise RateLimitExceededError(
                f"Exceeded {self.config.backoff_max_retries} consecutive 429s. "
                "Stopping to avoid account/IP restrictions - resume later."
            )

        wait = min(
            self.config.backoff_initial_wait * (self.config.backoff_multiplier ** self._consecutive_429s),
            self.config.backoff_max_wait,
        )
        self._consecutive_429s += 1
        logger.warning(
            "429 received (consecutive=%d). Backing off %.0fs",
            self._consecutive_429s, wait,
        )
        time.sleep(wait)
        return wait

    def reset_backoff(self) -> None:
        """Call after a successful request to reset the 429 streak."""
        self._consecutive_429s = 0
