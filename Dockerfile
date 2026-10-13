# WatchFox production image: the website, the API and the watches. No model is inside: checking is done by a remote agent
# server reached over Tailscale, so the image stays small (no PyTorch).

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 WATCHFOX_HOME=/tmp/watchfox WATCHFOX_HOST=0.0.0.0 WATCHFOX_PORT=8787 WATCHFOX_TRUST_PROXY=1 WATCHFOX_WEB=/app/web/dist
# Tailscale (static binaries) so the app can join your tailnet in "userspace" mode: no special privileges are needed
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale /usr/local/bin/tailscale
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/watchfox /var/run/tailscale /var/lib/tailscale
COPY pyproject.toml ./
COPY watchfox ./watchfox
RUN pip install . && pip install --no-deps "laya>=0.4.1"
COPY --from=web /web/dist ./web/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8787
ENTRYPOINT ["/entrypoint.sh"]
