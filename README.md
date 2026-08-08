# grokbot

One repo, one `.env`:

| Tool | Path | Run |
|------|------|-----|
| **Grok / xAI demos** | `api_usage_demo/grok/` | `python -m api_usage_demo.grok.client` · `./api_usage_demo/grok/ask.sh` |
| **X / Twitter demos** | `api_usage_demo/twitter/` | `python -m api_usage_demo.twitter.client` · `./api_usage_demo/twitter/ask.sh` |
| **X search extractor** | `x_search/` | `python -m x_search.extract` · `./x_search/ask.sh` |
| **User scraper** | `user_based_scraper/` | `python -m user_based_scraper.user` · `./user_based_scraper/ask.sh` |
| **Interest classifier** | `user_interest_classifier/` | `python -m user_interest_classifier.classify` · `./user_interest_classifier/ask.sh` |
| **Insights DB + API** | `insights/` | `python -m insights.seed_personas` · `python -m insights.seed_events` · `uvicorn insights.api:app --port 8000` |

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

```bash
python -m insights.seed_personas          # 4 demo personas
python -m insights.seed_events            # top ~18 Polymarket events (live Gamma fetch)
uvicorn insights.api:app --port 8000      # GET /personas · /events?persona= · /events/{id}
```

Sentiment rows from `seed_events` are flagged `source='seed-placeholder'` — a
deterministic stand-in until the Grok Sentiment Agent (build step 5) overwrites
them with real X + Reddit analysis. `app.db` is gitignored; re-seed to rebuild.
