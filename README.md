# grokbot

Polymarket × X opinion intelligence: scrape public X activity, classify user interests with Grok, harvest topic discussion, synthesize camp-level reports, then surface them in an X-style markets UI with auto-generated explainer videos.

**Demo** (Markets UI walkthrough): [`docs/markets-ui-demo.mp4`](docs/markets-ui-demo.mp4) · Pipeline map: [`docs/pipeline-flow.html`](docs/pipeline-flow.html)

```
X user activity ──► Interest agent ──┐
Polymarket odds ─────────────────────┼──► Report agent (Grok) ──► Markets UI
X topic harvest + opinion threads ───┘              └──► Explainer video
```

One repo, one `.env`:

| Tool | Path | Run |
|------|------|-----|
| **User scraper** | `user_based_scraper/` | `python -m user_based_scraper.user` · `./user_based_scraper/ask.sh` |
| **Interest classifier** | `user_interest_classifier/` | `python -m user_interest_classifier.classify` · `./user_interest_classifier/ask.sh` |
| **X topic harvest** | `x_search_public/` | topic search → posts + volume |
| **Polymarket contract** | `polymarket_contract/` | live odds / liquidity (Gamma · CLOB · Data API) |
| **Report agent** | `polymarket_x_report.py` | `python polymarket_x_report.py input.json` |
| **Explainer video** | `summary/` | `python -m summary.imagine_brief --input camps.json` |
| **Markets UI** | `markets-ui/` | serve folder → `index.html` / `event.html` |
| **Grok / X demos** | `api_usage_demo/` | `./api_usage_demo/grok/ask.sh` · `./api_usage_demo/twitter/ask.sh` |

```
grokbot/
├── markets-ui/               # X-style feed + event pages (odds, camps, video)
├── docs/                     # pipeline overview + UI demo clip
├── user_based_scraper/       # public timeline export
├── user_interest_classifier/ # timeline → Grok personas / interests
├── x_search_public/          # topic harvest + opinion threads
├── polymarket_contract/      # verified market data
├── polymarket_x_report.py    # hub: camps, leaning, tweet synthesis → DB/UI/video
├── summary/                  # Grok Imagine + ffmpeg explainer mp4
├── api_usage_demo/           # grok/ + twitter/ clients
└── common/                   # shared env helpers
```

## Setup (venv + requirements)

```bash
cp .env.example .env
# fill XAI_API_KEY + X_* credentials (+ DATABASE_URL for batch reports)

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x user_based_scraper/ask.sh user_interest_classifier/ask.sh \
  api_usage_demo/grok/ask.sh api_usage_demo/twitter/ask.sh \
  summary/ask.sh
```

## Markets UI

Static dashboard: persona-filtered home feed and event pages with odds, camp posts, charts, and explainer video.

```bash
# from repo root so ../outputs/... video paths resolve
python3 -m http.server 8011
open http://localhost:8011/markets-ui/index.html
```

## API usage demos

```bash
python -m api_usage_demo.grok.client text "What is the capital of France?"
./api_usage_demo/grok/ask.sh image "A collage of London landmarks..."

python -m api_usage_demo.twitter.client user elonmusk
./api_usage_demo/twitter/ask.sh me
```

## X topic harvest

Topic → high-recall X post dataset (search + optional hydration). See `x_search_public/`.

## Polymarket X report

Send discussion JSON (`topic`, `global`, `posts`) to Grok and write a compact report. Uses `grok-4.5`; needs `XAI_API_KEY`.

```bash
python polymarket_x_report.py input.json --output report.json
```

Output: topic, tweet/reply synthesis, top three posts per camp, `X_leaning` (`yes` / `no` / `mixed`), and topic volume (`num_tweets`). Malformed input or bad model responses are refused rather than inventing a report.

### PostgreSQL / Supabase batch mode

With `DATABASE_URL`, analyze unprocessed `public.topic_opinions` rows and upsert into `public.topic_opinion_reports`:

```bash
export DATABASE_URL='postgresql://...'
python polymarket_x_report.py --from-db
```

Use `--force` to regenerate and `--limit N` for a bounded batch.

## Explainer video

Camp JSON → Grok monologue → tweet cards → Grok Imagine host clips → ffmpeg PiP (`summary_imagine.mp4`). See [`summary/README.md`](summary/README.md).

```bash
python -m summary.imagine_brief --input path/to/camps.json -o outputs/summary/run
./summary/ask.sh --input path/to/camps.json
```

## User-based scraper

Public posts / reposts / quotes (optional replies). Other users' likes are private on X.

```bash
python -m user_based_scraper.user elonmusk --max-pages 3 -o outputs/elon.json
./user_based_scraper/ask.sh elonmusk --include-replies -o outputs/elon.json
```

## User interest classifier

Scrapes the public timeline, then asks Grok to label interests. Default output: `outputs/<user>_interests.json`.

```bash
python -m user_interest_classifier.classify elonmusk --max-pages 2
./user_interest_classifier/ask.sh elonmusk
```
