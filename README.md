# grokbot

Minimal template for the [xAI API](https://docs.x.ai/docs/overview): text, images, video, and TTS.

## Setup

1. Copy your API key from https://console.x.ai/
2. Put it in either place (both are gitignored):

```bash
# option A
cp .env.example .env
# edit .env → XAI_API_KEY=xai-...

# option B
echo 'xai-...' > api_key.txt
```

3. Install deps (Python path):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Video shell helper also needs `jq` (`brew install jq`).

## Usage

### Python

```bash
python xai_client.py text "What is the capital of France?"
python xai_client.py image "A collage of London landmarks in a stenciled street-art style"
python xai_client.py video "A glowing crystal-powered rocket launching from Mars"
python xai_client.py tts "Hello! Welcome to the xAI Text to Speech API." -o hello.mp3

# shorthand → text
python xai_client.py "Explain quantum entanglement in one sentence"
```

### Shell (`./ask.sh`)

```bash
chmod +x ask.sh
./ask.sh text  "Explain quantum entanglement in one sentence"
./ask.sh image "A collage of London landmarks in a stenciled street-art style"
./ask.sh video "A glowing crystal-powered rocket launching from Mars"
./ask.sh tts   "Hello! Welcome to the xAI Text to Speech API." -o hello.mp3
```

### Raw curl

**Text** (`/v1/responses`)

```bash
curl -sS https://api.x.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-4.5",
    "input": "Fix this function and explain the bug: function median(a){a.sort();return a[a.length/2]}"
  }'
```

**Image** (`/v1/images/generations`)

```bash
curl -sS https://api.x.ai/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-image-quality",
    "prompt": "A collage of London landmarks in a stenciled street-art style"
  }'
```

**Video** (start + poll)

```bash
REQUEST_ID=$(curl -sS -X POST https://api.x.ai/v1/videos/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -d '{
    "model": "grok-imagine-video",
    "prompt": "A glowing crystal-powered rocket launching from Mars"
  }' | jq -r '.request_id')

while true; do
  RESULT=$(curl -sS "https://api.x.ai/v1/videos/$REQUEST_ID" \
    -H "Authorization: Bearer $XAI_API_KEY")
  STATUS=$(echo "$RESULT" | jq -r '.status')
  if [ "$STATUS" = "done" ]; then
    echo "$RESULT" | jq -r '.video.url'
    break
  fi
  if [ "$STATUS" = "failed" ] || [ "$STATUS" = "expired" ]; then
    echo "$RESULT" >&2
    exit 1
  fi
  sleep 5
done
```

**TTS** (`/v1/tts`)

```bash
HTTP_CODE=$(curl -sS -X POST https://api.x.ai/v1/tts \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello! Welcome to the xAI Text to Speech API.",
    "voice_id": "eve",
    "language": "en"
  }' \
  --output hello.mp3 \
  --write-out '%{http_code}')

if [ "$HTTP_CODE" != "200" ]; then
  echo "TTS error $HTTP_CODE: $(cat hello.mp3)" >&2
  rm -f hello.mp3
else
  echo "Saved to hello.mp3"
fi
```
