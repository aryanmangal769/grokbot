"""Agent 2: read the comment threads on harvested posts and summarise opinion.

Takes the output of `xsearch` (posts.jsonl) and, for each post, fetches the
reply thread via x_search and summarises it in the same call — the replies are
already in context, so summarising separately would pay twice to read them.

A second, tool-free call synthesises the per-post records into a global view.
Keeping synthesis separate means prompt iteration never re-fetches threads.

Two things learned from real threads shape this:
  * Reply engagement is near-zero (0 likes, single-digit views is normal), so
    there is no signal to weight replies by. Reach weighting comes from the
    parent post's view count instead.
  * Roughly a third of replies are jokes, emoji, or spam. Those are counted as
    noise, never as opinion, or camp shares just measure who posted popcorn.

Usage (from repo root):
  python -m x_search_public.grok.opinion data/infantino-bet
  python -m x_search_public.grok.opinion data/infantino-bet --limit 10 -o opinion.json
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from x_search_public.grok.xsearch import (
    DEFAULT_MODEL,
    _responses,
    call_cost_usd,
    parse_structured,
    tool_invocations,
)
from common.env import REPO_ROOT

DEFAULT_WORKERS = 8
# Enough turns to go back for more replies when the first fetch returns only the
# top or latest slice of a thread.
DEFAULT_MAX_TURNS = 5


# --------------------------------------------------------------------------
# stage 1: per-post thread read
# --------------------------------------------------------------------------

THREAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "post_summary": {
            "type": "string",
            "description": "What the post itself claims or reports, in one or two sentences",
        },
        "comment_summary": {
            "type": "string",
            "description": (
                "What people are actually saying in the replies: the dominant reaction, "
                "the main disagreements, and the tone. Three to five sentences."
            ),
        },
        "replies_read": {
            "type": "integer",
            "description": "How many replies you actually read. Never estimate or inflate this.",
        },
        "replies_substantive": {
            "type": "integer",
            "description": "Of those, how many expressed a real view. Excludes jokes, emoji, spam.",
        },
        "supports_count": {
            "type": "integer",
            "description": "Substantive replies whose view makes the topic outcome MORE likely",
        },
        "opposes_count": {
            "type": "integer",
            "description": "Substantive replies whose view makes the outcome LESS likely",
        },
        "mixed_or_unclear_count": {
            "type": "integer",
            "description": "Substantive replies that are ambivalent or off-axis",
        },
        "thread_agrees_with_post": {
            "type": "boolean",
            "description": "Do the repliers broadly accept the post's framing?",
        },
        "dominant_themes": {
            "type": "array",
            "description": "Recurring arguments or motifs in the replies, short phrases",
            "items": {"type": "string"},
        },
        "notable_claims": {
            "type": "array",
            "description": (
                "Specific factual or structural claims made in replies that could be "
                "checked and would matter to the outcome. Often the most valuable output."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "by_handle": {"type": "string"},
                    "bears_on_outcome": {
                        "type": "string",
                        "enum": ["makes-more-likely", "makes-less-likely", "unclear"],
                    },
                },
                "required": ["claim", "by_handle", "bears_on_outcome"],
            },
        },
        "noise_note": {
            "type": "string",
            "description": "Brief note on how much of the thread was junk, and what kind",
        },
    },
    "required": [
        "post_summary",
        "comment_summary",
        "replies_read",
        "replies_substantive",
        "supports_count",
        "opposes_count",
        "mixed_or_unclear_count",
        "thread_agrees_with_post",
        "dominant_themes",
        "notable_claims",
        "noise_note",
    ],
}

THREAD_PROMPT = """Read the reply thread on this X post and report what people are saying.

Post: {url}
Posted by: @{handle}
What it says: {claim}

The question being forecast is: {topic}

FETCH ALL THE COMMENTS ON THIS POST, not a sample. Search more than once: fetch
the thread, then go back for the replies you have not seen — top replies and
latest replies are different sets, and one fetch rarely returns everything. Keep
going until repeated searches stop surfacing new replies.

