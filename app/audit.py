"""Аудит-лог действий пользователя (запись в PostgreSQL).

Запись в журнал не должна ломать основной поток: любые ошибки логируются,
но не пробрасываются. IP и User-Agent извлекаются из запроса.
"""

from fastapi import Request

from app.db import get_db_connection
from app.logging_config import logger

# Допустимые действия (для согласованности значений в журнале)
REGISTER = "register"
LOGIN_SUCCESS = "login_success"
LOGIN_FAILED = "login_failed"
TOKEN_REFRESH = "token_refresh"
LOGOUT = "logout"
ONBOARDING = "onboarding"
GOAL_APPROVED = "goal_approved"
TWO_FA_ENABLED = "2fa_enabled"
TWO_FA_DISABLED = "2fa_disabled"
LOGIN_2FA_FAILED = "login_2fa_failed"


def _client_ip(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


def record_event(
    request: Request,
    action: str,
    email: str | None = None,
    detail: str | None = None,
) -> None:
    """Пишет событие в audit_log. Ошибки подавляются (best-effort)."""
    ip = _client_ip(request)
    user_agent = (request.headers.get("user-agent") if request else None) or None
    if user_agent:
        user_agent = user_agent[:512]

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_log (action, email, ip, user_agent, detail)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (action, email, ip, user_agent, detail),
        )
        conn.commit()
    except Exception as e:
        logger.warning("Не удалось записать событие аудита (%s): %s", action, e)
    finally:
        if conn is not None:
            conn.close()
