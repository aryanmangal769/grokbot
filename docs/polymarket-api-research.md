# Polymarket API Research — X Insights Tab

**Project goal:** A tab inside X that surfaces the latest Polymarket bets tailored to a user, and shows which way sentiment is swaying based on what people say about that bet on X.

**Date:** 2026-08-08

---

## The three APIs

| API | Base URL | Auth | Role |
|-----|----------|------|------|
| **Gamma** | `https://gamma-api.polymarket.com` | None (public) | Market/event discovery, metadata, descriptions, images, **search** |
| **CLOB** | `https://clob.polymarket.com` | Public reads; L2 key for user data | Live prices, order books, trades, candlesticks |
| **Data API** | `https://data-api.polymarket.com` | None for public wallet reads | Positions/holdings, user activity, price history |

A **read-only insights tab is entirely public** — no wallet signing required unless you let users trade or read their own private portfolio.

---

## 1. Discovering & ranking bets ("tailored to them")

- **`GET /markets`** (Gamma) — core catalog. Filter by `tag_id`/`related_tags`, `volume_num_min`, `liquidity_num_min`, `end_date_min/max`, `closed=false`, `order` by volume.
- **`GET /events`** (Gamma) — events are **containers** grouping related markets (e.g. "2028 Election" -> many candidate markets). Each event nests a `markets[]` array plus `tags`, `series`.
- **`GET /public-search`** (Gamma) — text search across markets/events/profiles. **This is the bridge from an X topic/hashtag -> matching Polymarket markets.**
- **Tags / Series** — `List Tags`, `Get Related Tags` map a user's interests (derived from X activity) to Polymarket categories. This is the **personalization hook**: the X API has no interests endpoint, so derive interests from a user's posts/follows/likes, map keywords -> tags, then pull markets by `tag_id`.

**Key market fields:** `question`, `slug`, `conditionId`, `clobTokenIds`, `outcomes`, `outcomePrices`, `volumeNum`, `liquidityNum`, `volume24hr`, `endDate`, `image`, `tags`, `bestBid`/`bestAsk`/`spread`/`lastTradePrice`.

> **Data-shape gotcha:** `outcomes` and `outcomePrices` come back as **JSON-encoded strings** (e.g. `'["Yes","No"]'` / `'["0.43","0.57"]'`). `JSON.parse` them; `outcomePrices[0]` x 100 = implied probability %.

---

## 2. Bet data — odds & "which way it's moving"

- **`outcomePrices`** = implied probability (0-1) per outcome. "Yes" at 0.63 = 63% implied.
- **`GET /prices-history`** (Data API) — historical price series for one market; **Batch Prices History** for many at once. Powers the **trend line / sparkline** ("+8% in 24h").
- **CLOB `Get Klines`** — candlestick/OHLC data (max 1000 pts).
- **CLOB `Get Order Book` / `Get BBO` / `Get Spread`** — depth and best bid/ask.
- **`Get Last Trade Price`, `Get Midpoint`** — quick current-price reads.
- **WebSocket Market Channel** — real-time order book/price push, no polling.
- **`volume24hr`, `volume1wk`, `Get Open Interest`** — momentum/attention signals.

---

## 3. Sentiment correlation (the X differentiator)

- **Comments API** (`List Comments`, `Get Comments by User Address`) — Polymarket's **native on-platform commentary** per market; a money-backed sentiment baseline to contrast with X chatter.
- **`Get Top Holders` / positions endpoints** — where the smart money / whales sit; juxtapose against loud X sentiment ("crowd says X, capital says Y").
- **X side:** pull posts mentioning the market's `question`/slug/keywords, run sentiment, compare **X sentiment direction vs. the Polymarket price move**. Product insight = **divergence detection**: "X is bullish, but the market just dropped 6% — money disagrees."

---

## Suggested data flow

1. Derive user interests from X -> keyword set.
2. Map keywords -> Polymarket **tags** + **`/public-search`** -> candidate markets.
3. Rank by `volume24hr` / recency / `endDate`.
4. Per card: `question`, current `outcomePrices` (%), 24h move from **prices-history**, volume.
5. Overlay: X sentiment score vs. price trend + **native Polymarket comments**; flag divergence.

Steps 2-4 are **public, no-auth, no-wallet**.

---

## Live endpoint examples (verified 2026-08-08)

### Search (X-topic -> market bridge)

```
GET https://gamma-api.polymarket.com/public-search?q=<query>&limit_per_type=N
```
Returns `events`, `tags`, and (if enabled) profiles.

- `q=trump` -> *Trump out as President before 2027?* ($10.5M), *Who will Trump meet with in 2026?* ($817K)
- `q=fed rate cut` -> multiple *Fed rate cut by...?* events, biggest at **$31.5M** volume

### Hottest right now (by 24h volume)

```
GET https://gamma-api.polymarket.com/events?closed=false&active=true&order=volume24hr&ascending=false&limit=N
```
Note: top of this list is **auto-generated esports** (LoL/Dota, $2-6M/24h each) — noisy for a social tab; filter out by tag unless you want a sports angle.

### Biggest opinion markets (Politics, tag_id=2, by total volume)

```
GET https://gamma-api.polymarket.com/events?closed=false&active=true&tag_id=2&order=volume&ascending=false&limit=N
```

| Event | Total volume | Live read (2026-08-08) |
|---|---|---|
| Democratic Nominee 2028 | $1.25B | Newsom 18%, AOC 15%, Buttigieg 5% |
| Republican Nominee 2028 | $686M | Vance 43%, Rubio 23% |
| Presidential Winner 2028 | $680M | Vance 21%, Newsom 10%, AOC 10% |
| Netanyahu out by...? | $124M | end-of-2026: 46% |
| Brazil Presidential Election | $121M | Lula 64% |
| French Presidential 2027 | $118M | Le Pen 30% |

---

## Full endpoint index (by API)

**Gamma:** `/markets`, `/events`, `/public-search`, `/tags`, related-tags, `/series`, market/event by id/slug, sports metadata.

**CLOB — market data:** Get BBO, Get Book, Get Recent Trades, Get Statistics, Get Tickers, Get Klines. **Account/portfolio (auth):** portfolio, balances, fills, PnL, equity, positions, top holders.

**Data API — prices:** midpoint, market price, last trade price, spread, order book, `/prices-history`, batch prices history. **User activity/holdings (public by wallet):** current/closed positions, positions for market, total value, trades for user/markets, user activity, total markets traded. **Search:** search markets/events/profiles, public profile by wallet.

**Comments API:** List Comments, Get Comments by ID, Get Comments by User Address.

**WebSocket:** Market Channel (order book/prices/lifecycle), Sports Channel, User Channel (auth).
