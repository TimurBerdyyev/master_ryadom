FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user. uploads/ is created here so a fresh Docker volume mounted on it
# inherits this ownership and stays writable.
RUN useradd --system --uid 1000 --home /app app \
    && mkdir -p /app/uploads \
    && chown -R app:app /app/uploads
USER app

# --proxy-headers: take the client IP from nginx's X-Forwarded-For (needed for per-IP rate limiting)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
