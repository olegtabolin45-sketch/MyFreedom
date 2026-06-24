"""Настройка логирования (без утечки чувствительных данных клиенту)."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("aeterna")
