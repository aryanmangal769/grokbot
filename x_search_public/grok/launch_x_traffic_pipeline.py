"""Unified launch point: a Polymarket contract title in, opinion.json out.

    topic (Postgres)
      -> agent 1  xsearch   : topic -> posts.jsonl
      -> agent 2  opinion    : posts.jsonl -> opinion.json
      -> Postgres            : title + collection

The contract row carries more than a title. `resolution_summary` states how the
market actually settles, which the planner would otherwise have to guess at, and
`data.outcomes` carries the live price — so the market anchor is an input rather
than something agent 2 has to rediscover from X posts.

Usage (from repo root):
  python -m x_search_public.grok.launch_x_traffic_pipeline --list
  python -m x_search_public.grok.launch_x_traffic_pipeline --title "Strait of Hormuz ..."
  python -m x_search_public.grok.launch_x_traffic_pipeline --condition-id 0xabc... --window 48
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

from x_search_public.grok import opinion as agent2
from x_search_public.grok import xsearch as agent1
from common.env import REPO_ROOT, require_env

RESULTS_TABLE = "public.topic_opinions"

# Outcomes decided by playing a match, not by anything said on X. Running the
# pipeline on these costs the same and tells you nothing.
MATCH_PATTERN = re.compile(
    r"(\bvs\.?\b|Game\s*\d|First Blood|win on 20\d\d|Total Rounds|O/U|Over/Under)",
    re.IGNORECASE,
)

CREATE_RESULTS_SQL = f"""
create table if not exists {RESULTS_TABLE} (
    id            bigserial primary key,
    title         text        not null,
    condition_id  text,
    collection    jsonb       not null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create unique index if not exists topic_opinions_title_key
    on {RESULTS_TABLE} (title);
"""


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------


def dsn() -> str:
    return require_env("POSTGRES_DSN", placeholder_prefix="postgresql://user")


def connect(readonly: bool = False):
    conn = psycopg2.connect(dsn(), connect_timeout=20)
    if readonly:
        conn.set_session(readonly=True)
    return conn


def ensure_results_table(conn) -> bool:
    """Create the results table if absent. Idempotent, so every run can call it."""
    with conn.cursor() as cur:
        cur.execute(
            "select to_regclass(%s) is not null",
            (RESULTS_TABLE,),
        )
        existed = cur.fetchone()[0]
        cur.execute(CREATE_RESULTS_SQL)
    conn.commit()
    return bool(existed)


def list_contracts(conn, *, limit: int = 100, forecastable_only: bool = False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            select title, condition_id, volume_24hr, liquidity, active, closed
              from public.polymarket_contracts
             order by volume_24hr desc nulls last
             limit %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    if forecastable_only:
        rows = [r for r in rows if not MATCH_PATTERN.search(r["title"] or "")]
    return rows


def fetch_contract(conn, *, title: str | None = None, condition_id: str | None = None):
    if not title and not condition_id:
        raise SystemExit("Need --title or --condition-id")
    where, param = ("condition_id = %s", condition_id) if condition_id else ("title = %s", title)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            select title, url, condition_id, summary, volume_24hr, liquidity,
                   active, closed, data
              from public.polymarket_contracts
             where {where}
             limit 1
            """,
            (param,),
        )
        row = cur.fetchone()
    if row is None and title:
        # Titles are long and easy to mistype; fall back to a prefix match.
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                select title, url, condition_id, summary, volume_24hr, liquidity,
                       active, closed, data
                  from public.polymarket_contracts
                 where title ilike %s
                 order by volume_24hr desc nulls last
                 limit 1
                """,
                (f"%{title}%",),
            )
            row = cur.fetchone()
    if row is None:
        raise SystemExit(f"No contract matching {condition_id or title!r}")
    return dict(row)


def save_opinion(conn, *, title: str, condition_id: str | None, collection: dict[str, Any]) -> None:
    """Upsert by title so re-running a topic refreshes it rather than duplicating."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            insert into {RESULTS_TABLE} (title, condition_id, collection)
                 values (%s, %s, %s)
            on conflict (title) do update
                    set collection   = excluded.collection,
                        condition_id = excluded.condition_id,
                        updated_at   = now()
            """,
            (title, condition_id, json.dumps(collection, ensure_ascii=False)),
        )
    conn.commit()


# --------------------------------------------------------------------------
# contract -> agent inputs
# --------------------------------------------------------------------------


def market_price(contract: dict[str, Any]) -> str:
    outcomes = (contract.get("data") or {}).get("outcomes") or []
    parts = []
    for o in outcomes:
        if isinstance(o, dict) and o.get("outcome") is not None:
            parts.append(f"{o['outcome']}={o.get('price')}")
    return ", ".join(parts) or "unknown"


def build_criteria(contract: dict[str, Any]) -> str:
    """The context the planner needs beyond the bare title."""
    data = contract.get("data") or {}
    bits = []
    if contract.get("summary"):
        bits.append(f"Market summary: {contract['summary']}")
    if data.get("resolution_summary"):
        bits.append(f"How this resolves: {data['resolution_summary']}")
    if data.get("end_date"):
        bits.append(f"Resolution date: {data['end_date']}")
    bits.append(f"Current market price: {market_price(contract)}")
    return "\n".join(bits)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "topic"


def window_for(contract: dict[str, Any], default: int) -> int:
    """Shorter window for imminent markets, longer for distant ones."""
    end = (contract.get("data") or {}).get("end_date")
    if not end:
        return default
    try:
        at = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return default
    hours_left = (at - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours_left <= 0:
        # Past its stated end date — the row may be stale, so do not let a
        # negative horizon pick the window.
        return default
    if hours_left <= 72:
        return 24
    if hours_left <= 24 * 30:
        return 48
    return 96


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def run(
    *,
    title: str | None,
    condition_id: str | None,
    outdir: Path | None = None,
    window: int = 0,
    facets: int = agent1.DEFAULT_FACETS,
    slices: int = 4,
    target: int = agent1.DEFAULT_TARGET,
    min_relevance: float = agent1.DEFAULT_MIN_RELEVANCE,
    min_impressions: int = agent1.DEFAULT_MIN_IMPRESSIONS,
    min_replies: int = 0,
    min_followers: int = agent1.DEFAULT_MIN_FOLLOWERS,
    thread_limit: int = 20,
    workers: int = 12,
    force: bool = False,
    save: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    def say(message: str) -> None:
        if verbose:
            print(message, file=sys.stderr, flush=True)

    conn = connect()
    try:
        existed = ensure_results_table(conn)
        say(f"results table {RESULTS_TABLE}: {'reused' if existed else 'created'}")

        contract = fetch_contract(conn, title=title, condition_id=condition_id)
        topic = contract["title"]
        say(f"topic: {topic}")
        say(f"market: {market_price(contract)}")

        if MATCH_PATTERN.search(topic) and not force:
            raise SystemExit(
                f"{topic!r} looks like a match/game outcome, which is decided by play "
                "rather than by anything said on X. Pass --force to run it anyway."
            )

        criteria = build_criteria(contract)
        window = window or window_for(contract, 48)
        outdir = outdir or Path("data") / slugify(topic)
        say(f"window {window}h -> {outdir}")

        say("--- agent 1: harvesting posts ---")
        dataset = agent1.harvest(
            topic,
            criteria=criteria,
            window_hours=window,
            slices=slices,
            n_facets=facets,
            workers=workers,
            do_hydrate=False,
            target=target,
            min_relevance=min_relevance,
            min_impressions=min_impressions,
            min_replies=min_replies,
            min_followers=min_followers,
            checkpoint=(outdir / "sweeps.jsonl"),
            verbose=verbose,
        )
        posts_path, _ = agent1.write_outputs(dataset, outdir)
        say(f"agent 1 -> {len(dataset['posts'])} posts")

        if not dataset["posts"]:
            raise SystemExit("agent 1 found no qualifying posts; nothing to summarise")

        say("--- agent 2: reading comment threads ---")
        collection = agent2.analyse(
            outdir,
            topic=topic,
            limit=thread_limit,
            workers=min(workers, 8),
            market_price=market_price(contract),
            checkpoint=(outdir / "threads.jsonl"),
            verbose=verbose,
        )

        out_path = (outdir if outdir.is_absolute() else REPO_ROOT / outdir) / "opinion.json"
        out_path.write_text(json.dumps(collection, indent=2, ensure_ascii=False), encoding="utf-8")
        say(f"agent 2 -> {out_path}")

        if save:
            save_opinion(
                conn,
                title=topic,
                condition_id=contract.get("condition_id"),
                collection=collection,
            )
            say(f"saved to {RESULTS_TABLE}")

        return collection
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Polymarket topic -> X posts -> public opinion, saved to Postgres"
    )
    parser.add_argument("--list", action="store_true", help="List contracts and exit")
    parser.add_argument("--forecastable", action="store_true", help="With --list, hide match outcomes")
    parser.add_argument("--title", default=None, help="Contract title (substring match works)")
    parser.add_argument("--condition-id", default=None)
    parser.add_argument("--outdir", type=Path, default=None)
    parser.add_argument("--window", type=int, default=0, help="Hours; 0 derives from end_date")
    parser.add_argument("--facets", type=int, default=agent1.DEFAULT_FACETS)
    parser.add_argument("--slices", type=int, default=4)
    parser.add_argument("--target", type=int, default=agent1.DEFAULT_TARGET)
    parser.add_argument("--min-relevance", type=float, default=agent1.DEFAULT_MIN_RELEVANCE)
    parser.add_argument("--min-impressions", type=int, default=agent1.DEFAULT_MIN_IMPRESSIONS)
    parser.add_argument("--min-replies", type=int, default=0)
    parser.add_argument("--min-followers", type=int, default=agent1.DEFAULT_MIN_FOLLOWERS)
    parser.add_argument("--threads", type=int, default=20, dest="thread_limit",
                        help="How many top-reach posts get their comments read")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--force", action="store_true", help="Run even if it looks like a match outcome")
    parser.add_argument("--no-save", action="store_true", help="Skip writing to Postgres")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.list:
        conn = connect(readonly=True)
        try:
            rows = list_contracts(conn, forecastable_only=args.forecastable)
        finally:
            conn.close()
        for r in rows:
            flag = "  " if not MATCH_PATTERN.search(r["title"] or "") else "x "
            print(f"{flag}{(r['volume_24hr'] or 0):>12,.0f}  {r['title']}")
        print(f"\n{len(rows)} contracts ('x' = match outcome, skipped without --force)")
        return

    run(
        title=args.title,
        condition_id=args.condition_id,
        outdir=args.outdir,
        window=args.window,
        facets=args.facets,
        slices=args.slices,
        target=args.target,
        min_relevance=args.min_relevance,
        min_impressions=args.min_impressions,
        min_replies=args.min_replies,
        min_followers=args.min_followers,
        thread_limit=args.thread_limit,
        workers=args.workers,
        force=args.force,
        save=not args.no_save,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
