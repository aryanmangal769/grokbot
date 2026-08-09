"""Polymarket Contract Skill — live, verified detail on one Polymarket contract.

NO MOCKED DATA. Every field either comes back from a real, successful API call
or is explicitly marked unavailable/None — never fabricated or guessed.

Sources (all public, no auth required):
  - Gamma API   https://gamma-api.polymarket.com   market/event metadata
  - CLOB API    https://clob.polymarket.com        live order book, midpoint, spread, price history
  - Data API    https://data-api.polymarket.com    on-chain trades

Verification is DETERMINISTIC PYTHON, not an LLM judgment call — outcome-price
sum checks, Gamma-vs-CLOB consistency, crossed-book detection, staleness, and
market-status checks all run as plain math/logic against the real pulled data.
Grok (xAI) only runs AFTER verification, to turn the verified bundle + the
market's long resolution-criteria text into a readable profile — instructed to
cite only numbers actually present in the bundle, and to say "unavailable"
rather than infer or invent when something is missing.

Usage:
    python -m polymarket_contract "some search text"
    python -m polymarket_contract --slug=dota2-amaru-jenz-2026-07-28

Env:
    XAI_API_KEY   optional — enables the Grok synthesis pass (Responses API).
                  Without it, run() still returns the full live+verified bundle,
                  just without the written profile.
"""
from __future__ import annotations
import os
import json
import time
import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field, ValidationError

try:
    from dotenv import load_dotenv
    import pathlib
    load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")  # this dir's .env, regardless of cwd
except ImportError:
    pass

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
UA = {"User-Agent": "polymarket-contract-skill/0.1"}
TIMEOUT = 20

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.5")


# --------------------------------------------------------------------------- #
# Low-level fetchers — each returns (data, error). error is None on success.
# A failure NEVER gets silently replaced with a guess; it's surfaced.
# --------------------------------------------------------------------------- #
def _get(url: str, params: dict | None = None) -> tuple[Any, Optional[str]]:
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code} from {url}"
        return r.json(), None
    except Exception as e:
        return None, f"request failed for {url}: {e}"


def gamma_search_events(query: str, limit: int = 8) -> tuple[list[dict], Optional[str]]:
    """Polymarket's own full-text search endpoint (Gamma /public-search) — real
    search across all events, not just the top-N by volume."""
    data, err = _get(f"{GAMMA}/public-search", {"q": query})
    if err:
        return [], err
    events = data.get("events", []) if isinstance(data, dict) else []
    if not events:
        return [], f"no events matched query={query!r} via public-search"

    # rank: prefer active+open, then relevance to query terms in the title, then volume
    q_terms = set(query.lower().split())
    def score(e: dict) -> tuple:
        title = e.get("title", "").lower()
        term_hits = sum(1 for t in q_terms if t in title)
        return (bool(e.get("active")) and not e.get("closed"), term_hits,
               float(e.get("volume24hr") or 0))
    events = sorted(events, key=score, reverse=True)
    return events[:limit], None


def gamma_event_by_slug(slug: str) -> tuple[Optional[dict], Optional[str]]:
    data, err = _get(f"{GAMMA}/events", {"slug": slug})
    if err:
        return None, err
    if not data:
        return None, f"no event found for slug={slug!r}"
    return data[0], None


def clob_midpoint(token_id: str) -> tuple[Optional[float], Optional[str]]:
    data, err = _get(f"{CLOB}/midpoint", {"token_id": token_id})
    if err:
        return None, err
    try:
        return float(data["mid"]), None
    except Exception:
        return None, f"unexpected midpoint payload: {data}"


def clob_spread(token_id: str) -> tuple[Optional[float], Optional[str]]:
    data, err = _get(f"{CLOB}/spread", {"token_id": token_id})
    if err:
        return None, err
    try:
        return float(data["spread"]), None
    except Exception:
        return None, f"unexpected spread payload: {data}"


