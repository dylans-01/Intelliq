#!/usr/bin/env bash
# Starts an isolated Intelliq server for end-to-end tests: fresh data directory,
# sample content, built web app, fixed development admin password.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$(mktemp -d)"
export INTELLIQ_DATA_DIR="$DATA"
export INTELLIQ_WEB_DIST="$ROOT/apps/web/dist"
export INTELLIQ_DEV_ADMIN_PASSWORD="e2e-admin-password"
export INTELLIQ_LOGIN_RATE=1000
test -f "$INTELLIQ_WEB_DIST/index.html" || { echo "Build the web app first: npm run web:build" >&2; exit 1; }
exec "$ROOT/apps/api/.venv/bin/intelliq" dev --host 127.0.0.1 --port 8765
