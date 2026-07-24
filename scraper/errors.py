"""Fixed error taxonomy for the scraper.

Every failure recorded in the manifest MUST use one of these codes.
This makes failures filterable, comparable across runs, and easier
to debug than free-form error strings.
"""
from enum import Enum


class ErrorCode(str, Enum):
    # Network / transport
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"  # DNS, SSL, connection reset, etc.
    HTTP_ERROR = "HTTP_ERROR"  # 4xx / 5xx — record status in message

    # Policy
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    BLOCKED_URL = "BLOCKED_URL"  # SSRF guard: target resolved to a non-public (internal/loopback/metadata) IP

    # Anti-bot
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    BOT_PROTECTION = "BOT_PROTECTION"  # Cloudflare interstitial, etc.

    # Content
    EMPTY_RENDERED_DOM = "EMPTY_RENDERED_DOM"
    NO_INTERNAL_LINKS_FOUND = "NO_INTERNAL_LINKS_FOUND"

    # Rendering
    SCREENSHOT_FAILED = "SCREENSHOT_FAILED"
    RENDER_ERROR = "RENDER_ERROR"

    # Pipeline
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
