# grokbot

One repo, one `.env`:

| Tool | Path | Run |
|------|------|-----|
| **Grok / xAI demos** | `api_usage_demo/grok/` | `python -m api_usage_demo.grok.client` · `./api_usage_demo/grok/ask.sh` |
| **X / Twitter demos** | `api_usage_demo/twitter/` | `python -m api_usage_demo.twitter.client` · `./api_usage_demo/twitter/ask.sh` |
| **X search extractor** | `x_search/` | `python -m x_search.extract` · `./x_search/ask.sh` |
| **User scraper** | `user_based_scraper/` | `python -m user_based_scraper.user` · `./user_based_scraper/ask.sh` |
| **Interest classifier** | `user_interest_classifier/` | `python -m user_interest_classifier.classify` · `./user_interest_classifier/ask.sh` |
| **Insights DB + API** | `insights/` | `python -m insights.seed_events` · `python -m insights.build_sentiment` · `uvicorn insights.api:app --port 8000` |

```
grokbot/
├── .env
├── common/
├── api_usage_demo/           # small API how-to clients
│   ├── grok/
│   └── twitter/
├── x_search/                 # topic → X dataset (xAI x_search)
├── user_based_scraper/       # public user timeline export
├── user_interest_classifier/ # scrape → Grok interest labels
├── data/                     # harvested datasets
└── docs/
```

## Setup

```bash
cp .env.example .env
# fill XAI_API_KEY + X_* credentials

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x api_usage_demo/grok/ask.sh api_usage_demo/twitter/ask.sh \
  x_search/ask.sh user_based_scraper/ask.sh user_interest_classifier/ask.sh
```

## API usage demos

```bash
python -m api_usage_demo.grok.client text "What is the capital of France?"
./api_usage_demo/grok/ask.sh image "A collage of London landmarks..."

python -m api_usage_demo.twitter.client user elonmusk
./api_usage_demo/twitter/ask.sh me
```

## X search extractor

Topic → high-recall X post dataset via xAI `x_search` + optional X API hydration. See [`x_search/README.md`](x_search/README.md).

```bash
python -m x_search.extract "brazil presidential elections" --outdir data/brazil-elections
./x_search/ask.sh "world cup" --window 24 --slices 6
```

## User-based scraper

Public posts / reposts / quotes (optional replies). Other users' likes are private on X.

```bash
python -m user_based_scraper.user elonmusk --max-pages 3 -o outputs/elon.json
./user_based_scraper/ask.sh elonmusk --include-replies -o outputs/elon.json
```

## User interest classifier

Scrapes the public timeline, then asks Grok (xAI API) to label interests. Saves to `outputs/<user>_interests.json` by default.

```bash
python -m user_interest_classifier.classify elonmusk --max-pages 2
./user_interest_classifier/ask.sh elonmusk
```

## Insights DB + API

Local-first backend for the X prediction-markets tab (see
[`docs/system-design.md`](docs/system-design.md)). SQLite stand-in for Supabase +
a FastAPI read layer that the browser feed and Expo phone app both consume.

The DB holds **only Polymarket bet data** (events + sentiment + posts). User
interests are **not** stored — they come live from the Grok interest classifier
(`user_interest_classifier` → `outputs/<user>_interests.json`) and rank events
per user at request time.

```bash
# 1. classify demo users (real Grok, writes outputs/<user>_interests.json)
python -m user_interest_classifier.classify VitalikButerin --max-pages 2
python -m user_interest_classifier.classify BarackObama    --max-pages 2
python -m user_interest_classifier.classify business       --max-pages 2

# 2. seed the bet DB (live Gamma fetch -> top ~18 events)
python -m insights.seed_events

# 3. real Grok sentiment: X (x_search) + Reddit (PullPush) -> analysis
python -m insights.build_sentiment --limit 6      # or --all

# 4. serve
uvicorn insights.api:app --port 8000   # GET /users · /events?user= · /events/{id}
```

`seed_events` writes placeholder sentiment (`source='seed-placeholder'`);
`build_sentiment` overwrites it with **real** analysis (`source='grok'`) by
harvesting X (x_search) + Reddit (PullPush) and reasoning with Grok, and fills
`top_posts` with grounded, stance-tagged posts. `app.db` is gitignored; re-run
seed + build to rebuild.
