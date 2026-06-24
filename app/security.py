"""Хэширование паролей и работа с токенами.

Схема аутентификации:
- access-токен — короткоживущий JWT (минуты) с уникальным `jti`; при logout
  его `jti` попадает в blacklist в Redis до момента истечения.
- refresh-токен — непредсказуемая случайная строка (opaque), хранится в Redis
  (`refresh:<token>` → email) с TTL в днях; отзывается простым удалением ключа.
"""
import secrets
import time
import uuid

import bcrypt
import jwt
from fastapi import HTTPException

from app import config
from app.logging_config import logger
from app.redis_client import get_redis

# Префиксы ключей в Redis
_REFRESH_PREFIX = "refresh:"
_BLACKLIST_PREFIX = "bl:"


# --- Пароли ---

def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except Exception:
        return False


# --- Access-токены (JWT) ---

def generate_access_token(username: str, email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": email,
        "name": username,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    """Проверяет access-токен и возвращает email (sub). Бросает 401 при проблеме."""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Срок действия токена истек.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Невалидный токен сессии.")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Невалидный тип токена.")

    jti = payload.get("jti")
    if jti and _is_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Токен отозван.")

    return payload["sub"]


# --- Refresh-токены (opaque, в Redis) ---

def generate_refresh_token(email: str) -> str:
    token = secrets.token_urlsafe(48)
    ttl = config.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    get_redis().setex(_REFRESH_PREFIX + token, ttl, email)
    return token


def consume_refresh_token(token: str) -> str:
    """Проверяет refresh-токен, отзывает его (ротация) и возвращает email.

    Бросает 401, если токен неизвестен/истёк. После успешного вызова токен
    больше не действителен — выдаётся новая пара (защита от повторного использования).
    """
    redis = get_redis()
    key = _REFRESH_PREFIX + token
    email = redis.get(key)
    if email is None:
        raise HTTPException(status_code=401, detail="Невалидный или истёкший refresh-токен.")
    redis.delete(key)
    return email


def revoke_refresh_token(token: str) -> None:
    """Удаляет refresh-токен (logout). Молча игнорирует отсутствие/ошибки Redis."""
    try:
        get_redis().delete(_REFRESH_PREFIX + token)
    except Exception as e:
        logger.warning("Не удалось отозвать refresh-токен: %s", e)


# --- Blacklist access-токенов ---

def blacklist_access_token(token: str) -> None:
    """Заносит jti access-токена в blacklist до момента его естественного истечения."""
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return
    ttl = int(exp) - int(time.time())
    if ttl > 0:
        try:
            get_redis().setex(_BLACKLIST_PREFIX + jti, ttl, "1")
        except Exception as e:
            logger.warning("Не удалось добавить токен в blacklist: %s", e)


def _is_blacklisted(jti: str) -> bool:
    try:
        return get_redis().exists(_BLACKLIST_PREFIX + jti) == 1
    except Exception as e:
        # Fail-open: если Redis недоступен, не блокируем валидные токены, но логируем
        logger.warning("Проверка blacklist недоступна (Redis): %s", e)
        return False
