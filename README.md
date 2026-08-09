# grokbot (prod)

Core production features: requirements + venv setup, user-based scraper, and user interest classifier.

One repo, one `.env`:

| Tool | Path | Run |
|------|------|-----|
| **User scraper** | `user_based_scraper/` | `python -m user_based_scraper.user` · `./user_based_scraper/ask.sh` |
| **Interest classifier** | `user_interest_classifier/` | `python -m user_interest_classifier.classify` · `./user_interest_classifier/ask.sh` |
| **Summary video** | `summary/` | `python -m summary.imagine_brief --input camps.json` · see `summary/README.md` |
| **Grok / xAI demos** | `api_usage_demo/grok/` | `python -m api_usage_demo.grok.client` · `./api_usage_demo/grok/ask.sh` |
| **X / Twitter demos** | `api_usage_demo/twitter/` | `python -m api_usage_demo.twitter.client` · `./api_usage_demo/twitter/ask.sh` |
| **X search extractor** | `x_search/` | `python -m x_search.extract` · `./x_search/ask.sh` |

```
grokbot/
├── .env
├── common/
├── api_usage_demo/           # small API how-to clients (used by scraper/classifier)
│   ├── grok/
│   └── twitter/
├── x_search/                 # topic → X dataset (xAI x_search)
├── user_based_scraper/       # public user timeline export
├── user_interest_classifier/ # scrape → Grok interest labels
├── data/                     # harvested datasets
└── docs/
```

## Setup (venv + requirements)

```bash
cp .env.example .env
# fill XAI_API_KEY + X_* credentials

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x user_based_scraper/ask.sh user_interest_classifier/ask.sh \
  api_usage_demo/grok/ask.sh api_usage_demo/twitter/ask.sh \
  summary/ask.sh
```

The `user_based_scraper` and `user_interest_classifier` (and their supporting modules) are the core on this prod branch.
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
