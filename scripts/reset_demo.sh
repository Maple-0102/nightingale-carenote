#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"

if [ "${1:-}" != "--yes" ]; then
  echo "This resets only the Nightingale demo container and its synthetic Docker data volume."
  echo "Run ./scripts/reset_demo.sh --yes to continue."
  exit 2
fi

docker compose down -v --remove-orphans
CARENOTE_SESSION_SECRET="${CARENOTE_SESSION_SECRET:-local-demo-secret-change-me}" docker compose up --build -d

container_id=$(docker compose ps -q carenote)
attempt=1
while [ "$attempt" -le 30 ]; do
  health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")
  if [ "$health" = "healthy" ]; then
    echo "Nightingale demo reset complete: http://127.0.0.1:8000"
    exit 0
  fi
  sleep 1
  attempt=$((attempt + 1))
done

echo "The demo restarted but did not become healthy within 30 seconds."
docker compose ps
exit 1
