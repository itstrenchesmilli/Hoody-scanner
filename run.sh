#!/usr/bin/env bash
# One-command start for the Hoody setup scanner.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PORT="${PORT:-8788}"
HOST="${HOST:-127.0.0.1}"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

export PYTHONPATH="$ROOT"
echo
echo "Hoody Setup Scanner"
echo "Open:  http://${HOST}:${PORT}/"
echo "API:   http://${HOST}:${PORT}/api/status"
echo "The first live scan of Bitunix perps takes 1–3 minutes."
echo "Not financial advice."
echo
exec python -m uvicorn backend.main:app --host "$HOST" --port "$PORT"
