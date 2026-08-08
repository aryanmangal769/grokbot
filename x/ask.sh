#!/usr/bin/env bash
# X API v2 curl helpers. Keys come from the repo-root `.env`.
# Account: https://console.x.com/accounts/2085957761431465984
#
#   ./x/ask.sh user elonmusk
#   ./x/ask.sh me
#   ./x/ask.sh search "from:xai"
#   ./x/ask.sh post "Hello from grokbot"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi
}

need_bearer() {
  if [[ -z "${X_BEARER_TOKEN:-}" || "$X_BEARER_TOKEN" == your-* ]]; then
    echo "Missing X_BEARER_TOKEN. Add it to $REPO_ROOT/.env (see .env.example)." >&2
    exit 1
  fi
}

need_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required for this command (brew install jq)" >&2
    exit 1
  fi
}

cmd_user() {
  need_bearer
  local username="${1:?username required}"
  username="${username#@}"

  curl -sS "https://api.x.com/2/users/by/username/${username}?user.fields=id,name,username,created_at,description,public_metrics,verified" \
    -H "Authorization: Bearer $X_BEARER_TOKEN"
  echo
}

cmd_me() {
  # User-context endpoints need OAuth 1.0a signing — use the Python client.
  python3 -m x.client me
}

cmd_search() {
  need_bearer
  need_jq
  local query="${*:-from:xai}"
  local encoded
  encoded=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$query")

  curl -sS "https://api.x.com/2/tweets/search/recent?query=${encoded}&max_results=10&tweet.fields=created_at,lang,public_metrics,author_id" \
    -H "Authorization: Bearer $X_BEARER_TOKEN"
  echo
}

cmd_post() {
  # POST /2/tweets requires OAuth 1.0a — use the Python client.
  local text="${*:-Hello from grokbot}"
  python3 -m x.client post "$text"
}

usage() {
  cat <<'EOF'
Usage:
  ./x/ask.sh user <username>     # Bearer read
  ./x/ask.sh me                  # OAuth 1.0a (Python)
  ./x/ask.sh search <query>      # Bearer read
  ./x/ask.sh post <text>         # OAuth 1.0a (Python)

Credentials live in the repo-root .env (X_BEARER_TOKEN + OAuth 1.0a keys).
Console: https://console.x.com/accounts/2085957761431465984
EOF
}

main() {
  if [[ $# -eq 0 ]]; then
    usage
    exit 1
  fi

  case "$1" in
    -h|--help|help) usage; return ;;
  esac

  load_env

  case "$1" in
    user) shift; cmd_user "$@" ;;
    me) shift; cmd_me "$@" ;;
    search) shift; cmd_search "$@" ;;
    post) shift; cmd_post "$@" ;;
    *)
      echo "Unknown command: $1" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
