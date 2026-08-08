# System Design — X Prediction Markets Insights Tab

**Project goal:** A tab inside X that surfaces the latest Polymarket **events** tailored to a
user's interests, and shows which way sentiment is swaying based on what people say about that
bet on **X + Reddit** — including where our crowd-sentiment estimate **diverges** from the
market price (the "edge").

**Date:** 2026-08-08
**Owners:** Juno (browser feed + UI), Apollo (Expo phone app), grokbot pipeline (data + analysis)

---

## 1. Guiding principle — read-fast, precompute-heavy

The user-facing app is **just a reader**. Everything expensive — fetching markets, searching
X/Reddit via Grok, running sentiment — happens **offline in a background pipeline** that
populates a DB. The clients never call Polymarket or Grok live; they read pre-computed rows.
This keeps the feed instant and costs bounded.

```
┌──────────────────── BACKGROUND PIPELINE (grokbot, Python, cron every N min) ────────────────────┐
│                                                                                                  │
│  1. Polymarket Fetcher ─► 2. Grok Search Agent ─► 3. Sentiment Agent ─► 4. Writer                │
│     top events + data        top X + Reddit posts    X-implied % + momentum    upsert to DB        │
│     (Gamma/CLOB/Data)        (x_search, reddit_intel) + summary + analytics                        │
│                                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                             │  writes
                                             ▼
                                   ┌───────────────────┐
                                   │   DB (Postgres)   │  events · sentiment · top_posts · personas
                                   └───────────────────┘
                                             │  reads (fast REST/realtime)
                                             ▼
   ┌──────────────┐  persona + interests  ┌───────────────────────────────────────────────────┐
   │    User      │ ────────────────────► │  Clients                                            │
   │              │ ◄──────────────────── │  • Browser X feed — right-rail "Markets for you" ✅  │
   └──────────────┘  markets + X lean     │  • Expo phone app (Apollo)                           │
                                          │  • click → detail view → [Buy with X money] (mock)   │
                                          └───────────────────────────────────────────────────┘
```

---

## 2. Pipeline stages (mapped to existing grokbot modules)

| # | Stage | Does | Existing module |
|---|-------|------|-----------------|
| 1 | **Polymarket Fetcher** | Pull top **events** + `question, outcomePrices, volumeNum, liquidityNum, volume24hr, endDate, tags`. Rank by volume/liquidity; map user interests → `tag_id` via Gamma tags. | `docs/polymarket-api-research.md` (Gamma / CLOB / Data API) |
| 2 | **Grok Search Agent** | For each event topic, harvest top **X** posts + **Reddit** threads. Grok's native live X access is our only real path to X data (X API v2 has no home-feed/interests endpoint — Apollo's finding). | `x_search/extract.py`, `reddit_intel.py` |
| 3 | **Sentiment Agent** | From harvested posts, produce **both**: (a) `x_implied_prob` %, (b) `momentum` ▲/▼/—, plus a `summary` and supporting analytics. Contrast vs Polymarket price → **divergence**. | *(new — LLM over harvested `data/`)* |
| 4 | **Writer** | Upsert events + sentiment + top_posts into the DB. | *(new — thin DB layer)* |

Personalization for the demo is **mock personas** (below), so the interest-derivation path
(`user_interest_classifier/`, `user_based_scraper/`) is optional/deferred.

---

## 3. The dual percentage (the product's core)

Every market row shows **two numbers side by side**:

- **Market price** — Polymarket's implied probability (`outcomePrices[0] × 100`).
- **X-implied probability** — our Sentiment Agent's estimate from X + Reddit chatter, with a
  **momentum arrow** (▲ leaning Yes / ▼ leaning No / — split) and post volume.

When they diverge (e.g. *Market 68%* vs *X-implied 74% ▲*), that gap is the highlighted
**edge** — "the crowd is more bullish than the money." This is the one-glance value prop.

---

## 4. Data model

```
events        (id, question, category, tag_ids[], yes_price, no_price,
               volume, liquidity, volume_24hr, resolution_date,
               polymarket_slug, image_url, updated_at)

sentiment     (event_id FK, x_implied_pct, direction[up/down/flat],
               confidence, momentum_score, summary, updated_at)

top_posts     (id, event_id FK, platform[x/reddit], author, handle,
               text, likes, reposts, url, stance[yes/no/neutral], captured_at)

personas      (id, name, avatar, interests[])          # demo personalization
```

Harvested datasets already land in `data/<topic>/` (`manifest.json` + `posts.jsonl`); the
Writer maps those into `top_posts` + `sentiment`.

---

## 5. Clients & UX

**Right-rail "Markets for you" widget** (browser — built ✅; Apollo mirrors on phone)
- Top 5–6 events matched to the persona's interests.
- Per row: category · question · **Market % vs X-implied % + momentum** · Volume · Liquidity.
- X-native styling (blends into X's own design language, not Polymarket's).

**Detail view** (on click)
- Top tweets / Reddit posts for the event (with stance).
- Sentiment **summary** from the agent.
- Analytics: market price vs X-implied over time, volume/liquidity, momentum, divergence flag.
- **[ Buy with X money ]** — **mock** confirm flow for the prototype (no wallet, no real funds).

**Mock personas** (demo login): e.g. *Crypto Degen*, *Politics Junkie*, *Sports Bettor*,
*AI/Tech* — each with an `interests[]` array that filters which events surface.

---

## 6. Recommended stack

| Layer | Choice | Why |
|-------|--------|-----|
| Pipeline | **Python** (existing grokbot modules) on a cron | Reuse `x_search`, `reddit_intel`, Grok clients already here |
| DB + API | **Supabase (Postgres)** | One DB both Expo phone + browser read; auto REST + realtime; auth for personas |
| Polymarket | **Gamma** (discovery/search), **CLOB** (prices), **Data API** (history) | All public reads; no wallet for read-only insights |
| Grok | **xAI Grok API** (`XAI_API_KEY`, already provisioned) | Native live X + Reddit search |
| Clients | Browser feed (Juno) + Expo app (Apollo), both read Supabase | Shared source of truth |

The pipeline stays Python (no separate TS worker); it just writes to Supabase, and the two
clients read from it.

---

## 7. Build order

1. **DB schema in Supabase** (`events`, `sentiment`, `top_posts`, `personas`) + seed 4 personas.
2. **Seed** ~10–15 real Polymarket events (one-time Gamma fetch) so the UI has live-looking data.
3. **Widget → dual number** (market vs X-implied + momentum), reading from Supabase.
4. **Detail view**: top tweets, summary, analytics, mock Buy-with-X-money.
5. **Wire the real pipeline** (Grok search + sentiment) to refresh the DB on a cron — last,
   since 1–4 run fine on seeded data.

---

## 8. Open items

- **Divergence thresholds** — how big a market-vs-X gap flags an "edge" badge.
- **Refresh cadence** — how often the cron re-runs (per-event vs global).
- **Sentiment calibration** — how `x_implied_pct` is computed from post stance + engagement.
- **Persona set** — final list + interest tags to map onto Polymarket `tag_id`s.