def clob_book(token_id: str) -> tuple[Optional[dict], Optional[str]]:
    data, err = _get(f"{CLOB}/book", {"token_id": token_id})
    if err:
        return None, err
    if "bids" not in data or "asks" not in data:
        return None, f"book response missing bids/asks: keys={list(data.keys())}"
    return data, None


def clob_price_history(token_id: str, interval: str = "1h", fidelity: int = 10
                       ) -> tuple[list[dict], Optional[str]]:
    data, err = _get(f"{CLOB}/prices-history",
                     {"market": token_id, "interval": interval, "fidelity": fidelity})
    if err:
        return [], err
    return data.get("history", []), None


def data_api_trades(condition_id: str, limit: int = 20) -> tuple[list[dict], Optional[str]]:
    data, err = _get(f"{DATA_API}/trades", {"market": condition_id, "limit": limit})
    if err:
        return [], err
    return (data if isinstance(data, list) else data.get("data", [])), None


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class OutcomeSnapshot:
    outcome: str
    token_id: str
    gamma_price: Optional[float]
    clob_midpoint: Optional[float]
    clob_spread: Optional[float]
    best_bid: Optional[float]
    best_ask: Optional[float]
    bid_depth: Optional[float]      # sum of size at top 5 bid levels
    ask_depth: Optional[float]      # sum of size at top 5 ask levels
    book_timestamp: Optional[str]   # epoch ms as returned by CLOB, converted to ISO
    price_history: list[dict]
    errors: list[str] = field(default_factory=list)


@dataclass
class ContractReport:
    fetched_at: str
    query: str
    resolved_slug: Optional[str]
    title: Optional[str]
    description: Optional[str]
    resolution_source: Optional[str]
    end_date: Optional[str]
    active: Optional[bool]
    closed: Optional[bool]
    accepting_orders: Optional[bool]
    uma_resolution_status: Optional[str]
    volume: Optional[float]
    volume_24hr: Optional[float]
    liquidity: Optional[float]
    condition_id: Optional[str]
    outcomes: list[OutcomeSnapshot]
    recent_trades: list[dict]
    verification: dict
    sources_ok: list[str]
    sources_failed: list[str]
    grok_profile: Optional[dict] = None


def _empty_verification(reason: str) -> dict:
    """Used whenever no market was found to verify against — never omit the
    'summary'/'checks'/'all_pass' keys, so callers can rely on the shape."""
    return {"checks": {}, "summary": f"0/0 checks run — {reason}", "all_pass": False}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _pick_market(event: dict) -> tuple[Optional[dict], Optional[str]]:
    """An event can bundle many markets (e.g. multi-outcome). For a single
    'contract', pick the most liquid binary market in the event."""
    markets = event.get("markets", [])
    if not markets:
        return None, "event has no markets[]"
    markets = sorted(markets, key=lambda m: float(m.get("liquidityNum") or 0), reverse=True)
    return markets[0], None


