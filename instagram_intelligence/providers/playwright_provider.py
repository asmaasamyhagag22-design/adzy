"""
PlaywrightProvider - free, browser-based fallback (no login, no third party).

Strategy: rather than guessing Instagram's current internal API shape and
calling it directly (which is exactly what keeps breaking Instaloader every
time IG tweaks a doc_id), this loads the public profile page in a real
headless browser and passively intercepts whatever JSON responses the page
itself fetches while rendering - the same data the browser needs to draw
the page. We then recursively search those captured JSON blobs for
dict shapes that look like a profile node or a post node, and defensively
extract known field names. Unknown/missing fields stay None - never
fabricated - consistent with the rest of this project's discipline.

HONEST LIMITATION: this still makes anonymous requests from the same
network/IP as InstaloaderProvider. If Instagram's current throttling is
IP-based rather than client-fingerprint-based, this will hit the same wall.
It's a genuinely different technique (real browser TLS/JS fingerprint), not
a guaranteed workaround - treat it as another free thing to try, not a fix.

Setup required (one-time, not pip-installable alone):
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _deep_find_dicts(obj: Any) -> Iterator[dict]:
    """Recursively yield every dict found anywhere inside a nested JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _deep_find_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _deep_find_dicts(item)


def _find_profile_node(blobs: list[dict]) -> dict | None:
    for blob in blobs:
        for d in _deep_find_dicts(blob):
            if "biography" in d and ("edge_followed_by" in d or "follower_count" in d):
                return d
    return None


def _find_post_nodes(blobs: list[dict], limit: int, expected_username: str) -> list[dict]:
    """
    Matches real post nodes, not lightweight thumbnail/suggested-content
    stubs that happen to share a `shortcode` + `display_url` shape.

    Three checks beyond the basic shape match, all learned from real
    contamination found in testing:
      - Requires a `taken_at_timestamp`/`taken_at` - lightweight prefetch
        stubs had a shortcode + one image URL and nothing else.
      - Requires an `owner` field that matches the profile being scraped.
      - Critically: a node with NO `owner` field at all is REJECTED, not
        waved through. Real testing surfaced a cluster of high-engagement
        outlier posts (10-100x the surrounding posts' likes) sitting in an
        otherwise-unexplained multi-year date gap - almost certainly
        algorithmic "suggested content" from other accounts, which lacks
        ownership metadata entirely and was silently passing a looser
        "reject only if owner present AND mismatched" check. Better to
        drop a node we can't verify than risk misattributing someone
        else's viral post to this brand's history.
    """
    seen: set[str] = set()
    posts: list[dict] = []
    skipped_wrong_owner = 0
    skipped_no_owner = 0
    skipped_no_timestamp = 0

    for blob in blobs:
        for d in _deep_find_dicts(blob):
            shortcode = d.get("shortcode") or d.get("code")
            if not shortcode or shortcode in seen:
                continue
            if not ("display_url" in d or "video_url" in d or "is_video" in d):
                continue

            owner = d.get("owner")
            if not isinstance(owner, dict) or not owner.get("username"):
                skipped_no_owner += 1
                continue
            if owner["username"].lower() != expected_username.lower():
                skipped_wrong_owner += 1
                continue

            if not (d.get("taken_at_timestamp") or d.get("taken_at")):
                skipped_no_timestamp += 1
                continue

            seen.add(shortcode)
            posts.append(d)
            if len(posts) >= limit:
                break
        if len(posts) >= limit:
            break

    if skipped_wrong_owner or skipped_no_owner or skipped_no_timestamp:
        logger.info(
            "Post extraction filtered out %d no-owner, %d wrong-owner, %d no-timestamp "
            "candidate nodes (likely suggested-content/algorithmic recommendations, not real posts)",
            skipped_no_owner, skipped_wrong_owner, skipped_no_timestamp,
        )
    return posts


def _find_comment_nodes(blobs: list[dict], limit: int) -> list[dict]:
    """
    Matches comment-like nodes across the several JSON shapes Instagram's
    comment endpoints have used (GraphQL edge style vs newer api/v1 style).
    Broadened after real testing showed the original text+owner.username-only
    match returned zero comments across every sampled post - too narrow a
    shape assumption, not actually "no comments on this post".
    """
    comments = []
    seen_texts: set[tuple] = set()
    for blob in blobs:
        for d in _deep_find_dicts(blob):
            text = d.get("text") or d.get("comment_text")
            if not text or not isinstance(text, str):
                continue
            owner = d.get("owner") or d.get("user")
            username = owner.get("username") if isinstance(owner, dict) else None
            if not username:
                continue  # can't attribute this comment to anyone - skip rather than guess
            dedup_key = (username, text[:100])
            if dedup_key in seen_texts:
                continue
            seen_texts.add(dedup_key)
            comments.append(d)
            if len(comments) >= limit:
                return comments
    return comments


