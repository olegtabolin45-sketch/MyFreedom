"""Общие фикстуры для тестов.

Тесты рассчитаны на запуск в окружении с доступом к PostgreSQL и Redis
(например, внутри контейнера aeterna-app, где сконфигурированы переменные
окружения и сетевой доступ к сервисам db/redis).
"""

import os
import uuid

import pytest

# JWT_SECRET обязателен при импорте app.config; задаём дефолт, если не задан.
os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")
# В тестах rate limiting мешает (все запросы с одного IP) — отключаем.
os.environ["RATE_LIMIT_ENABLED"] = "false"
# Не ходим в сеть за котировками MOEX во время тестов.
os.environ["QUOTES_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def unique_email():
    return f"test_{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def valid_password():
    return "Passw0rd!"


@pytest.fixture
def registered(client, unique_email, valid_password):
    """Регистрирует пользователя и возвращает его данные + токены."""
    resp = client.post(
        "/api/register",
        json={"username": "Тестовый", "email": unique_email, "password": valid_password},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "email": unique_email,
        "password": valid_password,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
    }
