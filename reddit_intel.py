"""Reddit Intel — an autonomous research skill.

Give it a topic; it does the rest:
  1. discover the most relevant subreddits (PullPush aggregate, junk-filtered)
  2. gather the chatter across them (Arctic Shift + PullPush, keyless)
  3. analyze it into a structured intelligence read (Grok live, or heuristics offline)

Exposed three ways:
  - run(topic)                      -> full report dict
  - REDDIT_INTEL_TOOL               -> function-calling spec so Grok can call it
  - GET /api/reddit/intel?topic=..  -> HTTP (see main.py)

No API key needed to pull; add XAI_API_KEY to upgrade the analysis to Grok.
"""
from __future__ import annotations
import datetime as dt
import collections
import requests

from ..models import Post
from ..connectors import reddit_live
from .. import grok

UA = {"User-Agent": "arbiter-intel/0.1"}
PULLPUSH = "https://api.pullpush.io/reddit/search/submission/"

# subs that pollute topic search: bots, karma farms, nation-sim & fiction games
JUNK = {"autotldr", "100thupvote", "gustavosaltuniverses", "imaginaryelections",
        "subsimulatorgpt2", "worldpowers", "geosim", "alternatehistory",
        "the_meltdown", "subsimgpt2interactive"}
JUNK_HINTS = ("sim", "althist", "alternate", "imaginary", "circlejerk")

# Arctic matches all query terms, so a 3-word phrase under-recalls. Reduce a
# topic to its 1-2 most distinctive tokens for search (keep proper nouns).
# Filler/verbs dropped so a full natural-language question reduces to its key
# nouns, e.g. "will infantino remain fifa president" -> "infantino fifa".
STOP = {"presidential", "the", "a", "an", "of", "in", "for", "on", "to", "vs",
        "and", "race", "who", "will", "win", "wins", "2024", "2025", "2026",
        "remain", "remains", "stay", "stays", "be", "become", "becomes", "get",
        "gets", "keep", "keeps", "continue", "continues", "still", "next",
        "year", "this", "is", "are", "was", "were", "do", "does", "did", "can",
        "could", "should", "would", "may", "might", "going", "gonna", "as"}


def _core_query(topic: str) -> str:
    """Reduce a topic to its 2 most salient tokens, in order (Arctic under-recalls
    on 3+ word queries). Keeps short-but-key terms like 'etf', 'fed', 'war'."""
    toks = [t for t in topic.split() if t.lower() not in STOP]
    if not toks:
        toks = [t for t in topic.split() if t.lower() not in {"the", "a", "an"}]
    return " ".join(toks[:2])


def _probe_query(topic: str) -> str:
    """Single most-distinctive token for high-recall subreddit discovery on Arctic
    (whose AND-matching kills multi-word recall). Longest non-stopword wins."""
    toks = [t for t in topic.split() if t.lower() not in STOP] or topic.split()
    return max(toks, key=len) if toks else topic


# neutral, high-traffic floor so gather never comes back empty for any topic
SEED_SUBS = ["worldnews", "news", "OutOfTheLoop"]

# ---- Guardrails -----------------------------------------------------------
# Bound how much the skill fetches/analyzes on any single run. These cap cost
# (Grok tokens), noise, and load on the free archives. Override per call via
# run(topic, limits={...}); they are NOT topic-specific.
DEFAULT_LIMITS = {
    "max_subreddits": 8,     # how many subs to monitor
    "per_sub_limit": 40,     # posts pulled per subreddit (Arctic cap is 100)
    "max_posts": 150,        # hard cap on posts fed to the analyzer
    "lookback_days": 180,    # time window
    "pool_probe": 24,        # max candidate subs probed in the Arctic fallback
}


def _limits(overrides: dict | None) -> dict:
    lim = dict(DEFAULT_LIMITS)
    if overrides:
        lim.update({k: v for k, v in overrides.items() if k in DEFAULT_LIMITS})
    return lim


# --------------------------------------------------------------------------- #
# 1. discover
# --------------------------------------------------------------------------- #
def _pullpush(q: str, size: int = 100) -> list[dict]:
    try:
        r = requests.get(PULLPUSH, params={"q": q, "size": size, "sort": "desc"},
                         headers=UA, timeout=25)
        return r.json().get("data") or []
    except Exception:
        return []


def _is_junk(sub: str) -> bool:
    s = sub.lower()
    return s in JUNK or any(h in s for h in JUNK_HINTS)


# Broad, MULTI-DOMAIN candidate pool so the skill generalizes to ANY topic
# (politics, crypto, sports, tech, markets, science, entertainment). Used as the
# Arctic fallback when PullPush's Reddit-wide search is unavailable.
CANDIDATE_POOL = [
    # news / politics / geo
    "worldnews", "news", "politics", "geopolitics", "neoliberal", "worldpolitics",
    "PoliticalDiscussion", "anime_titties", "UpliftingNews", "OutOfTheLoop",
    # markets / finance
    "wallstreetbets", "stocks", "investing", "StockMarket", "options", "Economics",
    "finance", "SecurityAnalysis",
    # crypto
    "CryptoCurrency", "Bitcoin", "ethereum", "CryptoMarkets",
    # prediction markets / betting
    "PredictionMarkets", "Polymarket", "sportsbook",
    # sports
    "sports", "soccer", "nba", "nfl",
    # tech / ai / science
    "technology", "artificial", "OpenAI", "singularity", "MachineLearning",
    "science", "space", "Futurology",
    # entertainment / general
    "movies", "television", "Music", "todayilearned",
]


