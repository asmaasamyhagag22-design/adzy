"""
ProviderCascade: tries each configured provider in order, per method call.

Fallback happens at the method-call level, not the individual-item level:
if Instaloader's fetch_posts() fails partway through (e.g. rate-limited on
post #14 of 30), it raises rather than returning a partial list, and the
cascade retries the FULL fetch_posts(username, 30) call against the next
provider (Apify) - not just the remaining 17. This means a few results may
be re-fetched (and, on Apify, re-billed) rather than picked up exactly
where the free tier left off. That's a deliberate simplicity trade-off:
true per-item hand-off would need every provider to expose resumable
partial state, which adds real complexity for a cost difference of a few
cents at this project's scale. Worth revisiting if volumes grow.

This also implements BaseInstagramProvider, so it's a drop-in replacement
for a single provider anywhere in the codebase.
"""
from __future__ import annotations

import logging

from models.schemas import InstagramComment, InstagramPost, InstagramProfile
from providers.base import BaseInstagramProvider, ProviderError

logger = logging.getLogger(__name__)


class AllProvidersFailedError(ProviderError):
    def __init__(self, method: str, attempts: list[tuple[str, str]]):
        self.attempts = attempts
        detail = "; ".join(f"{name}: {err}" for name, err in attempts)
        super().__init__(f"All providers failed for {method}(). Attempts - {detail}")


class ProviderCascade(BaseInstagramProvider):
    name = "cascade"

    def __init__(self, providers: list[BaseInstagramProvider]):
        if not providers:
            raise ValueError("ProviderCascade needs at least one provider")
        self.providers = providers
        self.last_used_provider: str | None = None
        # Deliberately not calling super().__init__() - the cascade has no
        # config/rate_limiter of its own, it delegates entirely to members.

    def _cascade(self, method_name: str, *args, **kwargs):
        attempts: list[tuple[str, str]] = []
        for provider in self.providers:
            if not provider.is_available():
                reason = provider.unavailable_reason() if hasattr(provider, "unavailable_reason") else "unknown reason"
                logger.info("Skipping %s (%s)", provider.name, reason)
                continue
            try:
                logger.info("Trying provider=%s for %s%s", provider.name, method_name, args)
                result = getattr(provider, method_name)(*args, **kwargs)
                self.last_used_provider = provider.name
                if provider.name != self.providers[0].name:
                    logger.warning(
                        "Fell back to provider=%s for %s - free tier didn't complete this call.",
                        provider.name, method_name,
                    )
                return result
            except ProviderError as e:
                logger.warning("provider=%s failed on %s: %s", provider.name, method_name, e)
                attempts.append((provider.name, str(e)))
                continue
        raise AllProvidersFailedError(method_name, attempts)

    def fetch_profile(self, username: str) -> InstagramProfile:
        return self._cascade("fetch_profile", username)

    def fetch_posts(self, username: str, limit: int) -> list[InstagramPost]:
        return self._cascade("fetch_posts", username, limit)

    def fetch_comments(self, shortcode: str, limit: int) -> list[InstagramComment]:
        return self._cascade("fetch_comments", shortcode, limit)

    def is_available(self) -> bool:
        return any(p.is_available() for p in self.providers)
