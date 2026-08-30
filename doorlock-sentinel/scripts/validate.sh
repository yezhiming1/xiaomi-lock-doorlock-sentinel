#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/doorlock-validation.XXXXXX")

cleanup() {
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
    rm -rf -- "$WORK"
  fi
}
trap cleanup EXIT HUP INT TERM

cd "$ROOT"
export PYTHONPATH="$ROOT/src"

"$PYTHON_BIN" -m compileall -q src tests scripts
"$PYTHON_BIN" -m ruff check .
"$PYTHON_BIN" -m pytest
"$PYTHON_BIN" scripts/validate_model_lock.py
"$PYTHON_BIN" scripts/audit_public_tree.py

MIGRATION_DB="$WORK/migration.sqlite3"
DOORLOCK_ENVIRONMENT=test \
DOORLOCK_DATABASE_URL="sqlite:///$MIGRATION_DB" \
"$PYTHON_BIN" -m alembic upgrade head
DOORLOCK_ENVIRONMENT=test \
DOORLOCK_DATABASE_URL="sqlite:///$MIGRATION_DB" \
"$PYTHON_BIN" -m alembic downgrade base
DOORLOCK_ENVIRONMENT=test \
DOORLOCK_DATABASE_URL="sqlite:///$MIGRATION_DB" \
"$PYTHON_BIN" -m alembic upgrade head

"$PYTHON_BIN" scripts/smoke.py

for script in docker/*.sh scripts/*.sh; do
  sh -n "$script"
done

if command -v npm >/dev/null 2>&1; then
  (
    cd services/wecom-bot
    npm ci --no-audit --no-fund
    npm test
    npm run build
    npm audit --omit=dev --audit-level=high
  )
else
  printf '%s\n' 'VALIDATION_FAIL npm is required' >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  LOCKED_HASH=041f73f47371333d1d17a6fee6c8ab4e6aecabefe398ff32cca4e2d5eaee0af9
  DOORLOCK_PUBLIC_BASE_URL=https://doorlock.example.invalid \
  DOORLOCK_TRUSTED_HOSTS=doorlock.example.invalid,localhost,127.0.0.1 \
  DOORLOCK_INBOX_DIR="$WORK/inbox" \
  DOORLOCK_DATA_DIR="$WORK/data" \
  DOORLOCK_MODELS_DIR="$WORK/models" \
  DOORLOCK_SECRETS_DIR="$WORK/secrets" \
  DOORLOCK_DETECTOR_SHA256="$LOCKED_HASH" \
  DOORLOCK_RECOGNIZER_SHA256=4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43 \
  docker compose config --quiet
  if [ "${DOORLOCK_VALIDATE_DOCKER:-0}" = "1" ]; then
    docker build --tag doorlock-sentinel:validation .
  fi
else
  printf '%s\n' 'VALIDATION_FAIL docker is required' >&2
  exit 1
fi

if [ "${DOORLOCK_VALIDATE_MODELS:-0}" = "1" ]; then
  "$PYTHON_BIN" scripts/download_models.py --directory "$WORK/models"
fi

printf '%s\n' 'VALIDATION_PASS'
