"""Подключения к базам данных.

PostgreSQL обслуживается через пул соединений SQLAlchemy (драйвер pg8000).
`get_db_connection()` отдаёт «сырое» DBAPI-соединение из пула — API совместим
с pg8000 (`conn.cursor()`, `%s`-параметры, `commit()`), а `conn.close()` не
закрывает сокет, а возвращает соединение в пул. Это убирает открытие нового
TCP+auth на каждый запрос (узкое место: один общий factory на ~20 эндпоинтов).
"""

from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

from app import config

_engine = None


def _get_engine():
    """Лениво создаёт пул-движок (один на процесс)."""
    global _engine
    if _engine is None:
        url = (
            f"postgresql+pg8000://{config.DB_USER}:{config.DB_PASSWORD}"
            f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
        )
        _engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=config.DB_POOL_SIZE,
            max_overflow=config.DB_MAX_OVERFLOW,
            pool_pre_ping=True,  # проверяет живость соединения перед выдачей
            pool_recycle=1800,  # пересоздаёт соединения старше 30 мин
        )
    return _engine


def get_db_connection():
    """PostgreSQL — пользователи, сессии, цели. Соединение из пула (close() → возврат в пул)."""
    return _get_engine().raw_connection()


def get_trino_connection():
    """Trino — зарезервировано для аналитики капитала (см. ROADMAP, этап 4)."""
    from trino.dbapi import connect

    return connect(
        host=config.TRINO_HOST,
        port=config.TRINO_PORT,
        user=config.TRINO_USER,
        catalog=config.TRINO_CATALOG,
        schema=config.TRINO_SCHEMA,
    )
