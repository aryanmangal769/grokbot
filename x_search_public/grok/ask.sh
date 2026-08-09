#!/usr/bin/env bash
# xAI curl helpers. Keys come from the repo-root `.env`.
#
#   ./x_search_public/grok/ask.sh text  "Your prompt"
#   ./x_search_public/grok/ask.sh image "A collage of London landmarks..."
#   ./x_search_public/grok/ask.sh video "A glowing crystal-powered rocket..."
#   ./x_search_public/grok/ask.sh tts   "Hello!" [-o hello.mp3] [--voice eve]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

load_key() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi

  if [[ -z "${XAI_API_KEY:-}" || "$XAI_API_KEY" == xai-your-key-here ]]; then
    echo "Missing XAI_API_KEY. Add it to $REPO_ROOT/.env (see .env.example)." >&2
    exit 1
  fi
  export XAI_API_KEY
}

need_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for this command (brew install jq)" >&2
    exit 1
  fi
}

cmd_text() {
  local prompt="${*:-Fix this function and explain the bug: function median(a){a.sort();return a[a.length/2]}}"
  local model="${XAI_MODEL:-grok-4.5}"
  local prompt_json
  prompt_json=$(json_escape "$prompt")

  curl -sS https://api.x.ai/v1/responses \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $XAI_API_KEY" \
    -d "{
      \"model\": \"$model\",
      \"input\": $prompt_json
    }"
  echo
}

cmd_image() {
  local prompt="${*:-A collage of London landmarks in a stenciled street-art style}"
  local model="${XAI_IMAGE_MODEL:-grok-imagine-image-quality}"
  local prompt_json
  prompt_json=$(json_escape "$prompt")

  curl -sS https://api.x.ai/v1/images/generations \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $XAI_API_KEY" \
    -d "{
      \"model\": \"$model\",
      \"prompt\": $prompt_json
    }"
  echo
}

cmd_video() {
  need_jq
  local prompt="${*:-A glowing crystal-powered rocket launching from Mars}"
  local model="${XAI_VIDEO_MODEL:-grok-imagine-video}"
  local prompt_json request_id result status
  prompt_json=$(json_escape "$prompt")

  request_id=$(
    curl -sS -X POST https://api.x.ai/v1/videos/generations \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $XAI_API_KEY" \
      -d "{
        \"model\": \"$model\",
        \"prompt\": $prompt_json
      }" | jq -r '.request_id'
  )

  if [[ -z "$request_id" || "$request_id" == null ]]; then
    echo "Failed to start video generation (no request_id)" >&2
    exit 1
  fi
  echo "video request_id=$request_id" >&2

  while true; do
    result=$(
      curl -sS "https://api.x.ai/v1/videos/$request_id" \
        -H "Authorization: Bearer $XAI_API_KEY"
    )
    status=$(echo "$result" | jq -r '.status')
    if [[ "$status" == "done" ]]; then
      echo "$result" | jq -r '.video.url'
      break
    fi
    if [[ "$status" == "failed" || "$status" == "expired" ]]; then
      echo "$result" >&2
      exit 1
    fi
    echo "status=$status …" >&2
    sleep 5
  done
}

cmd_tts() {
  local voice="eve"
  local language="en"
  local output="hello.mp3"
  local text_parts=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --voice) voice="$2"; shift 2 ;;
      --language) language="$2"; shift 2 ;;
      -o|--output) output="$2"; shift 2 ;;
      *) text_parts+=("$1"); shift ;;
    esac
  done

  local text="${text_parts[*]:-Hello! Welcome to the xAI Text to Speech API.}"
  local text_json http_code
  text_json=$(json_escape "$text")

  http_code=$(
    curl -sS -X POST https://api.x.ai/v1/tts \
      -H "Authorization: Bearer $XAI_API_KEY" \
      -H "Content-Type: application/json" \
      -d "{
        \"text\": $text_json,
        \"voice_id\": \"$voice\",
        \"language\": \"$language\"
      }" \
      --output "$output" \
      --write-out '%{http_code}'
  )

  if [[ "$http_code" != "200" ]]; then
    echo "TTS error $http_code: $(cat "$output")" >&2
    rm -f "$output"
    exit 1
  fi
  echo "Saved to $output"
}

usage() {
  cat <<'EOF'
Usage:
  ./x_search_public/grok/ask.sh text  [prompt]
  ./x_search_public/grok/ask.sh image [prompt]
  ./x_search_public/grok/ask.sh video [prompt]
  ./x_search_public/grok/ask.sh tts   [text] [--voice eve] [--language en] [-o hello.mp3]
  ./x_search_public/grok/ask.sh [prompt]          # shorthand for text
EOF
}

main() {
  if [[ $# -gt 0 ]]; then
    case "$1" in
      -h|--help|help) usage; return ;;
    esac
  fi

  load_key

  if [[ $# -eq 0 ]]; then
    cmd_text
    return
  fi

  case "$1" in
    text) shift; cmd_text "$@" ;;
    image) shift; cmd_image "$@" ;;
    video) shift; cmd_video "$@" ;;
    tts) shift; cmd_tts "$@" ;;
    *) cmd_text "$@" ;;
  esac
}

main "$@"
