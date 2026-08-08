# grokbot

One repo, one `.env`, three tools:

| Tool | Folder | Client | Shell |
|------|--------|--------|-------|
| **Grok / xAI** | `grok/` | `python -m grok.client` | `./grok/ask.sh` |
| **X / Twitter API** | `twitter/` | `python -m twitter.client` | `./twitter/ask.sh` |
| **User scraper** | `scraper/` | `python -m scraper.user` | `./scraper/ask.sh` |

```
grokbot/
├── .env                 # both keys (gitignored)
├── common/env.py
├── grok/                # xAI: text, image, video, TTS
├── twitter/             # X API helpers
└── scraper/             # public user timeline export
```

## Setup

```bash
cp .env.example .env
# fill XAI_API_KEY + X_* credentials

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x grok/ask.sh twitter/ask.sh scraper/ask.sh
```

## Grok (xAI)

```bash
python -m grok.client text "What is the capital of France?"
python -m grok.client image "A collage of London landmarks..."
./grok/ask.sh tts "Hello!" -o hello.mp3
```

## Twitter (X API)

```bash
python -m twitter.client user elonmusk
python -m twitter.client me
python -m twitter.client search "from:xai" --max 10
./twitter/ask.sh post "Hello from grokbot"
```

## Scraper (public user activity)

Collects posts / reposts / quotes (and optionally replies).  
**Other users' likes are private on X** and cannot be scraped via the official API.

```bash
python -m scraper.user elonmusk --max-pages 3 -o outputs/elon.json
./scraper/ask.sh elonmusk --include-replies -o outputs/elon.json
```
