"""Шифрование секретов пользователя (например, API-токенов брокера) для БД.

Используем Fernet (AES-128-CBC + HMAC) с ключом, производным от JWT_SECRET,
чтобы не вводить отдельную переменную окружения. Токены брокера хранятся
только в зашифрованном виде и нигде не логируются.
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app import config

# Ключ Fernet (32 байта, url-safe base64) из JWT_SECRET
_KEY = base64.urlsafe_b64encode(hashlib.sha256(config.JWT_SECRET.encode()).digest())
_fernet = Fernet(_KEY)


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