Replies are written by OTHER accounts, not by @{handle}. Search for posts
replying to this one, by anyone.

Count carefully and honestly. `replies_read` is how many you actually read — not
how many the post says it has, and never a guess. If the thread claims 41 replies
and you retrieved 30, say 30.

Expect a good share of replies to be filler: emoji, popcorn jokes, one-line
quips, engagement bait, conspiracy spam. That is normal and worth reporting
rather than hiding. Filler counts toward `replies_read` but NOT toward
`replies_substantive`, and never toward the camp counts. Only count a reply as
supports/opposes if it expresses an actual view on the question above.

Sort each substantive reply by whether its view makes the forecast outcome more
or less likely — not by whether it is positive or negative in tone.

Pay special attention to replies making concrete factual or structural claims —
who has authority over whom, what a process requires, what dates apply. These
are often buried in low-engagement replies and are the most useful thing in the
thread. Record them in notable_claims with the handle that said it, even when
you suspect the claim is wrong; contradictory claims are worth capturing.

Report only what you actually observed. If the thread could not be retrieved,
set replies_read to 0 and say so in noise_note."""


def read_thread(
    post: dict[str, Any],
    *,
    topic: str,
    model: str,
    max_turns: int,
) -> dict[str, Any]:
    row = (post.get("model_rows") or [{}])[0]
    # Deliberately unrestricted: replies come from accounts other than the
    # author, so scoping to the author's handle hides the entire thread.
    tool: dict[str, Any] = {"type": "x_search"}

    payload = {
        "model": model,
        "input": THREAD_PROMPT.format(
            url=post["url"],
            handle=post.get("handle", "unknown"),
            claim=(row.get("claim") or row.get("text") or "")[:300],
            topic=topic,
        ),
        "tools": [tool],
        "max_turns": max_turns,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "thread_read",
                "schema": THREAD_SCHEMA,
                "strict": True,
            }
        },
    }

    base = {
        "url": post["url"],
        "handle": post.get("handle"),
        "views": row.get("views", -1),
        "post_views": row.get("views", -1),
        "evidence_type": row.get("evidence_type"),
        "bearing": row.get("bearing"),
        "decision_value": post.get("relevance"),
    }

    try:
        response = _responses(payload, timeout=600)
    except RuntimeError as exc:
        return {**base, "error": str(exc), "replies_read": 0, "cost_usd": 0.0}

    parsed = parse_structured(response)
    if not parsed:
        # An unparsed response must not look like a thread with zero replies —
        # otherwise it lands in the dataset as a blank record and silently
        # inflates the "threads read" denominator.
        return {
            **base,
            "error": "unparsed thread response",
            "replies_read": 0,
            "cost_usd": call_cost_usd(response),
        }
    return {
        **base,
        **parsed,
        "error": None,
        "cost_usd": call_cost_usd(response),
        "invocations": tool_invocations(response),
    }


# --------------------------------------------------------------------------
# stage 2: synthesis
# --------------------------------------------------------------------------

GLOBAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "consensus": {
            "type": "string",
            "description": "Where public opinion actually sits, one paragraph. Be concrete.",
        },
        "camps": {
            "type": "array",
            "description": "The distinct positions people hold, largest first",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "position": {"type": "string"},
                    "approx_share": {
                        "type": "number",
                        "description": "0.0-1.0 rough share of substantive replies. Approximate.",
                    },
                    "strongest_argument": {"type": "string"},
                    "who_holds_it": {"type": "string"},
                },
                "required": [
                    "name",
                    "position",
                    "approx_share",
                    "strongest_argument",
                    "who_holds_it",
                ],
            },
        },
        "evidence_vs_sentiment": {
            "type": "string",
            "description": "Where the crowd and the hard evidence disagree, and which to trust",
        },
        "market_anchor": {
            "type": "string",
            "description": "Any market price seen in the data, and whether opinion justifies drift",
        },
        "checkable_claims": {
            "type": "array",
            "description": "The claims from replies most worth verifying, deduped across threads",
            "items": {"type": "string"},
        },
        "what_would_change_it": {
            "type": "array",
            "description": "Concrete events that would move the consensus",
            "items": {"type": "string"},
        },
        "confidence_note": {
            "type": "string",
            "description": "Honest statement of how thin the sample is and what that means",
        },
    },
    "required": [
        "consensus",
        "camps",
        "evidence_vs_sentiment",
        "market_anchor",
        "checkable_claims",
        "what_would_change_it",
        "confidence_note",
    ],
}

GLOBAL_PROMPT = """You are synthesising public opinion on a forecasting question from
the comment threads of {n} X posts.

