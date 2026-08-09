"""Hydrate camp-JSON tweet URLs via X API v2 (real text, metrics, avatars, premium)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import requests

from common.env import REPO_ROOT, require_env
from summary.media_dl import download_url

API_BASE = "https://api.x.com/2"

STATUS_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/[^/]+/status/(\d+)",
    re.I,
)


def status_id_from_url(url: str) -> str | None:
    m = STATUS_RE.search(url or "")
    return m.group(1) if m else None


def _bearer() -> str:
    return require_env("X_BEARER_TOKEN", placeholder_prefix="your-")


def hi_res_avatar_url(url: str | None) -> str | None:
    """X serves _normal (48px); bump to 400px for crisp cards."""
    if not url:
        return None
    return (
        url.replace("_normal.", "_400x400.")
        .replace("_bigger.", "_400x400.")
        .replace("_mini.", "_400x400.")
    )


def fetch_tweets_by_ids(ids: list[str]) -> dict[str, dict[str, Any]]:
    """Return map id -> hydrated tweet + author identity."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    seen: set[str] = set()
    uniq: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)

    resp = requests.get(
        f"{API_BASE}/tweets",
        headers={"Authorization": f"Bearer {_bearer()}"},
        params={
            "ids": ",".join(uniq),
            "tweet.fields": "created_at,public_metrics,lang,author_id",
            "expansions": "author_id",
            "user.fields": (
                "username,name,verified,verified_type,profile_image_url,"
                "public_metrics,protected"
            ),
        },
        timeout=60,
    )
    if not resp.ok:
        raise SystemExit(
            f"X API tweet hydrate failed HTTP {resp.status_code}: {resp.text[:500]}"
        )
    payload = resp.json()
    users = {u["id"]: u for u in (payload.get("includes") or {}).get("users") or []}
    out: dict[str, dict[str, Any]] = {}
    for t in payload.get("data") or []:
        tid = str(t["id"])
        u = users.get(t.get("author_id") or "", {})
        m = t.get("public_metrics") or {}
        handle = u.get("username") or "user"
        vtype = (u.get("verified_type") or "").lower() or None
        # verified_type: blue | business | government | none
        is_premium = bool(u.get("verified")) or (vtype not in (None, "", "none"))
        out[tid] = {
            "id": tid,
            "text": t.get("text") or "",
            "handle": handle,
            "name": u.get("name") or handle,
            "verified": is_premium,
            "verified_type": vtype if vtype not in (None, "none") else (
                "blue" if u.get("verified") else None
            ),
            "profile_image_url": hi_res_avatar_url(u.get("profile_image_url")),
            "created_at": t.get("created_at"),
            "metrics": {
                "like_count": int(m.get("like_count") or 0),
                "retweet_count": int(m.get("retweet_count") or 0),
                "reply_count": int(m.get("reply_count") or 0),
                "quote_count": int(m.get("quote_count") or 0),
                "impression_count": int(m.get("impression_count") or 0),
                "bookmark_count": int(m.get("bookmark_count") or 0),
            },
            "url": f"https://x.com/{handle}/status/{tid}",
        }
    missing = [i for i in uniq if i not in out]
    if missing:
        print(f"  warn: X API missing ids: {missing}", file=sys.stderr)
    return out


def cache_avatar(url: str | None, handle: str, cache_dir: Path) -> str | None:
    if not url:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", handle or "user")[:40]
    ext = ".jpg"
    lower = url.lower().split("?")[0]
    if lower.endswith(".png"):
        ext = ".png"
    elif lower.endswith(".webp"):
        ext = ".webp"
    dest = cache_dir / f"{safe}{ext}"
    try:
        download_url(url, dest)
        return str(dest)
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: avatar download @{handle}: {exc}", file=sys.stderr)
        return None


def hydrate_camp_data(
    data: dict[str, Any],
    *,
    avatar_cache: Path | None = None,
) -> dict[str, Any]:
    """Attach real X text/metrics/avatar/premium onto each tweet object."""
    camps = data.get("top_tweets") or {}
    id_list: list[str] = []
    refs: list[tuple[str, str, dict]] = []

    for camp_name in ("camp1", "camp2"):
        block = camps.get(camp_name) or {}
        if not isinstance(block, dict):
            continue
        for k, v in block.items():
            if not str(k).startswith("tweet"):
                continue
            if isinstance(v, dict):
                tid = status_id_from_url(str(v.get("url") or ""))
                if tid:
                    id_list.append(tid)
                    refs.append((camp_name, k, v))
            elif isinstance(v, str) and "status/" in v:
                tid = status_id_from_url(v)
                if tid:
                    id_list.append(tid)
                    refs.append((camp_name, k, {"url": v}))

    print(f"Hydrating {len(id_list)} tweets from X API …", file=sys.stderr)
    hydrated = fetch_tweets_by_ids(id_list)
    cache = avatar_cache or (REPO_ROOT / "outputs/summary/avatar_cache")

    for camp_name, key, obj in refs:
        tid = status_id_from_url(str(obj.get("url") or ""))
        live = hydrated.get(tid or "")
        if not live:
            print(f"  warn: no live data for {camp_name}.{key} ({tid})", file=sys.stderr)
            continue
        obj["id"] = live["id"]
        obj["url"] = live["url"]
        obj["handle"] = live["handle"]
        obj["name"] = live["name"]
        obj["text"] = live["text"]
        obj["created_at"] = live["created_at"]
        obj["metrics"] = live["metrics"]
        obj["verified"] = live["verified"]
        obj["verified_type"] = live["verified_type"]
        obj["profile_image_url"] = live["profile_image_url"]
        obj["avatar_path"] = cache_avatar(
            live["profile_image_url"], live["handle"], cache
        )
        m = live["metrics"]
        print(
            f"  @{live['handle']} verified={live['verified_type'] or live['verified']} "
            f"likes={m['like_count']} rts={m['retweet_count']} "
            f"replies={m['reply_count']} views={m['impression_count']}",
            file=sys.stderr,
        )
        print(f"    {(live['text'] or '')[:100].replace(chr(10), ' ')}", file=sys.stderr)

    data["top_tweets"] = camps
    data["_hydrated"] = True
    return data