def _tally_sub(sub: str, topic: str, after: str, before: str) -> tuple[str, int, int]:
    hits = reddit_live.arctic_search(sub, topic, after, before, limit=40)
    return sub, len(hits), sum(h.reach for h in hits)


def discover_subreddits(topic: str, max_subs: int = 8, days: int = 365,
                        pool_probe: int = 24) -> list[dict]:
    """Find where a topic is discussed. Works for ANY topic.

    PRIMARY  : PullPush Reddit-wide full-text (the only cross-Reddit search).
    FALLBACK : Arctic over a broad multi-domain candidate pool (Arctic full-text
               requires a subreddit scope, so we probe the pool in parallel).
    `pool_probe` caps how many candidate subs the fallback touches (guardrail).
    """
    from concurrent.futures import ThreadPoolExecutor
    q = _core_query(topic)
    cnt, score = collections.Counter(), collections.Counter()

    # 1) PRIMARY — PullPush is Reddit-wide and topic-agnostic (max size 100)
    for x in _pullpush(q, size=100):
        s = x.get("subreddit")
        if s and not _is_junk(s):
            cnt[s] += 1
            score[s] += int(x.get("score", 0) or 0)

    # 2) FALLBACK — only if PullPush gave nothing (Arctic pool probe is slow).
    #    Use the single-token probe query for higher recall when locating subs.
    if not cnt:
        pq = _probe_query(topic)
        now = dt.datetime.now(dt.timezone.utc)
        now_iso = now.isoformat()
        after = (now - dt.timedelta(days=days)).isoformat()
        with ThreadPoolExecutor(max_workers=12) as ex:
            for sub, c, sc in ex.map(lambda s: _tally_sub(s, pq, after, now_iso),
                                     CANDIDATE_POOL[:pool_probe]):
                if c and not _is_junk(sub):
                    cnt[sub] += c; score[sub] += sc

    ranked = sorted(([s, cnt[s], score[s]] for s in cnt if cnt[s] >= 1),
                    key=lambda t: -(t[2] + t[1] * 5))
    return [{"subreddit": s, "posts": c, "upvotes": sc} for s, c, sc in ranked[:max_subs]]


# --------------------------------------------------------------------------- #
# 2. gather
# --------------------------------------------------------------------------- #
def collect(topic: str, subreddits: list[str], days: int = 180,
            per_sub_limit: int = 40, max_posts: int = 150) -> tuple[list[Post], list[str]]:
    """Collect from EVERY available Reddit source, merge + dedupe, then cap at
    `max_posts` (guardrail on cost/noise). Returns (posts, sources_used)."""
    from ..connectors import reddit_official
    now = dt.datetime.now(dt.timezone.utc)
    after = (now - dt.timedelta(days=days)).isoformat()
    q = _core_query(topic)
    posts: list[Post] = []
    used: list[str] = []

    arc = reddit_live.arctic_multi(subreddits, q, after, now.isoformat(),
                                   limit=per_sub_limit, kind="posts")
    if arc:
        used.append("arctic-shift"); posts += arc

    pp = reddit_live.pullpush_search(q, after, now.isoformat(), size=per_sub_limit)
    if pp:
        used.append("pullpush"); posts += pp

    if reddit_official.configured():                 # official API if creds present
        off = reddit_official.fetch(q, subreddits)
        if off:
            used.append("reddit-official-api"); posts += off

    seen, out = set(), []
    for p in sorted(posts, key=lambda p: p.ts):
        if p.id and p.id not in seen:
            seen.add(p.id); out.append(p)
    return out[-max_posts:], used                    # cap: keep most recent


def gather(topic: str, subreddits: list[str], days: int = 180) -> list[Post]:
    return collect(topic, subreddits, days)[0]


# --------------------------------------------------------------------------- #
# 3. analyze  (Grok live, else offline heuristics)
# --------------------------------------------------------------------------- #
INTEL_SCHEMA = (
    '{"sentiment":"bullish|bearish|mixed","sentiment_score":-1..1,'
    '"conviction":0..1,"momentum":"rising|falling|flat",'
    '"narratives":[{"claim":"...","stance":"yes|no|neutral","support":int}],'
    '"key_posts":["quote the 1-3 most informative posts"],'
    '"notable_accounts":["..."],"coordination_hint":0..1,'
    '"market_read":"what this implies for the related prediction market",'
    '"summary":"3-4 sentences"}')

BULL = {"win", "winning", "wins", "leads", "leading", "ahead", "favorite", "surge",
        "surges", "rising", "rise", "up", "likely", "gains", "strong", "boost"}
