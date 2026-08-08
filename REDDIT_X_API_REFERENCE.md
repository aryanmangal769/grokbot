# Reddit & X API Reference — for Arbiter's intel skills

Every useful endpoint for collecting social signal, with auth, params, limits, and
how Arbiter uses it. Sources: xAI/X & Reddit developer docs, Arctic Shift API README,
2026 access write-ups (SocialCrawl, redditapis, Pushshift-alternatives).

Legend: **✅ used now** · **➕ planned** · **○ optional**

---

## 1. Reddit — keyless archives (what the skill uses today)

No account, no OAuth. Ideal for building/demos; **rate-limit under heavy use** →
cache or move to the official API for production.

### 1a. Arctic Shift  `https://arctic-shift.photon-reddit.com/api`  (no auth)
> Full-text `query` **must be scoped to a subreddit or author** — there is no
> Reddit-wide keyword search here (use PullPush for that).

| Endpoint | Key params | Returns | Arbiter use |
|---|---|---|---|
| `/posts/search` | `subreddit`\|`author`, `query`, `title`, `selftext`, `after`, `before`, `limit`(≤100), `sort`, `fields` | posts | ✅ per-sub gather |
| `/comments/search` | `subreddit`\|`author`, `body`, `link_id`, `parent_id`, `after`,`before` | comments | ✅ comment gather |
| `/posts/search/aggregate` | `aggregate=created_utc\|author\|subreddit`, `frequency`, `min_count`, `limit` | grouped counts | ➕ momentum / per-author volume |
| `/comments/search/aggregate` | same | grouped counts | ➕ discussion volume |
| `/time_series` | `key=global/posts/count`, `r/<sub>/posts/count`, `r/<sub>/subscribers`, `precision` | time series | ➕ lead-lag / momentum |
| `/comments/tree` | `link_id` (required), `limit`(≤25k) | threaded comments | ➕ deep-dive a viral thread |
| `/users/interactions/users` | `author` (required), `subreddit`, `after`,`before` | who interacts w/ a user | ➕ coordination / sockpuppet graph |

Rate limits exposed via `X-RateLimit-Remaining` / `X-RateLimit-Reset` headers.

### 1b. PullPush  `https://api.pullpush.io/reddit/search`  (no auth)
> The **only Reddit-wide full-text search** (Pushshift schema). ~1k req/hr, occasional outages.

| Endpoint | Key params | Arbiter use |
|---|---|---|
| `/submission/` | `q`, `subreddit`, `author`, `after`, `before`, `size`(≤100), `sort` | ✅ **subreddit discovery** + full-text gather |
| `/comment/` | `q`, `subreddit`, `author`, `link_id`, `size` | ➕ comment full-text |

> Note: PullPush `after`/`before` are unreliable → the skill fetches recent matches and filters dates client-side.

---

## 2. Reddit — official Data API  `https://oauth.reddit.com`  (OAuth)

Register a "script" app at reddit.com/prefs/apps → `client_id` + `secret`.
**Free 100 q/min** (non-commercial, approval-gated); commercial **$0.24/1k** + contract.
Unauthenticated `.json` = **403 since May 2026**.

| Endpoint | Purpose | Arbiter use |
|---|---|---|
| `POST /api/v1/access_token` | OAuth token (`client_credentials` or `password` grant) | ○ auth |
| `GET /search` | Reddit-wide keyword search | ○ live discovery |
| `GET /r/{sub}/search` | subreddit search (`restrict_sr=1`, `t`, `sort`) | ○ live gather |
| `GET /r/{sub}/{new\|hot\|top\|rising}` | subreddit listings | ○ real-time monitor |
| `GET /comments/{post_id}` | post + comment tree | ○ thread deep-dive |
| `GET /user/{name}/about` | **account age + karma** | ○ **sockpuppet scoring** |
| `GET /user/{name}/{submitted\|comments}` | user history | ○ author profiling |
| `GET /api/info?id={fullname}` | lookup by id | ○ hydration |
| `GET /r/{sub}/about` | subreddit metadata/subscribers | ○ venue sizing |
| `GET /subreddits/search` | find subreddits by name | ○ discovery assist |

Third-party managed gateways (e.g. `api.redditapis.com`, ~36 REST endpoints, single bearer, no OAuth handshake) exist if you want to skip PRAW — paid.

---

## 3. X (Twitter) API v2  `https://api.x.com/2`  (Bearer)

App-only Bearer token for search/lookup. Common field expansions:
`tweet.fields=created_at,public_metrics,author_id,lang` ·
`user.fields=created_at,public_metrics,verified` · `expansions=author_id`.

### Search & volume — the core for Arbiter
| Endpoint | Purpose | Arbiter use |
|---|---|---|
| `GET /2/tweets/search/recent` | last **7 days** by query | ✅ topic/entity chatter |
| `GET /2/tweets/search/all` | **full archive** (Academic/Pro) | ➕ historical info-gap backtest |
| `GET /2/tweets/counts/recent` · `/counts/all` | tweet **volume** over time | ➕ burst detection / lead-lag |
| `GET /2/tweets/search/stream` + `/stream/rules` | **filtered real-time stream** | ➕ live firehose monitor |
| `GET /2/tweets/sample/stream` · `sample10` | 1% / 10% volume stream | ○ ambient sampling |

### Lookup & graph — for originator + coordination
| Endpoint | Purpose | Arbiter use |
|---|---|---|
| `GET /2/tweets/{id}` · `?ids=` | tweet hydration + metrics | ✅ originator post |
| `GET /2/users/by/username/{u}` · `/2/users/{id}` | **account age**, followers, verified | ✅ proximity/sockpuppet score |
| `GET /2/users/{id}/tweets` | user timeline | ➕ author history |
| `GET /2/users/{id}/mentions` · `/liked_tweets` | engagement | ○ amplification |
| `GET /2/tweets/{id}/retweeted_by` · `/quote_tweets` · `/liking_users` | **amplification graph** | ➕ coordination detection |
| `GET /2/users/{id}/followers` · `/following` | social graph | ○ cluster analysis |

### Context
| Endpoint | Purpose | Arbiter use |
|---|---|---|
| `GET /2/trends/by/woeid/{woeid}` · personalized trends | trending topics | ○ topic surfacing |
| `GET /2/spaces/search` · `/2/spaces/{id}` | live audio Spaces | ○ (stretch) claim fact-check |
| `GET /2/lists/...` | curated lists | ○ watchlists |

> **xAI Live Search** (grokked search over web + X, time-boundable via `from_date`/`to_date`)
> is the fastest way to pull X + web signal *with grounding* without wiring raw X endpoints —
> it's how Arbiter does the "did a credible source exist before T?" check.

---

## 4. What the Reddit Intel skill actually calls right now
- **Discovery:** PullPush `/submission/` (Reddit-wide) → rank subs; Arctic per-sub pool as fallback.
- **Gather:** Arctic `/posts/search` per discovered sub + PullPush full-text (+ official API if configured).
- **Insight:** posts → Grok 4.5 (`chat/completions`, `reasoning_effort=low`) → structured JSON.

## 5. Recommended production path
1. **X:** Live Search for grounded pulls; `search/recent` + `counts/recent` + user lookup for raw signal & account-age.
2. **Reddit:** keep keyless archives for breadth + backfill; add the **official API** for author account-age and live listings; **cache** everything to survive archive throttling.
