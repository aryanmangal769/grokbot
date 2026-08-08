# grokbot

One repo, one `.env`:

| Tool | Path | Run |
|------|------|-----|
| **Grok / xAI demos** | `api_usage_demo/grok/` | `python -m api_usage_demo.grok.client` · `./api_usage_demo/grok/ask.sh` |
| **X / Twitter demos** | `api_usage_demo/twitter/` | `python -m api_usage_demo.twitter.client` · `./api_usage_demo/twitter/ask.sh` |
| **User scraper** | `user_based_scraper/` | `python -m user_based_scraper.user` · `./user_based_scraper/ask.sh` |
| **Interest classifier** | `user_interest_classifier/` | `python -m user_interest_classifier.classify` · `./user_interest_classifier/ask.sh` |

```
grokbot/
├── .env
├── common/
├── api_usage_demo/
│   ├── grok/                 # xAI: text, image, video, TTS
│   └── twitter/              # X API helpers
├── user_based_scraper/       # public user timeline export
└── user_interest_classifier/ # scrape → Grok interest labels
```

## Setup

```bash
cp .env.example .env
# fill XAI_API_KEY + X_* credentials

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x api_usage_demo/grok/ask.sh api_usage_demo/twitter/ask.sh user_based_scraper/ask.sh user_interest_classifier/ask.sh
```

## API usage demos

```bash
python -m api_usage_demo.grok.client text "What is the capital of France?"
./api_usage_demo/grok/ask.sh image "A collage of London landmarks..."

python -m api_usage_demo.twitter.client user elonmusk
./api_usage_demo/twitter/ask.sh me
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
