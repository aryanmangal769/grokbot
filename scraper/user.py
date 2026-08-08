"""Scrape public X user activity (posts / replies / reposts / quotes).

Likes of *other* users are private on X and are not returned by the API.
This tool only collects what is publicly available on a user's timeline.

Usage (from repo root):
  python -m scraper.user elonmusk
  python -m scraper.user elonmusk --max-pages 5 -o outputs/elon.json
  python -m scraper.user elonmusk --include-replies
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.env import REPO_ROOT
from twitter.client import bearer_get, get_user_by_username

TWEET_FIELDS = (
    "created_at,lang,public_metrics,conversation_id,"
    "in_reply_to_user_id,referenced_tweets,author_id"
)


def classify_post(post: dict[str, Any]) -> str:
    refs = post.get("referenced_tweets") or []
    types = {r.get("type") for r in refs}
    if "retweeted" in types:
        return "repost"
    if "quoted" in types:
        return "quote"
    if "replied_to" in types or post.get("in_reply_to_user_id"):
        return "reply"
    return "post"


def fetch_user_timeline(
    username: str,
    *,
    max_pages: int = 3,
    max_results: int = 100,
    include_replies: bool = False,
) -> dict[str, Any]:
    """Paginate GET /2/users/:id/tweets for a public account."""
    profile = get_user_by_username(username)["data"]
    user_id = profile["id"]

    params: dict[str, Any] = {
        "max_results": max(5, min(100, max_results)),
        "tweet.fields": TWEET_FIELDS,
    }
    if not include_replies:
        params["exclude"] = "replies"

    posts: list[dict[str, Any]] = []
    next_token: str | None = None
    pages = 0

    while pages < max_pages:
        page_params = dict(params)
        if next_token:
            page_params["pagination_token"] = next_token

        page = bearer_get(f"/users/{user_id}/tweets", params=page_params)
        pages += 1

        for item in page.get("data") or []:
            row = dict(item)
            row["interaction"] = classify_post(item)
            posts.append(row)

        next_token = (page.get("meta") or {}).get("next_token")
        if not next_token:
            break

    counts: dict[str, int] = {}
    for p in posts:
        kind = p["interaction"]
        counts[kind] = counts.get(kind, 0) + 1

    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "username": profile.get("username"),
        "user": profile,
        "pages_fetched": pages,
        "post_count": len(posts),
        "by_type": counts,
        "note": (
            "Public timeline only. Other users' likes are private on X "
            "and cannot be fetched via the official API."
        ),
        "posts": posts,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Scrape a public X user's timeline into JSON",
    )
    parser.add_argument("username", help="X username, with or without @")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Pagination pages to fetch (default 3, 100 posts/page)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=100,
        help="Posts per page (5–100)",
    )
    parser.add_argument(
        "--include-replies",
        action="store_true",
        help="Include reply tweets in the timeline",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write JSON to this path (default: stdout)",
    )
    args = parser.parse_args(argv)

    data = fetch_user_timeline(
        args.username,
        max_pages=args.max_pages,
        max_results=args.max_results,
        include_replies=args.include_replies,
    )

    text = json.dumps(data, indent=2)
    if args.output:
        out = args.output if args.output.is_absolute() else REPO_ROOT / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(
            f"Wrote {data['post_count']} posts "
            f"({data['by_type']}) → {out}",
            file=sys.stderr,
        )
    else:
        print(text)


if __name__ == "__main__":
    main()
