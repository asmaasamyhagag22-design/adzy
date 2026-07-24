"""
Marketing intelligence derivation - pure computation over already-scraped
posts. No network calls, no new scraping risk - this was sitting as an
empty default in every JSON output until now.

Every derived claim stays traceable to source posts (shortcodes), per this
project's evidence-gating principle, and `confidence` reflects sample size
honestly rather than presenting a 5-post sample with the same authority as
a 30-post one.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from models.schemas import DataConfidence, InstagramPost, MarketingIntelligence, TopPost


def _confidence_for_sample(n: int) -> DataConfidence:
    if n >= 30:
        return DataConfidence.HIGH
    if n >= 15:
        return DataConfidence.MEDIUM
    if n >= 5:
        return DataConfidence.LOW
    return DataConfidence.NONE


def compute_marketing_intelligence(posts: list[InstagramPost]) -> MarketingIntelligence:
    if not posts:
        return MarketingIntelligence()

    dated = [p for p in posts if p.posted_at is not None]
    confidence = _confidence_for_sample(len(posts))

    # --- Posting schedule ---
    most_active_weekday = most_active_hour = None
    if dated:
        weekday_counts = Counter(p.posted_at.strftime("%A") for p in dated)
        most_active_weekday = weekday_counts.most_common(1)[0][0]
        hour_counts = Counter(p.posted_at.hour for p in dated)
        most_active_hour = hour_counts.most_common(1)[0][0]

    # --- Most engaging content type ---
    most_engaging_content_type = None
    by_type: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        by_type[p.media.media_type.value].append(p.engagement)
    if by_type:
        avg_by_type = {t: statistics.mean(v) for t, v in by_type.items() if v}
        if avg_by_type:
            most_engaging_content_type = max(avg_by_type, key=avg_by_type.get)

    # --- Most engaging hashtag (only hashtags appearing on 2+ posts, to
    #     avoid a one-off post's engagement masquerading as a hashtag trend) ---
    most_engaging_hashtag = None
    hashtag_engagement: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        for h in p.hashtags:
            hashtag_engagement[h].append(p.engagement)
    qualifying = {h: v for h, v in hashtag_engagement.items() if len(v) >= 2}
    if qualifying:
        avg_hashtag_engagement = {h: statistics.mean(v) for h, v in qualifying.items()}
        most_engaging_hashtag = max(avg_hashtag_engagement, key=avg_hashtag_engagement.get)

    # --- Top / worst posts (by total engagement = likes + comments) ---
    ranked = sorted(posts, key=lambda p: p.engagement, reverse=True)
    top_n = max(1, len(ranked) // 5) if len(ranked) >= 5 else min(3, len(ranked))
    top_posts = [
        TopPost(shortcode=p.shortcode, engagement=p.engagement,
                 reason=f"Top {top_n} by likes+comments among {len(posts)} sampled posts")
        for p in ranked[:top_n]
    ]
    worst_posts = [
        TopPost(shortcode=p.shortcode, engagement=p.engagement,
                 reason=f"Bottom {top_n} by likes+comments among {len(posts)} sampled posts")
        for p in ranked[-top_n:]
    ] if len(ranked) > top_n else []

    # --- Consistency score: lower variance in gaps between posts = higher score ---
    consistency_score = None
    if len(dated) >= 3:
        sorted_dates = sorted((p.posted_at for p in dated))
        gaps_days = [
            (sorted_dates[i + 1] - sorted_dates[i]).total_seconds() / 86400
            for i in range(len(sorted_dates) - 1)
        ]
        if gaps_days and statistics.mean(gaps_days) > 0:
            coefficient_of_variation = (
                statistics.pstdev(gaps_days) / statistics.mean(gaps_days) if len(gaps_days) > 1 else 0
            )
            # Map CoV to a 0-100 score: 0 variation -> 100, high variation -> approaches 0.
            consistency_score = round(max(0.0, 100 / (1 + coefficient_of_variation)), 1)

    # --- Brand activity score: simple 0-100 blend of posting frequency and consistency ---
    brand_activity_score = None
    if dated:
        span_days = max((max(p.posted_at for p in dated) - min(p.posted_at for p in dated)).days, 1)
        posts_per_week = len(dated) / (span_days / 7)
        # Normalize against a rough "very active" benchmark of 7 posts/week -> caps at 100
        frequency_component = min(100.0, (posts_per_week / 7) * 100)
        if consistency_score is not None:
            brand_activity_score = round((frequency_component * 0.6) + (consistency_score * 0.4), 1)
        else:
            brand_activity_score = round(frequency_component, 1)

    return MarketingIntelligence(
        most_active_weekday=most_active_weekday,
        most_active_hour=most_active_hour,
        most_engaging_content_type=most_engaging_content_type,
        most_engaging_topic=None,  # requires NLP topic clustering - Phase 2, not fabricated here
        most_engaging_hashtag=most_engaging_hashtag,
        top_posts=top_posts,
        worst_posts=worst_posts,
        consistency_score=consistency_score,
        brand_activity_score=brand_activity_score,
        confidence=confidence,
    )


def compute_hashtag_stats(posts: list[InstagramPost]) -> dict:
    """Top-level hashtag frequency + average engagement, for the profile's `hashtags` field."""
    if not posts:
        return {}

    freq = Counter(h for p in posts for h in p.hashtags)
    if not freq:
        return {}

    engagement_by_tag: dict[str, list[int]] = defaultdict(list)
    for p in posts:
        for h in p.hashtags:
            engagement_by_tag[h].append(p.engagement)

    return {
        "top_hashtags_by_frequency": [
            {"hashtag": tag, "count": count} for tag, count in freq.most_common(10)
        ],
        "top_hashtags_by_avg_engagement": [
            {"hashtag": tag, "avg_engagement": round(statistics.mean(vals), 1), "post_count": len(vals)}
            for tag, vals in sorted(
                engagement_by_tag.items(), key=lambda kv: statistics.mean(kv[1]), reverse=True
            )[:10]
        ],
        "total_unique_hashtags": len(freq),
    }
