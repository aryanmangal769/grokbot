"""Topic -> X dataset: one-shot high-recall sweep over a recent window.

`x_search` is an agentic tool, not a search endpoint: one call answers a question
and leaves citations behind. To turn it into an extractor we fan out — many
deliberately different calls across facets and time slices — and treat the union
of citations as the harvest. The prose is a byproduct.

Pipeline:
  1. plan     one tool-free Grok call: topic -> search facets
  2. sweep    facets x time slices -> parallel x_search calls (structured output)
  3. merge    dedupe by post id, union model rows with citation URLs
  4. hydrate  X API /2/tweets?ids= -> exact text, timestamps, metrics, media
  5. media    images -> Grok vision; videos -> targeted x_search
  6. emit     posts.jsonl + manifest.json

Usage (from repo root):
  python -m x_search.extract "world cup"
  python -m x_search.extract "Vinicius red card" --window 24 --slices 8
  python -m x_search.extract "grokathon" --no-hydrate --outdir data/grokathon
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common.env import REPO_ROOT, optional_env, require_env

API_BASE = "https://api.x.ai/v1"

DEFAULT_MODEL = "grok-4.5"
DEFAULT_WINDOW_HOURS = 24
DEFAULT_SLICES = 6
DEFAULT_FACETS = 8
DEFAULT_MAX_TURNS = 4
DEFAULT_WORKERS = 8

# The API reports exact spend as `cost_in_usd_ticks` at 1e-10 USD per tick.
USD_PER_TICK = 1e-10

POST_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def _api_key() -> str:
    return require_env("XAI_API_KEY", placeholder_prefix="xai-your-key")


def _responses(payload: dict[str, Any], *, timeout: int = 600) -> dict[str, Any]:
    """POST /v1/responses. Raises RuntimeError instead of exiting, so one bad
    sweep call cannot take down the whole harvest."""
    req = urllib.request.Request(
        f"{API_BASE}/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:600]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc


# --------------------------------------------------------------------------
# response parsing
#
# The Responses API nests output items; citations have appeared both as a
# top-level list and as per-annotation entries. Rather than bet on one shape we
# read the documented paths and also sweep the whole payload for post URLs.
# --------------------------------------------------------------------------


def output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                chunks.append(part.get("text") or "")
    return "\n".join(c for c in chunks if c)


def _walk_strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_strings(value)


def citation_posts(response: dict[str, Any]) -> dict[str, str]:
    """Every X post URL anywhere in the response, keyed by post id."""
    found: dict[str, str] = {}
    for text in _walk_strings(response):
        for match in POST_URL_RE.finditer(text):
            handle, post_id = match.group(1), match.group(2)
            found.setdefault(post_id, f"https://x.com/{handle}/status/{post_id}")
    return found


def parse_structured(response: dict[str, Any]) -> dict[str, Any] | None:
    text = output_text(response).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Structured output occasionally arrives fenced or with a preamble.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None


def tool_invocations(response: dict[str, Any]) -> int:
    usage = response.get("usage") or {}
    details = usage.get("server_side_tool_usage_details")
    if isinstance(details, dict):
        return sum(v for v in details.values() if isinstance(v, int))
    count = usage.get("num_server_side_tools_used")
    return count if isinstance(count, int) else 0


def call_cost_usd(response: dict[str, Any]) -> float:
    """Exact billed cost for the call, tokens and tool invocations included.

    `cost_in_usd_ticks` is reported at 1e-10 USD per tick — cross-checked against
    grok-4.5 token rates plus $5/1k x_search calls on a known response.
    """
    ticks = (response.get("usage") or {}).get("cost_in_usd_ticks")
    return (ticks or 0) * USD_PER_TICK


# --------------------------------------------------------------------------
# 1. plan
# --------------------------------------------------------------------------

FACET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_narrow": {
            "type": "boolean",
            "description": "True for a single specific moment/claim, false for a broad ongoing topic",
        },
        "entities": {
            "type": "array",
            "description": "People, teams, orgs, places central to the topic",
            "items": {"type": "string"},
        },
        "facets": {
            "type": "array",
            "description": "Distinct search angles that together maximise recall",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language instruction for what to find on X",
                    },
                    "angle": {
                        "type": "string",
                        "description": "Short slug, e.g. breaking-news, reactions, video-clips, analysis",
                    },
                    "allowed_x_handles": {
                        "type": "array",
                        "description": "Up to 20 handles (no @) to restrict this facet to; empty means unrestricted",
                        "items": {"type": "string"},
                    },
                },
                "required": ["query", "angle", "allowed_x_handles"],
            },
        },
    },
    "required": ["is_narrow", "entities", "facets"],
}

PLAN_PROMPT = """You are planning an exhaustive sweep of X (Twitter) for a topic.

