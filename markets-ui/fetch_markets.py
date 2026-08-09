"""Snapshot report-backed Polymarket markets into a static markets.json.

This is a one-off data step, NOT a runtime backend: run it to refresh the
snapshot, then the panel (index.html) reads markets.json as a plain static file.

Usage (from this folder):
  pip install "psycopg[binary]" python-dotenv
  python fetch_markets.py            # writes the profile-selected markets.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

# A market can appear in more than one profile when it is relevant to both
# audiences. Every entry here is backed by a topic_opinion_report; Infantino
# is intentionally omitted from sports.
PROFILE_MARKETS = {
    "sports": [
        "Will Brooklyn Nets win the 2027 NBA Finals?",
        "Will Mohammed bin Salman attend Cristiano Ronaldo's wedding?",
        "Will Mohamed Salah win the 2026 Ballon d'Or?",
        "Will Kai and Speed beat the Minecraft challenge by August 17?",
        "Will Manchester City win the 2026-27 UEFA Champions League Championship?",
    ],
    "politics": [
        "Strait of Hormuz traffic returns to normal by August 31?",
        "Putin out as President of Russia by December 31, 2026?",
        "Iran leadership change by December 31?",
        "Will Count Binface win the Clacton by-election?",
        "Will the U.S. invade Iran before 2027?",
    ],
    "culture": [
        "Will Elon Musk post <40 tweets from August 8 to August 10, 2026?",
        "Will Kai and Speed beat the Minecraft challenge by August 17?",
        "Will the total domestic gross for Spider-Man: Brand New Day be less than 400m by August 31?",
        "Will Mohammed bin Salman attend Cristiano Ronaldo's wedding?",
        "Will Count Binface win the Clacton by-election?",
    ],
}


def profiles_for(title: str) -> list[str]:
    return [profile for profile, titles in PROFILE_MARKETS.items() if title in titles]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()

    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            """SELECT p.title, p.url, p.condition_id, p.summary, p.volume, p.volume_24hr,
                      p.liquidity, p.data, r.report
               FROM public.polymarket_contracts AS p
               JOIN public.topic_opinion_reports AS r ON r.condition_id = p.condition_id
               WHERE p.active AND NOT p.closed
               ORDER BY p.volume_24hr DESC NULLS LAST"""
        ).fetchall()

    all_markets = []
    for title, u, cid, summary, vol, vol24, liq, data, report in rows:
        profiles = profiles_for(title)
        if not profiles:
            continue
        d = data if isinstance(data, dict) else json.loads(data or "{}")
        report = report if isinstance(report, dict) else json.loads(report or "{}")
        outcomes = [
            {
                "label": o.get("outcome"),
                "pct": round((o.get("price") or 0) * 100, 1),
                "best_bid": o.get("best_bid"),
                "best_ask": o.get("best_ask"),
                "bid_depth": o.get("bid_depth"),
                "ask_depth": o.get("ask_depth"),
            }
            for o in (d.get("outcomes") or []) if isinstance(o, dict)
        ]
        outcomes.sort(key=lambda o: o["pct"], reverse=True)
        top = outcomes[0] if outcomes else {"label": "Yes", "pct": 0}
        all_markets.append({
            "id": cid,
            "title": title,
            "url": u,
            "profiles": profiles,
            "summary": summary,
            "volume": vol or 0,
            "volume_24hr": vol24 or 0,
            "liquidity": liq or 0,
            "end_date": d.get("end_date"),
            "top_outcome": {"label": top["label"], "pct": top["pct"]},
            "outcomes": outcomes[:4],
            "price_read": d.get("price_read"),
            "resolution_summary": d.get("resolution_summary"),
            "notes": d.get("notes") or [],
            "report": report,
        })

    # Keep the selected report-backed markets in the exact profile order above.
    by_title = {market["title"]: market for market in all_markets}
    markets = []
    for titles in PROFILE_MARKETS.values():
        for title in titles:
            market = by_title.get(title)
            if market and market not in markets:
                markets.append(market)

    out = HERE / "markets.json"
    out.write_text(json.dumps(markets, indent=2))
    counts = {profile: sum(profile in m["profiles"] for m in markets) for profile in PROFILE_MARKETS}
    print(f"Wrote {len(markets)} markets -> {out}  {counts}")


if __name__ == "__main__":
    main()
