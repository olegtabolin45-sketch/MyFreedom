"""Подключения к базам данных."""

import pg8000.dbapi

from app import config


def get_db_connection():
    """PostgreSQL — пользователи, сессии, цели."""
    return pg8000.dbapi.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )


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