Question: {topic}
{market}
Here are the per-thread reads. Each records how many replies were actually read,
how many were substantive, and how the substantive ones split.

{threads}

Write the global view. Rules:

Weight by the PARENT POST's view count, not by reply counts. A 60/40 split in a
thread under a 300,000-view post says far more about public opinion than the
same split under a 2,000-view post. Reply-level engagement is near zero
everywhere and carries no signal.

The total substantive sample here is small — a few hundred replies, sampled by
relevance rather than randomly. Your shares are approximate and you must say so
plainly in confidence_note. Do not imply precision you do not have.

Separate what the crowd believes from what the evidence shows. These posts were
selected for decision value, so the sample of THREADS is news-driven even though
the REPLIES are ordinary people. Where the crowd's view conflicts with official
statements or institutional actions, say which deserves more weight and why.

In checkable_claims, surface the structural and factual assertions worth
verifying — especially where two replies contradict each other. Those
contradictions are the most actionable thing in this data."""


def synthesise(
    threads: list[dict[str, Any]], *, topic: str, model: str, market_price: str = ""
) -> tuple[dict[str, Any], float]:
    digest = []
    for t in threads:
        if t.get("error"):
            continue
        digest.append(
            {
                "url": t["url"],
                "handle": t.get("handle"),
                "post_views": t.get("post_views"),
                "evidence_type": t.get("evidence_type"),
                "post_summary": t.get("post_summary"),
                "comment_summary": t.get("comment_summary"),
                "replies_read": t.get("replies_read"),
                "replies_substantive": t.get("replies_substantive"),
                "supports": t.get("supports_count"),
                "opposes": t.get("opposes_count"),
                "themes": t.get("dominant_themes"),
                "notable_claims": t.get("notable_claims"),
            }
        )

    payload = {
        "model": model,
        "input": GLOBAL_PROMPT.format(
            n=len(digest),
            topic=topic,
            market=(
                f"\nThe prediction market currently prices this at: {market_price}. "
                "Use this as the anchor in market_anchor rather than any price mentioned "
                "in the posts, which may be stale.\n"
                if market_price
                else ""
            ),
            threads=json.dumps(digest, ensure_ascii=False, indent=1)[:120000],
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "global_opinion",
                "schema": GLOBAL_SCHEMA,
                "strict": True,
            }
        },
    }
    response = _responses(payload, timeout=420)
    return (parse_structured(response) or {}), call_cost_usd(response)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


# Run metadata is kept out of the emitted JSON entirely: a downstream agent
# reading this file should see only the opinion data, not what it cost to
# produce or when. Operators get the cost on stderr instead.
_RUN_META_KEYS = ("cost_usd", "invocations")


def _strip_meta(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _RUN_META_KEYS}


def _totals(threads: list[dict[str, Any]]) -> dict[str, int]:
    ok = [t for t in threads if not t.get("error")]
    return {
        "threads_read": len(ok),
        "threads_failed": sum(1 for t in threads if t.get("error")),
        "replies_read": sum(t.get("replies_read") or 0 for t in ok),
        "replies_substantive": sum(t.get("replies_substantive") or 0 for t in ok),
        "supports": sum(t.get("supports_count") or 0 for t in ok),
        "opposes": sum(t.get("opposes_count") or 0 for t in ok),
        "mixed": sum(t.get("mixed_or_unclear_count") or 0 for t in ok),
        "notable_claims": sum(len(t.get("notable_claims") or []) for t in ok),
    }


def analyse(
    indir: Path,
    *,
    topic: str | None = None,
    model: str = DEFAULT_MODEL,
    market_price: str = "",
    limit: int = 0,
    workers: int = DEFAULT_WORKERS,
    max_turns: int = DEFAULT_MAX_TURNS,
    checkpoint: Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    indir = indir if indir.is_absolute() else REPO_ROOT / indir
    posts = [
        json.loads(line)
        for line in (indir / "posts.jsonl").open(encoding="utf-8")
        if line.strip()
    ]
    if topic is None:
        topic = json.loads((indir / "manifest.json").read_text(encoding="utf-8"))["topic"]

    if limit:
        # Deliberately by reach, not by the input's decision-value ranking. A
        # low-reach official statement outranks a viral reaction for evidence,
        # but has no comment thread to read — and comments are the job here.
        posts.sort(key=lambda p: -((p.get("model_rows") or [{}])[0].get("views") or 0))
        posts = posts[:limit]

    def say(message: str) -> None:
        if verbose:
            print(message, file=sys.stderr, flush=True)

    say(f"reading comment threads on {len(posts)} posts …")

    handle = None
    if checkpoint is not None:
        checkpoint = checkpoint if checkpoint.is_absolute() else REPO_ROOT / checkpoint
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        handle = checkpoint.open("w", encoding="utf-8")

    threads: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(read_thread, p, topic=topic, model=model, max_turns=max_turns): p
            for p in posts
        }
        for i, future in enumerate(as_completed(futures), 1):
            try:
                thread = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad thread must not kill the run
                thread = {"url": futures[future]["url"], "error": str(exc), "replies_read": 0}
            threads.append(thread)
            if handle is not None:
                handle.write(json.dumps(_strip_meta(thread), ensure_ascii=False) + "\n")
                handle.flush()
            note = thread.get("error") or (
                f"{thread.get('replies_read', 0)} replies "
                f"({thread.get('replies_substantive', 0)} substantive)"
            )
            say(f"  [{i}/{len(posts)}] @{thread.get('handle')} — {note}")
    if handle is not None:
        handle.close()

    totals = _totals(threads)
    say(f"totals: {totals}")

    say("synthesising global view …")
    global_view, synth_cost = synthesise(
        threads, topic=topic, model=model, market_price=market_price
    )

    cost = sum(t.get("cost_usd", 0.0) for t in threads) + synth_cost
    say(f"cost ${cost:.4f}")

    return {
        "topic": topic,
        "totals": totals,
        "global": global_view,
        "posts": [
            _strip_meta(t)
            for t in sorted(
                (t for t in threads if not t.get("error")),
                key=lambda t: -(t.get("post_views") or 0),
            )
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read comment threads on harvested posts and summarise public opinion"
    )
    parser.add_argument("indir", type=Path, help="Directory containing posts.jsonl + manifest.json")
    parser.add_argument("--topic", default=None, help="Override the topic from manifest.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=0, help="Only read the first N posts")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("-o", "--output", type=Path, default=None, help="Write JSON here")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    outdir = args.indir if args.indir.is_absolute() else REPO_ROOT / args.indir

    result = analyse(
        args.indir,
        topic=args.topic,
        model=args.model,
        limit=args.limit,
        workers=args.workers,
        max_turns=args.max_turns,
        checkpoint=outdir / "threads.jsonl",
        verbose=not args.quiet,
    )

    output = args.output or (outdir / "opinion.json")
    output = output if output.is_absolute() else REPO_ROOT / output
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"opinion -> {output}")


if __name__ == "__main__":
    main()
