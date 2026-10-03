# Деплой «Мастер рядом»

Схема: **Caddy** (HTTPS, сертификат Let's Encrypt) → **nginx** (сайт + `/api/`) → **backend** (FastAPI) →
**Postgres** + **Redis**. Всё в Docker. Миграции базы применяются автоматически при старте backend.

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

Большую часть заполнит `setup.sh`; вручную нужно указать SMS-шлюз и (по желанию) Telegram-бот.

| Переменная | Что указать |
|---|---|
| `ENVIRONMENT` | `production` — сервер не запустится с небезопасными значениями ниже |
| `DOMAIN` | ваш домен без `https://` |
| `SITE_URL` | `https://ваш-домен` |
| `JWT_SECRET`, `POSTGRES_PASSWORD` | случайные строки: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CORS_ORIGINS` | `https://ваш-домен` |
| `SMS_PROVIDER` | реальный SMS-шлюз (см. ниже) — с `console` production не стартует |
| `TELEGRAM_*` | токен и имя бота, случайный `TELEGRAM_WEBHOOK_SECRET` (см. ниже) |
| `SUBSCRIPTIONS_ENABLED` | `true`, когда готовы включить платный тариф мастеров |

## 3. Запуск

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker compose exec backend python -m app.create_admin "+996700000000" "надёжный-пароль" "Имя"
```

Проверка: `https://ваш-домен` открывается с замком, `https://ваш-домен/api/health` → `{"status":"ok"}`.

Обновление после изменений в коде:

```bash
git pull && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

## 4. SMS

Нужен договор с SMS-шлюзом (в Кыргызстане — например Nikita/SMSPRO.kg). Подключение — подкласс
`SmsProvider` в `backend/app/sms.py` (инструкция в начале файла), затем `SMS_PROVIDER=<имя>` и данные шлюза в `.env`.
Через SMS идут коды подтверждения (регистрация, смена пароля) и уведомления мастерам, выбравшим SMS.

## 5. Почта (email-уведомления мастерам)

Мастер указывает email при регистрации; письма о новых заказах уходят тем, кто включил уведомления и выбрал Email.
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
- [ ] Регистрация: SMS с кодом приходит на реальный номер
- [ ] Восстановление пароля работает
- [ ] Telegram: мастер подключает бота из профиля, приходит уведомление о новом заказе
- [ ] Email: мастер с каналом Email получает письмо о новом заказе (проверьте папку «Спам»)
- [ ] Создан администратор, админ-панель открывается
- [ ] Бэкап выполнен вручную (`deploy/backup.sh`) и проверено восстановление
