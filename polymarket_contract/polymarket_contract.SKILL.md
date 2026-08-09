---
name: polymarket-contract
description: >
  Live, verified detail on one real Polymarket contract. Pulls directly from
  Polymarket's own Gamma/CLOB/Data APIs (no auth needed) — market metadata,
  live order book, midpoint, spread, price history, and recent on-chain trades.
  Every field is either real data from a successful API call or explicitly
  marked unavailable — nothing is mocked, guessed, or fabricated. Runs 13
  deterministic verification checks against the pulled data (price-sum
  sanity, Gamma-vs-CLOB consistency, crossed-book detection, staleness,
  market-status, UMA resolution state) before anything is reported. Grok
  (xAI) then writes a plain-language profile, but only over the verified
  bundle — instructed to cite only numbers present and say "unavailable"
  rather than invent.
implementation: polymarket_contract.py
version: 0.1
---

# Polymarket Contract skill

One call in, one real, verified contract snapshot out.

```bash
cd polymarket_contract
pip install -r requirements.txt

python polymarket_contract.py "bitcoin"                          # free-text search
python polymarket_contract.py --slug=dota2-amaru-jenz-2026-07-28  # exact event
```

```python
from polymarket_contract import run
report = run(query="fed rate decision")
report = run(slug="some-event-slug")
```

## No mocked data — how that's actually enforced

- Every field comes from `requests.get(...)` against a real Polymarket endpoint.
  A failed call returns `(None, "error string")` — it is **never** silently
  replaced with a placeholder. Failures surface in `sources_failed[]`.
- Verification (`verify()`) is **plain deterministic Python** — sums, diffs,
  comparisons — not an LLM guess about whether the data looks right.
- Grok only runs *after* verification, over the already-verified bundle, and
  is explicitly instructed not to invent numbers or resolution rules that
  aren't in the data it was given.

## Data sources (all public, no API key required)

| API | Base | Used for |
|---|---|---|
| **Gamma** | `gamma-api.polymarket.com` | market/event metadata, question, resolution text, `clobTokenIds` |
| **CLOB** | `clob.polymarket.com` | live `midpoint`, `spread`, full order `book`, `prices-history` |
| **Data API** | `data-api.polymarket.com` | recent on-chain `trades` |

## Verification checks (13, run every time)

| Check | What it catches |
|---|---|
| `outcome_price_sum` | outcome prices should sum to ~1.00 |
| `gamma_vs_clob[outcome]` | Gamma's listed price vs. the live CLOB midpoint — flags staleness/lag |
| `book_not_crossed[outcome]` | best_bid must be < best_ask — a crossed book is a real data-integrity red flag |
| `spread_matches_book[outcome]` | the `/spread` endpoint's value should equal `best_ask - best_bid` |
| `book_freshness[outcome]` | order-book snapshot age — flags a stale book (seen live: 21 min old on a thin market) |
| `has_liquidity[outcome]` | at least one side of the book actually has depth |
| `market_is_live` | `active=true` and `closed=false` |
| `resolution_status` | flags if UMA's resolution status is actively `disputed` |

## Grok synthesis (optional, `XAI_API_KEY`)

Set `XAI_API_KEY` in `.env` (auto-loaded — no manual export needed) to get a
written profile: a plain-language description, a faithful summary of the
resolution criteria (Gamma's `description` field is often long legal text),
a read on the live price/order-book picture, and any verification flags in
plain language. Runs on the **Responses-API-equivalent** chat completions
call with structured JSON output; only ever reasons over the verified bundle,
never fetches anything itself.

## Output shape

`run()` returns a `ContractReport` dataclass — `fetched_at`, `title`,
`description`, `resolution_source`, `end_date`, `active`/`closed`,
`volume`/`volume_24hr`/`liquidity`, `condition_id`, `outcomes[]` (each with
`gamma_price`, `clob_midpoint`, `best_bid`/`best_ask`, `bid_depth`/`ask_depth`,
`price_history`, per-outcome `errors[]`), `recent_trades[]`, `verification`
(`checks`, `summary`, `all_pass`), `sources_ok[]`/`sources_failed[]`, and
`grok_profile`. Use `to_dict(report)` for a plain JSON-able dict.

## Known real-world gotcha (found live, not hypothetical)

A tested pull on a real market flagged `book_freshness` as **FAIL** — the
order-book snapshot was 1249s (~21 min) old. That's a genuine signal (thin
market, low recent trading activity), not a bug — exactly the kind of thing
this skill exists to catch rather than paper over.