Topic: {topic}
Window: the last {window} hours (now is {now}).

Produce {n} search facets that together maximise RECALL. They must be genuinely
different angles, not rephrasings — think: breaking news and primary sources,
official/organisation accounts, on-the-ground first-hand accounts, video and
photo clips, expert or tactical analysis, sceptical or contrarian takes,
numbers and statistics, downstream consequences and reactions.

If the topic is a single specific moment, set is_narrow=true and instead vary by
vantage point: the participants, the officials/authorities, the broadcasters,
the fan reaction, the clip itself, the aftermath.

For facets where a small set of accounts dominates coverage (official bodies,
beat reporters, wire services), list them in allowed_x_handles. Otherwise leave
it empty — an empty list searches all of X, which is usually what you want.
Only use handles you are confident exist."""


def plan_facets(
    topic: str,
    *,
    window_hours: int,
    n_facets: int,
    model: str,
    now: datetime,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "input": PLAN_PROMPT.format(
            topic=topic,
            window=window_hours,
            n=n_facets,
            now=now.strftime("%Y-%m-%d %H:%M UTC"),
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "search_plan",
                "schema": FACET_SCHEMA,
                "strict": True,
            }
        },
    }
    plan = parse_structured(_responses(payload, timeout=180)) or {}
    facets = [f for f in plan.get("facets") or [] if f.get("query")]
    if not facets:
        facets = [{"query": topic, "angle": "fallback", "allowed_x_handles": []}]
    return {
        "is_narrow": bool(plan.get("is_narrow")),
        "entities": plan.get("entities") or [],
        "facets": facets[:n_facets],
    }


# --------------------------------------------------------------------------
# 2. sweep
# --------------------------------------------------------------------------

SWEEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": {"type": "string", "description": "https://x.com/<handle>/status/<id>"},
                    "handle": {"type": "string", "description": "Author handle, no @"},
                    "posted_at": {"type": "string", "description": "ISO 8601 if known, else empty"},
                    "text": {"type": "string", "description": "Post text as read, verbatim where possible"},
                    "claim": {
                        "type": "string",
                        "description": "The single atomic factual assertion, normalised for clustering",
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Short slug for what happened, e.g. goal, red-card, injury, announcement, rumour, reaction",
                    },
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "stance": {
                        "type": "string",
                        "enum": ["positive", "negative", "neutral", "mixed", "unknown"],
                    },
                    "has_media": {"type": "boolean"},
                    "media_description": {
                        "type": "string",
                        "description": (
                            "If you actually viewed an attached image or video, describe what it "
                            "shows and transcribe any visible text. Empty string otherwise."
                        ),
                    },
                    "why_relevant": {"type": "string"},
                },
                "required": [
                    "url",
                    "handle",
                    "posted_at",
                    "text",
                    "claim",
                    "event_type",
                    "entities",
                    "stance",
                    "has_media",
                    "media_description",
                    "why_relevant",
                ],
            },
        }
    },
    "required": ["posts"],
}

SWEEP_PROMPT = """Search X for posts about: {query}

Overall topic: {topic}
Only consider posts published between {start} and {end}.

Return as many DISTINCT relevant posts as you can find — this is a data
extraction job, not a summary. Breadth beats depth: prefer 20 different posts
over 5 posts explained thoroughly. Include smaller accounts, replies and quote
posts, not just the viral ones.