BEAR = {"lose", "losing", "loses", "behind", "trails", "trailing", "drop", "drops",
        "falling", "fall", "down", "scandal", "unlikely", "threat", "collapse", "weak"}


def _offline_analyze(topic: str, posts: list[Post]) -> dict:
    if not posts:
        return {"summary": "No Reddit chatter found for this topic.", "sentiment": "mixed"}
    b = s = 0
    for p in posts:
        w = set(p.text.lower().split())
        b += len(w & BULL); s += len(w & BEAR)
    net = (b - s) / max(1, b + s)
    top = sorted(posts, key=lambda p: p.reach, reverse=True)[:3]
    authors = collections.Counter(p.author for p in posts)
    repeat = [a for a, c in authors.items() if c > 1 and a != "[deleted]"]
    return {
        "sentiment": "bullish" if net > 0.15 else "bearish" if net < -0.15 else "mixed",
        "sentiment_score": round(net, 2),
        "conviction": round(min(1.0, len(posts) / 40), 2),
        "momentum": "flat",
        "narratives": [{"claim": t.text[:120], "stance": "neutral", "support": t.reach}
                       for t in top],
        "key_posts": [f"@{t.author}: {t.text[:160]}" for t in top],
        "notable_accounts": repeat[:5],
        "coordination_hint": round(min(1.0, len(repeat) / max(1, len(authors))), 2),
        "market_read": "Heuristic only — add XAI_API_KEY for Grok's market read.",
        "summary": (f"{len(posts)} posts on '{topic}' from "
                    f"{posts[0].ts[:10]} to {posts[-1].ts[:10]}. "
                    f"Lexical tilt: {b} bullish vs {s} bearish signals "
                    f"(net {net:+.2f}). Top post scored {top[0].reach} upvotes."),
        "engine": "offline-heuristic",
    }


def analyze(topic: str, posts: list[Post]) -> dict:
    if posts and grok.live():
        g = grok.reason_json(
            system=("You are a research analyst reading Reddit chatter about a topic that "
                    "trades on prediction markets. Extract sentiment, competing narratives, "
                    "the most informative posts, and any coordination. Be terse and factual."),
            user=reddit_live.format_for_grok(topic, posts),
            schema_hint=INTEL_SCHEMA, effort="low")
        if g:
            g["engine"] = "grok-4.5"
            return g
    return _offline_analyze(topic, posts)


# --------------------------------------------------------------------------- #
# orchestrator — the skill
# --------------------------------------------------------------------------- #
def run(topic: str, limits: dict | None = None,
        extra_subs: list[str] | None = None) -> dict:
    """Autonomous Reddit research on `topic`. `limits` overrides DEFAULT_LIMITS
    (guardrails on breadth/volume/window) — see DEFAULT_LIMITS for keys."""
    lim = _limits(limits)
    discovered = discover_subreddits(topic, max_subs=lim["max_subreddits"],
                                     pool_probe=lim["pool_probe"])
    subs = [d["subreddit"] for d in discovered] + SEED_SUBS + (extra_subs or [])
    subs = list(dict.fromkeys(subs))              # dedupe, keep order
    posts, sources_used = collect(topic, subs, days=lim["lookback_days"],
                                  per_sub_limit=lim["per_sub_limit"],
                                  max_posts=lim["max_posts"])
    intel = analyze(topic, posts)
    return {
        "topic": topic,
        "query_used": _core_query(topic),
        "limits": lim,
        "sources_used": sources_used,
        "discovered_subreddits": discovered,
        "subs_monitored": subs,
        "stats": {"posts": len(posts),
                  "range": [posts[0].ts[:16], posts[-1].ts[:16]] if posts else None},
        "posts_sample": [p.model_dump() for p in posts[-15:]],
        "intel": intel,
    }


# function-calling spec so Grok can invoke this skill autonomously
REDDIT_INTEL_TOOL = {
    "type": "function",
    "function": {
        "name": "reddit_intel",
        "description": ("Autonomously research a topic on Reddit: discover the most "
                        "relevant subreddits, pull the chatter, and return sentiment, "
                        "narratives, key posts, and a market read. Use for any question "
                        "about public opinion, breaking sentiment, or crowd signal."),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "e.g. 'brazil presidential election'"},
                "days": {"type": "integer", "description": "lookback window, default 120"},
            },
            "required": ["topic"],
        },
    },
}


if __name__ == "__main__":
    import sys, json
    topic = " ".join(sys.argv[1:]) or "brazil presidential election"
    rep = run(topic)
    print(f"\n=== REDDIT INTEL: {topic} ===")
    print("discovered subs:", ", ".join(f"r/{d['subreddit']}({d['posts']}p/{d['upvotes']}u)"
                                         for d in rep["discovered_subreddits"]))
    print("posts:", rep["stats"]["posts"], "| range:", rep["stats"]["range"])
    print("engine:", rep["intel"].get("engine"))
    print("\nINTEL:")
    print(json.dumps({k: rep["intel"][k] for k in
                      ("sentiment", "sentiment_score", "summary", "key_posts", "notable_accounts")
                      if k in rep["intel"]}, indent=1))
