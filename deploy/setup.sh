#!/usr/bin/env bash
# First-time server setup (Ubuntu). Run from the project folder on the server:
#   sudo ./deploy/setup.sh
# Installs Docker if needed and creates .env with generated secrets. Safe to re-run: an existing .env is kept.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "→ Устанавливаю Docker…"
  curl -fsSL https://get.docker.com | sh
fi

if [ -f .env ]; then
  echo "→ .env уже есть — не трогаю. Проверьте значения по DEPLOY.md."
else
  read -rp "Домен сайта (например master-ryadom.kg): " DOMAIN
  DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%/}"
  secret() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48; }
  cp .env.example .env
  sed -i.bak \
    -e "s|^ENVIRONMENT=.*|ENVIRONMENT=production|" \
    -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" \
    -e "s|^SITE_URL=.*|SITE_URL=https://${DOMAIN}|" \
    -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://${DOMAIN}|" \
    -e "s|^JWT_SECRET=.*|JWT_SECRET=$(secret)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" \
    -e "s|^TELEGRAM_WEBHOOK_SECRET=.*|TELEGRAM_WEBHOOK_SECRET=$(secret)|" \
    .env
  rm -f .env.bak
  chmod 600 .env
  echo "→ Создан .env с новыми секретами (ENVIRONMENT=production, домен ${DOMAIN})."
fi

cat <<'NEXT'

Дальше:
  1. В .env впишите SMS_PROVIDER (реальный шлюз) и, при желании, TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME.
     С SMS_PROVIDER=console сервер в режиме production не запустится — так задумано (DEPLOY.md, раздел 4).
  2. Запуск:
       docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
  3. Администратор:
       docker compose exec backend python -m app.create_admin "+996XXXXXXXXX" "пароль" "Имя"
  4. Бэкапы: добавьте deploy/backup.sh в cron (DEPLOY.md, раздел 6).
NEXT
