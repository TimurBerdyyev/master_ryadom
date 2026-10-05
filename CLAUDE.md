# Мастер рядом — заметки для разработки

Сервис поиска мастеров: клиент создаёт заказ → мастера его категории присылают цены → клиент выбирает → мастер ведёт статус → клиент оставляет отзыв.

## Запуск и проверка
- `./scripts/dev.sh` — API :8000 (SQLite `backend/local.db`) + сайт :8080 без кеша браузера.
- `./scripts/test.sh` — pytest (`backend/tests/`, временная SQLite) + `pip-audit`. Запускать перед каждым коммитом.
- Docker: `docker compose up --build` (Postgres; nginx из `deploy/nginx.conf` проксирует `/api/` → backend).

## Устройство
- `backend/app/` — FastAPI + SQLAlchemy 2.0. Схема БД — **миграции Alembic** (`backend/alembic/versions/`),
  применяются автоматически при старте (`app/migrations.py`). После изменения моделей:
  `cd backend && alembic revision --autogenerate -m "..."`, проверить файл, закоммитить.
  Тест `test_models_match_migrations` падает, если миграцию забыли. Для Postgres в `downgrade` удалять enum-типы.
- Тесты можно прогнать на Postgres: `TEST_DATABASE_URL=postgresql://... python -m pytest`.
- `backend/app/routers/orders.py` — весь жизненный цикл заказа: `MASTER_PROGRESS` задаёт допустимые статусы,
  контакты сторон (`client_contact`/`master_contact`) отдаются только после выбора мастера (`_order_out`).
- `web/` — статические страницы без сборки. Общий код: `web/js/api.js` (все вызовы API + хелперы `esc`,
  `avatarHtml`, `formatPrice`, `statusPillHtml`, `toast`, `requireLogin`, `safeNext`), `web/js/nav.js` (шапка по роли).
- Стили — один файл `web/css/style.css` на CSS-переменных (`--primary`, `--accent`, `--ink`, `--muted`, `--r-*`, `--shadow-*`).

## Дизайн-система
- Шрифты: **Unbounded** — только заголовки, логотип, крупные цифры (`--font-display`); **Onest** — весь остальной текст.
  Оба с кириллицей, подключены в начале `style.css`.
- Иконки: **Lucide** (ISC) спрайтом в `web/js/icons.js`. В JS — `icon("name")`, в HTML —
  `<svg class="i"><use href="#i-name"></use></svg>`. Новую иконку добавлять `<symbol>` в спрайт
  (исходники: `npm pack lucide-static`). **Эмодзи вместо иконок не использовать.**
- `icons.js` подключается на каждой странице перед `api.js`.
- Цвета: синий `--primary` — основные действия, оранжевый `--accent` — главный CTA («Создать заказ», «Предложить»),
  зелёный — подтверждение/WhatsApp. У каждой категории своя иконка и цвет — `CATEGORY_STYLE` в `api.js`,
  рисовать через `categoryBadge()`.
- Готовые хелперы: `avatarHtml`, `ratingHtml`, `starsHtml`, `verifiedBadge`, `statusPillHtml`, `emptyState`, `skeletons`, `toast`.
- Проверять вёрстку на 390px и 1280px; анимации уважают `prefers-reduced-motion`.

## Телефон, SMS, уведомления
- Email обязателен для всех и уникален; подтверждается кодом из письма: `/auth/send-code` (purpose=register|reset|change_email)
  → `/auth/register` / `/auth/reset-password` / `/auth/change-email`. Вход — по телефону или email (`/auth/login`, поле `phone`).
  Смена пароля разлогинивает старые токены (`password_changed_at`). Коды — `app/verification.py`
  (10 мин, 5 неверных попыток, повтор через 60 с, 5 писем/час на адрес). Email не попадает в публичные ответы (только `/auth/me`).
- `EMAIL_PROVIDER=console` (по умолчанию) пишет письма в лог и **возвращает код в ответе API** (`debug_code`) —
  только для разработки; с `ENVIRONMENT=production` сервер с ним не стартует. SMS (`app/sms.py`) сейчас только для
  уведомлений и недоступны, пока `SMS_PROVIDER=console`.
- Уведомления мастерам вне сайта — **только по согласию** (галочка при регистрации, по умолчанию выключена),
  канал Telegram или SMS, меняется в профиле. Вызов: `notify(db, user_id, title, text, kind=..., **params)`
  из `app/notify.py` — in-app запись + сообщение по каналу мастера на его языке, отправка после commit в фоне.
  Шаблоны сообщений — `TEMPLATES` в `notify.py`.
- Email мастера обязателен при регистрации (у клиентов — нет); канал `email` отправляет письма через `app/email.py`
  (`EMAIL_PROVIDER=console|smtp`, `SMTP_SECURITY=starttls|ssl|none`, TLS не отключается молча).
