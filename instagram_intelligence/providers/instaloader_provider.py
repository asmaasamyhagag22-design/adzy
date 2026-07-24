"""
InstaloaderProvider - primary scraping backend, no-login mode by default.

Every Instaloader call that hits the network is preceded by
rate_limiter.wait_before_request(). On a 429 (surfaced by instaloader as
instaloader.exceptions.ConnectionException / TooManyRequestsException
depending on version), we call rate_limiter.handle_429() and retry once
per backoff step, up to the configured max_retries.
"""
from __future__ import annotations

import logging
from datetime import datetime

from models.schemas import (
    InstagramComment,
    InstagramProfile,
    InstagramPost,
    MediaType,
    PostMedia,
    TaggedAccount,
)
from providers.base import BaseInstagramProvider, ProviderError
from utils.rate_limiter import RateLimitExceededError

logger = logging.getLogger(__name__)

try:
    import instaloader
    from instaloader.exceptions import ConnectionException, InstaloaderException, ProfileNotExistsException
    INSTALOADER_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    INSTALOADER_AVAILABLE = False


class InstaloaderProvider(BaseInstagramProvider):
    name = "instaloader"

    def __init__(self, config, rate_limiter, login_username: str | None = None,
                 login_password: str | None = None, session_file: str | None = None):
        super().__init__(config, rate_limiter)
        if not INSTALOADER_AVAILABLE:
            raise ProviderError("instaloader is not installed. `pip install instaloader`.")

        self._loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )
        self._logged_in = False
        if login_username and login_password:
            self._login(login_username, login_password, session_file)

    def _login(self, username: str, password: str, session_file: str | None) -> None:
        try:
            if session_file:
                try:
                    self._loader.load_session_from_file(username, session_file)
                    self._logged_in = True
                    return
                except FileNotFoundError:
                    pass
            self._loader.login(username, password)
            if session_file:
                self._loader.save_session_to_file(session_file)
            self._logged_in = True
        except InstaloaderException as e:
            logger.warning("Login failed, continuing in no-login mode: %s", e)
            self._logged_in = False

    def _with_retry(self, fn, *args, **kwargs):
        """Wraps a network-hitting call with rate limiting + 429 backoff."""
        while True:
            self.rate_limiter.wait_before_request()
            try:
                result = fn(*args, **kwargs)
                self.rate_limiter.reset_backoff()
                return result
            except ConnectionException as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    try:
                        self.rate_limiter.handle_429()
                        continue
                    except RateLimitExceededError:
                        raise ProviderError(
                            "Rate limit exhausted for this run. Progress has been "
                            "checkpointed - resume later rather than retrying immediately."
                        ) from e
                raise ProviderError(f"Instaloader connection error: {e}") from e
            except ProfileNotExistsException as e:
                # Instagram's anonymous GraphQL endpoint has been returning a
                # blanket 403 that Instaloader (as of 4.15.1) misreports as
                # "profile does not exist" - even for accounts confirmed to
                # exist (see instaloader/instaloader#2682). We can't trust
                # this signal on its own, so we surface it as a ProviderError
                # rather than a hard "not found" - letting the cascade try
                # the next provider (e.g. Apify), which uses a different
                # access path and can actually confirm existence or absence.
                raise ProviderError(
                    f"Instaloader reported '{e}' - this may be a genuine missing "
                    "profile, OR Instagram's current anonymous-access 403 lockdown "
                    "misreported as not-found (known issue, still open as of mid-2026). "
                    "Falling through to the next provider to get a reliable answer."
                ) from e
            except InstaloaderException as e:
                raise ProviderError(f"Instaloader error: {e}") from e

    def fetch_profile(self, username: str) -> InstagramProfile:
        profile = self._with_retry(instaloader.Profile.from_username, self._loader.context, username)
        return InstagramProfile(
            username=profile.username,
            full_name=profile.full_name or None,
            biography=profile.biography or None,
            website=profile.external_url or None,
            category=getattr(profile, "business_category_name", None),
            followers=profile.followers,
            following=profile.followees,
            post_count=profile.mediacount,
            is_verified=profile.is_verified,
            is_business_account=getattr(profile, "is_business_account", None),
            profile_image_url=profile.profile_pic_url,
            business_email=getattr(profile, "business_email", None) or None,
            business_phone=getattr(profile, "business_phone_number", None) or None,
            business_address=getattr(profile, "business_address_json", None) or None,
        )

    def fetch_posts(self, username: str, limit: int) -> list[InstagramPost]:
        profile = self._with_retry(instaloader.Profile.from_username, self._loader.context, username)
        posts: list[InstagramPost] = []

        post_iterator = self._with_retry(lambda: profile.get_posts())
        for post in post_iterator:
            if len(posts) >= limit:
                break
            # Each `post` attribute access below may itself trigger a lazy
            # network fetch in Instaloader - route through the same guard.
            posts.append(self._with_retry(self._convert_post, post))

        return posts

    def _convert_post(self, post) -> InstagramPost:
        if post.typename == "GraphSidecar":
            media_type = MediaType.CAROUSEL
        elif post.is_video:
            media_type = MediaType.VIDEO
        else:
            media_type = MediaType.IMAGE

        image_urls, video_urls = [], []
        carousel_count = None
        if media_type == MediaType.CAROUSEL:
            nodes = list(post.get_sidecar_nodes())
            carousel_count = len(nodes)
            for node in nodes:
                (video_urls if node.is_video else image_urls).append(
                    node.video_url if node.is_video else node.display_url
                )
        elif media_type == MediaType.VIDEO:
            video_urls.append(post.video_url)
        else:
            image_urls.append(post.url)

        return InstagramPost(
            shortcode=post.shortcode,
            url=f"https://www.instagram.com/p/{post.shortcode}/",
            caption=post.caption or None,
            posted_at=post.date_utc,
            likes=post.likes,
            comments_count=post.comments,
            media=PostMedia(
                media_type=media_type,
                image_urls=image_urls,
                video_urls=video_urls,
                carousel_item_count=carousel_count,
            ),
            hashtags=list(post.caption_hashtags) if post.caption_hashtags else [],
            mentions=list(post.caption_mentions) if post.caption_mentions else [],
            location_name=post.location.name if post.location else None,
            tagged_accounts=[
                TaggedAccount(username=u.username, full_name=u.full_name or None)
                for u in (post.tagged_users or [])
            ],
            is_pinned=None,  # instaloader does not expose this reliably in no-login mode
        )

    def fetch_comments(self, shortcode: str, limit: int) -> list[InstagramComment]:
        post = self._with_retry(
            instaloader.Post.from_shortcode, self._loader.context, shortcode
        )
        comments: list[InstagramComment] = []
        comment_iterator = self._with_retry(lambda: post.get_comments())
        for c in comment_iterator:
            if len(comments) >= limit:
                break
            comments.append(
                InstagramComment(
                    author_username=c.owner.username if c.owner else None,
                    text=c.text,
                    posted_at=c.created_at_utc,
                    like_count=getattr(c, "likes_count", None),
                )
            )
        return comments

    def is_available(self) -> bool:
        return INSTALOADER_AVAILABLE
