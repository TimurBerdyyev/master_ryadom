# Мастер рядом

Сервис поиска мастеров (сантехники, электрики, ремонт и т.д.): клиент создаёт заявку,
подходящие мастера присылают предложения по цене, клиент выбирает лучшее.

MVP: backend на FastAPI + PostgreSQL, простой веб-клиент вместо мобильного приложения/бота
(контакт с мастером — звонок или WhatsApp, см. ТЗ п.9).

Регистрация не обязательна для просмотра: категории, поиск мастеров, профиль
и отзывы доступны анонимно. Без входа скрывается телефон мастера (вместо кнопок
«Позвонить»/WhatsApp — приглашение войти); создание заказа и просмотр номера
требуют регистрации.

## Структура проекта

```
backend/   FastAPI-приложение, модели БД, JWT-авторизация
web/       Статический веб-клиент (HTML/CSS/JS без сборки)
docker-compose.yml
```

## Запуск через Docker (рекомендуется)

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

- API: http://localhost:8000 (документация — http://localhost:8000/docs)
- Веб-клиент: http://localhost:8080

При старте backend сам создаёт таблицы и заполняет список категорий услуг.

## Запуск backend локально без Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # укажите свою строку подключения к PostgreSQL
uvicorn app.main:app --reload
```

Веб-клиент можно открыть просто как статические файлы (`web/index.html`) —
он сам определяет `http://localhost:8000` как адрес API при локальном запуске.

## Основные сущности (раздел 13 ТЗ)

`users`, `masters`, `categories`, `services`, `orders`, `order_offers`, `reviews`,
`messages`, `photos`, `addresses`, `working_hours`, `notifications`, `payments`, `complaints`.

## Реализованные API-эндпоинты

- `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- `GET /categories`
- `GET /masters` (фильтры: category_id, city, verified, min_rating, lat/lon), `GET /masters/{id}` —
  доступны без авторизации, но телефон мастера (`user.phone`) виден только вошедшим пользователям
- `GET /masters/me`, `PATCH /masters/me` — просмотр и редактирование своего профиля (только для роли `master`)
- `POST /masters/me/services`, `DELETE /masters/me/services/{id}` — свои услуги и цены
- `POST /orders`, `GET /orders`, `GET /orders/{id}`
- `POST /orders/{id}/offer` — мастер предлагает цену
- `POST /orders/{id}/accept` — клиент выбирает предложение
- `POST /orders/{id}/cancel`
- `POST /orders/{id}/review`, `GET /masters/{id}/reviews`
- `GET /notifications`

## Что дальше (не входит в этот этап)

- Загрузка фото (сейчас поля `photo`/`photos` в моделях есть, эндпоинтов загрузки нет)
- Админ-панель (раздел 12 ТЗ)
- Мобильное приложение на Flutter (Этап 4 плана MVP)
- Оплата/комиссия (раздел 18 ТЗ) — таблица `payments` в БД уже заложена
