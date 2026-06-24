"""Эндпоинты статуса пользователя и онбординга."""

from fastapi import APIRouter, HTTPException, Request

from app import audit
from app.db import get_db_connection
from app.logging_config import logger
from app.schemas import OnboardingRequest
from app.security import decode_access_token

router = APIRouter(prefix="/api", tags=["users"])


@router.get("/user/status")
async def get_user_status(token: str):
    email = decode_access_token(token)

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
            raise HTTPException(status_code=404, detail="Пользователь не найден.")

        return {"username": user[0], "is_onboarded": bool(user[1])}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка проверки статуса: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка проверки статуса.") from e
    finally:
        if conn is not None:
            conn.close()


@router.get("/goals")
async def get_goals(token: str):
    """Возвращает финансовые цели пользователя (данные онбординга) для дашборда."""
    email = decode_access_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT currency, initial_capital, monthly_deposit,
                   target_income, years_horizon, risk_profile
            FROM user_goals WHERE email = %s
            """,
            (email,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Цели ещё не заданы.")

        return {
            "currency": row[0],
            "initial_capital": row[1],
            "monthly_deposit": row[2],
            "target_income": row[3],
            "years_horizon": row[4],
            "risk_profile": row[5],
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка получения целей: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения целей.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/onboarding")
async def save_onboarding(data: OnboardingRequest, token: str, request: Request):
    email = decode_access_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM user_goals WHERE email = %s", (email,))

        cursor.execute(
            """
            INSERT INTO user_goals
            (email, currency, initial_capital, monthly_deposit,
             target_income, years_horizon, risk_profile)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                email,
                data.currency,
                data.initial_capital,
                data.monthly_deposit,
                data.target_income,
                data.years_horizon,
                data.risk_profile,
            ),
        )

        cursor.execute("UPDATE users SET is_onboarded = true WHERE email = %s", (email,))

        conn.commit()
        audit.record_event(request, audit.ONBOARDING, email=email)
        return {"status": "success", "message": "Данные онбординга успешно сохранены."}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка сохранения онбординга: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Ошибка записи данных конфигурации целей.",
        ) from e
    finally:
        if conn is not None:
            conn.close()
