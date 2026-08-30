"""
ApifyProvider - fallback backend for when the free path (Instaloader) gets
rate-limited or blocked. Pay-per-result, no login, no proxy management on
our end - Apify's actors handle that.

Two things this provider is deliberately paranoid about:

1. COST SAFETY. Apify bills per result. A config bug or a bad `limit`
   argument here could otherwise turn into a real invoice. Every call is
   hard-capped by `apify.cost_safety.max_results_per_call` regardless of
   what the caller asks for, and we log the requested vs. capped amount.

2. SCHEMA DRIFT. Apify actor output field names have shifted across
   versions/actors in the past (e.g. `likesCount` vs `likes`). We never
   trust a single field name - `_pick()` tries a list of known aliases and
   only returns None (never a fabricated default) if none are present.
   This mirrors the field-name-mismatch lesson from the rest of this
   project: better an honest None than a silent misread.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from models.schemas import (
    InstagramComment,
    InstagramProfile,
    InstagramPost,
    MediaType,
    PostMedia,
    TaggedAccount,
)
from providers.base import BaseInstagramProvider, ProviderError

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    REQUESTS_AVAILABLE = False

APIFY_BASE_URL = "https://api.apify.com/v2"


def _pick(item: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-None value among aliased field names."""
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class ApifyProvider(BaseInstagramProvider):
    name = "apify"

    def __init__(self, config, rate_limiter, apify_config: dict):
        super().__init__(config, rate_limiter)
        if not REQUESTS_AVAILABLE:
            raise ProviderError("`requests` is not installed. `pip install requests`.")

        # ش1 token unification: the configured env first, then the repo-wide accessor which
        # accepts BOTH names (APIFY_TOKEN canonical, APIFY_API_TOKEN legacy).
        self.token = os.environ.get(apify_config.get("api_token_env", "APIFY_API_TOKEN"))
        if not self.token:
            try:
                from social_intel.tokens import apify_token
                self.token = apify_token()
            except Exception:  # noqa: BLE001
                self.token = None
        self.actor_posts = apify_config.get("actor_posts", "apify/instagram-scraper")
        self.actor_comments = apify_config.get("actor_comments", "apify/instagram-comment-scraper")
        self.timeout = apify_config.get("timeout_secs", 180)
        self.max_results_per_call = apify_config.get("cost_safety", {}).get("max_results_per_call", 100)

    def is_available(self) -> bool:
        return REQUESTS_AVAILABLE and bool(self.token)

    def unavailable_reason(self) -> str | None:
        """Human-readable reason this provider can't run right now, or None if it can."""
        if not REQUESTS_AVAILABLE:
            return "`requests` package not installed"
        if not self.token:
            return "APIFY_API_TOKEN not found in environment or .env file"
        return None

    def _run_actor_sync(self, actor: str, run_input: dict) -> list[dict]:
        if not self.token:
            raise ProviderError(
                "APIFY_API_TOKEN is not set. Apify fallback is unavailable until "
                "it's configured - the cascade should have skipped this provider."
            )
        url = f"{APIFY_BASE_URL}/acts/{actor.replace('/', '~')}/run-sync-get-dataset-items"
        try:
            response = requests.post(
                url,
                params={"token": self.token},
                json=run_input,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise ProviderError(f"Apify request failed for actor {actor}: {e}") from e

        if response.status_code == 429:
            raise ProviderError(f"Apify rate-limited this call (actor {actor}).")
        if not response.ok:
            raise ProviderError(
                f"Apify actor {actor} returned {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as e:
            raise ProviderError(f"Apify returned non-JSON response for {actor}: {e}") from e

    def _capped(self, requested: int) -> int:
        capped = min(requested, self.max_results_per_call)
        if capped < requested:
            logger.warning(
                "Apify cost-safety cap: requested %d results, capping to %d "
                "(config: apify.cost_safety.max_results_per_call)",
                requested, capped,
            )
        return capped

    def fetch_profile(self, username: str) -> InstagramProfile:
        items = self._run_actor_sync(
            self.actor_posts,
            {
                "directUrls": [f"https://www.instagram.com/{username}/"],
                "resultsType": "details",
                "resultsLimit": 1,
            },
        )
        if not items:
            raise ProviderError(f"Apify returned no profile data for {username}")
        item = items[0]

        return InstagramProfile(
            username=_pick(item, "username", "handle") or username,
            full_name=_pick(item, "fullName", "full_name"),
            biography=_pick(item, "biography", "bio"),
            website=_pick(item, "externalUrl", "website"),
            category=_pick(item, "businessCategoryName", "category"),
            followers=_pick(item, "followersCount", "followers"),
            following=_pick(item, "followsCount", "following"),
            post_count=_pick(item, "postsCount", "postCount"),
            is_verified=_pick(item, "verified", "isVerified"),
            is_business_account=_pick(item, "isBusinessAccount"),
            profile_image_url=_pick(item, "profilePicUrlHD", "profilePicUrl"),
            business_email=_pick(item, "businessEmail"),
            business_phone=_pick(item, "businessPhoneNumber"),
            business_address=_pick(item, "businessAddress"),
        )

    def fetch_posts(self, username: str, limit: int) -> list[InstagramPost]:
        capped_limit = self._capped(limit)
        items = self._run_actor_sync(
            self.actor_posts,
            {
                "directUrls": [f"https://www.instagram.com/{username}/"],
                "resultsType": "posts",
                "resultsLimit": capped_limit,
            },
        )
        return [self._convert_post(item) for item in items if _pick(item, "shortCode", "shortcode")]

    def _convert_post(self, item: dict[str, Any]) -> InstagramPost:
        type_str = (_pick(item, "type", "productType") or "").lower()
        if "sidecar" in type_str or "carousel" in type_str or item.get("childPosts"):
            media_type = MediaType.CAROUSEL
        elif "video" in type_str or _pick(item, "videoUrl"):
            media_type = MediaType.VIDEO
        else:
            media_type = MediaType.IMAGE

        image_urls, video_urls = [], []
        carousel_count = None
        children = item.get("childPosts") or []
        if children:
            carousel_count = len(children)
            for child in children:
                v = _pick(child, "videoUrl")
                i = _pick(child, "displayUrl", "imageUrl")
                (video_urls if v else image_urls).append(v or i)
        else:
            v = _pick(item, "videoUrl")
            i = _pick(item, "displayUrl", "imageUrl")
            if v:
                video_urls.append(v)
            elif i:
                image_urls.append(i)

        shortcode = _pick(item, "shortCode", "shortcode")
        return InstagramPost(
            shortcode=shortcode,
            url=_pick(item, "url") or f"https://www.instagram.com/p/{shortcode}/",
            caption=_pick(item, "caption"),
            posted_at=_parse_dt(_pick(item, "timestamp", "takenAt")),
            likes=_pick(item, "likesCount", "likes"),
            comments_count=_pick(item, "commentsCount", "comments"),
            media=PostMedia(
                media_type=media_type,
                image_urls=image_urls,
                video_urls=video_urls,
                carousel_item_count=carousel_count,
            ),
            hashtags=_pick(item, "hashtags") or [],
            mentions=_pick(item, "mentions") or [],
            location_name=(item.get("locationName") if item.get("locationName") else None),
            tagged_accounts=[
                TaggedAccount(username=_pick(u, "username"), full_name=_pick(u, "full_name", "fullName"))
                for u in (item.get("taggedUsers") or [])
                if _pick(u, "username")
            ],
            is_pinned=_pick(item, "isPinned"),
        )

    def fetch_comments(self, shortcode: str, limit: int) -> list[InstagramComment]:
        capped_limit = self._capped(limit)
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        items = self._run_actor_sync(
            self.actor_comments,
            {"directUrls": [post_url], "resultsLimit": capped_limit},
        )
        comments = []
        for item in items:
            comments.append(
                InstagramComment(
                    author_username=_pick(item, "ownerUsername", "author"),
                    text=_pick(item, "text") or "",
                    posted_at=_parse_dt(_pick(item, "timestamp")),
                    like_count=_pick(item, "likesCount"),
                )
            )
        return comments
