"""
Core data models for the Instagram Intelligence Collector.

Design principles (per project no-fabrication policy):
- Fields we couldn't observe are `None`, never a placeholder string or 0.
- `Optional[bool]` is used wherever "unknown" must be distinguishable from
  "false" (e.g. `is_verified`, `is_business_account`) - mirrors the
  UNKNOWN vs FALSE discipline used in the competitive SWOT engine.
- Every model is a Pydantic v2 BaseModel so it serializes cleanly to the
  final JSON schema and validates on the way in.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #

class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"
    REEL = "reel"
    UNKNOWN = "unknown"


class ScrapeStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"       # e.g. profile ok, posts truncated by rate limit
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


class DataConfidence(str, Enum):
    """Mirrors the HIGH/MEDIUM/LOW/NONE tiering used elsewhere in the project."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #

class InstagramProfile(BaseModel):
    username: str
    full_name: Optional[str] = None
    biography: Optional[str] = None
    website: Optional[str] = None
    external_urls: list[str] = Field(default_factory=list)
    category: Optional[str] = None            # IG's own self-reported category, if any
    followers: Optional[int] = None
    following: Optional[int] = None
    post_count: Optional[int] = None
    is_verified: Optional[bool] = None
    is_business_account: Optional[bool] = None
    profile_image_url: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_address: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("username")
    @classmethod
    def strip_at(cls, v: str) -> str:
        return v.lstrip("@").strip().lower()


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

class PostMedia(BaseModel):
    media_type: MediaType
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    carousel_item_count: Optional[int] = None


class TaggedAccount(BaseModel):
    username: str
    full_name: Optional[str] = None


class InstagramPost(BaseModel):
    shortcode: str
    url: str
    caption: Optional[str] = None
    posted_at: Optional[datetime] = None
    likes: Optional[int] = None
    comments_count: Optional[int] = None
    media: PostMedia
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    location_name: Optional[str] = None
    tagged_accounts: list[TaggedAccount] = Field(default_factory=list)
    is_pinned: Optional[bool] = None

    # Populated only for the comment-sample subset (see config: full_comment_sample_size)
    comments_sampled: bool = False
    sample_comments: list["InstagramComment"] = Field(default_factory=list)

    @property
    def engagement(self) -> int:
        return (self.likes or 0) + (self.comments_count or 0)


class InstagramComment(BaseModel):
    author_username: Optional[str] = None
    text: str
    posted_at: Optional[datetime] = None
    like_count: Optional[int] = None


InstagramPost.model_rebuild()


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #

class EngagementStats(BaseModel):
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    median_likes: Optional[float] = None
    median_comments: Optional[float] = None
    posts_per_week: Optional[float] = None
    posts_per_month: Optional[float] = None
    avg_caption_length: Optional[float] = None
    avg_hashtags_per_post: Optional[float] = None
    avg_mentions_per_post: Optional[float] = None
    video_ratio: Optional[float] = None
    carousel_ratio: Optional[float] = None
    image_ratio: Optional[float] = None
    sample_size: int = 0   # how many posts these stats are derived from - always report this


class MediaAnalytics(BaseModel):
    image_count: int = 0
    video_count: int = 0
    carousel_count: int = 0
    avg_images_per_carousel: Optional[float] = None
    media_type_distribution: dict[str, float] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# NLP outputs
# --------------------------------------------------------------------------- #

class EvidenceRef(BaseModel):
    """Every NLP/marketing-intelligence claim must point back to source posts."""
    post_shortcodes: list[str] = Field(default_factory=list)
    note: Optional[str] = None


class DetectedPromotion(BaseModel):
    promotion_type: str
    confidence: float
    evidence: EvidenceRef


class DetectedCTA(BaseModel):
    cta_type: str
    confidence: float
    evidence: EvidenceRef


class TopicCluster(BaseModel):
    label: str                     # derived label, not hardcoded
    keywords: list[str]
    post_shortcodes: list[str]
    size: int


class SentimentBreakdown(BaseModel):
    positive_pct: Optional[float] = None
    neutral_pct: Optional[float] = None
    negative_pct: Optional[float] = None
    sample_size: int = 0
    common_complaints: list[str] = Field(default_factory=list)
    common_praise: list[str] = Field(default_factory=list)
    frequent_questions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Marketing intelligence (derived, evidence-gated)
# --------------------------------------------------------------------------- #

class TopPost(BaseModel):
    shortcode: str
    engagement: int
    reason: str   # e.g. "top decile by likes+comments among sampled posts"


class MarketingIntelligence(BaseModel):
    most_active_weekday: Optional[str] = None
    most_active_hour: Optional[int] = None
    most_engaging_content_type: Optional[str] = None
    most_engaging_topic: Optional[str] = None
    most_engaging_hashtag: Optional[str] = None
    top_posts: list[TopPost] = Field(default_factory=list)
    worst_posts: list[TopPost] = Field(default_factory=list)
    consistency_score: Optional[float] = None   # 0-100, based on posting regularity
    brand_activity_score: Optional[float] = None
    confidence: DataConfidence = DataConfidence.NONE


# --------------------------------------------------------------------------- #
# Top-level profile intelligence output
# --------------------------------------------------------------------------- #

class ScrapeMeta(BaseModel):
    status: ScrapeStatus
    provider_used: str
    posts_requested: int
    posts_collected: int
    comments_sampled_posts: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    errors: list[str] = Field(default_factory=list)


class InstagramIntelligenceProfile(BaseModel):
    """The complete per-account output, matching the JSON schema in the spec."""
    profile: InstagramProfile
    posts: list[InstagramPost] = Field(default_factory=list)
    engagement: EngagementStats = Field(default_factory=EngagementStats)
    media_analytics: MediaAnalytics = Field(default_factory=MediaAnalytics)
    hashtags: dict = Field(default_factory=dict)
    topics: list[TopicCluster] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    campaigns: list[str] = Field(default_factory=list)
    promotions: list[DetectedPromotion] = Field(default_factory=list)
    ctas: list[DetectedCTA] = Field(default_factory=list)
    mentions: dict = Field(default_factory=dict)
    sentiment: SentimentBreakdown = Field(default_factory=SentimentBreakdown)
    marketing_intelligence: MarketingIntelligence = Field(default_factory=MarketingIntelligence)
    scrape_meta: ScrapeMeta
