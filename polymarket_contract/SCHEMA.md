# Polymarket Contract — JSON & DB Schema

The schema for the document this component produces per contract, the Postgres
table it's stored in, and how semantic search over it works. The sample JSON
below is **real, live-pulled output** (not fabricated) — captured from
`python polymarket_contract.py "will us acquire greenland" --save-json=...`.

---

## 1. The document (`ContractDocument`, in `polymarket_contract.py`)

Pydantic-validated — this is the exact shape every stored contract has, whether
you read it as JSON or as a DB row's `data` column.

| Field | Type | Notes |
|---|---|---|
| `title` | string | **the DB primary key**, per the "key by title" requirement. Polymarket's own question text. |
| `slug` | string \| null | Polymarket's URL slug — more collision-proof than `title` if you ever need an alternate key |
| `condition_id` | string \| null | the on-chain condition ID |
| `fetched_at` | ISO timestamp | when this snapshot was pulled |
| `description` | string \| null | full resolution-criteria legal text, from Gamma |
| `resolution_source` | string \| null | | 
| `end_date` | ISO timestamp \| null | |
| `active`, `closed` | bool \| null | market status |
| `volume`, `volume_24hr`, `liquidity` | float \| null | |
| `outcomes[]` | `OutcomeDoc[]` | see below — one per outcome (usually Yes/No) |
| `verification_summary` | string | e.g. `"12/12 checks passed"` |
| `verification_all_pass` | bool | |
| `verification_checks` | object | the full per-check breakdown (see §3) |
| `sources_ok[]` / `sources_failed[]` | string[] | exactly which live API calls succeeded/failed |
| `summary` | string \| null | Grok's `profile` text, promoted to top level for easy querying |
| `grok_profile` | `GrokProfile` \| null | the full structured Grok output |

### `OutcomeDoc`

| Field | Type |
|---|---|
| `outcome` | string (e.g. `"Yes"`) |
| `token_id` | string — the CLOB token ID |
| `gamma_price`, `clob_midpoint`, `clob_spread` | float \| null |
| `best_bid`, `best_ask` | float \| null |
| `bid_depth`, `ask_depth` | float \| null — summed size of top 5 book levels |
| `book_timestamp` | ISO timestamp \| null |
| `errors[]` | string[] — non-fatal issues fetching this outcome |

### `GrokProfile` (schema-validated LLM output — see §4)

| Field | Type |
|---|---|
| `profile` | string — 2-3 sentence plain description |
| `resolution_summary` | string — faithful summary of the real resolution text |
| `price_read` | string — current price/order-book picture |
| `flags[]` | string[] — verification issues or data gaps, plain language |

---

## 2. Sample document (real, live data)

```json
{
  "title": "Will the US acquire part of Greenland in 2026?",
  "slug": "will-the-us-acquire-any-part-of-greenland-in-2026",
  "condition_id": "0x890fc3ba40458db0b67560691aa1597344c2a0560d18e80f586a4b35f510b4ce",
  "fetched_at": "2026-08-09T00:33:48.623469+00:00",
  "description": "This market will resolve to “Yes” if the United States acquires control of any land territory that is part of Greenland by December 31, 2026, 11:59 PM ET. Otherwise, this market will resolve to “No”. [... full legal resolution text ...]",
  "resolution_source": "",
  "end_date": "2026-12-31T00:00:00Z",
  "active": true,
  "closed": false,
  "volume": 10550474.43926499,
  "volume_24hr": 1401.388935,
  "liquidity": 102389.6877,
  "outcomes": [
    {
      "outcome": "Yes",
      "token_id": "60745476350338574489643206785963608049827415658137795738920858026399504782388",
      "gamma_price": 0.055,
      "clob_midpoint": 0.055,
      "clob_spread": 0.01,
      "best_bid": 0.05,
      "best_ask": 0.06,
      "bid_depth": 472301.62,
      "ask_depth": 14776.45,
      "book_timestamp": "2026-08-09T00:33:49.587000+00:00",
      "errors": []
    },
    {
      "outcome": "No",
      "token_id": "104895545296438735617666172336621441242754294947987367085791779928220778311973",
      "gamma_price": 0.945,
      "clob_midpoint": 0.945,
      "clob_spread": 0.01,
      "best_bid": 0.94,
      "best_ask": 0.95,
      "bid_depth": 14776.45,
      "ask_depth": 472301.62,
      "book_timestamp": "2026-08-09T00:33:49.587000+00:00",
      "errors": []
    }
  ],
  "verification_summary": "12/12 checks passed",
  "verification_all_pass": true,
  "verification_checks": {
    "outcome_price_sum": {"pass": true, "value": 1.0, "expected": "~1.00 (±0.02)"},
    "gamma_vs_clob[Yes]": {"pass": true, "value": 0.0, "expected": "<=0.03 diff between listed price and live midpoint"},
    "book_not_crossed[Yes]": {"pass": true, "value": "bid=0.05 ask=0.06", "expected": "best_bid < best_ask"},
    "book_freshness[Yes]": {"pass": true, "value": "4s old", "expected": "<=300s old"},
    "market_is_live": {"pass": true, "value": "active=True closed=False", "expected": "active=true, closed=false"}
  },
  "sources_ok": ["gamma:search", "clob:midpoint[Yes]", "clob:spread[Yes]", "clob:book[Yes]",
                "clob:prices-history[Yes]", "clob:midpoint[No]", "clob:spread[No]",
                "clob:book[No]", "clob:prices-history[No]", "data_api:trades"],
  "sources_failed": [],
  "summary": "This live prediction market asks whether the United States will acquire any part of Greenland by the end of 2026. It is actively traded with over $10 million in total volume and remains open through December 31, 2026.",
  "grok_profile": {
    "profile": "This live prediction market asks whether the United States will acquire any part of Greenland by the end of 2026. It is actively traded with over $10 million in total volume and remains open through December 31, 2026.",
    "resolution_summary": "The market resolves to Yes if the US acquires control of any land territory in Greenland by December 31, 2026, 11:59 PM ET via transfer of sovereignty, primary/exclusive jurisdiction or control, or use of force; otherwise No.",
    "price_read": "Yes trades at 5.5 cents (best bid 0.05 / ask 0.06) with heavy bid depth over 472k against thinner asks; No sits at 94.5 cents with the reverse book. The 1-cent spread is tight and books are fresh.",
    "flags": []
  }
}
```

