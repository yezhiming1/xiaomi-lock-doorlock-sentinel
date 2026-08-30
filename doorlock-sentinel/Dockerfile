FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS wecom-build
WORKDIR /build
COPY services/wecom-bot/package.json services/wecom-bot/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY services/wecom-bot/src ./src
COPY services/wecom-bot/tsconfig.json ./
RUN npm run build && npm prune --omit=dev

FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS runtime
LABEL org.opencontainers.image.title="Doorlock Sentinel" \
      org.opencontainers.image.description="NAS-native door-lock video recognition and review console" \
      org.opencontainers.image.version="0.0.3" \
      org.opencontainers.image.licenses="MIT"
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates ffmpeg libgomp1 python3 python3-pip python3-venv \
      supervisor tini util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && addgroup --system --gid 10001 doorlock \
    && adduser --system --uid 10001 --gid 10001 --home /nonexistent --no-create-home doorlock \
    && adduser doorlock root
WORKDIR /app
COPY pyproject.toml requirements.lock README.md alembic.ini models.lock.json ./
RUN /opt/venv/bin/pip install --no-cache-dir \
      pip==25.0.1 setuptools==80.9.0 wheel==0.45.1 \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock
COPY src/doorlock_sentinel ./src/doorlock_sentinel
COPY migrations ./migrations
RUN /opt/venv/bin/pip install --no-cache-dir --no-build-isolation --no-deps . \
    && /opt/venv/bin/pip check \
    && rm -rf /app/src/*.egg-info
COPY --from=wecom-build /build/dist ./services/wecom-bot/dist
COPY --from=wecom-build /build/node_modules ./services/wecom-bot/node_modules
COPY services/wecom-bot/package.json ./services/wecom-bot/package.json
COPY docker ./docker
COPY scripts ./scripts
COPY docs ./docs
RUN chmod +x /app/docker/entrypoint.sh /app/scripts/*.sh \
    && mkdir -p /data/derived /data/exports /inbox /models /run/doorlock \
    && chown -R doorlock:doorlock /data /run/doorlock
EXPOSE 8787
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
HEALTHCHECK --interval=30s --timeout=8s --start-period=120s --retries=3 CMD ["/opt/venv/bin/python", "/app/docker/healthcheck.py"]
