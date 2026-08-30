"""
BaseInstagramProvider: the interface every scraping backend implements.

    BaseInstagramProvider
            |
            +-- InstaloaderProvider   (primary)
            +-- PlaywrightProvider    (fallback)
            +-- <FutureProvider>      (e.g. Apify-backed, Graph API-backed)

Providers know HOW to talk to a data source. They do not know about
analytics, NLP, or scoring - that separation is what lets us swap
Instaloader for Apify later without touching anything downstream.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from models.schemas import InstagramComment, InstagramPost, InstagramProfile
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    posts_per_profile: int
    full_comment_sample_size: int
    comment_sample_strategy: str
    skip_stories: bool
    skip_highlights: bool


class ProviderError(Exception):
    """Base class for provider-level failures (distinct from rate-limit exhaustion)."""


class BaseInstagramProvider(ABC):
    """
    Contract:
      - fetch_profile / fetch_posts must return partial results with a
        recorded error rather than raising, wherever partial data is usable.
      - Every network call must go through self.rate_limiter.wait_before_request()
        BEFORE the request, and self.rate_limiter.handle_429() on a 429.
      - Implementations must never fabricate a field - unknown stays None.
    """

    name: str = "base"

    def __init__(self, config: ProviderConfig, rate_limiter: RateLimiter):
        self.config = config
        self.rate_limiter = rate_limiter

    @abstractmethod
    def fetch_profile(self, username: str) -> InstagramProfile:
        ...

    @abstractmethod
    def fetch_posts(self, username: str, limit: int) -> list[InstagramPost]:
        ...

    @abstractmethod
    def fetch_comments(self, shortcode: str, limit: int) -> list[InstagramComment]:
        ...

    def is_available(self) -> bool:
        """Cheap health check used by the orchestrator's fallback cascade."""
        return True
