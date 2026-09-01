#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PREDECESSOR_IMAGE=doorlock-sentinel:0.0.4
TARGET_IMAGE=doorlock-sentinel:0.0.5

if [ -z "${DOORLOCK_EXPECTED_PREDECESSOR_IMAGE_ID:-}" ]; then
  printf '%s\n' 'UPGRADE_BUILD_FAIL expected predecessor image ID is required' >&2
  exit 2
fi
if [ -z "${DOORLOCK_EXPECTED_SOURCE_COMMIT:-}" ]; then
  printf '%s\n' 'UPGRADE_BUILD_FAIL expected source commit is required' >&2
  exit 2
fi

actual_predecessor=$(docker image inspect "$PREDECESSOR_IMAGE" --format '{{.Id}}')
if [ "$actual_predecessor" != "$DOORLOCK_EXPECTED_PREDECESSOR_IMAGE_ID" ]; then
  printf '%s\n' 'UPGRADE_BUILD_FAIL predecessor image identity mismatch' >&2
  exit 3
fi

actual_source=$(git -C "$ROOT" rev-parse HEAD)
if [ "$actual_source" != "$DOORLOCK_EXPECTED_SOURCE_COMMIT" ]; then
  printf '%s\n' 'UPGRADE_BUILD_FAIL source commit identity mismatch' >&2
  exit 4
fi

docker build \
  --pull=false \
  --build-arg "PREDECESSOR_IMAGE=$PREDECESSOR_IMAGE" \
  --build-arg "DOORLOCK_SOURCE_REVISION=$actual_source" \
  --file "$ROOT/Dockerfile.upgrade" \
  --tag "$TARGET_IMAGE" \
  "$ROOT"

label_version=$(docker image inspect "$TARGET_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.version"}}')
label_revision=$(docker image inspect "$TARGET_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
if [ "$label_version" != "0.0.5" ] || [ "$label_revision" != "$actual_source" ]; then
  printf '%s\n' 'UPGRADE_BUILD_FAIL output image identity mismatch' >&2
  exit 5
fi

docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --entrypoint /opt/venv/bin/python "$TARGET_IMAGE" \
  -c 'import importlib.metadata as m; import doorlock_sentinel as d; assert d.__version__ == m.version("doorlock-sentinel") == "0.0.5"'

printf '%s\n' "UPGRADE_BUILD_PASS source=$actual_source predecessor=$actual_predecessor"
