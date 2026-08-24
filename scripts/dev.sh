#!/usr/bin/env bash
set -euo pipefail

ROOT_DIRECTORY="$(cd "$(dirname "$0")/.." && pwd)"
PIDS=()

cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup INT TERM EXIT

if [[ ! -f "$ROOT_DIRECTORY/services/api/.env" ]]; then
  echo "Missing services/api/.env. Copy services/api/.env.example and configure PostgreSQL first."
  exit 1
fi

(cd "$ROOT_DIRECTORY/services/recommendation-engine" && ./run.sh) & PIDS+=("$!")
(cd "$ROOT_DIRECTORY/services/api" && npm run dev) & PIDS+=("$!")
(cd "$ROOT_DIRECTORY/apps/mobile" && npm start) & PIDS+=("$!")

echo "Pub Discovery is running. Press Ctrl-C to stop all services."

# macOS ships with Bash 3.2, which does not support `wait -n`.
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      exit 1
    fi
  done
  sleep 1
done
