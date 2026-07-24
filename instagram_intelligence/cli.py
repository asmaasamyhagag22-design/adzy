"""
Works identically for any public Instagram business account - no industry,
region, or business-type assumptions anywhere in this pipeline.

Usage:
    python cli.py <any_business_username>
    python cli.py <any_business_username> <competitor_username> <competitor_username> ...

Examples (illustrative only - any public business account works the same way):
    python cli.py nike
    python cli.py starbucks
    python cli.py some_local_pharmacy_in_cairo
"""
from __future__ import annotations

import sys

from collector import InstagramCollector


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python cli.py <username> [<username2> ...]")
        sys.exit(1)

    usernames = sys.argv[1:]
    collector = InstagramCollector(config_path="config/config.yaml")

    for username in usernames:
        print(f"\n=== Collecting @{username} ===")
        try:
            result = collector.collect(username)
            print(f"  status: {result.scrape_meta.status.value}")
            print(f"  posts collected: {result.scrape_meta.posts_collected}")
            print(f"  avg likes: {result.engagement.avg_likes}")
        except Exception as e:
            print(f"  FAILED: {e}")


if __name__ == "__main__":
    main()
