#!/usr/bin/env bash
# Classify a public X user's interests (scraper → Grok via xAI API).
#
#   ./user_interest_classifier/ask.sh elonmusk
#   ./user_interest_classifier/ask.sh elonmusk --max-pages 2 -o outputs/elon_interests.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" || "$1" == "help" ]]; then
  cat <<'EOF'
Usage:
  ./user_interest_classifier/ask.sh <username> [--max-pages N] [--include-replies] [-o path.json]

Scrapes public timeline via user_based_scraper, then classifies with Grok (xAI API).
EOF
  exit 0
fi

if [[ -f "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="python3"
fi

exec "$PY" -m user_interest_classifier.classify "$@"
