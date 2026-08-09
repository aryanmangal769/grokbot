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
  python -m x_search_public.grok.xsearch "world cup"
  python -m x_search_public.grok.xsearch "Vinicius red card" --window 24 --slices 8
  python -m x_search_public.grok.xsearch "grokathon" --no-hydrate --outdir data/grokathon
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
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
# 3 slices and 2 turns keep a sweep in the $1-2 range. Blanket media
# understanding during the sweep is what made the first runs cost $13, so the
# deep media pass is aimed at selected posts instead.
DEFAULT_SLICES = 3
DEFAULT_FACETS = 8
DEFAULT_MAX_TURNS = 2
DEFAULT_WORKERS = 8
DEFAULT_TARGET = 300
DEFAULT_MIN_RELEVANCE = 0.5
# Off by default: 50 engagements is ~5k views, which would swallow the 500-view
# gate below and make it meaningless. Raise it only to tighten past that.
DEFAULT_MIN_ENGAGEMENT = 0
DEFAULT_MIN_FOLLOWERS = 200
# Engagement is the gate because impression_count, though documented as public,
# is widely reported to return 0. Impressions are still recorded when real and
# used for ranking, at roughly 1 engagement per 100 impressions.
IMPRESSIONS_PER_ENGAGEMENT = 100
# Reach gate. Applied to the estimate too when impression_count is missing,
# otherwise posts with an unreported count would skip the check entirely.
DEFAULT_MIN_IMPRESSIONS = 500
# Discourse floor: a post with no replies has no public opinion in it, and its
# thread gives the downstream comment stage nothing to read.
DEFAULT_MIN_REPLIES = 2

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


RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5


