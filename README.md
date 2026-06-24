# MyFreedom — Aeterna Analytics

Веб-приложение для учёта капитала и расчёта финансовой независимости.

## Что внутри

- `index.html` — лендинг с регистрацией и входом (Aeterna Analytics)
- `dashboard.html` — калькулятор финансового трека (сложный процент)
- `server.py` — бэкенд API на Python

## Запуск

Весь стек (приложение + PostgreSQL + Redis) поднимается через Docker Compose:

```bash
cp .env.example .env   # заполните JWT_SECRET
docker compose up -d --build
```

Приложение будет доступно на `http://127.0.0.1:8000` (фронтенд отдаётся тем же сервером).
Миграции Alembic применяются автоматически при старте контейнера.

## Тесты

Тесты прогоняются внутри контейнера приложения (есть доступ к PostgreSQL и Redis):

```bash
docker compose exec app sh -c "pip install -r requirements-dev.txt && python -m pytest"
```

Покрыты: заголовки безопасности, валидация входных данных, полный цикл
аутентификации (регистрация/вход/refresh с ротацией/logout с blacklist) и 2FA (TOTP).
