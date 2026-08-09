#!/usr/bin/env python3
"""Turn an X discussion dataset into a compact Polymarket sentiment report.

Examples:
    python polymarket_x_report.py data/input.json
    python polymarket_x_report.py - < data/input.json > report.json
    python polymarket_x_report.py data/input.json --output report.json

Requires XAI_API_KEY in the repository's .env file or environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
MODEL = "grok-4.5"
API_BASE = "https://api.x.ai/v1"

OUTPUT_SCHEMA: dict[str, Any] = {
    "topic": "Polymarket event title",
    "tweet_summary": "Concise synthesis of supplied posts and replies.",
    "top_tweets": {
        "camp1": {
            "name": "Name of a meaningful viewpoint",
            "tweet1": {"url": "", "handle": "", "summary": ""},
            "tweet2": {"url": "", "handle": "", "summary": ""},
            "tweet3": {"url": "", "handle": "", "summary": ""},
        },
        "camp2": {
            "name": "Name of the opposing or second-largest viewpoint",
            "tweet1": {"url": "", "handle": "", "summary": ""},
            "tweet2": {"url": "", "handle": "", "summary": ""},
            "tweet3": {"url": "", "handle": "", "summary": ""},
        },
    },
    "X_leaning": "yes, no, or mixed",
    "X_leaning_percentages": {
        "yes": "Integer percent of sampled discussion predicting the event occurs",
        "no": "Integer percent of sampled discussion predicting the event does not occur",
        "mixed": "Integer percent with no directional prediction or genuinely mixed view",
    },
    "num_tweets": "Total volume of tweets on this topic, supplied by the input dataset",
    "plots": {
        "outcome_leaning": {
            "title": "X prediction leaning",
            "type": "bar",
            "unit": "percent",
            "data": [
                {"label": "Yes", "value": 0},
                {"label": "No", "value": 0},
                {"label": "Mixed", "value": 0},
            ],
        },
        "camp_distribution": {
            "title": "Camp distribution",
            "type": "bar",
            "unit": "percent",
            "data": [{"label": "Camp name", "value": 0}],
        },
    },
}


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def read_input(path: str) -> dict[str, Any]:
    try:
        raw = (
            sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        )
        data = json.loads(raw)
    except FileNotFoundError:
        fail(f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        fail(f"input is not valid JSON: {exc}")
    if not isinstance(data, dict):
        fail("top-level input must be a JSON object")
    if not isinstance(data.get("topic"), str) or not data["topic"].strip():
        fail('input requires a non-empty string field: "topic"')
    if not isinstance(data.get("posts"), list):
        fail('input requires an array field: "posts"')
    if not isinstance(data.get("num_tweets"), int) or isinstance(data["num_tweets"], bool):
        fail('input requires a non-negative integer field: "num_tweets"')
    if data["num_tweets"] < 0:
        fail('input field "num_tweets" cannot be negative')
    return data


def prompt_for(dataset: dict[str, Any]) -> str:
    return f"""You are an analyst summarizing supplied X discussion data for a Polymarket event.

Return exactly one valid JSON object, with no Markdown or commentary, using this output schema:
{json.dumps(OUTPUT_SCHEMA, indent=2)}

Rules:
- Use only the supplied input. Review every post's summary and reply summary; do not infer missing facts or invent posts, handles, URLs, vote counts, or quotes.
- `topic` must equal the supplied input's `topic` exactly.
- `num_tweets` must equal the supplied input's `num_tweets` exactly. It is the total topic volume, not the number of sampled posts.
- Interpret `yes` as the event in the topic occurring and `no` as it not occurring. `X_leaning` must be exactly `yes`, `no`, or `mixed`. Do not equate people wanting an outcome with believing it will happen. Use `mixed` when the supplied evidence is too balanced, ambiguous, or not predictive enough.
- Set `X_leaning_percentages` to integer percentages that sum to 100: `yes` is the estimated share predicting the event occurs, `no` is the estimated share predicting it does not, and `mixed` is the share expressing no directional prediction or an irreducibly mixed view. These are directional estimates from the supplied sample, not probabilities or a representative poll. `X_leaning` must be the largest percentage category; use `mixed` for a close or inconclusive directional split.
- Treat `global` as the supplied cross-thread synthesis and `posts` as the auditable source set. Reconcile them: surface meaningful disagreement instead of blindly repeating either one.
- Choose the two camps that most affect the event probability, not merely the loudest moral or emotional reactions. State each camp's position in its `name` (for example, `Pressure will force a resignation`), so the output is understandable without the input.
- Rank each camp's three source posts by decision relevance first, then evidence quality, substantive reply signal, and reach. Do not select multiple near-duplicate posts unless the input has no better alternatives. A high view count shows reach, not truth.
- Each non-null tweet must cite one supplied post and preserve its exact `url` and `handle`; its `summary` must state the post's relevant claim and why it supports that camp. Rank strongest first. Use null for unavailable second or third tweets.
- `tweet_summary` must be one concise analytical paragraph. It should say where sampled X sentiment sits, distinguish prediction from preference, identify the strongest evidence, name the main uncertainty, and avoid claiming the dataset represents all of X.
- If the data has only one meaningful camp, use `No meaningful opposing camp` as camp2's name and set its tweet fields to null.
- Return plot-ready data in `plots`, not image files. `plots.outcome_leaning.data` must be exactly Yes, No, and Mixed and must mirror `X_leaning_percentages`. `plots.camp_distribution.data` must use the supplied `global.camps` names and their `approx_share` values converted to integer percentages; omit a camp only if its supplied share is absent. Do not fabricate chart values.

