#!/usr/bin/env bash
# Local development without Docker: API on :8000 (SQLite by default) + web client on :8080.
# Usage: ./scripts/dev.sh            (Ctrl+C stops both)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "Создаю .venv и ставлю зависимости…"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt" -r "$ROOT/backend/requirements-dev.txt"
fi

export DATABASE_URL="${DATABASE_URL:-sqlite:///./local.db}"

trap 'kill 0' EXIT
(cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app --reload --port 8000) &
"$PY" "$ROOT/scripts/serve_web.py" 8080 &
echo "API: http://localhost:8000/docs   Сайт: http://localhost:8080"
wait
