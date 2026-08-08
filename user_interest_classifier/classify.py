"""Classify a public X user's interests using scraped timeline + Grok (xAI).

Flow:
  1. Call user_based_scraper for public timeline data
  2. Feed a condensed version to Grok via the xAI Responses API
  3. Return structured interest labels

Usage (from repo root):
  python -m user_interest_classifier.classify elonmusk
  python -m user_interest_classifier.classify elonmusk --max-pages 2 -o outputs/elon_interests.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api_usage_demo.grok.client import DEFAULT_TEXT_MODEL, respond
from common.env import REPO_ROOT
from user_based_scraper.user import fetch_user_timeline

CLASSIFY_SYSTEM = """You classify a public X (Twitter) user's interests from their timeline.
Use only the provided profile + posts. Do not invent private activity (likes are private).
Return ONLY valid JSON matching this schema:
{
  "username": string,
  "summary": string,                 // 2-4 sentences
  "interests": [
    {
      "topic": string,               // short label, e.g. "space", "AI", "politics"
      "confidence": number,          // 0 to 1
      "evidence": [string]           // short quotes or paraphrases from posts
    }
  ],
  "themes": [string],                // broader themes
  "tone": string,                    // posting style in one short phrase
  "caveats": [string]                // data limits / uncertainty
}
Rank interests by confidence descending. Include 5-12 interests when possible.
"""


def _condense_posts(posts: list[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    """Keep model context manageable while preserving signal."""
    condensed: list[dict[str, Any]] = []
    for post in posts[:limit]:
        text = (post.get("text") or "").strip()
        if len(text) > 280:
            text = text[:277] + "..."
        condensed.append(
            {
                "interaction": post.get("interaction"),
                "created_at": post.get("created_at"),
                "text": text,
                "metrics": post.get("public_metrics") or {},
            }
        )
    return condensed


def build_prompt(scraped: dict[str, Any]) -> str:
    payload = {
        "username": scraped.get("username"),
        "profile": scraped.get("user"),
        "post_count": scraped.get("post_count"),
        "by_type": scraped.get("by_type"),
        "posts": _condense_posts(scraped.get("posts") or []),
        "note": scraped.get("note"),
    }
    return (
        f"{CLASSIFY_SYSTEM}\n\n"
        f"User timeline JSON:\n{json.dumps(payload, indent=2)}"
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError(f"Model did not return JSON:\n{text[:1000]}")


def classify_user(
    username: str,
    *,
    max_pages: int = 2,
    max_results: int = 100,
    include_replies: bool = False,
    model: str = DEFAULT_TEXT_MODEL,
) -> dict[str, Any]:
    print(f"Scraping @{username.lstrip('@')} …", file=sys.stderr)
    scraped = fetch_user_timeline(
        username,
        max_pages=max_pages,
        max_results=max_results,
        include_replies=include_replies,
    )
    print(
        f"Got {scraped['post_count']} posts ({scraped['by_type']}); asking Grok …",
        file=sys.stderr,
    )

    raw = respond(build_prompt(scraped), model=model)
    interests = _extract_json(raw)

    return {
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "username": scraped.get("username"),
        "source": {
            "post_count": scraped.get("post_count"),
            "by_type": scraped.get("by_type"),
            "pages_fetched": scraped.get("pages_fetched"),
            "scraped_at": scraped.get("scraped_at"),
        },
        "classification": interests,
        "raw_model_text": raw,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Classify X user interests via scraper + Grok (xAI)",
    )
    parser.add_argument("username", help="X username, with or without @")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--include-replies", action="store_true")
    parser.add_argument("--model", default=DEFAULT_TEXT_MODEL)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write JSON result (default: outputs/<user>_interests.json)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print full JSON to stdout instead of only a short summary",
    )
    args = parser.parse_args(argv)

    result = classify_user(
        args.username,
        max_pages=args.max_pages,
        max_results=args.max_results,
        include_replies=args.include_replies,
        model=args.model,
    )

    username = (result.get("username") or args.username).lstrip("@")
    out = args.output
    if out is None:
        out = Path(f"outputs/{username}_interests.json")
    out = out if out.is_absolute() else REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    classification = result.get("classification") or {}
    print(f"Saved → {out}", file=sys.stderr)
    print(f"\n@{username} interest summary", file=sys.stderr)
    print(classification.get("summary") or "(no summary)", file=sys.stderr)
    print("\nTop interests:", file=sys.stderr)
    for item in (classification.get("interests") or [])[:8]:
        topic = item.get("topic")
        conf = item.get("confidence")
        print(f"  - {topic} ({conf})", file=sys.stderr)

    if args.stdout:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