For every post give the real canonical URL (https://x.com/<handle>/status/<id>).
Never invent a URL or a post — if you did not actually read it, leave it out.
Quote text verbatim; do not paraphrase into the text field."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _time_slices(now: datetime, window_hours: int, slices: int) -> list[tuple[datetime, datetime]]:
    """Split the window into buckets.

    Without this the agent returns the same loud posts for the whole window and
    the timeline collapses; slicing forces coverage of quiet periods.
    """
    slices = max(1, slices)
    start = now - timedelta(hours=window_hours)
    step = timedelta(hours=window_hours) / slices
    return [(start + step * i, start + step * (i + 1)) for i in range(slices)]


def sweep_once(
    *,
    topic: str,
    facet: dict[str, Any],
    start: datetime,
    end: datetime,
    model: str,
    max_turns: int,
    images: bool,
    videos: bool = False,
) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "type": "x_search",
        "from_date": _iso(start),
        "to_date": _iso(end),
    }
    handles = [h.lstrip("@") for h in (facet.get("allowed_x_handles") or []) if h][:20]
    if handles:
        tool["allowed_x_handles"] = handles
    if images:
        tool["enable_image_understanding"] = True
    if videos:
        # Without X API hydration this is the only route to video content, since
        # the post's media URL is never in hand to send to vision directly.
        tool["enable_video_understanding"] = True

    payload = {
        "model": model,
        "input": SWEEP_PROMPT.format(
            query=facet["query"],
            topic=topic,
            start=_iso(start),
            end=_iso(end),
        ),
        "tools": [tool],
        "max_turns": max_turns,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "x_posts",
                "schema": SWEEP_SCHEMA,
                "strict": True,
            }
        },
    }

    try:
        response = _responses(payload)
    except RuntimeError as exc:
        # The tool documents from_date/to_date as ISO8601; some deployments only
        # accept plain dates. Retry once at day granularity before giving up.
        if "from_date" in str(exc) or "to_date" in str(exc) or "400" in str(exc):
            tool["from_date"] = start.strftime("%Y-%m-%d")
            tool["to_date"] = end.strftime("%Y-%m-%d")
            try:
                response = _responses(payload)
            except RuntimeError as exc2:
                return _empty_result(str(exc2))
        else:
            return _empty_result(str(exc))

    structured = parse_structured(response) or {}
    return {
        "error": None,
        "posts": structured.get("posts") or [],
        "citations": citation_posts(response),
        "invocations": tool_invocations(response),
        "cost_usd": call_cost_usd(response),
    }


def _empty_result(error: str) -> dict[str, Any]:
    return {"error": error, "posts": [], "citations": {}, "invocations": 0, "cost_usd": 0.0}


# --------------------------------------------------------------------------
# 3. merge
# --------------------------------------------------------------------------


def _post_id(url: str) -> tuple[str, str] | None:
    match = POST_URL_RE.search(url or "")
    if not match:
        return None
    return match.group(2), match.group(1)


