"""Управление двухфакторной аутентификацией (TOTP)."""

from fastapi import APIRouter, HTTPException, Request

from app import audit, two_factor
from app.db import get_db_connection
from app.logging_config import logger
from app.schemas import TwoFactorCodeRequest
from app.security import decode_access_token

router = APIRouter(prefix="/api/2fa", tags=["2fa"])


@router.post("/setup")
async def setup_2fa(token: str):
    """Генерирует секрет и QR-код. 2FA пока НЕ включается — нужен /enable с кодом."""
    email = decode_access_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT is_2fa_enabled FROM users WHERE email = %s", (email,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        if row[0]:
            raise HTTPException(status_code=400, detail="2FA уже включена.")

        secret = two_factor.generate_secret()
        # Сохраняем секрет, но включаем только после подтверждения кодом
        cursor.execute(
            "UPDATE users SET totp_secret = %s WHERE email = %s",
            (secret, email),
        )
        conn.commit()

        return {
            "secret": secret,
            "qr_code": two_factor.qr_png_base64(secret, email),
            "otpauth_uri": two_factor.provisioning_uri(secret, email),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка настройки 2FA: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка настройки 2FA.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/enable")
async def enable_2fa(data: TwoFactorCodeRequest, token: str, request: Request):
    """Подтверждает код из аутентификатора и включает 2FA."""
    email = decode_access_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT totp_secret, is_2fa_enabled FROM users WHERE email = %s",
            (email,),
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=400, detail="Сначала вызовите /api/2fa/setup.")
        if row[1]:
            raise HTTPException(status_code=400, detail="2FA уже включена.")

        if not two_factor.verify_code(row[0], data.code):
            raise HTTPException(status_code=400, detail="Неверный код подтверждения.")

        cursor.execute(
            "UPDATE users SET is_2fa_enabled = true WHERE email = %s",
            (email,),
        )
        conn.commit()
        audit.record_event(request, audit.TWO_FA_ENABLED, email=email)
        return {"status": "success", "message": "Двухфакторная аутентификация включена."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка включения 2FA: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка включения 2FA.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/disable")
async def disable_2fa(data: TwoFactorCodeRequest, token: str, request: Request):
    """Отключает 2FA после проверки текущего кода."""
    email = decode_access_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT totp_secret, is_2fa_enabled FROM users WHERE email = %s",
            (email,),
        )
        row = cursor.fetchone()
        if not row or not row[1]:
            raise HTTPException(status_code=400, detail="2FA не включена.")

        if not two_factor.verify_code(row[0], data.code):
            raise HTTPException(status_code=400, detail="Неверный код.")

        cursor.execute(
            "UPDATE users SET is_2fa_enabled = false, totp_secret = NULL WHERE email = %s",
            (email,),
        )
        conn.commit()
        audit.record_event(request, audit.TWO_FA_DISABLED, email=email)
        return {"status": "success", "message": "Двухфакторная аутентификация отключена."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка отключения 2FA: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка отключения 2FA.") from e
    finally:
        if conn is not None:
            conn.close()
