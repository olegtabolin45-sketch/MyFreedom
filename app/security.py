"""Хэширование паролей и работа с JWT."""
import time

import jwt
import bcrypt
from fastapi import HTTPException

from app import config


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


def generate_jwt_token(username: str, email: str) -> str:
    payload = {
        "sub": email,
        "name": username,
        "exp": time.time() + (config.TOKEN_EXPIRE_MINUTES * 60),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> str:
    """Возвращает email из токена."""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Срок действия токена истек.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Невалидный токен сессии.")