def _responses(payload: dict[str, Any], *, timeout: int = 600) -> dict[str, Any]:
    """POST /v1/responses. Raises RuntimeError instead of exiting, so one bad
    sweep call cannot take down the whole harvest.

    Retries throttling and transient server errors with exponential backoff and
    jitter. Without this, running many topics at once turns a rate limit into a
    dead planner call and a silently abandoned topic, rather than a short wait.
    """
    body = json.dumps(payload).encode("utf-8")
    last = ""
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(
            f"{API_BASE}/responses",
            data=body,
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
            last = f"HTTP {exc.code}: {detail[:600]}"
            if exc.code not in RETRY_STATUSES or attempt == MAX_RETRIES - 1:
                raise RuntimeError(last) from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        except OSError as exc:
            # Covers URLError and bare socket failures alike: a connection reset
            # mid-stream is not wrapped by urllib and would otherwise escape the
            # retry entirely and kill the run.
            last = f"network error: {getattr(exc, 'reason', exc)}"
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(last) from exc
            delay = 2**attempt
        time.sleep(min(60.0, delay + random.uniform(0, 1.5)))
    raise RuntimeError(last or "exhausted retries")


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
        "resolution_drivers": {
            "type": "array",
            "description": (
                "For a question-shaped topic: the concrete mechanisms that would decide "
                "the outcome — who has the power, what vote or process, what deadlines. "
                "Empty for topics that are not questions."
            ),
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
    "required": ["is_narrow", "entities", "resolution_drivers", "facets"],
}

PLAN_PROMPT = """You are planning an exhaustive sweep of X (Twitter) for a topic.

Topic: {topic}
Window: the last {window} hours (now is {now}).{criteria}

Produce {n} search facets that together maximise RECALL. They must be genuinely
different angles, not rephrasings — think: breaking news and primary sources,
official/organisation accounts, on-the-ground first-hand accounts, video and
photo clips, expert or tactical analysis, sceptical or contrarian takes,
numbers and statistics, downstream consequences and reactions.

If the topic is a single specific moment, set is_narrow=true and instead vary by
vantage point: the participants, the officials/authorities, the broadcasters,
the fan reaction, the clip itself, the aftermath.

If the topic is phrased as a QUESTION about something that may or may not happen
("Will X resign?", "Will X win?"), set is_narrow=true and treat this as
FORECASTING, not topic coverage. Someone is going to bet money on the answer.

First work out how this question actually resolves: who holds the power to make
it happen, what formal process or vote is required, what deadlines or scheduled
events exist, and what would have to occur first. List those in
resolution_drivers.

Then build one facet per driver, aimed at posts that would move a forecaster's
probability. Prioritise: direct statements by the principal, official
institutional positions and denials, formal actions (votes, motions, ethics
referrals, investigations), reporting from named beat journalists and insiders,
scheduled events with dates, and prediction-market or odds movement. Include at
least one facet hunting evidence the event will NOT happen.

Mass sentiment is NOT evidence. Thousands of fans demanding a resignation does
not change whether it happens; one sourced report that the board has scheduled a
vote does. Do not build facets that harvest opinion or reaction volume.

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
    criteria: str = "",
) -> dict[str, Any]:
    payload = {
        "model": model,
        "input": PLAN_PROMPT.format(
            topic=topic,
            window=window_hours,
            n=n_facets,
            now=now.strftime("%Y-%m-%d %H:%M UTC"),
            criteria=(
                f"\n\nKnown resolution context — trust this over your own assumptions:\n{criteria}"
                if criteria
                else ""
            ),
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
        "resolution_drivers": plan.get("resolution_drivers") or [],
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
                    "relevance": {
                        "type": "number",
                        "description": (
                            "0.0-1.0, DECISION VALUE: how much should this post move the "
                            "probability estimate of someone betting money on the outcome? "
                            "1.0 = a primary statement, formal action, or sourced report that "
                            "changes the odds. 0.6 = credible new detail. 0.3 = informed "
                            "commentary with no new facts. 0.1 = opinion, sentiment, or a "
                            "resignation demand from someone with no power. Popularity is NOT "
                            "decision value: a viral post with 10k likes saying he should quit "
                            "scores 0.1. Be strict."
                        ),
                    },
                    "evidence_type": {
                        "type": "string",
                        "description": "What kind of evidence this post carries",
                        "enum": [
                            "official-statement",
                            "institutional-action",
                            "insider-report",
                            "credible-reporting",
                            "scheduled-event",
                            "market-signal",
                            "informed-analysis",
                            "public-sentiment",
                            "rumour",
                            "noise",
                        ],
                    },
                    "novelty": {
                        "type": "string",
                        "description": "Does this add information, or repeat what is already known?",
                        "enum": ["new-information", "corroboration", "recycled", "commentary"],
                    },
                    "bearing": {
                        "type": "string",
                        "description": (
                            "For a question-shaped topic, does this post make the outcome more "
                            "likely, less likely, or neither?"
                        ),
                        "enum": ["supports", "refutes", "neutral", "not-applicable"],
                    },
                    "views": {
                        "type": "integer",
                        "description": (
                            "View/impression count shown on the post. Use -1 if you cannot "
                            "see it. Never guess or estimate a number."
                        ),
                    },
                    "replies": {
                        "type": "integer",
                        "description": "Reply count shown on the post, -1 if not visible. Never guess.",
                    },
                    "likes": {
                        "type": "integer",
                        "description": "Like count shown on the post, -1 if not visible. Never guess.",
                    },
                    "author_followers": {
                        "type": "integer",
                        "description": "Follower count of the author account, -1 if unknown. Never guess.",
                    },
                    "reposts": {
                        "type": "integer",
                        "description": "Repost/retweet count shown on the post, -1 if not visible.",
                    },
                    "quotes": {
                        "type": "integer",
                        "description": "Quote count shown on the post, -1 if not visible.",
                    },
                    "bookmarks": {
                        "type": "integer",
                        "description": "Bookmark count shown on the post, -1 if not visible.",
                    },
                    "quoted_post_url": {
                        "type": "string",
                        "description": (
                            "If this post quotes another post, its canonical URL, else empty. "
                            "The quoted post is often the higher-signal one."
                        ),
                    },
                    "quoted_post_views": {
                        "type": "integer",
                        "description": "Views on the quoted post, -1 if none or not visible.",
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
                    "relevance",
                    "evidence_type",
                    "novelty",
                    "bearing",
                    "views",
                    "replies",
                    "likes",
                    "author_followers",
                    "reposts",
                    "quotes",
                    "bookmarks",
                    "quoted_post_url",
                    "quoted_post_views",
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

Return as many DISTINCT posts as you can find — this is a data extraction job,
not a summary. Include smaller accounts, replies and quote posts.

DECISION VALUE IS THE BAR, not topical match. Someone is betting money on this
outcome. Include a post only if it would change a careful forecaster's estimate.

Include: statements by the principal or their office, formal institutional
actions (votes, motions, referrals, investigations), sourced reporting from
named journalists, scheduled events with dates, prediction-market prices and
movement, and specific informed argument about why it will or will not happen.

Exclude: generic demands and sentiment, "he must go" opinions from accounts with
no stake or standing, memes, and recycled coverage adding nothing new. These are
not evidence no matter how viral. A post with 10,000 likes saying he should
resign tells a forecaster nothing.

When a post QUOTES another post, the quoted one is often the real signal — a
small account quoting an official denial. Record the quoted post's URL and views
so it is not lost.

Score `relevance` as decision value and use the full range. If most of your
posts are above 0.8 you are not being strict enough.

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


def _relevance(entry: dict[str, Any]) -> float:
    scores = [
        r["relevance"]
        for r in entry.get("model_rows") or []
        if isinstance(r.get("relevance"), (int, float))
    ]
    return max(scores) if scores else 0.0


def _quality_score(entry: dict[str, Any]) -> float:
    """Rank by how relevant the model judged it, then by cross-facet consensus.

    Consensus is the one quality signal that costs nothing: independent facets
    converging on the same post is evidence it actually matters, not just that
    it matched a query.
    """
    score = _relevance(entry)
    score += 0.08 * (len(entry.get("found_by") or []) - 1)
    if entry.get("handle") and entry["handle"] != "i":
        score += 0.05
    return score


def select_candidates(
    posts: dict[str, dict[str, Any]],
    *,
    min_relevance: float,
) -> list[dict[str, Any]]:
    """Everything clearing the relevance bar, ranked, before metric filtering.

    Citation-only posts carry no model judgement, so they cannot clear a
    relevance bar and are excluded by construction.
    """
    scored = []
    for entry in posts.values():
        entry["relevance"] = round(_relevance(entry), 3)
        entry["quality_score"] = round(_quality_score(entry), 3)
        entry["consensus"] = len(entry.get("found_by") or [])
        if entry["relevance"] >= min_relevance:
            scored.append(entry)
    scored.sort(key=lambda e: -e["quality_score"])
    return scored


def _engagement_total(entry: dict[str, Any]) -> int:
    metrics = (entry.get("actual") or {}).get("metrics") or {}
    return sum(
        metrics.get(k, 0) or 0
        for k in ("like_count", "retweet_count", "reply_count", "quote_count")
    )


def _impressions(entry: dict[str, Any]) -> tuple[int, bool]:
    """Impressions and whether the figure had to be estimated."""
    metrics = (entry.get("actual") or {}).get("metrics") or {}
    shown = metrics.get("impression_count")
    if isinstance(shown, int) and shown > 0:
        return shown, False
    return _engagement_total(entry) * IMPRESSIONS_PER_ENGAGEMENT, True


def _reported_metrics(entry: dict[str, Any]) -> dict[str, int] | None:
    """Best metrics Grok read off the post, or None if it reported none.

    -1 means the model could not see the figure. Taking the max across rows
    means a facet that saw the numbers wins over one that did not.
    """
    rows = entry.get("model_rows") or []
    if not rows:
        return None
    picked = {
        "impressions": max((r.get("views", -1) for r in rows), default=-1),
        "replies": max((r.get("replies", -1) for r in rows), default=-1),
        "likes": max((r.get("likes", -1) for r in rows), default=-1),
        "followers": max((r.get("author_followers", -1) for r in rows), default=-1),
    }
    if all(v < 0 for v in picked.values()):
        return None
    return picked


def _ranked_score(entry: dict[str, Any]) -> float:
    """Final ranking: relevance and consensus, nudged by reach.

    Reach enters logarithmically — a 100k-impression post should outrank a
    1k one, but not so hard that a viral off-topic post beats a precise one.
    """
    score = entry.get("quality_score", 0.0)
    impressions = entry.get("impressions") or 0
    if impressions > 0:
        score += min(0.30, math.log10(impressions) / 20)
    return score


def apply_metric_filters(
    candidates: list[dict[str, Any]],
    *,
    min_engagement: int,
    min_followers: int,
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS,
    min_replies: int = DEFAULT_MIN_REPLIES,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Drop posts too small to carry public opinion.

    Only posts that actually hydrated can be judged. Un-hydrated posts are kept
    and flagged rather than silently dropped — otherwise a missing X token would
    look identical to a topic with no popular posts.
    """
    kept: list[dict[str, Any]] = []
    stats = {
        "kept": 0,
        "low_engagement": 0,
        "low_followers": 0,
        "low_impressions": 0,
        "low_replies": 0,
        "unknown_metrics": 0,
    }

    for entry in candidates:
        actual = entry.get("actual")
        if not actual:
            # No X API. Fall back to the numbers Grok read off the post itself:
            # unverified, but the only reach signal available.
            reported = _reported_metrics(entry)
            if reported is None:
                entry["metrics_unknown"] = True
                stats["unknown_metrics"] += 1
                kept.append(entry)
                continue
            entry.update(reported)
            entry["metrics_source"] = "model-reported"
            if reported["followers"] >= 0 and reported["followers"] < min_followers:
                stats["low_followers"] += 1
                continue
            if reported["replies"] >= 0 and reported["replies"] < min_replies:
                stats["low_replies"] += 1
                continue
            if reported["impressions"] >= 0 and reported["impressions"] < min_impressions:
                stats["low_impressions"] += 1
                continue
            stats["kept"] += 1
            kept.append(entry)
            continue

        entry["metrics_source"] = "x-api"
        impressions, estimated = _impressions(entry)
        metrics = actual.get("metrics") or {}
        engagement = _engagement_total(entry)
        followers = (actual.get("author") or {}).get("followers") or 0
        entry["engagement"] = engagement
        entry["replies"] = metrics.get("reply_count") or 0
        entry["impressions"] = impressions
        entry["impressions_estimated"] = estimated
        entry["followers"] = followers

        if followers < min_followers:
            stats["low_followers"] += 1
            continue
        if min_engagement and engagement < min_engagement:
            stats["low_engagement"] += 1
            continue
        if entry["replies"] < min_replies:
            stats["low_replies"] += 1
            continue
        if impressions < min_impressions:
            stats["low_impressions"] += 1
            continue

        stats["kept"] += 1
        kept.append(entry)

    return kept, stats


# --------------------------------------------------------------------------
# 4. hydrate
# --------------------------------------------------------------------------


def hydrate(posts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Replace model-reported fields with ground truth from the X API."""
    # Local import: X credentials are optional, so a missing token must not
    # break callers that only want the sweep.
    from x_search_public.twitter.client import get_tweets_by_ids

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
    "Find this exact X post and examine any image or video attached to it: {url}\n\n"
    "Topic context: {topic}\n\n"
    "Describe what the media shows in three or four sentences. For video, go in "
    "order. Transcribe any on-screen text, caption, or commentary verbatim. "
    "State only what you actually observe; if you cannot retrieve the post, say "
    "exactly that and nothing else."
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
    selected: list[dict[str, Any]],
    *,
    topic: str,
    model: str,
    limit: int,
    do_video: bool,
    workers: int,
) -> dict[str, int]:
    """Deep media understanding on selected posts only.

    Media understanding is token-billed and video is slow, so running it across
    a whole sweep is what makes a run expensive. Aimed at the ranked winners it
    costs a fraction and tells you more.

    Works with or without hydration: hydrated posts have real media URLs and go
    straight to vision, un-hydrated posts route through a single-post x_search,
    which is also the only path to video since Grok takes no direct video input.
    """
    candidates = [
        e
        for e in selected
        if _image_urls(e)
        or _has_video(e)
        or any(r.get("has_media") for r in e.get("model_rows") or [])
    ][:limit]

    stats = {"images": 0, "videos": 0, "via_search": 0}
    if not candidates:
        return stats

    def work(entry: dict[str, Any]) -> None:
        if _image_urls(entry):
            described = describe_images(entry, topic, model)
            if described:
                entry.setdefault("media_understanding", {})["images"] = described
                stats["images"] += 1
            if do_video and _has_video(entry):
                video = describe_video(entry, topic, model)
                if video:
                    entry.setdefault("media_understanding", {})["video"] = video
                    stats["videos"] += 1
            return

        # No hydration: one targeted search at the post itself, with both
        # modalities enabled so a single call covers whatever it carries.
        described = describe_via_search(entry, topic, model, video=do_video)
        if described:
            entry.setdefault("media_understanding", {})["via_search"] = described
            stats["via_search"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, candidates))
    return stats


def describe_via_search(
    entry: dict[str, Any], topic: str, model: str, *, video: bool
) -> str | None:
    """Media understanding for a post we have no hydrated media URL for."""
    tool: dict[str, Any] = {"type": "x_search", "enable_image_understanding": True}
    if video:
        tool["enable_video_understanding"] = True
    if entry.get("handle") and entry["handle"] != "i":
        tool["allowed_x_handles"] = [entry["handle"]]
    try:
        response = _responses(
            {
                "model": model,
                "input": VIDEO_PROMPT.format(url=entry["url"], topic=topic),
                "tools": [tool],
                "max_turns": 2,
            },
            timeout=600,
        )
    except RuntimeError as exc:
        return f"[media understanding failed: {exc}]"
    return output_text(response).strip() or None


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
    target: int = DEFAULT_TARGET,
    criteria: str = "",
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    min_engagement: int = DEFAULT_MIN_ENGAGEMENT,
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS,
    min_replies: int = DEFAULT_MIN_REPLIES,
    min_followers: int = DEFAULT_MIN_FOLLOWERS,
    hydrate_cap: int = 0,
    checkpoint: Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    # Hydration is billed per post read, so oversample enough to survive the
    # metric filters without paying to hydrate the whole harvest.
    hydrate_cap = hydrate_cap or target * 3

    def say(message: str) -> None:
        if verbose:
            print(message, file=sys.stderr, flush=True)

    say(f"planning facets for {topic!r} …")
    plan = plan_facets(
        topic,
        window_hours=window_hours,
        n_facets=n_facets,
        model=model,
        now=now,
        criteria=criteria,
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
    candidates = select_candidates(posts, min_relevance=min_relevance)
    say(
        f"merged -> {len(posts)} unique posts; "
        f"{len(candidates)} clear relevance >= {min_relevance}"
    )

    # Metric filters need hydrated data, so hydrate a pool larger than the
    # target and cut afterwards — filtering first would leave too few.
    pool = candidates[:hydrate_cap]
    hydration = {"ok": False, "reason": "skipped", "hydrated": 0}
    if do_hydrate and pool:
        say(f"hydrating {len(pool)} candidates via X API …")
        hydration = hydrate({e["id"]: e for e in pool})
        say(
            f"hydrated {hydration['hydrated']}/{len(pool)}"
            if hydration["ok"]
            else f"hydration unavailable: {hydration['reason']}"
        )

    filtered, filter_stats = apply_metric_filters(
        pool,
        min_engagement=min_engagement,
        min_followers=min_followers,
        min_impressions=min_impressions,
        min_replies=min_replies,
    )
    filtered.sort(key=lambda e: -_ranked_score(e))
    selected = filtered[:target]
    say(
        f"metric filter: kept {filter_stats['kept']}, "
        f"dropped {filter_stats['low_followers']} low-follower / "
        f"{filter_stats['low_replies']} low-reply / "
        f"{filter_stats['low_impressions']} low-view, "
        f"{filter_stats['unknown_metrics']} unknown -> selected {len(selected)}"
    )

    media_stats = {"images": 0, "videos": 0, "via_search": 0}
    if do_media and selected:
        say(f"media pass on top {media_limit} selected …")
        media_stats = media_pass(
            selected,
            topic=topic,
            model=model,
            limit=media_limit,
            do_video=do_video,
            workers=max(2, workers // 2),
        )
        say(
            f"described {media_stats['images']} image sets, "
            f"{media_stats['videos']} videos, {media_stats['via_search']} via search"
        )

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
            "selected_posts": len(selected),
            "target": target,
            "min_relevance": min_relevance,
            "min_engagement": min_engagement,
            "min_impressions": min_impressions,
            "min_replies": min_replies,
            "min_followers": min_followers,
            "relevance_candidates": len(candidates),
            "hydrate_pool": len(pool),
            "metric_filter": filter_stats,
            "dropped_below_relevance": sum(
                1 for p in posts.values() if p["source"] == "model" and _relevance(p) < min_relevance
            ),
            "citation_only_posts": sum(1 for p in posts.values() if p["source"] == "citation"),
            "hydration": hydration,
            "media": media_stats,
            "tool_invocations": invocations,
            "sweep_cost_usd": round(cost, 4),
            "errors": [{"facet": r["facet"], "error": r["error"]} for r in errors][:20],
            "per_facet": _per_facet_counts(results),
        },
        "posts": selected,
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


def rebuild_from_sweeps(
    sweeps_path: Path,
    *,
    topic: str,
    target: int,
    min_relevance: float,
    min_engagement: int,
    min_impressions: int,
    min_replies: int,
    min_followers: int,
    hydrate_cap: int = 0,
    do_hydrate: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Re-select from raw sweeps already on disk, without re-running the search.

    Thresholds change far more often than the underlying harvest does, so
    re-filtering should not cost another sweep.
    """
    hydrate_cap = hydrate_cap or target * 3
    results = [json.loads(line) for line in sweeps_path.open(encoding="utf-8") if line.strip()]

    def say(message: str) -> None:
        if verbose:
            print(message, file=sys.stderr, flush=True)

    say(f"rebuilding from {len(results)} cached sweeps in {sweeps_path}")
    posts = merge_results(results)
    candidates = select_candidates(posts, min_relevance=min_relevance)
    say(f"merged -> {len(posts)} unique; {len(candidates)} clear relevance >= {min_relevance}")

    pool = candidates[:hydrate_cap]
    hydration = {"ok": False, "reason": "skipped", "hydrated": 0}
    if do_hydrate and pool:
        say(f"hydrating {len(pool)} candidates via X API …")
        hydration = hydrate({e["id"]: e for e in pool})
        say(
            f"hydrated {hydration['hydrated']}/{len(pool)}"
            if hydration["ok"]
            else f"hydration unavailable: {hydration['reason']}"
        )

    filtered, filter_stats = apply_metric_filters(
        pool,
        min_engagement=min_engagement,
        min_followers=min_followers,
        min_impressions=min_impressions,
        min_replies=min_replies,
    )
    filtered.sort(key=lambda e: -_ranked_score(e))
    selected = filtered[:target]
    say(f"metric filter: {filter_stats} -> selected {len(selected)}")

    return {
        "topic": topic,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "rebuilt_from": str(sweeps_path),
        "manifest": {
            "calls": 0,
            "unique_posts": len(posts),
            "relevance_candidates": len(candidates),
            "hydrate_pool": len(pool),
            "selected_posts": len(selected),
            "target": target,
            "min_relevance": min_relevance,
            "min_engagement": min_engagement,
            "min_impressions": min_impressions,
            "min_replies": min_replies,
            "min_followers": min_followers,
            "metric_filter": filter_stats,
            "hydration": hydration,
            "sweep_cost_usd": 0.0,
        },
        "posts": selected,
    }


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
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Max posts to keep after ranking")
    parser.add_argument(
        "--min-relevance",
        type=float,
        default=DEFAULT_MIN_RELEVANCE,
        help="Drop posts the model scored below this (0.0-1.0)",
    )
    parser.add_argument(
        "--min-engagement",
        type=int,
        default=DEFAULT_MIN_ENGAGEMENT,
        help="Drop posts below this many likes+reposts+replies+quotes; needs hydration",
    )
    parser.add_argument(
        "--min-impressions",
        type=int,
        default=DEFAULT_MIN_IMPRESSIONS,
        help="Drop posts below this many views; needs hydration",
    )
    parser.add_argument(
        "--min-replies",
        type=int,
        default=DEFAULT_MIN_REPLIES,
        help="Drop posts with fewer replies; needs hydration",
    )
    parser.add_argument(
        "--min-followers",
        type=int,
        default=DEFAULT_MIN_FOLLOWERS,
        help="Drop posts from accounts below this follower count; needs hydration",
    )
    parser.add_argument(
        "--hydrate-cap",
        type=int,
        default=0,
        help="How many candidates to hydrate before filtering (default target x3)",
    )
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
    parser.add_argument(
        "--from-sweeps",
        type=Path,
        default=None,
        help="Re-select from a cached sweeps.jsonl instead of searching again",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    topic = " ".join(args.topic).strip()

    if args.from_sweeps:
        dataset = rebuild_from_sweeps(
            args.from_sweeps,
            topic=topic,
            target=args.target,
            min_relevance=args.min_relevance,
            min_engagement=args.min_engagement,
            min_impressions=args.min_impressions,
            min_replies=args.min_replies,
            min_followers=args.min_followers,
            hydrate_cap=args.hydrate_cap,
            do_hydrate=not args.no_hydrate,
            verbose=not args.quiet,
        )
        if args.outdir:
            posts_path, manifest_path = write_outputs(dataset, args.outdir)
            print(f"{len(dataset['posts'])} posts -> {posts_path}")
            print(f"manifest -> {manifest_path}")
        else:
            print(json.dumps(dataset, indent=2, ensure_ascii=False))
        return

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
        target=args.target,
        min_relevance=args.min_relevance,
        min_engagement=args.min_engagement,
        min_impressions=args.min_impressions,
        min_replies=args.min_replies,
        min_followers=args.min_followers,
        hydrate_cap=args.hydrate_cap,
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
