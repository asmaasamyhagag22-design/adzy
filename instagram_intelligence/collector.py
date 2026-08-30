"""
InstagramCollector - Phase 1 orchestrator.

Phase 1 scope (this file): profile scraping, post scraping, sampled comment
scraping, engagement + media analytics, checkpointed/resumable runs.

Explicitly NOT in Phase 1 (see README "Roadmap"): NLP layer (products,
promotions, campaigns, topics, CTAs), sentiment analysis, cross-competitor
pattern detection. Those consume this module's JSON output as their input,
so they can be built and tested independently once real scraped data exists
to validate against - building them against fabricated sample data first
would risk exactly the kind of hallucinated pipeline this project is
designed to avoid.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from analytics.engagement import compute_engagement_stats, compute_media_analytics, select_comment_sample
from analytics.marketing_intelligence import compute_hashtag_stats, compute_marketing_intelligence
from models.schemas import InstagramIntelligenceProfile, ScrapeMeta, ScrapeStatus
from providers.apify_provider import ApifyProvider
from providers.base import BaseInstagramProvider, ProviderConfig, ProviderError
from providers.instaloader_provider import InstaloaderProvider
from providers.playwright_provider import PlaywrightProvider
from providers.provider_cascade import ProviderCascade
from utils.checkpoint import CheckpointManager
from utils.config_loader import load_config
from utils.logger import setup_logging
from utils.rate_limiter import RateLimiter, RateLimiterConfig

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:  # pragma: no cover
    DOTENV_AVAILABLE = False

logger = logging.getLogger(__name__)


class InstagramCollector:
    def __init__(self, config_path: str = "config/config.yaml"):
        if DOTENV_AVAILABLE:
            # Loads variables from a .env file in the current directory into
            # os.environ, WITHOUT overriding anything already set there (so
            # a real environment variable always wins over .env, useful for
            # CI or shared machines). Silently does nothing if no .env exists.
            loaded = load_dotenv(override=False)
            logger.debug(".env %s", "loaded" if loaded else "not found (skipping)")
        else:
            logger.warning("python-dotenv not installed - .env file will be ignored. "
                            "`pip install python-dotenv` or set env vars manually.")

        self.config = load_config(config_path)
        setup_logging(
            level=self.config["logging"]["level"],
            log_file=self.config["logging"].get("file"),
        )

        rl_config = RateLimiterConfig.from_dict(self.config["scraping"]["rate_limit"])
        self.rate_limiter = RateLimiter(config=rl_config)

        volume = self.config["scraping"]["volume"]
        self.provider_config = ProviderConfig(
            posts_per_profile=volume["posts_per_profile"],
            full_comment_sample_size=volume["full_comment_sample_size"],
            comment_sample_strategy=volume["comment_sample_strategy"],
            skip_stories=volume["skip_stories"],
            skip_highlights=volume["skip_highlights"],
        )

        self.checkpoints = CheckpointManager(self.config["scraping"]["resume"]["checkpoint_dir"])
        self._provider = self._build_provider_cascade()

    def _build_provider_cascade(self) -> ProviderCascade:
        """
        Builds providers in the order given by scraping.provider_priority.
        Each provider decides its own is_available() (missing deps, missing
        API token, etc.) - unavailable ones are skipped by the cascade at
        call time, not at build time, so a provider that becomes available
        mid-run (e.g. a token added to the environment) would still need a
        fresh InstagramCollector instance to pick it up.
        """
        priority = self.config["scraping"]["provider_priority"]
        available: list[BaseInstagramProvider] = []

        for provider_name in priority:
            try:
                if provider_name == "instaloader":
                    available.append(self._init_instaloader_provider())
                elif provider_name == "playwright":
                    pw_login_user = pw_login_pass = None
                    mode = self.config["scraping"]["mode"]
                    if mode in ("login", "hybrid"):
                        login_cfg = self.config["login"]
                        pw_login_user = os.environ.get(login_cfg["username_env"])
                        pw_login_pass = os.environ.get(login_cfg["password_env"])
                    available.append(PlaywrightProvider(
                        self.provider_config, self.rate_limiter,
                        login_username=pw_login_user,
                        login_password=pw_login_pass,
                        storage_state_path=self.config["login"].get("playwright_storage_state_file"),
                    ))
                elif provider_name == "apify":
                    available.append(ApifyProvider(self.provider_config, self.rate_limiter, self.config["apify"]))
                else:
                    logger.warning("Unknown provider '%s' in provider_priority, skipping", provider_name)
            except ProviderError as e:
                logger.warning("Could not initialize provider '%s': %s", provider_name, e)

        if not available:
            raise ProviderError(
                "No providers could be initialized. Check that instaloader is "
                "installed and/or APIFY_API_TOKEN is set."
            )

        for p in available:
            status = "available" if p.is_available() else f"NOT available ({p.unavailable_reason()})" \
                if hasattr(p, "unavailable_reason") else "NOT available"
            logger.info("Provider cascade: %s -> %s", p.name, status)

        return ProviderCascade(available)

    def _init_instaloader_provider(self) -> InstaloaderProvider:
        mode = self.config["scraping"]["mode"]
        login_user = login_pass = session_file = None
        if mode in ("login", "hybrid"):
            login_cfg = self.config["login"]
            login_user = os.environ.get(login_cfg["username_env"])
            login_pass = os.environ.get(login_cfg["password_env"])
            session_file = login_cfg.get("session_file")
            if not login_user or not login_pass:
                logger.warning(
                    "mode=%s but %s/%s env vars not set - falling back to no-login.",
                    mode, login_cfg["username_env"], login_cfg["password_env"],
                )
        return InstaloaderProvider(
            self.provider_config, self.rate_limiter,
            login_username=login_user, login_password=login_pass, session_file=session_file,
        )

    def collect(self, username: str, resume: bool = True) -> InstagramIntelligenceProfile:
        username = username.lstrip("@").strip().lower()
        started_at = datetime.utcnow()
        errors: list[str] = []

        checkpoint = self.checkpoints.load(username) if resume else None
        if checkpoint:
            logger.info("Resuming %s from checkpoint (%d posts already collected)",
                        username, len(checkpoint.get("posts", [])))

        status = ScrapeStatus.SUCCESS
        providers_used: set[str] = set()
        try:
            profile = self._provider.fetch_profile(username)
            providers_used.add(self._provider.last_used_provider)
        except ProviderError as e:
            logger.error("Failed to fetch profile for %s: %s", username, e)
            raise  # profile is required; nothing useful to build without it

        try:
            posts = self._provider.fetch_posts(username, self.provider_config.posts_per_profile)
            providers_used.add(self._provider.last_used_provider)
        except ProviderError as e:
            logger.warning("Post scraping interrupted for %s: %s", username, e)
            errors.append(str(e))
            posts = checkpoint.get("posts", []) if checkpoint else []
            status = ScrapeStatus.PARTIAL if posts else ScrapeStatus.RATE_LIMITED
            self.checkpoints.save(username, {"posts": [p.model_dump(mode="json") for p in posts]})

        comment_sample = select_comment_sample(
            posts,
            self.provider_config.full_comment_sample_size,
            self.provider_config.comment_sample_strategy,
        )
        comments_done = 0
        for post in comment_sample:
            try:
                post.sample_comments = self._provider.fetch_comments(post.shortcode, limit=50)
                post.comments_sampled = True
                comments_done += 1
                providers_used.add(self._provider.last_used_provider)
            except ProviderError as e:
                logger.warning("Comment scraping failed for %s: %s", post.shortcode, e)
                errors.append(f"comments:{post.shortcode}: {e}")
                status = ScrapeStatus.PARTIAL
                break  # if we're rate-limited, stop trying further posts this run

        self.checkpoints.clear(username) if status == ScrapeStatus.SUCCESS else None
        provider_used_label = "+".join(sorted(providers_used)) if providers_used else "none"

        result = InstagramIntelligenceProfile(
            profile=profile,
            posts=posts,
            engagement=compute_engagement_stats(posts),
            media_analytics=compute_media_analytics(posts),
            marketing_intelligence=compute_marketing_intelligence(posts),
            hashtags=compute_hashtag_stats(posts),
            scrape_meta=ScrapeMeta(
                status=status,
                provider_used=provider_used_label,
                posts_requested=self.provider_config.posts_per_profile,
                posts_collected=len(posts),
                comments_sampled_posts=comments_done,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                errors=errors,
            ),
        )
        self._export(result)
        return result

    def _export(self, result: InstagramIntelligenceProfile) -> None:
        out_cfg = self.config["output"]
        base = Path(out_cfg["base_dir"]) / out_cfg["json_dir"]
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / f"{result.profile.username}.json"
        out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Wrote %s (status=%s, posts=%d)", out_path, result.scrape_meta.status.value,
                    result.scrape_meta.posts_collected)
