# One container for hosting (Render): the FastAPI API, its WebSocket and the built React app on a single origin,
# so session cookies, CSRF and /ws work exactly as they do behind nginx in docker-compose.
#   Render builds this from render.yaml. Locally: docker build -t hireflo . && docker run -p 8000:8000 hireflo

FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/srv/frontend \
    UPLOAD_DIR=/srv/backend/data/uploads

WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend/app ./app
COPY backend/pyproject.toml .
COPY --from=web /web/dist /srv/frontend

RUN useradd --create-home --uid 10001 driveops \
    && mkdir -p /srv/backend/data/uploads /srv/backend/seed \
    && chown -R driveops /srv/backend
USER driveops

EXPOSE 8000
# Render passes PORT and RENDER_EXTERNAL_URL; PUBLIC_URL (magic links, venue QR) defaults to the latter.
# SEED_ON_START=true seeds the demo season only when the database is empty.
CMD ["sh", "-c", "export PUBLIC_URL=\"${PUBLIC_URL:-${RENDER_EXTERNAL_URL:-http://localhost:${PORT:-8000}}}\"; export CORS_ORIGINS=\"${CORS_ORIGINS:-$PUBLIC_URL}\"; if [ \"$SEED_ON_START\" = \"true\" ]; then python -m app.seed; fi; exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --proxy-headers --forwarded-allow-ips='*'"]
