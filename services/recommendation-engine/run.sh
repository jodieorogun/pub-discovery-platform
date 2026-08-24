#!/usr/bin/env bash

# Start the best locally available version of the application.
set -euo pipefail

PROJECT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIRECTORY"

APP_PORT="${PORT:-8000}"
VENUE_DATABASE="data/local/westminster_camden_venues_enriched.json"
EVIDENCE_DATABASE="data/local/westminster_camden_evidence.sqlite3"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it from https://docs.astral.sh/uv/ and retry."
  exit 1
fi

# Fail with a useful message instead of Uvicorn's less obvious bind error.
if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$APP_PORT" -sTCP:LISTEN >/dev/null; then
  echo "Port $APP_PORT is already in use."
  echo "Run with another port: PORT=8001 ./run.sh"
  exit 1
fi

if [[ -f "$VENUE_DATABASE" && -f "$EVIDENCE_DATABASE" ]]; then
  echo "Starting the 408-venue SQLite + FTS5 recommendation engine."
  export APP_DATABASE_PATH="$EVIDENCE_DATABASE"
  export RAG_MODEL_CACHE_PATH="data/local/models"
  exec uv run --extra rag python -m uvicorn app.main:app --host 127.0.0.1 --port "$APP_PORT"
fi

if [[ -f "$EVIDENCE_DATABASE" ]]; then
  echo "Starting the unified SQLite + FTS5 recommendation engine."
  export APP_DATABASE_PATH="$EVIDENCE_DATABASE"
  export RAG_MODEL_CACHE_PATH="data/local/models"
  exec uv run --extra rag python -m uvicorn app.main:app --host 127.0.0.1 --port "$APP_PORT"
fi

# A fresh clone has no generated local database, so keep the demo runnable.
echo "Local RAG data was not found; starting the 15-venue deterministic demo."
echo "See README.md if you want to rebuild the full local database."
exec uv run python -m uvicorn app.main:app --host 127.0.0.1 --port "$APP_PORT"