def merge_results(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Union every sweep into one index keyed by post id.

    Model rows carry interpretation (claim, event_type); citations carry proof
    that the post exists. A post seen only as a citation is still kept — it is
    real, we just have no model commentary on it.
    """
    posts: dict[str, dict[str, Any]] = {}

    for result in results:
        facet = result.get("facet", "?")
        for url in (result.get("citations") or {}).values():
            parsed = _post_id(url)
            if not parsed:
                continue
            post_id, handle = parsed
            entry = posts.setdefault(
                post_id,
                {
                    "id": post_id,
                    "url": url,
                    "handle": handle,
                    "source": "citation",
                    "found_by": [],
                    "model_rows": [],
                },
            )
            if facet not in entry["found_by"]:
                entry["found_by"].append(facet)

        for row in result.get("posts") or []:
            parsed = _post_id(row.get("url") or "")
            if not parsed:
                # A row with no resolvable URL is unverifiable; drop it rather
                # than let a possibly-hallucinated post into the dataset.
                continue
            post_id, handle = parsed
            entry = posts.setdefault(
                post_id,
                {
                    "id": post_id,
                    "url": f"https://x.com/{handle}/status/{post_id}",
                    "handle": handle,
                    "source": "model",
                    "found_by": [],
                    "model_rows": [],
                },
            )
            entry["source"] = "model"
            entry["model_rows"].append(row)
            if facet not in entry["found_by"]:
                entry["found_by"].append(facet)

    return posts


# --------------------------------------------------------------------------
# 4. hydrate
# --------------------------------------------------------------------------


def hydrate(posts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Replace model-reported fields with ground truth from the X API."""
    # Local import: X credentials are optional, so a missing token must not
    # break callers that only want the sweep.
    from api_usage_demo.twitter.client import get_tweets_by_ids

    ids = list(posts)
    if not ids:
        return {"ok": False, "reason": "no posts", "hydrated": 0}

    try:
        raw = get_tweets_by_ids(ids)
    except SystemExit as exc:
        return {"ok": False, "reason": str(exc), "hydrated": 0}

    users = {u["id"]: u for u in (raw.get("includes") or {}).get("users", [])}
    media = {m["media_key"]: m for m in (raw.get("includes") or {}).get("media", [])}

    hydrated = 0
    for tweet in raw.get("data") or []:
        entry = posts.get(tweet["id"])
        if not entry:
            continue
        author = users.get(tweet.get("author_id") or "", {})
        keys = (tweet.get("attachments") or {}).get("media_keys") or []
        entry["actual"] = {
            "text": tweet.get("text"),
            "created_at": tweet.get("created_at"),
            "lang": tweet.get("lang"),
            "metrics": tweet.get("public_metrics"),
            "author": {
                "id": author.get("id"),
                "username": author.get("username"),
                "name": author.get("name"),
                "verified": author.get("verified"),
                "followers": (author.get("public_metrics") or {}).get("followers_count"),
            },
            "referenced": tweet.get("referenced_tweets") or [],
            "media": [media[k] for k in keys if k in media],
        }
        if author.get("username"):
            entry["handle"] = author["username"]
            entry["url"] = f"https://x.com/{author['username']}/status/{tweet['id']}"
        hydrated += 1

    errors = raw.get("errors") or []
    for err in errors:
        entry = posts.get(err.get("resource_id") or "")
        if entry:
            # Deleted, protected, or never existed — the last case means the
            # model fabricated the URL, which is worth knowing.
            entry["unavailable"] = err.get("title") or err.get("detail")

    return {"ok": True, "hydrated": hydrated, "errors": len(errors)}


# --------------------------------------------------------------------------
# 5. media pass
# --------------------------------------------------------------------------

VISION_PROMPT = (
    "This image is attached to an X post about: {topic}\n\n"
    "Describe what it shows in two or three sentences. Transcribe any text, "
    "scoreboard, chyron, or headline visible in it verbatim. If it is a "
    "screenshot of another post or article, say so and transcribe it. "
    "State only what is actually visible."
)

VIDEO_PROMPT = (
    "Find this exact X post and watch the video attached to it: {url}\n\n"
    "Topic context: {topic}\n\n"
    "Describe what happens in the video in three or four sentences, in order. "
    "Transcribe any on-screen text or commentary. State only what you observe."
)


def _image_urls(entry: dict[str, Any]) -> list[str]:
    urls = []
    for item in (entry.get("actual") or {}).get("media") or []:
        if item.get("type") == "photo" and item.get("url"):
            urls.append(item["url"])
    return urls


def _has_video(entry: dict[str, Any]) -> bool:
    return any(
        item.get("type") in {"video", "animated_gif"}
        for item in (entry.get("actual") or {}).get("media") or []
    )


def _engagement(entry: dict[str, Any]) -> int:
    metrics = (entry.get("actual") or {}).get("metrics") or {}
    return (
        metrics.get("like_count", 0)
        + metrics.get("retweet_count", 0) * 2
        + metrics.get("quote_count", 0) * 2
    )


def describe_images(entry: dict[str, Any], topic: str, model: str) -> str | None:
    urls = _image_urls(entry)[:4]
    if not urls:
        return None
    content: list[dict[str, Any]] = [
        {"type": "input_image", "image_url": url, "detail": "high"} for url in urls
    ]
    content.append({"type": "input_text", "text": VISION_PROMPT.format(topic=topic)})
    try:
        response = _responses(
            {"model": model, "input": [{"role": "user", "content": content}]},
            timeout=240,
        )
    except RuntimeError as exc:
        return f"[image understanding failed: {exc}]"
    return output_text(response).strip() or None


def describe_video(entry: dict[str, Any], topic: str, model: str) -> str | None:
    """Grok has no direct video input, so the only route to video understanding
    is x_search with enable_video_understanding aimed at the single post."""
    created = (entry.get("actual") or {}).get("created_at")
    tool: dict[str, Any] = {"type": "x_search", "enable_video_understanding": True}
    if entry.get("handle"):
        tool["allowed_x_handles"] = [entry["handle"]]
    if created:
        try:
            at = datetime.fromisoformat(created.replace("Z", "+00:00"))
            tool["from_date"] = _iso(at - timedelta(hours=1))
            tool["to_date"] = _iso(at + timedelta(hours=1))
        except ValueError:
            pass
    try:
        response = _responses(
            {
                "model": model,
                "input": VIDEO_PROMPT.format(url=entry["url"], topic=topic),
                "tools": [tool],
                "max_turns": 3,
            },
            timeout=600,
        )
    except RuntimeError as exc:
        return f"[video understanding failed: {exc}]"
    return output_text(response).strip() or None


def media_pass(
    posts: dict[str, dict[str, Any]],
    *,
    topic: str,
    model: str,
    limit: int,
    do_video: bool,
    workers: int,
) -> dict[str, int]:
    """Second tier, deliberately selective: media understanding is token-billed
    and video is slow, so only the highest-signal posts earn a look."""
    candidates = [
        e for e in posts.values() if e.get("actual") and (_image_urls(e) or _has_video(e))
    ]
    candidates.sort(key=_engagement, reverse=True)
    candidates = candidates[:limit]

    stats = {"images": 0, "videos": 0}
    if not candidates:
        return stats

    def work(entry: dict[str, Any]) -> None:
        described = describe_images(entry, topic, model)
        if described:
            entry.setdefault("media_understanding", {})["images"] = described
            stats["images"] += 1
        if do_video and _has_video(entry):
            video = describe_video(entry, topic, model)
            if video:
                entry.setdefault("media_understanding", {})["video"] = video
                stats["videos"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, candidates))
    return stats


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def harvest(
    topic: str,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    slices: int = DEFAULT_SLICES,
    n_facets: int = DEFAULT_FACETS,
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
    workers: int = DEFAULT_WORKERS,
    do_hydrate: bool = True,
    do_media: bool = True,
    do_video: bool = True,
    media_limit: int = 12,
    sweep_images: bool = False,
    sweep_videos: bool = False,
    checkpoint: Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    def say(message: str) -> None:
        if verbose:
            print(message, file=sys.stderr, flush=True)

    say(f"planning facets for {topic!r} …")
    plan = plan_facets(
        topic, window_hours=window_hours, n_facets=n_facets, model=model, now=now
    )
    facets = plan["facets"]
    buckets = _time_slices(now, window_hours, slices)
    say(
        f"{len(facets)} facets x {len(buckets)} slices = {len(facets) * len(buckets)} calls"
        f" (narrow={plan['is_narrow']})"
    )

    jobs = [(f, s, e) for f in facets for (s, e) in buckets]
    results: list[dict[str, Any]] = []
    done = 0

    # Append every sweep as it lands. A sweep is the expensive part of the run,
    # so a crash or a Ctrl-C on call 47 must not cost the previous 46.
    checkpoint_handle = None
    if checkpoint is not None:
        checkpoint = checkpoint if checkpoint.is_absolute() else REPO_ROOT / checkpoint
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_handle = checkpoint.open("w", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                sweep_once,
                topic=topic,
                facet=facet,
                start=start,
                end=end,
                model=model,
                max_turns=max_turns,
                images=sweep_images,
                videos=sweep_videos,
            ): (facet, start, end)
            for facet, start, end in jobs
        }
        for future in as_completed(futures):
            facet, start, _ = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad call must not kill the sweep
                result = {"error": str(exc), "posts": [], "citations": {}, "invocations": 0}
            result["facet"] = facet["angle"]
            result["slice"] = _iso(start)
            results.append(result)
            done += 1
            if checkpoint_handle is not None:
                checkpoint_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                checkpoint_handle.flush()
            found = len(result.get("citations") or {})
            note = result.get("error") or f"{found} posts"
            say(f"  [{done}/{len(jobs)}] {facet['angle']} @ {_iso(start)[:16]} — {note}")

    if checkpoint_handle is not None:
        checkpoint_handle.close()

    posts = merge_results(results)
    say(f"merged -> {len(posts)} unique posts")

    hydration = {"ok": False, "reason": "skipped", "hydrated": 0}
    if do_hydrate and posts:
        say("hydrating via X API …")
        hydration = hydrate(posts)
        say(
            f"hydrated {hydration['hydrated']}/{len(posts)}"
            if hydration["ok"]
            else f"hydration unavailable: {hydration['reason']}"
        )

    media_stats = {"images": 0, "videos": 0}
    if do_media and hydration.get("ok"):
        say(f"media pass on top {media_limit} …")
        media_stats = media_pass(
            posts,
            topic=topic,
            model=model,
            limit=media_limit,
            do_video=do_video,
            workers=max(2, workers // 2),
        )
        say(f"described {media_stats['images']} image sets, {media_stats['videos']} videos")

    invocations = sum(r.get("invocations", 0) for r in results)
    cost = sum(r.get("cost_usd", 0.0) for r in results)
    errors = [r for r in results if r.get("error")]

    return {
        "topic": topic,
        "generated_at": _iso(now),
        "window": {"hours": window_hours, "start": _iso(buckets[0][0]), "end": _iso(now)},
        "plan": plan,
        "manifest": {
            "facets": len(facets),
            "slices": len(buckets),
            "calls": len(jobs),
            "failed_calls": len(errors),
            "unique_posts": len(posts),
            "citation_only_posts": sum(1 for p in posts.values() if p["source"] == "citation"),
            "hydration": hydration,
            "media": media_stats,
            "tool_invocations": invocations,
            "sweep_cost_usd": round(cost, 4),
            "errors": [{"facet": r["facet"], "error": r["error"]} for r in errors][:20],
            "per_facet": _per_facet_counts(results),
        },
        "posts": sorted(
            posts.values(),
            key=lambda p: ((p.get("actual") or {}).get("created_at") or "", p["id"]),
        ),
    }


def _per_facet_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result["facet"]] = counts.get(result["facet"], 0) + len(result.get("citations") or {})
    return counts