def fetch_contract(query: str = None, slug: str = None) -> ContractReport:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    sources_ok, sources_failed = [], []

    if slug:
        event, err = gamma_event_by_slug(slug)
        if err:
            sources_failed.append(f"gamma:event_by_slug — {err}")
            return ContractReport(now, query or slug, slug, None, None, None, None,
                                  None, None, None, None, None, None, None, None,
                                  [], [], _empty_verification(err), sources_ok, sources_failed)
        sources_ok.append("gamma:event_by_slug")
    else:
        hits, err = gamma_search_events(query)
        if err or not hits:
            reason = err or "no matching event"
            sources_failed.append(f"gamma:search — {reason}")
            return ContractReport(now, query, None, None, None, None, None,
                                  None, None, None, None, None, None, None, None,
                                  [], [], _empty_verification(reason), sources_ok, sources_failed)
        sources_ok.append("gamma:search")
        event = hits[0]

    market, err = _pick_market(event)
    if err:
        sources_failed.append(f"pick_market — {err}")

    outcomes: list[OutcomeSnapshot] = []
    condition_id = market.get("conditionId") if market else None

    if market:
        try:
            outcome_names = json.loads(market.get("outcomes", "[]"))
            outcome_prices = json.loads(market.get("outcomePrices", "[]"))
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
        except Exception as e:
            sources_failed.append(f"gamma:market_json_parse — {e}")
            outcome_names, outcome_prices, token_ids = [], [], []

        for i, name in enumerate(outcome_names):
            token_id = token_ids[i] if i < len(token_ids) else None
            gamma_price = float(outcome_prices[i]) if i < len(outcome_prices) else None
            errs = []

            mid = spread = bid = ask = bid_depth = ask_depth = book_ts = None
            history: list[dict] = []

            if token_id:
                mid, e1 = clob_midpoint(token_id)
                if e1: errs.append(e1)
                else: sources_ok.append(f"clob:midpoint[{name}]")

                spread, e2 = clob_spread(token_id)
                if e2: errs.append(e2)
                else: sources_ok.append(f"clob:spread[{name}]")

                book, e3 = clob_book(token_id)
                if e3:
                    errs.append(e3)
                else:
                    sources_ok.append(f"clob:book[{name}]")
                    bids = sorted(book["bids"], key=lambda x: -float(x["price"]))
                    asks = sorted(book["asks"], key=lambda x: float(x["price"]))
                    bid = float(bids[0]["price"]) if bids else None
                    ask = float(asks[0]["price"]) if asks else None
                    bid_depth = sum(float(b["size"]) for b in bids[:5]) if bids else 0.0
                    ask_depth = sum(float(a["size"]) for a in asks[:5]) if asks else 0.0
                    ts_raw = book.get("timestamp")
                    if ts_raw:
                        book_ts = dt.datetime.fromtimestamp(
                            int(ts_raw) / 1000, tz=dt.timezone.utc).isoformat()

                history, e4 = clob_price_history(token_id)
                if e4: errs.append(e4)
                else: sources_ok.append(f"clob:prices-history[{name}]")
            else:
                errs.append("no clobTokenId for this outcome")

            outcomes.append(OutcomeSnapshot(
                outcome=name, token_id=token_id or "", gamma_price=gamma_price,
                clob_midpoint=mid, clob_spread=spread, best_bid=bid, best_ask=ask,
                bid_depth=bid_depth, ask_depth=ask_depth, book_timestamp=book_ts,
                price_history=history, errors=errs,
            ))

    trades: list[dict] = []
    if condition_id:
        trades, terr = data_api_trades(condition_id)
        if terr:
            sources_failed.append(f"data_api:trades — {terr}")
        else:
            sources_ok.append("data_api:trades")

    report = ContractReport(
        fetched_at=now, query=query or slug,
        resolved_slug=event.get("slug"), title=market.get("question") if market else event.get("title"),
        description=(market.get("description") if market else event.get("description")),
        resolution_source=(market.get("resolutionSource") if market else event.get("resolutionSource")),
        end_date=market.get("endDate") if market else event.get("endDate"),
        active=market.get("active") if market else event.get("active"),
        closed=market.get("closed") if market else event.get("closed"),
        accepting_orders=market.get("acceptingOrders") if market else None,
        uma_resolution_status=market.get("umaResolutionStatus") if market else None,
        volume=float(market["volumeNum"]) if market and market.get("volumeNum") else event.get("volume"),
        volume_24hr=float(market["volume24hr"]) if market and market.get("volume24hr") else event.get("volume24hr"),
        liquidity=float(market["liquidityNum"]) if market and market.get("liquidityNum") else event.get("liquidity"),
        condition_id=condition_id,
        outcomes=outcomes, recent_trades=trades,
        verification={}, sources_ok=sources_ok, sources_failed=sources_failed,
    )
    report.verification = verify(report)
    return report


