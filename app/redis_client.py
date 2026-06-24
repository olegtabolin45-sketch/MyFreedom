"""Ленивое подключение к Redis (refresh-токены и blacklist access-токенов)."""
import redis

from app import config

_client = None


def get_redis() -> redis.Redis:
    """Возвращает singleton-клиент Redis (decode_responses=True → строки, не bytes)."""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _client
