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

# Oracle Cloud Ubuntu images ship iptables rules that REJECT everything except SSH, even when the
# cloud firewall (Security List) allows 80/443 — open the web ports and keep them after reboot.
if command -v iptables >/dev/null 2>&1 && iptables -S INPUT 2>/dev/null | grep -q "REJECT"; then
  for port in 80 443; do
    iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || iptables -I INPUT 5 -p tcp -m state --state NEW --dport "$port" -j ACCEPT
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save >/dev/null 2>&1 || true; fi
  echo "→ Открыл порты 80/443 во встроенном файрволе (iptables)."
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
  1. В .env настройте почту: EMAIL_PROVIDER=smtp и SMTP_* — на неё приходят коды подтверждения.
     С EMAIL_PROVIDER=console сервер в режиме production не запустится — так задумано (DEPLOY.md, раздел 5).
     По желанию: TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME, SMS_PROVIDER.
  2. Oracle Cloud: в панели откройте порты 80 и 443 (VCN → Security List → Ingress rules), см. DEPLOY.md.
  3. Запуск:
       docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
  4. Проверка: ./deploy/smoke_test.sh https://ваш-домен
  5. Администратор:
       docker compose exec backend python -m app.create_admin "+996XXXXXXXXX" "пароль" "Имя"
  6. Бэкапы: добавьте deploy/backup.sh в cron (DEPLOY.md, раздел «Бэкапы»).
NEXT