# --------------------------------------------------------------------------- #
# Verification — deterministic checks against the LIVE data actually pulled.
# --------------------------------------------------------------------------- #
def verify(r: ContractReport) -> dict:
    checks: dict[str, dict] = {}

    # 1. outcome prices should sum to ~1.0
    prices = [o.gamma_price for o in r.outcomes if o.gamma_price is not None]
    if prices:
        total = round(sum(prices), 4)
        checks["outcome_price_sum"] = {
            "pass": abs(total - 1.0) <= 0.02,
            "value": total, "expected": "~1.00 (±0.02)",
        }

    # 2. Gamma price vs live CLOB midpoint — should be close (Gamma can lag)
    for o in r.outcomes:
        if o.gamma_price is not None and o.clob_midpoint is not None:
            diff = round(abs(o.gamma_price - o.clob_midpoint), 4)
            checks[f"gamma_vs_clob[{o.outcome}]"] = {
                "pass": diff <= 0.03,
                "value": diff, "expected": "<=0.03 diff between listed price and live midpoint",
            }

    # 3. crossed book check — best_bid must be < best_ask
    for o in r.outcomes:
        if o.best_bid is not None and o.best_ask is not None:
            checks[f"book_not_crossed[{o.outcome}]"] = {
                "pass": o.best_bid < o.best_ask,
                "value": f"bid={o.best_bid} ask={o.best_ask}",
                "expected": "best_bid < best_ask",
            }

    # 4. reported spread matches book-derived spread
    for o in r.outcomes:
        if o.clob_spread is not None and o.best_bid is not None and o.best_ask is not None:
            derived = round(o.best_ask - o.best_bid, 4)
            checks[f"spread_matches_book[{o.outcome}]"] = {
                "pass": abs(derived - round(o.clob_spread, 4)) <= 0.005,
                "value": f"reported={o.clob_spread} derived={derived}",
                "expected": "reported spread == best_ask - best_bid",
            }

    # 5. book freshness — flag if the order-book snapshot is stale
    now = dt.datetime.now(dt.timezone.utc)
    for o in r.outcomes:
        if o.book_timestamp:
            age_s = (now - dt.datetime.fromisoformat(o.book_timestamp)).total_seconds()
            checks[f"book_freshness[{o.outcome}]"] = {
                "pass": age_s <= 300,
                "value": f"{age_s:.0f}s old", "expected": "<=300s old",
            }

    # 6. liquidity presence
    for o in r.outcomes:
        if o.bid_depth is not None and o.ask_depth is not None:
            checks[f"has_liquidity[{o.outcome}]"] = {
                "pass": (o.bid_depth + o.ask_depth) > 0,
                "value": f"bid_depth={o.bid_depth} ask_depth={o.ask_depth}",
                "expected": "> 0 on at least one side",
            }

    # 7. market status
    if r.active is not None or r.closed is not None:
        checks["market_is_live"] = {
            "pass": bool(r.active) and not bool(r.closed),
            "value": f"active={r.active} closed={r.closed}",
            "expected": "active=true, closed=false",
        }

    # 8. UMA resolution status — flag if a resolution is actively in dispute/proposed
    if r.uma_resolution_status:
        checks["resolution_status"] = {
            "pass": r.uma_resolution_status not in ("disputed",),
            "value": r.uma_resolution_status,
            "expected": "not 'disputed'",
        }

    n_pass = sum(1 for c in checks.values() if c["pass"])
    return {
        "checks": checks,
        "summary": f"{n_pass}/{len(checks)} checks passed",
        "all_pass": n_pass == len(checks),
    }


# --------------------------------------------------------------------------- #
# Pydantic models — the schema-validated boundary.
#
# GrokProfile: xAI's structured-output response is generated straight from
# this model's JSON schema (response_format=json_schema) AND re-validated on
# receipt with GrokProfile.model_validate(). A response that doesn't fit the
# schema is treated as a failure (flagged), never silently accepted.
#
# ContractDocument: the single, schema-guaranteed JSON shape that gets saved
# to disk and to the DB — the "json which has the summary and all other
# things about the contract" the whole component exists to produce.
# --------------------------------------------------------------------------- #
class GrokProfile(BaseModel):
    profile: str = Field(..., description="2-3 sentence plain-language description of this contract")
    resolution_summary: str = Field(..., description="1-3 sentence summary of how/when this "
                                    "resolves, from the actual resolution text given — do not "
                                    "invent criteria not present")
    price_read: str = Field(..., description="1-2 sentences on the current live price + "
                            "order-book picture")
    flags: list[str] = Field(default_factory=list, description="any verification failures or "
                             "data gaps, explained in plain language — empty if none")


