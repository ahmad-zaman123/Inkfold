#!/usr/bin/env bash
# Runs n8n on this machine with the Inkfold workflow imported and switched on.
# Needs Node 24+. Reads HUBSPOT_TOKEN, HUBSPOT_PORTAL_ID and INKFOLD_SHARED_SECRET from n8n/.env.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
runtime="$here/.data/runtime"
# The test run points this somewhere else so it never touches your own n8n data.
data="${N8N_DATA_DIR:-$here/.data}"
port="${N8N_PORT:-5690}"

if [ -z "${N8N_SKIP_ENV_FILE:-}" ] && [ -f "$here/.env" ]; then
  set -a; . "$here/.env"; set +a
fi
: "${INKFOLD_SHARED_SECRET:?Set INKFOLD_SHARED_SECRET in n8n/.env (the same value as N8N_SHARED_SECRET in Backend/.env).}"

node_major="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$node_major" -lt 24 ]; then
  echo "n8n needs Node 24 or newer (found $(node --version))." >&2
  exit 1
fi

if [ ! -x "$runtime/node_modules/.bin/n8n" ]; then
  echo "Installing n8n into n8n/.data/runtime (one time, a few minutes)..."
  mkdir -p "$runtime"
  # --force gets past an arm64-only package n8n lists as required. npm 11 skips install scripts,
  # and SQLite (n8n's default database) needs its one to fetch its native build.
  (cd "$runtime" && npm install --no-audit --no-fund --force n8n@2.46.4 && npm rebuild sqlite3)
fi

export N8N_USER_FOLDER="${N8N_USER_FOLDER:-$data}"
export N8N_PORT="$port"
export N8N_HOST=localhost
export WEBHOOK_URL="http://localhost:$port/"
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_PERSONALIZATION_ENABLED=false
export N8N_RUNNERS_ENABLED=true
export N8N_BLOCK_ENV_ACCESS_IN_NODE=false
export NODE_FUNCTION_ALLOW_BUILTIN=crypto
export N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true

n8n="$runtime/node_modules/.bin/n8n"
"$n8n" import:workflow --input="$here/document-to-hubspot.json"
"$n8n" publish:workflow --id=fwDocToHubspot01
echo "n8n is starting at http://localhost:$port (webhook: http://localhost:$port/webhook/inkfold)"
exec "$n8n" start
