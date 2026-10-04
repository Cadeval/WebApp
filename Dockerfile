# syntax=docker/dockerfile:1
# Verified multi-platform OCI digests; update only after reviewing the new image.
FROM python:3.14.6-slim-trixie@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 libgomp1 libgl1 \
    && rm -rf /var/lib/apt/lists/*

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM base AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
# Only locked binary wheels; no local environment, compiler, or dev group.
RUN uv sync --locked --no-dev --no-install-project --no-cache --no-build
COPY docker/ ./docker/
COPY apps/ ./apps/
COPY config/ ./config/
COPY resources/ ./resources/
COPY .well-known/ ./.well-known/
COPY sbom/ ./sbom/
COPY manage.py LICENSE SECURITY.md ./
# BuildKit also applies the exact .dockerignore allowlist before transmitting
# context. Recheck the received source and produce public static files only.
RUN python docker/context.py --received-context /build \
    && /opt/venv/bin/python docker/collect_static.py \
    && find /build -type d -exec chmod 0755 {} + \
    && find /build -type f -exec chmod 0644 {} +

FROM base AS runtime
LABEL org.opencontainers.image.title="Cadevil" \
      org.opencontainers.image.version="0.15.1" \
      org.opencontainers.image.description="IFC building assessment and signed workflow plugins" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.container \
    DATABASE_URL=sqlite:////app/data/db-instance.sqlite3 \
    ADMIN_LOG_PATH=/app/data/live-logs.sqlite3 \
    IFC_GEOMETRY_THREADS=2
RUN groupadd --gid 10001 cadevil \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app/data --shell /usr/sbin/nologin cadevil \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/apps /app/apps
COPY --from=builder /build/config /app/config
COPY --from=builder /build/resources /app/resources
COPY --from=builder /build/.well-known /app/.well-known
COPY --from=builder /build/sbom /app/sbom
COPY --from=builder /build/manage.py /build/LICENSE /build/SECURITY.md /app/
COPY --from=builder /build/docker/entrypoint.py /build/docker/healthcheck.py /app/docker/
USER 10001:10001
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
CMD ["serve"]
