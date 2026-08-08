# grokbot

One repo, one `.env`, two API surfaces:

| API | Console | Client | Shell |
|-----|---------|--------|-------|
| **xAI** | https://console.x.ai/ | `python -m xai.client` | `./xai/ask.sh` |
| **X** | https://console.x.com/accounts/2085957761431465984 | `python -m x.client` | `./x/ask.sh` |

```
grokbot/
├── .env                 # both keys (gitignored)
├── .env.example
├── common/env.py        # shared env loader
├── xai/
│   ├── client.py
│   └── ask.sh
└── x/
    ├── client.py
    └── ask.sh
```

## Setup

1. Copy keys into `.env` (see `.env.example`):

```bash
cp .env.example .env
# edit .env:
#   XAI_API_KEY=...
#   X_BEARER_TOKEN=...
#   X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET=...
```

2. Install Python deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Make shell helpers executable:

```bash
chmod +x xai/ask.sh x/ask.sh
```

## xAI

```bash
python -m xai.client text "What is the capital of France?"
python -m xai.client image "A collage of London landmarks in a stenciled street-art style"
python -m xai.client video "A glowing crystal-powered rocket launching from Mars"
python -m xai.client tts "Hello!" -o hello.mp3

./xai/ask.sh text "Explain quantum entanglement"
./xai/ask.sh image "..."
./xai/ask.sh video "..."
./xai/ask.sh tts "Hello!" -o hello.mp3
```

## X (Twitter)

Bearer token covers read helpers. OAuth 1.0a keys cover `me` and `post`.

```bash
python -m x.client user elonmusk
python -m x.client me
python -m x.client search "from:xai" --max 10
python -m x.client post "Hello from grokbot"

./x/ask.sh user elonmusk
./x/ask.sh me
./x/ask.sh search "from:xai"
./x/ask.sh post "Hello from grokbot"
```
