#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
mkdir -p secrets runtime models
[ -f .env ] || cp .env.example .env
python3 scripts/create_secrets.py \
  --directory secrets \
  --password-output initial_web_password.txt
python3 scripts/download_models.py --directory models
printf '%s\n' 'Bootstrap complete. Review .env before starting the isolated container.'
