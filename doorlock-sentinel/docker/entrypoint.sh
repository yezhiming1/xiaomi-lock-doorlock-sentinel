#!/bin/sh
set -eu

mkdir -p /run/doorlock/secrets /tmp/doorlock

data_owner=$(stat -c '%u' /data)
case "$data_owner" in
  0)
    mkdir -p /data/derived /data/exports
    chown doorlock:doorlock /data/derived /data/exports
    chown doorlock:doorlock /data
    ;;
  10001)
    runuser -u doorlock -- mkdir -p /data/derived /data/exports
    for directory in /data/derived /data/exports; do
      if [ "$(runuser -u doorlock -- stat -c '%u' "$directory")" != "10001" ]; then
        echo "Persistent data subdirectory has an unexpected owner" >&2
        exit 78
      fi
    done
    ;;
  *)
    echo "Persistent data directory has an unexpected owner" >&2
    exit 78
    ;;
esac

for name in internal_api_secret web_password_hash security_pepper wecom_bot_secret; do
  source="/run/secrets/$name"
  target="/run/doorlock/secrets/$name"
  if [ -f "$source" ]; then
    cp "$source" "$target"
    chmod 0400 "$target"
  fi
done

for required in internal_api_secret web_password_hash security_pepper; do
  if [ ! -s "/run/doorlock/secrets/$required" ]; then
    echo "Required runtime secret is missing: $required" >&2
    exit 78
  fi
done
if [ "${WECOM_ENABLED:-false}" = "true" ] && [ ! -s /run/doorlock/secrets/wecom_bot_secret ]; then
  echo "WeCom is enabled but its bot secret is missing" >&2
  exit 78
fi

chown doorlock:doorlock /run/doorlock /run/doorlock/secrets /tmp/doorlock
chown doorlock:doorlock /run/doorlock/secrets/* 2>/dev/null || true
runuser -u doorlock -- /opt/venv/bin/alembic -c /app/alembic.ini upgrade head
exec /usr/bin/supervisord -c /app/docker/supervisord.conf
