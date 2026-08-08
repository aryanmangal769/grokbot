#!/usr/bin/env bash
# Scrape a public X user's timeline (posts / reposts / quotes [/ replies]).
#
#   ./user_based_scraper/ask.sh elonmusk
#   ./user_based_scraper/ask.sh elonmusk --max-pages 5 -o outputs/elon.json
#   ./user_based_scraper/ask.sh elonmusk --include-replies

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" || "$1" == "help" ]]; then
  cat <<'EOF'
Usage:
  ./user_based_scraper/ask.sh <username> [--max-pages N] [--include-replies] [-o path.json]

Fetches public timeline activity only. Other users' likes are private on X.
EOF
  exit 0
fi

if [[ -f "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="python3"
fi

exec "$PY" -m user_based_scraper.user "$@"
