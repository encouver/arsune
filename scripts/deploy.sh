#!/usr/bin/env bash
# Deploy Arsune to https://arsune.augevi.com. Usage: scripts/deploy.sh [--dry-run]
# Copies the repo with rsync (never .env), then builds and starts the stack over ssh.
set -euo pipefail

HOST=augevi@api.staging.personaizit.com
REMOTE_DIR=arsune
URL=https://arsune.augevi.com/
COMPOSE="docker compose -p arsune -f compose.yaml -f compose.prod.yaml"
cd "$(dirname "$0")/.."

RSYNC=(rsync -az --delete
  --exclude .git --exclude .env --exclude '.env.*' --exclude node_modules --exclude .venv
  --exclude frontend/.next --exclude test-results --exclude dist --exclude .claude
  --exclude .beads --exclude __pycache__ --exclude .data)

if [ "${1:-}" = "--dry-run" ]; then
  # Dry-run against an empty local folder: shows what would be sent, never contacts the server.
  empty=$(mktemp -d)
  trap 'rmdir "$empty"' EXIT
  "${RSYNC[@]}" --dry-run --stats ./ "$empty/" | grep -E 'Number of (regular )?files|Total (transferred )?file size'
  exit 0
fi

"${RSYNC[@]}" ./ "$HOST:$REMOTE_DIR/"
ssh "$HOST" "cd $REMOTE_DIR && $COMPOSE up -d --build --wait --wait-timeout 300 && $COMPOSE ps --format '{{.Service}} {{.Status}}'"

echo "Public check (status codes only):"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$URL" || true)
  [ "$code" = 200 ] && break
  sleep 5
done
echo "GET $URL -> $code"
[ "$code" = 200 ]
