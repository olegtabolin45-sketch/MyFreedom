"""Эндпоинты регистрации и входа."""
import bcrypt
from fastapi import APIRouter, HTTPException, Request, status

from app.db import get_db_connection
from app.logging_config import logger
from app.rate_limit import check_rate_limit
from app.schemas import LoginRequest, RegisterRequest
from app.security import generate_jwt_token, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


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

        token = generate_jwt_token(data.username, normalized_email)
        return {
            "status": "success",
            "token": token,
            "username": data.username,
            "is_onboarded": False,
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка регистрации: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при регистрации.",
        )
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
            "SELECT username, password_hash, is_onboarded FROM users WHERE email = %s",
            (normalized_email,),
        )
        user = cursor.fetchone()

        if not user:
            fake_salt = b"$2b$12$L7RMD8clNRE1bepshLrrUu"
            bcrypt.hashpw(data.password.encode("utf-8"), fake_salt)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный адрес электронной почты или пароль.",
            )

        username, db_hashed_password, is_onboarded = user[0], user[1], user[2]

        if not verify_password(data.password, db_hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный адрес электронной почты или пароль.",
            )

        token = generate_jwt_token(username, normalized_email)
        return {
            "status": "success",
            "token": token,
            "username": username,
            "is_onboarded": bool(is_onboarded),
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка авторизации: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка авторизации.",
        )
    finally:
        if conn is not None:
            conn.close()
