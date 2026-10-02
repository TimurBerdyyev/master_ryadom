# Мастер рядом — заметки для разработки

Сервис поиска мастеров: клиент создаёт заказ → мастера его категории присылают цены → клиент выбирает → мастер ведёт статус → клиент оставляет отзыв.

## Запуск и проверка
- `./scripts/dev.sh` — API :8000 (SQLite `backend/local.db`) + сайт :8080 без кеша браузера.
- `./scripts/test.sh` — pytest (`backend/tests/`, временная SQLite) + `pip-audit`. Запускать перед каждым коммитом.
- Docker: `docker compose up --build` (Postgres; nginx из `deploy/nginx.conf` проксирует `/api/` → backend).

## Устройство
- `backend/app/` — FastAPI + SQLAlchemy 2.0. Таблицы создаются через `create_all` при старте (`main.py` lifespan),
  недостающие индексы досоздаются там же. Alembic подключён, но миграций пока нет: новая **колонка** в
  существующей таблице сама не появится — для неё нужна миграция или пересоздание локальной БД.
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
пагинация в админке, перенос rate-limit в Redis при нескольких процессах backend.
