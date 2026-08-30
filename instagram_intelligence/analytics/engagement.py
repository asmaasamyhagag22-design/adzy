"""
Engagement + media analytics. Pure functions over InstagramPost lists -
no network calls, no fabrication: every stat reports its sample_size, and
an empty post list yields all-None stats rather than zeros (zero would
falsely imply "measured and found to be zero").
"""
from __future__ import annotations

import statistics
from collections import Counter

from models.schemas import EngagementStats, InstagramPost, MediaAnalytics, MediaType


def compute_engagement_stats(posts: list[InstagramPost]) -> EngagementStats:
    if not posts:
        return EngagementStats(sample_size=0)

    likes = [p.likes for p in posts if p.likes is not None]
    comments = [p.comments_count for p in posts if p.comments_count is not None]
    caption_lens = [len(p.caption) for p in posts if p.caption]
    hashtag_counts = [len(p.hashtags) for p in posts]
    mention_counts = [len(p.mentions) for p in posts]

    dated = [p for p in posts if p.posted_at is not None]
    posts_per_week = posts_per_month = None
    if len(dated) >= 2:
        span_days = (max(p.posted_at for p in dated) - min(p.posted_at for p in dated)).days
        if span_days > 0:
            posts_per_week = len(dated) / (span_days / 7)
            posts_per_month = len(dated) / (span_days / 30)

    media_types = Counter(p.media.media_type for p in posts)
    total = len(posts)

    return EngagementStats(
        avg_likes=statistics.mean(likes) if likes else None,
        avg_comments=statistics.mean(comments) if comments else None,
        median_likes=statistics.median(likes) if likes else None,
        median_comments=statistics.median(comments) if comments else None,
        posts_per_week=posts_per_week,
        posts_per_month=posts_per_month,
        avg_caption_length=statistics.mean(caption_lens) if caption_lens else None,
        avg_hashtags_per_post=statistics.mean(hashtag_counts) if hashtag_counts else None,
        avg_mentions_per_post=statistics.mean(mention_counts) if mention_counts else None,
        video_ratio=media_types.get(MediaType.VIDEO, 0) / total,
        carousel_ratio=media_types.get(MediaType.CAROUSEL, 0) / total,
        image_ratio=media_types.get(MediaType.IMAGE, 0) / total,
        sample_size=total,
    )


def compute_media_analytics(posts: list[InstagramPost]) -> MediaAnalytics:
    if not posts:
        return MediaAnalytics()

    media_types = Counter(p.media.media_type for p in posts)
    carousel_posts = [p for p in posts if p.media.media_type == MediaType.CAROUSEL]
    carousel_sizes = [p.media.carousel_item_count for p in carousel_posts if p.media.carousel_item_count]
    total = len(posts)

    return MediaAnalytics(
        image_count=media_types.get(MediaType.IMAGE, 0),
        video_count=media_types.get(MediaType.VIDEO, 0),
        carousel_count=media_types.get(MediaType.CAROUSEL, 0),
        avg_images_per_carousel=(sum(carousel_sizes) / len(carousel_sizes)) if carousel_sizes else None,
        media_type_distribution={mt.value: count / total for mt, count in media_types.items()},
    )


def select_comment_sample(posts: list[InstagramPost], sample_size: int, strategy: str) -> list[InstagramPost]:
    """Pick which posts get full comment scraping - see config: comment_sample_strategy."""
    if strategy == "all":
        return posts[:sample_size]
    if strategy == "recent":
        dated = sorted((p for p in posts if p.posted_at), key=lambda p: p.posted_at, reverse=True)
        return dated[:sample_size]
    # default: top_engagement
    return sorted(posts, key=lambda p: p.engagement, reverse=True)[:sample_size]