Before responding, verify internally that every cited `(url, handle)` pair occurs in the input and that the two camps are distinct. Return only the JSON object.

Input dataset:
{json.dumps(dataset, ensure_ascii=False, indent=2)}
"""


def parse_response(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"Grok did not return valid JSON: {exc}")
    if not isinstance(report, dict):
        fail("Grok returned JSON that was not an object")
    return report


def validate_report(report: dict[str, Any], dataset: dict[str, Any]) -> None:
    required = {
        "topic",
        "tweet_summary",
        "top_tweets",
        "X_leaning",
        "X_leaning_percentages",
        "num_tweets",
        "plots",
    }
    missing = required - report.keys()
    if missing:
        fail(f"Grok response omitted required field(s): {', '.join(sorted(missing))}")
    if report["topic"] != dataset["topic"]:
        fail("Grok response changed the topic")
    if not isinstance(report["tweet_summary"], str):
        fail("Grok response has a non-string tweet_summary")
    if not isinstance(report["num_tweets"], int) or isinstance(
        report["num_tweets"], bool
    ):
        fail("Grok response has a non-integer num_tweets")
    if report["num_tweets"] != dataset["num_tweets"]:
        fail("Grok response changed the supplied total num_tweets")
    if report["X_leaning"] not in {"yes", "no", "mixed"}:
        fail("Grok response has invalid X_leaning (expected yes, no, or mixed)")
    percentages = report["X_leaning_percentages"]
    if not isinstance(percentages, dict) or set(percentages) != {"yes", "no", "mixed"}:
        fail("Grok response must include yes, no, and mixed X_leaning_percentages")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in percentages.values()):
        fail("X_leaning_percentages must contain non-negative integers")
    if sum(percentages.values()) != 100:
        fail("X_leaning_percentages must sum to 100")
    if percentages[report["X_leaning"]] != max(percentages.values()):
        fail("X_leaning must be a largest X_leaning_percentages category")
    plots = report["plots"]
    if not isinstance(plots, dict) or {"outcome_leaning", "camp_distribution"} - plots.keys():
        fail("Grok response must include outcome_leaning and camp_distribution plots")
    outcome_plot = plots["outcome_leaning"]
    if not isinstance(outcome_plot, dict) or not isinstance(outcome_plot.get("data"), list):
        fail("Grok response has an invalid outcome_leaning plot")
    plot_percentages = {
        item.get("label", "").lower(): item.get("value")
        for item in outcome_plot["data"]
        if isinstance(item, dict)
    }
    if plot_percentages != percentages:
        fail("outcome_leaning plot must mirror X_leaning_percentages")
    camp_plot = plots["camp_distribution"]
    if not isinstance(camp_plot, dict) or not isinstance(camp_plot.get("data"), list):
        fail("Grok response has an invalid camp_distribution plot")
    if (
        not isinstance(report["top_tweets"], dict)
        or {"camp1", "camp2"} - report["top_tweets"].keys()
    ):
        fail("Grok response must include top_tweets.camp1 and top_tweets.camp2")
    source_posts = {
        (post.get("url"), post.get("handle"))
        for post in dataset["posts"]
        if isinstance(post, dict)
    }
    for camp_name in ("camp1", "camp2"):
        camp = report["top_tweets"][camp_name]
        if not isinstance(camp, dict) or not isinstance(camp.get("name"), str):
            fail(f"Grok response has an invalid top_tweets.{camp_name}")
        for tweet_name in ("tweet1", "tweet2", "tweet3"):
            tweet = camp.get(tweet_name)
            if tweet is None:
                continue
            if not isinstance(tweet, dict) or not all(
                isinstance(tweet.get(key), str) for key in ("url", "handle", "summary")
            ):
                fail(f"Grok response has an invalid {camp_name}.{tweet_name}")
            if (tweet["url"], tweet["handle"]) not in source_posts:
                fail(
                    f"Grok response cited a post not in the input: {camp_name}.{tweet_name}"
                )


def analyze(dataset: dict[str, Any], model: str) -> dict[str, Any]:
    try:
        from dotenv import load_dotenv
        from openai import OpenAI
    except ImportError as exc:
        fail(f"missing dependency ({exc.name}); run: pip install -r requirements.txt")

    load_dotenv(REPO_ROOT / ".env")
    if not os.getenv("XAI_API_KEY"):
        fail(f"missing XAI_API_KEY; add it to {REPO_ROOT / '.env'} or the environment")
    client = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url=API_BASE, timeout=120.0)
    response = client.responses.create(model=model, input=prompt_for(dataset))
    report = parse_response(response.output_text)
    validate_report(report, dataset)
    return report


def database_url() -> str:
    """Load the database URL without storing it in report data or logs."""
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        fail(f"missing dependency ({exc.name}); run: pip install -r requirements.txt")
    load_dotenv(REPO_ROOT / ".env")
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        fail("missing DATABASE_URL; set it in the environment or .env")
    return value


def ensure_report_table(conn: Any) -> None:
    """Create the output table once; no source records are changed."""
    conn.execute(
        """
        create table if not exists public.topic_opinion_reports (
            id bigint generated by default as identity primary key,
            topic_opinion_id bigint not null unique
                references public.topic_opinions(id) on delete cascade,
            title text not null,
            condition_id text,
            report jsonb not null,
            model text not null,
            generated_at timestamptz not null,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        """
    )
    conn.commit()


def dataset_from_row(row: dict[str, Any]) -> dict[str, Any]:
    collection = row["collection"]
    if not isinstance(collection, dict):
        raise ValueError(f"topic_opinions id={row['id']} has a non-object collection")
    dataset = dict(collection)
    dataset["topic"] = dataset.get("topic") or row["title"]
    dataset["num_tweets"] = (
        dataset.get("num_tweets")
        or (dataset.get("totals") or {}).get("num_tweets")
        or row.get("num_tweets")
    )
    if not isinstance(dataset["num_tweets"], int) or isinstance(
        dataset["num_tweets"], bool
    ):
        raise ValueError(
            f"topic_opinions id={row['id']} is missing total num_tweets; "
            "do not use the sampled posts or threads count as a substitute"
        )
    return dataset


def run_database_reports(model: str, limit: int | None, force: bool) -> int:
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        fail(f"missing dependency ({exc.name}); run: pip install -r requirements.txt")

    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=15) as conn:
        ensure_report_table(conn)
        where = "" if force else "where r.topic_opinion_id is null"
        query = f"""
            select t.id, t.title, t.condition_id, t.collection
            from public.topic_opinions t
            left join public.topic_opinion_reports r on r.topic_opinion_id = t.id
            {where}
            order by t.updated_at asc, t.id asc
        """
        if limit is not None:
            query += " limit %s"
            rows = conn.execute(query, (limit,)).fetchall()
        else:
            rows = conn.execute(query).fetchall()

        if not rows:
            print("No topic_opinions records need analysis.", file=sys.stderr)
            return 0

        completed = 0
        for row in rows:
            try:
                dataset = dataset_from_row(row)
            except ValueError as exc:
                print(f"Skipped: {exc}", file=sys.stderr)
                continue
            report = analyze(dataset, model)
            conn.execute(
                """
                insert into public.topic_opinion_reports
                    (topic_opinion_id, title, condition_id, report, model, generated_at)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (topic_opinion_id) do update set
                    title = excluded.title,
                    condition_id = excluded.condition_id,
                    report = excluded.report,
                    model = excluded.model,
                    generated_at = excluded.generated_at,
                    updated_at = now()
                """,
                (
                    row["id"],
                    dataset["topic"],
                    row["condition_id"],
                    Jsonb(report),
                    model,
                    datetime.now(timezone.utc),
                ),
            )
            conn.commit()
            completed += 1
            print(f"Stored report for topic_opinions id={row['id']}", file=sys.stderr)
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="Path to input JSON, or - to read stdin")
    parser.add_argument(
        "-o", "--output", type=Path, help="Write report JSON to this file"
    )
    parser.add_argument("--model", default=MODEL, help=f"Grok model (default: {MODEL})")
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Analyze unprocessed public.topic_opinions rows and store reports in PostgreSQL",
    )
    parser.add_argument("--limit", type=int, help="Maximum database rows to analyze")
    parser.add_argument(
        "--force", action="store_true", help="Reanalyze rows that already have a stored report"
    )
    args = parser.parse_args()
    if args.from_db:
        if args.input:
            parser.error("input and --from-db cannot be used together")
        if args.output:
            parser.error("--output cannot be used with --from-db")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be positive")
        run_database_reports(args.model, args.limit, args.force)
        return
    if not args.input:
        parser.error("input is required unless --from-db is specified")
    report = analyze(read_input(args.input), args.model)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