def _extract_caption(node: dict) -> str | None:
    caption = node.get("caption")
    if isinstance(caption, str):
        return caption or None
    if isinstance(caption, dict):
        return caption.get("text") or None
    edge_caption = node.get("edge_media_to_caption", {})
    edges = edge_caption.get("edges") if isinstance(edge_caption, dict) else None
    if edges:
        return edges[0].get("node", {}).get("text") or None
    return None


def _extract_count(node: dict, *edge_keys_then_direct: str) -> int | None:
    """Try edge-style ({'edge_x': {'count': N}}) then direct keys, in order given."""
    for key in edge_keys_then_direct:
        val = node.get(key)
        if isinstance(val, dict) and "count" in val:
            return val["count"]
        if isinstance(val, int):
            return val
    return None


def _extract_timestamp(node: dict) -> datetime | None:
    ts = node.get("taken_at_timestamp") or node.get("taken_at")
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _extract_hashtags_mentions(caption: str | None) -> tuple[list[str], list[str]]:
    if not caption:
        return [], []
    hashtags = re.findall(r"#(\w+)", caption, flags=re.UNICODE)
    mentions = re.findall(r"@([\w.]+)", caption, flags=re.UNICODE)
    return hashtags, mentions


def _safe_filename(url: str) -> str:
    return re.sub(r"[^\w.-]", "_", url.replace("https://", ""))[:100]