- Telegram-бот — `app/telegram.py` (привязка по одноразовой ссылке `t.me/<bot>?start=<token>`),
  вебхук `/telegram/webhook` с секретом в заголовке; локально — `python -m app.telegram_poll`.

## Города
- Список городов — `backend/app/cities.py` (`CITIES`, канонические русские названия; переводы — `city.*` в `i18n.js`).
  Сайт берёт список из `/config`, выбор только из списка (`cityOptionsHtml()`), сервер нормализует регистр (`normalize_city`).
- Заказ обязательно с городом; мастер указывает город при регистрации. Лента `/orders/feed` и уведомления
  о новых заказах — только заказы города мастера (+ старые заказы без города). Мастер без города видит пустую ленту.

## Деплой
Пошагово — `DEPLOY.md` (Caddy с HTTPS → nginx → backend; Postgres, Redis; бэкапы `deploy/backup.sh`).
`ENVIRONMENT=production` не даёт запуститься с дефолтным JWT, console-SMS, `CORS_ORIGINS=*`, `SITE_URL` на localhost.

## Языки (ru / ky / en)
- Все тексты интерфейса — в `web/js/i18n.js`, каждая строка `"ключ": [ru, ky, en]`. Скрипт подключается
  на каждой странице сразу после `icons.js`. Выбор языка — переключатель в шапке (`ensureLangSwitch` в `nav.js`),
  хранится в `localStorage.lang`, по умолчанию — язык браузера.
- Статичная разметка: `data-i18n="key"` (текст), `data-i18n-html`, `data-i18n-ph` (placeholder),
  `data-i18n-aria`, `data-i18n-title`. Из JS — `t("key", {vars})`, числа со словом — `plural(n, "p.orders")`.
  **Новый текст на страницу — только через ключ, сразу на трёх языках.** Пропущенный ключ пишет `console.error`.
- Названия категорий из БД переводит `catName()`, статусы — `statusLabel()`. Сервер отвечает по-русски:
  ошибки и уведомления переводит `tServer()` (точные строки `srv.*` + `SERVER_PATTERNS`). Добавили новую
  ошибку в backend — добавьте её перевод в `i18n.js`.
- Даты/цены — только через `formatDate()` / `formatPrice()` / `priceFrom()` (для ky дата форматируется вручную:
  браузеры не знают кыргызской локали). Кыргызские переводы стоит вычитать носителю языка.
- Пользовательские данные (имена, описания, услуги) не переводятся.

## Платный тариф мастеров (выключен до запуска)
- Флаг `SUBSCRIPTIONS_ENABLED` (по умолчанию `false`): пока выключен, мастера пользуются всем бесплатно,
  строки подписок не создаются, страница «Тариф» и пункт меню скрыты.
- Включение: при старте все мастера без подписки получают пробный период (`SUBSCRIPTION_TRIAL_DAYS`, 30 дней),
  новые — при регистрации. Без доступа мастер скрыт из поиска, а лента и отклики отвечают **402**.
- Логика — `backend/app/subscriptions.py` (`PLANS` — скидки за 3/6/12 мес, `mark_paid` суммирует оплату
  с остатком срока). Эндпоинты — `routers/subscriptions.py`: `/config`, `/subscription/me`, `/subscription/me/checkout`,
  `/payments/webhook/{provider}`, админские `/admin/subscriptions*`.
- Оплата: сейчас `PAYMENT_PROVIDER=manual` — заявка создаётся, админ подтверждает её на вкладке «Подписки».
  Реальный эквайринг подключается подклассом `PaymentProvider` в `backend/app/payments.py` (инструкция в начале файла).
- Подписка хранится в отдельных таблицах (`master_subscriptions`, `subscription_payments`), чтобы не требовать
  миграции существующей таблицы `masters`.

## Правила, которые нельзя нарушать
- **Любые данные пользователя в HTML — только через `esc()`**. Не вставлять значения в `onclick="...'${x}'"`
  (там работают только числовые id с сервера).
- Редиректы по параметрам URL — только через `safeNext()`.
- Каждый эндпоинт с `order_id` / `photo_id` / `service_id` проверяет владельца (см. `_check_order_access`).
- Новые связи, которые попадают в ответ списком, загружать через `selectinload` — иначе N+1 запросов
  (поиск мастеров на 40 мастерах был 121 запрос, стал 4).
- Публичная регистрация не даёт роль `admin`; админа создаёт `python -m app.create_admin`.
- Телефоны нормализуются (`normalize_phone` в `schemas.py`) — сравнивать только нормализованные.

## Идеи на будущее
Чат по заказу (таблица `messages` уже есть), аватар мастера, поиск по расстоянию на фронте (API: `sort=distance&lat&lon`),
пагинация в админке, реальные SMS-шлюз и эквайринг, перевод описаний мастеров.
