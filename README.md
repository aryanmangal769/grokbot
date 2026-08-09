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
| **Polymarket X report** | `polymarket_x_report.py` | `python polymarket_x_report.py input.json` |

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

## Polymarket X report

Send a pre-fetched discussion JSON (with `topic`, `global`, and `posts`) to Grok
and write a compact, machine-readable report. It uses `grok-4.5` and requires
`XAI_API_KEY` in `.env`.

```bash
python polymarket_x_report.py input.json --output report.json
# or: python polymarket_x_report.py - < input.json > report.json
```

The report contains the exact topic, a tweet/reply synthesis, the top three
source posts for each of two camps, `X_leaning` (`yes`, `no`, or `mixed`), and
the supplied total topic tweet volume (`num_tweets`), rather than the number of
sampled posts. The script refuses malformed input or an
invalid model response rather than silently emitting an unreliable report.

### PostgreSQL / Supabase batch mode

With `DATABASE_URL` set, analyze each unprocessed row in
`public.topic_opinions` and upsert its report into
`public.topic_opinion_reports`:

```bash
export DATABASE_URL='postgresql://...'
python polymarket_x_report.py --from-db
```

The source collection must include `num_tweets` (either at its top level or
under `totals`). This is the total topic volume—not the count of sampled posts
or threads—and the batch job skips nothing by silently substituting a sample
count. Use `--force` to regenerate existing reports and `--limit N` for a
bounded batch.

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