*(`verification_checks` is truncated above for readability — the full document has all 8-13 checks depending on how many outcomes/data points were available.)*

---

## 3. Postgres table (`db.py: SCHEMA_SQL`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector — enable in Supabase under Database > Extensions

CREATE TABLE IF NOT EXISTS polymarket_contracts (
    title                   TEXT PRIMARY KEY,     -- keyed by title, as requested
    slug                    TEXT,
    condition_id            TEXT,
    fetched_at              TIMESTAMPTZ NOT NULL,
    summary                 TEXT,                  -- promoted from grok_profile.profile
    volume                  DOUBLE PRECISION,
    volume_24hr             DOUBLE PRECISION,
    liquidity               DOUBLE PRECISION,
    active                  BOOLEAN,
    closed                  BOOLEAN,
    verification_all_pass   BOOLEAN,
    verification_summary    TEXT,
    data                    JSONB NOT NULL,        -- the FULL ContractDocument above
    embedding               vector(384),           -- local sentence-transformer vector
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_polymarket_contracts_slug ON polymarket_contracts (slug);
CREATE INDEX IF NOT EXISTS idx_polymarket_contracts_condition_id ON polymarket_contracts (condition_id);
CREATE INDEX IF NOT EXISTS idx_polymarket_contracts_embedding
    ON polymarket_contracts USING hnsw (embedding vector_cosine_ops);
```

**Design:** a handful of columns are *promoted* out of `data` (title, slug, volume,
verification status, summary) purely so you can filter/sort/index on them
cheaply in SQL — but `data` always holds the complete, authoritative document.
If you ever need a field that isn't promoted, it's in `data` as JSONB and still
queryable (`data->>'condition_id'`, `data->'outcomes'`, etc).

---

## 4. How Grok's output gets schema-validated (not just hoped for)

`grok_profile()` builds the `response_format` straight from `GrokProfile`'s own
Pydantic JSON schema (`GrokProfile.model_json_schema()`) and sends it as a
**strict** `json_schema` response format — the API itself constrains Grok's
output shape. The response is then *re-validated* on receipt with
`GrokProfile.model_validate_json(...)`; if that fails for any reason, the
error is captured in `flags[]` rather than crashing or silently accepting a
malformed object.

---

## 5. Semantic (cosine similarity) search

`embedding` is a 384-dim vector from a **local** sentence-transformer model
(`all-MiniLM-L6-v2` — see `embeddings.py`; xAI has no embeddings endpoint, so
this runs entirely on your machine, no external API). Built from
`title + summary + description[:400]` (see `ingest_bulk.py: embed_text_for`).

```sql
-- raw SQL: 10 most semantically similar contracts to some query vector
SELECT title, slug, summary, 1 - (embedding <=> '[0.01,-0.02,...]'::vector) AS similarity
FROM polymarket_contracts
WHERE embedding IS NOT NULL
ORDER BY embedding <=> '[0.01,-0.02,...]'::vector
LIMIT 10;
```

```python
# the same thing, from Python — embeds your query text locally, then runs the SQL above
import db
db.search_similar("will there be a ceasefire", k=10)
# -> [{"title": "...", "slug": "...", "summary": "...", "similarity": 0.83}, ...]
```

`<=>` is pgvector's cosine-distance operator; `1 - distance` converts it to a
0-1 similarity score (1.0 = identical direction).

---

## 6. End-to-end flow

```
python ingest_bulk.py --n=100
  │
  ├─▶ Gamma: top 100 active events by 24h volume   (real, no query needed)
  │
  ├─▶ per contract: fetch_contract(slug)             (same path as the single-contract CLI)
  │     ├─ Gamma: metadata, resolution text, outcome prices
  │     ├─ CLOB: midpoint, spread, order book, price history — per outcome
  │     ├─ Data API: recent trades
  │     └─ verify(): 8-13 deterministic checks
  │
  ├─▶ grok_profile(report)                           (schema-validated Grok summary)
  │
  ├─▶ embeddings.embed(title + summary + description) (local, 384-dim)
  │
  └─▶ db.save_contract(doc, embedding)                (upsert by title, into Postgres)

Later:  db.search_similar("some natural-language query")  -->  ranked, real contracts
```
