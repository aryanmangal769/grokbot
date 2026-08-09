"""Bulk ingestion — NO user input. Pulls the top N real, active Polymarket
contracts (ranked by 24h volume), verifies each, generates a Grok summary,
computes a local semantic embedding, and upserts into Postgres keyed by title.

Run:
    python ingest_bulk.py                 # top 100, with Grok summaries
    python ingest_bulk.py --n=50          # top 50
    python ingest_bulk.py --no-grok       # skip Grok synthesis (faster, no xAI calls)

Every contract goes through the same live-fetch + deterministic-verification
path as the single-contract flow (polymarket_contract.fetch_contract) — bulk
mode changes nothing about what's real; it just runs that path N times against
Polymarket's own top-volume listing instead of a search query.
"""
from __future__ import annotations
import sys
import time

from polymarket_contract import (
    GAMMA, UA, _get, fetch_contract, grok_profile, to_document,
)
import db
import embeddings


def top_event_slugs(n: int) -> list[str]:
    """The real top-N active Polymarket events by 24h volume — straight from
    Gamma, Polymarket's own discovery API. No user input, no query needed."""
    data, err = _get(f"{GAMMA}/events",
                     {"active": "true", "closed": "false", "limit": n,
                      "order": "volume24hr", "ascending": "false"})
    if err:
        print(f"FATAL: could not list top events: {err}")
        return []
    return [e["slug"] for e in data if e.get("slug")]


def embed_text_for(doc) -> str:
    parts = [doc.title or "", doc.summary or "", doc.resolution_summary or ""]
    return " — ".join(p for p in parts if p)


def run_bulk(n: int = 100, use_grok: bool = True, sleep_between: float = 0.15) -> dict:
    if not db.configured():
        print("FATAL: DATABASE_URL not set in .env — nothing to ingest into.")
        return {"ok": False, "error": "DATABASE_URL not set"}

    print(f"Ensuring schema (table + pgvector extension + index)...")
    init = db.init_schema()
    if not init.get("ok"):
        print("FATAL: schema init failed:", init.get("error"))
        return init
    print("Schema OK.\n")

    slugs = top_event_slugs(n)
    print(f"Fetched {len(slugs)} real, active Polymarket event slugs (top by 24h volume).\n")

    saved, failed = [], []
    for i, slug in enumerate(slugs, 1):
        try:
            report = fetch_contract(slug=slug)
            if not report.title:
                failed.append({"slug": slug, "error": "no contract resolved"})
                print(f"[{i}/{len(slugs)}] SKIP {slug} — no contract resolved")
                continue

            if use_grok:
                gp = grok_profile(report)
                report.grok_profile = gp.model_dump() if gp else None

            doc = to_document(report)
            vec = embeddings.embed(embed_text_for(doc))
            result = db.save_contract(doc, embedding=vec)

            if result.get("ok"):
                saved.append(doc.title)
                print(f"[{i}/{len(slugs)}] OK    {doc.title!r}")
            else:
                failed.append({"slug": slug, "error": result.get("error")})
                print(f"[{i}/{len(slugs)}] DB-FAIL {slug} — {result.get('error')}")
        except Exception as e:
            failed.append({"slug": slug, "error": str(e)})
            print(f"[{i}/{len(slugs)}] EXC   {slug} — {e}")
        time.sleep(sleep_between)  # be polite to Polymarket's public API

    print(f"\n=== DONE: {len(saved)} saved, {len(failed)} failed (of {len(slugs)}) ===")
    if failed:
        print("Failures:", failed[:10], "..." if len(failed) > 10 else "")
    return {"ok": True, "saved": len(saved), "failed": len(failed), "total": len(slugs)}


if __name__ == "__main__":
    args = sys.argv[1:]
    n = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--n=")), 100)
    use_grok = "--no-grok" not in args
    run_bulk(n=n, use_grok=use_grok)
