"""Эндпоинты регистрации и входа."""

import bcrypt
from fastapi import APIRouter, HTTPException, Request, status

from app import audit, two_factor
from app.db import get_db_connection
from app.logging_config import logger
from app.rate_limit import check_rate_limit
from app.schemas import LoginRequest, LogoutRequest, RefreshRequest, RegisterRequest
from app.security import (
    blacklist_access_token,
    consume_refresh_token,
    generate_access_token,
    generate_refresh_token,
    hash_password,
    revoke_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/api", tags=["auth"])


def _token_payload(username: str, email: str, is_onboarded: bool) -> dict:
    """Единый формат ответа с парой токенов.

    Поле `token` дублирует access-токен ради обратной совместимости со старым
    фронтендом; новый код должен использовать `access_token` / `refresh_token`.
    """
    access = generate_access_token(username, email)
    refresh = generate_refresh_token(email)
    return {
        "status": "success",
        "token": access,
        "access_token": access,
        "refresh_token": refresh,
        "username": username,
        "is_onboarded": is_onboarded,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_user(data: RegisterRequest, request: Request):
    check_rate_limit(request, "register", max_requests=5, window_seconds=60)
    normalized_email = data.email.lower()

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT email FROM users WHERE email = %s",
            (normalized_email,),
        )
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Аккаунт с таким Email уже существует.",
            )

        hashed_password = hash_password(data.password)

        cursor.execute(
            """
            INSERT INTO users (username, email, password_hash, is_onboarded)
            VALUES (%s, %s, %s, false)
            """,
            (data.username, normalized_email, hashed_password),
        )
        conn.commit()

        audit.record_event(request, audit.REGISTER, email=normalized_email)
        return _token_payload(data.username, normalized_email, is_onboarded=False)

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка регистрации: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при регистрации.",
        ) from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/login")
async def login_user(data: LoginRequest, request: Request):
    check_rate_limit(request, "login", max_requests=10, window_seconds=60)
    normalized_email = data.email.lower()

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT username, password_hash, is_onboarded, is_2fa_enabled, totp_secret "
            "FROM users WHERE email = %s",
            (normalized_email,),
        )
        user = cursor.fetchone()

        if not user:
            fake_salt = b"$2b$12$L7RMD8clNRE1bepshLrrUu"
            bcrypt.hashpw(data.password.encode("utf-8"), fake_salt)
            audit.record_event(request, audit.LOGIN_FAILED, email=normalized_email)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный адрес электронной почты или пароль.",
            )

        username, db_hashed_password, is_onboarded = user[0], user[1], user[2]
        is_2fa_enabled, totp_secret = user[3], user[4]

        if not verify_password(data.password, db_hashed_password):
            audit.record_event(request, audit.LOGIN_FAILED, email=normalized_email)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный адрес электронной почты или пароль.",
            )

        # Второй фактор: пароль верен, но нужен корректный TOTP-код
        if is_2fa_enabled:
            if not data.totp_code:
                # Пароль принят — фронт должен запросить код 2FA
                return {"status": "2fa_required", "requires_2fa": True}
            if not two_factor.verify_code(totp_secret, data.totp_code):
                audit.record_event(request, audit.LOGIN_2FA_FAILED, email=normalized_email)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Неверный код двухфакторной аутентификации.",
                )

        audit.record_event(request, audit.LOGIN_SUCCESS, email=normalized_email)
        return _token_payload(username, normalized_email, is_onboarded=bool(is_onboarded))

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка авторизации: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка авторизации.",
        ) from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/refresh")
async def refresh_tokens(data: RefreshRequest, request: Request):
    """Обменивает refresh-токен на новую пару токенов (с ротацией refresh)."""
    check_rate_limit(request, "refresh", max_requests=30, window_seconds=60)
    email = consume_refresh_token(data.refresh_token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, is_onboarded FROM users WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Пользователь не найден.",
            )
        audit.record_event(request, audit.TOKEN_REFRESH, email=email)
        return _token_payload(user[0], email, is_onboarded=bool(user[1]))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка обновления токена: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка обновления токена.",
        ) from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/logout")
async def logout(data: LogoutRequest, request: Request):
    """Отзывает refresh-токен и (если передан) заносит access-токен в blacklist."""
    revoke_refresh_token(data.refresh_token)
    if data.access_token:
        blacklist_access_token(data.access_token)
    audit.record_event(request, audit.LOGOUT)
    return {"status": "success", "message": "Сессия завершена."}
