"""Ограничение частоты запросов (в памяти, скользящее окно).

Примечание: лимитер хранит состояние в памяти процесса. Для нескольких
инстансов нужен общий бэкенд (Redis) — см. ROADMAP, этап 2.
"""
import time
import threading
from collections import defaultdict

from fastapi import HTTPException, Request, status

from app import config
from app.logging_config import logger

_rate_buckets = defaultdict(list)
_rate_lock = threading.Lock()


def check_rate_limit(request: Request, scope: str, max_requests: int, window_seconds: int):
    """Ограничивает число запросов с одного IP. Бросает 429 при превышении."""
    if not config.RATE_LIMIT_ENABLED:
        return
    client_ip = request.client.host if request.client else "unknown"
    key = f"{scope}:{client_ip}"
    now = time.time()
    cutoff = now - window_seconds
    with _rate_lock:
        timestamps = _rate_buckets[key]
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= max_requests:
            retry_after = int(window_seconds - (now - timestamps[0])) + 1
            logger.warning("Rate limit превышен: scope=%s ip=%s", scope, client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много попыток. Пожалуйста, попробуйте позже.",
                headers={"Retry-After": str(retry_after)},
            )
        timestamps.append(now)
