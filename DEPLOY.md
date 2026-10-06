# Деплой «Мастер рядом»

Схема: **Caddy** (HTTPS, сертификат Let's Encrypt) → **nginx** (сайт + `/api/`) → **backend** (FastAPI) →
**Postgres** + **Redis**. Всё в Docker. Миграции базы применяются автоматически при старте backend.

## 0. Бесплатный тестовый сервер (Oracle Cloud + DuckDNS + Brevo)

Всё бесплатно и бессрочно; нужна банковская карта для проверки личности в Oracle (деньги не списываются).

**Сервер — Oracle Cloud Always Free** (2 ядра ARM, 12 ГБ памяти, 200 ГБ диска — с запасом для проекта):
1. Зарегистрируйтесь на <https://www.oracle.com/cloud/free/>; домашний регион выберите поближе (например Frankfurt) —
   поменять его потом нельзя.
2. Compute → Instances → Create instance: образ **Ubuntu 22.04/24.04**, Shape → Ampere **VM.Standard.A1.Flex**,
   2 OCPU / 12 ГБ. Скачайте SSH-ключ (или вставьте свой публичный). Если пишет «Out of capacity» — попробуйте позже
   или другой Availability Domain.
3. Откройте порты: Networking → Virtual Cloud Networks → ваша сеть → Security List → Add Ingress Rules:
   Source `0.0.0.0/0`, TCP, порты `80` и `443` (по одному правилу). Встроенный файрвол Ubuntu откроет `setup.sh`.
4. Подключитесь: `ssh -i ключ.key ubuntu@<публичный-IP>`.

**Домен — DuckDNS** (бесплатный поддомен `имя.duckdns.org`, HTTPS-сертификат выдаётся автоматически):
войдите на <https://www.duckdns.org> через Google/GitHub, создайте поддомен и впишите в него публичный IP сервера.
В `.env`: `DOMAIN=имя.duckdns.org`.

**Почта — Brevo** (бесплатно 300 писем в день — хватит для теста; без карты):
зарегистрируйтесь на <https://www.brevo.com>, подтвердите адрес отправителя (Senders), затем
SMTP & API → SMTP: возьмите логин и ключ. В `.env`:
```
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=<логин из Brevo>
SMTP_PASSWORD=<SMTP-ключ из Brevo>
SMTP_FROM=Мастер рядом <ваш-подтверждённый-адрес>
SMTP_SECURITY=starttls
```
Альтернатива для пары тестов — Gmail с «паролем приложения» (`smtp.gmail.com`, порт 587).

Дальше — шаги 1–3 ниже (`setup.sh`, `.env`, запуск), затем `./deploy/smoke_test.sh https://имя.duckdns.org`.

## 1. Сервер

- VPS с Ubuntu 22.04+, 2 ГБ RAM достаточно для старта.
- Домен (например `master-ryadom.kg`): A-запись `@` и `www` → IP сервера.
- Открыты порты 80 и 443 (и 22 для SSH). Остальные порты наружу не открываются — compose слушает их только на `127.0.0.1`.

```bash
git clone https://github.com/TimurBerdyyev/master_ryadom.git /opt/master_ryadom
cd /opt/master_ryadom
sudo ./deploy/setup.sh
```

`setup.sh` ставит Docker (если его нет), спрашивает домен и создаёт `.env` с новыми случайными секретами
и `ENVIRONMENT=production`. Существующий `.env` не перезаписывает.

## 2. Настройки `.env`

Большую часть заполнит `setup.sh`; вручную нужно указать почту (SMTP) и, по желанию, Telegram-бот и SMS-шлюз.

| Переменная | Что указать |
|---|---|
| `ENVIRONMENT` | `production` — сервер не запустится с небезопасными значениями ниже |
| `DOMAIN` | ваш домен без `https://` |
| `SITE_URL` | `https://ваш-домен` |
| `JWT_SECRET`, `POSTGRES_PASSWORD` | случайные строки: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CORS_ORIGINS` | `https://ваш-домен` |
| `EMAIL_PROVIDER`, `SMTP_*` | отправка почты (раздел 5) — **обязательно**: коды подтверждения приходят на email; с `console` production не стартует |
| `SMS_PROVIDER` | необязательно: только для SMS-уведомлений мастерам (раздел 4) |
| `TELEGRAM_*` | токен и имя бота, случайный `TELEGRAM_WEBHOOK_SECRET` (см. ниже) |
| `SUBSCRIPTIONS_ENABLED` | `true`, когда готовы включить платный тариф мастеров |

## 3. Запуск

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker compose exec backend python -m app.create_admin "+996700000000" "надёжный-пароль" "Имя"
```

Проверка: `https://ваш-домен` открывается с замком, а скрипт проверит API, статику и базу:

```bash
./deploy/smoke_test.sh https://ваш-домен
```

Если сайт не открывается: `docker compose logs caddy` (сертификат), `docker compose logs backend` (настройки —
в режиме production сервер пишет, что именно не так), а на Oracle — проверьте правила 80/443 в Security List.

Обновление после изменений в коде:

```bash
git pull && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

## 4. SMS (необязательно)

Коды подтверждения сейчас приходят на email, SMS для запуска не нужны. SMS-шлюз понадобится только для
SMS-уведомлений мастерам (пока он не подключён, этот вариант показан как «Скоро»). Подключение — подкласс
`SmsProvider` в `backend/app/sms.py` (инструкция в начале файла), затем `SMS_PROVIDER=<имя>` и данные шлюза в `.env`.

## 5. Почта (обязательно)

Email обязателен для всех при регистрации: на него приходит код подтверждения (так проверяется, что адрес настоящий),
код для смены пароля и — мастерам, включившим уведомления, — новые заказы.
В `.env`: `EMAIL_PROVIDER=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
(например `Мастер рядом <noreply@ваш-домен>`), `SMTP_SECURITY=starttls` (порт 587) или `ssl` (465).
Подойдёт любой почтовый ящик с «паролем приложения» (Gmail, Yandex, Mail.ru) — для больших объёмов лучше
транзакционный сервис (SendPulse, Mailgun, Brevo и т.п.), чтобы письма не попадали в спам.
Чтобы письма доходили, у домена должны быть записи SPF/DKIM — их выдаёт почтовый сервис.

## 6. Telegram-бот

1. В Telegram: @BotFather → `/newbot` → получите токен и имя бота.
2. В `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (без @), `TELEGRAM_WEBHOOK_SECRET` (случайная строка).
3. После запуска один раз: `docker compose exec backend python -m app.telegram_setup` — бот начнёт присылать
   обновления на `https://ваш-домен/api/telegram/webhook`.

Локально (без публичного адреса) вместо вебхука: `cd backend && python -m app.telegram_poll`.

## 7. Бэкапы

```bash
crontab -e
30 3 * * * /opt/master_ryadom/deploy/backup.sh >> /var/log/master_ryadom_backup.log 2>&1
```

Каждую ночь — дамп базы и архив фото в `backups/`, хранятся 14 дней. Копируйте их и на другой
сервер/хранилище: бэкап на том же диске не спасёт при потере сервера. Восстановление — в конце `deploy/backup.sh`.

## 8. Миграции базы

Применяются автоматически при старте. После изменения моделей в коде:

```bash
cd backend && alembic revision --autogenerate -m "что изменилось"
```

Проверьте сгенерированный файл в `backend/alembic/versions/` и закоммитьте его — тест
`test_models_match_migrations` упадёт, если миграцию забыли.

## Чек-лист перед запуском

- [ ] `ENVIRONMENT=production`, сервер стартует без ошибок (`docker compose logs backend`)
- [ ] HTTPS работает, `http://` перенаправляет на `https://`
- [ ] Регистрация: письмо с кодом приходит на реальный email (проверьте «Спам»)
- [ ] Восстановление пароля по email работает, вход по email и по телефону
- [ ] Telegram: мастер подключает бота из профиля, приходит уведомление о новом заказе
- [ ] Email: мастер с каналом Email получает письмо о новом заказе (проверьте папку «Спам»)
- [ ] Создан администратор, админ-панель открывается
- [ ] Бэкап выполнен вручную (`deploy/backup.sh`) и проверено восстановление