def write_outputs(dataset: dict[str, Any], outdir: Path) -> tuple[Path, Path]:
    outdir = outdir if outdir.is_absolute() else REPO_ROOT / outdir
    outdir.mkdir(parents=True, exist_ok=True)

    posts_path = outdir / "posts.jsonl"
    with posts_path.open("w", encoding="utf-8") as handle:
        for post in dataset["posts"]:
            handle.write(json.dumps(post, ensure_ascii=False) + "\n")

    manifest_path = outdir / "manifest.json"
    manifest = {k: v for k, v in dataset.items() if k != "posts"}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return posts_path, manifest_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract X posts on a topic over a recent window via xAI x_search"
    )
    parser.add_argument("topic", nargs="+", help="Topic or event, broad or specific")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_HOURS, help="Hours to look back")
    parser.add_argument("--slices", type=int, default=DEFAULT_SLICES, help="Time buckets in the window")
    parser.add_argument("--facets", type=int, default=DEFAULT_FACETS, help="Search angles to plan")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--media-limit", type=int, default=12, help="Posts to run media understanding on")
    parser.add_argument("--sweep-images", action="store_true", help="Image understanding during search (costly)")
    parser.add_argument(
        "--sweep-videos",
        action="store_true",
        help="Video understanding during search; the only video route without X API hydration",
    )
    parser.add_argument("--no-hydrate", action="store_true", help="Skip X API ground truth")
    parser.add_argument("--no-media", action="store_true", help="Skip the media pass entirely")
    parser.add_argument("--no-video", action="store_true", help="Media pass images only")
    parser.add_argument("--outdir", type=Path, default=None, help="Write posts.jsonl + manifest.json here")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Append raw sweeps here as they land (defaults to <outdir>/sweeps.jsonl)",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    topic = " ".join(args.topic).strip()

    checkpoint = args.checkpoint
    if checkpoint is None and args.outdir is not None:
        checkpoint = args.outdir / "sweeps.jsonl"

    dataset = harvest(
        topic,
        window_hours=args.window,
        slices=args.slices,
        n_facets=args.facets,
        model=args.model,
        max_turns=args.max_turns,
        workers=args.workers,
        do_hydrate=not args.no_hydrate,
        do_media=not args.no_media,
        do_video=not args.no_video,
        media_limit=args.media_limit,
        sweep_images=args.sweep_images,
        sweep_videos=args.sweep_videos,
        checkpoint=checkpoint,
        verbose=not args.quiet,
    )

    if args.outdir:
        posts_path, manifest_path = write_outputs(dataset, args.outdir)
        print(f"{len(dataset['posts'])} posts -> {posts_path}")
        print(f"manifest -> {manifest_path}")
    else:
        print(json.dumps(dataset, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
