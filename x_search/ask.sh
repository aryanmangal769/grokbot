#!/usr/bin/env bash
# Topic → X dataset via xAI x_search.
#
#   ./x_search/ask.sh "brazil presidential elections" --outdir data/brazil-elections
#   ./x_search/ask.sh "world cup" --window 24 --slices 6

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" || "$1" == "help" ]]; then
  cat <<'EOF'
Usage:
  ./x_search/ask.sh "<topic>" [--window H] [--slices N] [--facets N] [--outdir path]

See x_search/README.md for the full pipeline.
EOF
  exit 0
fi

if [[ -f "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="python3"
fi

exec "$PY" -m x_search.extract "$@"