class PlaywrightProvider(BaseInstagramProvider):
    name = "playwright"

    def __init__(
        self, config, rate_limiter, headless: bool = True,
        debug_dir: str = "./logs/playwright_debug",
        login_username: str | None = None,
        login_password: str | None = None,
        storage_state_path: str | None = None,
    ):
        super().__init__(config, rate_limiter)
        self.headless = headless
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.login_username = login_username
        self.login_password = login_password
        self.storage_state_path = Path(storage_state_path) if storage_state_path else None
        if self.storage_state_path:
            self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    def is_available(self) -> bool:
        return PLAYWRIGHT_AVAILABLE

    def unavailable_reason(self) -> str | None:
        if not PLAYWRIGHT_AVAILABLE:
            return "playwright not installed (`pip install playwright && playwright install chromium`)"
        return None

    def _ensure_logged_in_session(self) -> None:
        """
        Runs once: if we have credentials but no saved session yet, performs
        a real (non-headless) login and saves the resulting cookies/storage
        to disk. Every subsequent call reuses that saved session - no
        repeated logins, which is itself a pattern that draws scrutiny.

        Deliberately non-headless for this one step: Instagram's login flow
        is the most sensitive point of contact, and if it throws a
        checkpoint/2FA prompt, a visible window lets you solve it by hand
        instead of silently failing.
        """
        if not (self.login_username and self.login_password and self.storage_state_path):
            logger.info(
                "Skipping login - credentials not configured (login_username=%s, "
                "login_password=%s, storage_state_path=%s). Running anonymous.",
                bool(self.login_username), bool(self.login_password), bool(self.storage_state_path),
            )
            return
        if self.storage_state_path.exists():
            logger.info(
                "Reusing existing saved session at %s (delete this file to force a fresh login)",
                self.storage_state_path,
            )
            return  # already logged in from a previous run

        logger.info("No saved Playwright session found - logging in once (a browser window will open)...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                page.goto("https://www.instagram.com/accounts/login/", wait_until="networkidle", timeout=30000)
                self._dismiss_overlays(page)

                username_selectors = ['input[name="username"]', 'input[aria-label*="username" i]',
                                       'input[aria-label*="phone" i]', 'input[type="text"]']
                password_selectors = ['input[name="password"]', 'input[type="password"]']

                username_field = None
                for attempt in range(5):  # poll for up to ~10s across frames, not just one page-level check
                    username_field = self._find_field_across_frames(page, username_selectors)
                    if username_field:
                        break
                    page.wait_for_timeout(2000)

                if not username_field:
                    debug_path = self.debug_dir / f"login_page_blocked_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                    raise ProviderError(
                        f"Login page didn't show a findable username field after searching all "
                        f"frames for ~10s - something is blocking it, or Instagram's login form "
                        f"structure has changed beyond the selectors tried. Screenshot saved to "
                        f"{debug_path} - check it to see what's actually on screen."
                    )

                password_field = self._find_field_across_frames(page, password_selectors)
                if not password_field:
                    debug_path = self.debug_dir / f"login_no_password_field_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                    raise ProviderError(
                        f"Found the username field but no password field. Screenshot saved to {debug_path}."
                    )

                username_field.fill(self.login_username)
                password_field.fill(self.login_password)
                submit_button = self._find_field_across_frames(page, ['button[type="submit"]'])
                if submit_button:
                    submit_button.click()
                else:
                    password_field.press("Enter")  # fallback if the submit button selector doesn't match

                # Give plenty of time for a checkpoint/2FA prompt to appear
                # and, if needed, for YOU to solve it manually in the window.
                page.wait_for_timeout(15000)
                self._dismiss_overlays(page)

                if self._find_field_across_frames(page, username_selectors):
                    debug_path = self.debug_dir / f"login_still_on_form_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                    raise ProviderError(
                        f"Still on the login form after submitting - login likely failed "
                        f"(wrong credentials, or a checkpoint/2FA challenge needs solving "
                        f"manually). Screenshot saved to {debug_path}. Re-run when you can "
                        f"watch the browser window and solve any prompt yourself."
                    )

                context.storage_state(path=str(self.storage_state_path))
                logger.info("Login successful - session saved to %s for future runs.", self.storage_state_path)
            finally:
                browser.close()

    def _find_field_across_frames(self, page, selectors: list[str]):
        """
        Searches the main page AND every iframe for a matching input.
        Added after real testing showed the login field fully visible in a
        screenshot (even pre-filled by autofill) while page.wait_for_selector
        still timed out - the strongest explanation is the form renders
        inside an iframe, which page-level selectors don't search by default.
        """
        for frame in [page.main_frame] + page.main_frame.child_frames:
            for sel in selectors:
                try:
                    locator = frame.locator(sel).first
                    if locator.count() > 0:
                        return locator
                except Exception:
                    continue
        return None

    def _dismiss_overlays(self, page) -> None:
        """
        Best-effort dismissal of Instagram's various interstitial popups
        (cookie consent, 'Save Your Login Info', 'Turn on Notifications',
        and the 'Sign up to see more' modal seen blocking a post's comments
        in real testing). Every click is wrapped so a missing/changed
        selector never breaks the actual scrape - this is pure best-effort.
        """
        selectors = [
            'button:has-text("Not Now")',
            'button:has-text("Not now")',
            'button:has-text("Allow all cookies")',
            'button:has-text("Decline optional cookies")',
            'button:has-text("Close")',
            # Arabic equivalents - Instagram may serve a localized UI based on
            # IP geolocation, and this project's accounts are Egypt-based, so
            # English-only text selectors could silently match nothing.
            'button:has-text("ليس الآن")',
            'button:has-text("السماح بجميع ملفات تعريف الارتباط")',
            'button:has-text("رفض")',
            'button:has-text("إغلاق")',
            'div[role="dialog"] svg[aria-label="Close"]',
            'div[role="dialog"] [aria-label="إغلاق"]',
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if locator.count() > 0 and locator.is_visible():
                    locator.click(timeout=2000)
                    page.wait_for_timeout(500)
            except Exception:
                continue  # selector not present or not clickable right now - fine

    def _expand_comments(self, page) -> None:
        """
        Instagram's post page shows only 1-2 preview comments by default -
        the rest load via an API call triggered ONLY by clicking something
        like "View all N comments". Without this click, that API call never
        fires at all, regardless of login state - this is the most likely
        real explanation for consistent 0-JSON-blobs-captured results on
        every single comment fetch attempt so far.
        """
        selectors = [
            'text=/View all \\d+ comments/i',
            'text=/Load more comments/i',
            'text=/عرض كل التعليقات/',
            'text=/عرض جميع التعليقات/',
            'text=/تحميل المزيد من التعليقات/',
            'button:has-text("View all")',
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if locator.count() > 0 and locator.is_visible():
                    locator.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    return  # found and clicked one - that's enough to trigger the load
            except Exception:
                continue

    def _capture_json_responses(self, url: str, scroll_rounds: int = 0, expand_comments: bool = False) -> list[dict]:
        if not PLAYWRIGHT_AVAILABLE:
            raise ProviderError("Playwright is not installed.")

        self._ensure_logged_in_session()
        self.rate_limiter.wait_before_request()
        blobs: list[dict] = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context_kwargs = {"user_agent": USER_AGENT, "viewport": {"width": 1280, "height": 900}}
                if self.storage_state_path and self.storage_state_path.exists():
                    context_kwargs["storage_state"] = str(self.storage_state_path)
                context = browser.new_context(**context_kwargs)
                page = context.new_page()

                def on_response(response):
                    try:
                        ctype = response.headers.get("content-type", "")
                        if "application/json" in ctype and response.status == 200:
                            blobs.append(response.json())
                    except Exception:
                        pass  # non-JSON or unreadable response - skip, don't fail the run

                page.on("response", on_response)
                page.goto(url, wait_until="networkidle", timeout=30000)
                self._dismiss_overlays(page)
                if expand_comments:
                    self._expand_comments(page)

                # Always capture what the page actually looked like right
                # after load - this is the single most useful debugging
                # artifact when extraction fails downstream: was it a real
                # profile page, a login wall, a cookie-consent banner, or a
                # block/challenge page? Failures without this were pure
                # guesswork.
                try:
                    debug_path = self.debug_dir / f"{_safe_filename(url)}_{int(time.time())}.png"
                    page.screenshot(path=str(debug_path))
                    logger.info("Playwright debug screenshot saved: %s", debug_path)
                except Exception:
                    logger.debug("Could not save debug screenshot (non-fatal)", exc_info=True)

                pre_scroll_blob_count = len(blobs)

                for i in range(scroll_rounds):
                    # window.mouse.wheel alone can miss the mark if Instagram's
                    # grid isn't scrolled via plain body/window scroll (common
                    # in modern SPAs using an inner scroll container) - combine
                    # a direct DOM scroll with a synthetic wheel event so
                    # whichever mechanism the page actually listens to fires.
                    page.evaluate("window.scrollBy(0, 3000)")
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(3000)
                    self._dismiss_overlays(page)

                if scroll_rounds > 0:
                    new_from_scrolling = len(blobs) - pre_scroll_blob_count
                    logger.info(
                        "Scrolling (%d rounds) produced %d additional JSON response(s) "
                        "beyond the initial page load (%d before -> %d after). Zero here "
                        "means scrolling isn't actually triggering more content to load.",
                        scroll_rounds, new_from_scrolling, pre_scroll_blob_count, len(blobs),
                    )

                # Logged-in pages often embed data directly in the initial
                # HTML (a <script type="application/json"> block) rather
                # than fetching it via a separate XHR call - real testing
                # showed only 1 lightweight status-check blob captured via
                # response interception once authenticated, strongly
                # suggesting the actual profile/post data was server-embedded
                # and simply never passed through on_response at all.
                try:
                    embedded_scripts = page.eval_on_selector_all(
                        'script[type="application/json"]',
                        "elements => elements.map(el => el.textContent)",
                    )
                    for text in embedded_scripts:
                        try:
                            blobs.append(json.loads(text))
                        except (json.JSONDecodeError, TypeError):
                            continue
                    if embedded_scripts:
                        logger.info(
                            "Captured %d embedded <script type=application/json> block(s) "
                            "in addition to %d XHR response(s)",
                            len(embedded_scripts), len(blobs) - len(embedded_scripts),
                        )
                except Exception:
                    logger.debug("Could not extract embedded script JSON (non-fatal)", exc_info=True)

                # Refresh the saved session's cookies after a successful
                # authenticated visit - keeps the session alive longer.
                if self.storage_state_path and self.storage_state_path.exists():
                    try:
                        context.storage_state(path=str(self.storage_state_path))
                    except Exception:
                        pass

                browser.close()
        except PlaywrightTimeoutError as e:
            if not blobs:
                raise ProviderError(f"Playwright timed out loading {url} with no data captured: {e}") from e
            logger.warning("Playwright timed out on %s but captured %d responses first", url, len(blobs))
        except Exception as e:
            raise ProviderError(f"Playwright error loading {url}: {e}") from e

        self.rate_limiter.reset_backoff()
        return blobs

    def fetch_profile(self, username: str) -> InstagramProfile:
        blobs = self._capture_json_responses(f"https://www.instagram.com/{username}/")
        node = _find_profile_node(blobs)
        if not node:
            blob_summary = [
                sorted(b.keys())[:8] if isinstance(b, dict) else type(b).__name__
                for b in blobs
            ]
            logger.warning(
                "Profile extraction failed for %s - captured %d JSON blob(s), "
                "top-level keys (first 8 each): %s. Check the debug screenshot "
                "logged above to see what the page actually rendered.",
                username, len(blobs), blob_summary,
            )
            raise ProviderError(
                f"Playwright loaded the page for {username} but no recognizable profile "
                "JSON was captured - Instagram may have changed its page structure, or "
                "the page may have shown a login wall instead of the profile. Check the "
                "debug screenshot path logged just above this error."
            )

        return InstagramProfile(
            username=node.get("username", username),
            full_name=node.get("full_name") or None,
            biography=node.get("biography") or None,
            website=node.get("external_url") or None,
            category=node.get("category_name") or node.get("business_category_name"),
            followers=_extract_count(node, "edge_followed_by", "follower_count"),
            following=_extract_count(node, "edge_follow", "following_count"),
            post_count=_extract_count(node, "edge_owner_to_timeline_media", "media_count"),
            is_verified=node.get("is_verified"),
            is_business_account=node.get("is_business_account"),
            profile_image_url=node.get("profile_pic_url_hd") or node.get("profile_pic_url"),
            business_email=node.get("business_email") or None,
            business_phone=node.get("business_phone_number") or None,
            business_address=node.get("business_address_json") or None,
        )

    def fetch_posts(self, username: str, limit: int) -> list[InstagramPost]:
        # A handful of scroll rounds to trigger lazy-loaded pagination requests.
        scroll_rounds = min(10, max(1, limit // 12))
        blobs = self._capture_json_responses(f"https://www.instagram.com/{username}/", scroll_rounds=scroll_rounds)
        nodes = _find_post_nodes(blobs, limit, expected_username=username)
        if not nodes:
            raise ProviderError(f"No post data captured for {username} via Playwright.")
        posts = [self._convert_post(n) for n in nodes]
        # Capture order isn't guaranteed chronological (initial load vs.
        # scroll-triggered pagination can interleave) - sort explicitly so
        # downstream consumers can rely on posts[0] being the most recent.
        posts.sort(key=lambda p: p.posted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return posts

    def _convert_post(self, node: dict) -> InstagramPost:
        caption = _extract_caption(node)
        hashtags, mentions = _extract_hashtags_mentions(caption)
        children = node.get("edge_sidecar_to_children", {}).get("edges", [])

        if children:
            media_type = MediaType.CAROUSEL
            image_urls, video_urls = [], []
            for edge in children:
                child = edge.get("node", {})
                if child.get("is_video"):
                    video_urls.append(child.get("video_url"))
                else:
                    image_urls.append(child.get("display_url"))
            carousel_count = len(children)
        elif node.get("is_video"):
            media_type = MediaType.VIDEO
            video_urls = [node.get("video_url")] if node.get("video_url") else []
            image_urls = []
            carousel_count = None
        else:
            media_type = MediaType.IMAGE
            image_urls = [node.get("display_url")] if node.get("display_url") else []
            video_urls = []
            carousel_count = None

        shortcode = node.get("shortcode") or node.get("code")
        return InstagramPost(
            shortcode=shortcode,
            url=f"https://www.instagram.com/p/{shortcode}/",
            caption=caption,
            posted_at=_extract_timestamp(node),
            likes=_extract_count(node, "edge_media_preview_like", "edge_liked_by", "like_count"),
            comments_count=_extract_count(node, "edge_media_to_comment", "edge_media_to_parent_comment", "comment_count"),
            media=PostMedia(
                media_type=media_type,
                image_urls=[u for u in image_urls if u],
                video_urls=[u for u in video_urls if u],
                carousel_item_count=carousel_count,
            ),
            hashtags=hashtags,
            mentions=mentions,
            location_name=(node.get("location") or {}).get("name"),
            tagged_accounts=[
                TaggedAccount(username=t.get("node", {}).get("user", {}).get("username", ""))
                for t in node.get("edge_media_to_tagged_user", {}).get("edges", [])
                if t.get("node", {}).get("user", {}).get("username")
            ],
            is_pinned=None,
        )

    def fetch_comments(self, shortcode: str, limit: int) -> list[InstagramComment]:
        blobs = self._capture_json_responses(
            f"https://www.instagram.com/p/{shortcode}/", scroll_rounds=3, expand_comments=True
        )
        nodes = _find_comment_nodes(blobs, limit)
        logger.info(
            "Comment extraction for %s: %d JSON blobs captured, %d comments matched",
            shortcode, len(blobs), len(nodes),
        )
        result = []
        for n in nodes:
            owner = n.get("owner") or n.get("user") or {}
            result.append(
                InstagramComment(
                    author_username=owner.get("username"),
                    text=n.get("text") or n.get("comment_text") or "",
                    posted_at=_extract_timestamp(n) or _extract_timestamp({"taken_at": n.get("created_at")}),
                    like_count=_extract_count(n, "edge_liked_by", "comment_like_count"),
                )
            )
        return result
