#!/usr/bin/env bash
# Backend tests + dependency vulnerability audit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
"$ROOT/.venv/bin/python" -m pytest -q
"$ROOT/.venv/bin/pip-audit" -r requirements.txt
