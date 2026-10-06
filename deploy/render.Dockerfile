# Single-service image for Render (and similar PaaS): the backend serves the web client at /
# and the API at /api. Build context is the repository root (see render.yaml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVE_WEB_DIR=/app/web

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY web/ ./web/
RUN rm -rf tests .env *.db

RUN useradd --system --uid 1000 --home /app app \
    && mkdir -p /app/uploads \
    && chown -R app:app /app/uploads
USER app

# Render passes the port in $PORT (10000 by default); --proxy-headers gives the real client IP for rate limits.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
