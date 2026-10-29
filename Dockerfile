# ---- web UI build -------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx tsc -b && npx vite build --outDir /ui/dist --emptyOutDir

# ---- runtime ------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ASSTATS_DATA_DIR=/data

RUN groupadd --system --gid 1000 asstats \
 && useradd --system --uid 1000 --gid asstats --home-dir /data --no-create-home asstats

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY asstats ./asstats
COPY --from=ui /ui/dist ./asstats/static
RUN pip install . supervisor \
 && rm -rf /app/asstats /app/build

COPY docker/supervisord.conf /etc/supervisord.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data && chown asstats:asstats /data

VOLUME ["/data"]
EXPOSE 8080/tcp 9000/udp 6343/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisord.conf"]
