# polymarket-contract

Live, verified Polymarket contract data — search-to-JSON to semantic-search
database. **No mocked data anywhere**: every field is either a real response
from Polymarket's own APIs or explicitly marked unavailable. Deterministic
checks run against every pull before Grok ever sees it. Built for Grokathon.

```bash
python polymarket_contract.py "will the fed cut rates"
```
```
=== Will the Fed cut rates at the September 2026 FOMC meeting? ===
VERIFICATION: 12/12 checks passed
GROK PROFILE: { "profile": "...", "resolution_summary": "...", ... }
STORED DOCUMENT (lean schema): { "title": "...", "url": "...", ... }
```

## What this is

Three things, all real, no other data source involved:
1. **A single-contract lookup** — search or slug in, a verified JSON document out.
2. **Bulk ingestion** — the top N live Polymarket contracts by volume, no user input, into Postgres.
3. **Semantic search** — natural-language query → cosine-similarity ranked real contracts.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in XAI_API_KEY, DATABASE_URL

python polymarket_contract.py "bitcoin above 100k"     # one contract, full detail
python ingest_bulk.py --n=100                          # bulk load, no prompts
python db.py "cryptocurrency price prediction"         # semantic search
```

## Architecture

```
polymarket_contract.py   fetch (Gamma+CLOB+Data API) -> verify -> Grok summary -> lean document
embeddings.py             local semantic vectors (sentence-transformers, no external API)
db.py                     Postgres/pgvector storage + save/search, any DATABASE_URL
ingest_bulk.py            orchestrates the above for the top-N real contracts, no user input
```

See **[SCHEMA.md](SCHEMA.md)** for the exact JSON/DB schema with a real
captured sample, and **[polymarket_contract.SKILL.md](polymarket_contract.SKILL.md)**
for the skill manifest (verification checks, config, output shape).

## Data sources — Polymarket + Grok, nothing else

| Source | Auth | Used for |
|---|---|---|
| Gamma API (`gamma-api.polymarket.com`) | none | search, metadata, resolution text |
| CLOB API (`clob.polymarket.com`) | none | live order book, midpoint, spread, price history |
| Data API (`data-api.polymarket.com`) | none | recent on-chain trades |
| xAI / Grok | `XAI_API_KEY` | schema-validated plain-language summary only |
| local sentence-transformer | none (runs on-device) | semantic embedding for cosine search |

xAI has no embeddings endpoint, so semantic search runs on a local model
(`all-MiniLM-L6-v2`) — zero third-party API calls beyond Polymarket + Grok.

## Verification (not an LLM guess — deterministic)

Every pull runs through checks like: outcome prices sum to ~1.0, Gamma's
listed price vs. the live CLOB midpoint, crossed-book detection, reported
spread vs. book-derived spread, order-book freshness, market-is-live status,
and UMA resolution status. Failures surface as plain-language `notes[]` in
the document (via Grok, or a deterministic fallback if no `XAI_API_KEY` is
set) — never silently hidden.

## Config (`.env`)

| Var | Required for |
|---|---|
| `XAI_API_KEY` | the Grok summary pass (optional — skipped gracefully without it) |
| `DATABASE_URL` | saving/searching (optional — single-contract lookups work without it) |

`DATABASE_URL` is a Postgres connection string — for Supabase, use the
**Transaction pooler** URI (Project Settings → Database → Connection string),
not the direct-connection one (that host is IPv6-only and fails to resolve
on most networks). Also enable the **`vector`** extension under
Database → Extensions.

## Known real-world finding

A live pull once flagged `book_freshness` as failed — a real market's order
book was ~21 minutes stale. Not a bug: genuine low-liquidity signal the
verification layer is designed to catch.