class OutcomeMetric(BaseModel):
    """Just what you need to read this outcome's live price + tradability."""
    outcome: str
    price: Optional[float]        # live CLOB midpoint, falls back to Gamma's listed price
    best_bid: Optional[float]
    best_ask: Optional[float]
    bid_depth: Optional[float]    # summed size, top 5 book levels
    ask_depth: Optional[float]


class ContractDocument(BaseModel):
    """The stored/exported document — trimmed to what's actually useful for
    understanding a contract, keyed by `title` per the request. `condition_id`
    is kept alongside as the collision-proof key if two contracts ever share
    a title. No slug (use `url`), no verification-check dump, no raw legal
    text — just the metrics and Grok's plain-language reads."""
    title: str
    url: str
    condition_id: Optional[str]
    fetched_at: str
    end_date: Optional[str]
    active: Optional[bool]
    closed: Optional[bool]
    volume: Optional[float]
    volume_24hr: Optional[float]
    liquidity: Optional[float]
    outcomes: list[OutcomeMetric]
    summary: Optional[str] = None              # what this contract is
    resolution_summary: Optional[str] = None   # how/when it resolves
    price_read: Optional[str] = None           # current price/order-book picture, plain language
    notes: list[str] = Field(default_factory=list)  # real data-quality issues only, if any


# --------------------------------------------------------------------------- #
# Grok synthesis pass — optional, runs only over the already-verified bundle.
# --------------------------------------------------------------------------- #
def grok_profile(r: ContractReport) -> Optional[GrokProfile]:
    if not XAI_API_KEY:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=XAI_API_KEY, base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"))

    bundle = {
        "title": r.title, "resolution_source": r.resolution_source,
        "end_date": r.end_date, "active": r.active, "closed": r.closed,
        "volume": r.volume, "volume_24hr": r.volume_24hr, "liquidity": r.liquidity,
        "outcomes": [{"outcome": o.outcome, "gamma_price": o.gamma_price,
                     "clob_midpoint": o.clob_midpoint, "best_bid": o.best_bid,
                     "best_ask": o.best_ask, "bid_depth": o.bid_depth,
                     "ask_depth": o.ask_depth} for o in r.outcomes],
        "verification": r.verification,
        "sources_failed": r.sources_failed,
        "resolution_text": (r.description or "")[:2500],
    }
    schema = GrokProfile.model_json_schema()
    try:
        resp = client.chat.completions.create(
            model=XAI_MODEL,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "grok_profile", "strict": True, "schema": schema}},
            messages=[
                {"role": "system", "content":
                 "You describe a live prediction-market contract using ONLY the verified "
                 "data given below. Never invent a number, date, or resolution rule that "
                 "isn't present in the data. If verification flagged an issue or a source "
                 "failed, mention it plainly."},
                {"role": "user", "content": json.dumps(bundle, default=str)},
            ],
        )
        raw = resp.choices[0].message.content
        return GrokProfile.model_validate_json(raw)
    except ValidationError as e:
        return GrokProfile(profile="", resolution_summary="", price_read="",
                           flags=[f"Grok response failed schema validation: {e}"])
    except Exception as e:
        return GrokProfile(profile="", resolution_summary="", price_read="",
                           flags=[f"Grok synthesis failed: {e}"])


def run(query: str = None, slug: str = None) -> ContractReport:
    report = fetch_contract(query=query, slug=slug)
    gp = grok_profile(report)
    report.grok_profile = gp.model_dump() if gp else None
    return report


