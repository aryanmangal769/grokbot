"""X API v2 client (console.x.com).

Reads credentials from the shared repo-root `.env`.

Usage (from repo root):
  python -m x.client user elonmusk
  python -m x.client me
  python -m x.client search "from:xai" --max 10
  python -m x.client post "Hello from grokbot"
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import requests
from requests_oauthlib import OAuth1

from common.env import require_env

API_BASE = "https://api.x.com/2"

# Account used for this project:
# https://console.x.com/accounts/2085957761431465984


def load_bearer_token() -> str:
    return require_env("X_BEARER_TOKEN", placeholder_prefix="your-")


def load_oauth1() -> OAuth1:
    return OAuth1(
        require_env("X_API_KEY", placeholder_prefix="your-"),
        require_env("X_API_SECRET", placeholder_prefix="your-"),
        require_env("X_ACCESS_TOKEN", placeholder_prefix="your-"),
        require_env("X_ACCESS_TOKEN_SECRET", placeholder_prefix="your-"),
    )


def _raise_for_status(resp: requests.Response) -> None:
    if resp.ok:
        return
    try:
        detail = json.dumps(resp.json(), indent=2)
    except Exception:
        detail = resp.text
    raise SystemExit(f"HTTP {resp.status_code} {resp.request.method} {resp.url}\n{detail}")


def bearer_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = requests.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {load_bearer_token()}"},
        params=params or {},
        timeout=60,
    )
    _raise_for_status(resp)
    return resp.json()


def oauth1_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resp = requests.request(
        method,
        f"{API_BASE}{path}",
        auth=load_oauth1(),
        params=params or {},
        json=json_body,
        timeout=60,
    )
    _raise_for_status(resp)
    if not resp.content:
        return {}
    return resp.json()


def get_user_by_username(username: str) -> dict[str, Any]:
    username = username.lstrip("@")
    return bearer_get(
        f"/users/by/username/{username}",
        params={
            "user.fields": "id,name,username,created_at,description,public_metrics,verified",
        },
    )


def get_me() -> dict[str, Any]:
    """Authenticated account profile (OAuth 1.0a user context)."""
    return oauth1_request(
        "GET",
        "/users/me",
        params={
            "user.fields": "id,name,username,created_at,description,public_metrics,verified",
        },
    )


def search_recent(query: str, *, max_results: int = 10) -> dict[str, Any]:
    max_results = max(10, min(100, max_results))
    return bearer_get(
        "/tweets/search/recent",
        params={
            "query": query,
            "max_results": max_results,
            "tweet.fields": "created_at,lang,public_metrics,author_id",
        },
    )


def post_tweet(text: str) -> dict[str, Any]:
    """Create a post (OAuth 1.0a user context)."""
    return oauth1_request("POST", "/tweets", json_body={"text": text})


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="X API v2 client")
    sub = parser.add_subparsers(dest="command", required=True)

    user_p = sub.add_parser("user", help="Lookup a user by username (Bearer)")
    user_p.add_argument("username")

    sub.add_parser("me", help="Show the authenticated account (OAuth 1.0a)")

    search_p = sub.add_parser("search", help="Recent search (Bearer)")
    search_p.add_argument("query", nargs="+")
    search_p.add_argument("--max", type=int, default=10, dest="max_results")

    post_p = sub.add_parser("post", help="Create a post (OAuth 1.0a)")
    post_p.add_argument("text", nargs="+")

    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.command == "user":
        _print_json(get_user_by_username(args.username))
        return

    if args.command == "me":
        _print_json(get_me())
        return

    if args.command == "search":
        query = " ".join(args.query)
        _print_json(search_recent(query, max_results=args.max_results))
        return

    if args.command == "post":
        text = " ".join(args.text)
        _print_json(post_tweet(text))
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