def _fallback_notes(report: ContractReport) -> list[str]:
    """Used when Grok didn't run (no XAI_API_KEY) — still surface real,
    concrete data issues rather than silently dropping them from `notes`."""
    notes = []
    if report.sources_failed:
        notes.append(f"{len(report.sources_failed)} source call(s) failed: "
                     + "; ".join(report.sources_failed[:3]))
    failed_checks = [k for k, v in report.verification.get("checks", {}).items()
                     if not v.get("pass")]
    if failed_checks:
        notes.append(f"failed checks: {', '.join(failed_checks)}")
    return notes


def to_document(report: ContractReport) -> ContractDocument:
    """The full internal report -> the lean, schema-guaranteed storage
    document. Verification still runs internally (feeds Grok's `notes`, and
    is printed in full by the CLI for debugging) — it's just not persisted
    as a verbose checks dump in the stored/exported schema."""
    gp = GrokProfile.model_validate(report.grok_profile) if report.grok_profile else None
    url = f"https://polymarket.com/event/{report.resolved_slug}" if report.resolved_slug else ""
    return ContractDocument(
        title=report.title or report.query, url=url,
        condition_id=report.condition_id, fetched_at=report.fetched_at,
        end_date=report.end_date, active=report.active, closed=report.closed,
        volume=report.volume, volume_24hr=report.volume_24hr, liquidity=report.liquidity,
        outcomes=[OutcomeMetric(
            outcome=o.outcome, price=(o.clob_midpoint if o.clob_midpoint is not None else o.gamma_price),
            best_bid=o.best_bid, best_ask=o.best_ask,
            bid_depth=o.bid_depth, ask_depth=o.ask_depth,
        ) for o in report.outcomes],
        summary=gp.profile if gp else None,
        resolution_summary=gp.resolution_summary if gp else None,
        price_read=gp.price_read if gp else None,
        notes=(gp.flags if gp else _fallback_notes(report)),
    )


def to_dict(report: ContractReport) -> dict:
    return asdict(report)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    slug_arg = next((a.split("=", 1)[1] for a in args if a.startswith("--slug=")), None)
    save_json_arg = next((a.split("=", 1)[1] for a in args if a.startswith("--save-json=")), None)
    save_db = "--save-db" in args
    query_arg = " ".join(a for a in args if not a.startswith("--")) or None

    rep = run(query=query_arg, slug=slug_arg)
    print(f"\n=== {rep.title or rep.query} ===")
    print("slug:", rep.resolved_slug, "| active:", rep.active, "| closed:", rep.closed)
    print("sources ok:", rep.sources_ok)
    if rep.sources_failed:
        print("sources FAILED:", rep.sources_failed)
    print("volume:", rep.volume, "| 24h:", rep.volume_24hr, "| liquidity:", rep.liquidity)
    for o in rep.outcomes:
        print(f"  [{o.outcome}] gamma={o.gamma_price} clob_mid={o.clob_midpoint} "
              f"bid={o.best_bid} ask={o.best_ask} depth(b/a)={o.bid_depth}/{o.ask_depth}")
        if o.errors:
            print("     errors:", o.errors)
    print("\nVERIFICATION:", rep.verification["summary"])
    for k, v in rep.verification["checks"].items():
        mark = "PASS" if v["pass"] else "FAIL"
        print(f"  [{mark}] {k}: {v['value']}  (expect {v['expected']})")
    if rep.grok_profile:
        print("\nGROK PROFILE:")
        print(json.dumps(rep.grok_profile, indent=1))
    else:
        print("\n(no XAI_API_KEY set — skipping Grok synthesis pass)")

    if rep.title:   # only build/save a document if we actually found a contract
        doc = to_document(rep)
        print("\nSTORED DOCUMENT (lean schema):")
        print(doc.model_dump_json(indent=1))
        if save_json_arg:
            with open(save_json_arg, "w") as f:
                f.write(doc.model_dump_json(indent=2))
            print(f"\nSaved JSON -> {save_json_arg}")
        if save_db:
            from db import save_contract
            result = save_contract(doc)
            print(f"\nDB save: {result}")
