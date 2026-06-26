"""Управление портфелями пользователя (список, создание, удаление).

Несколько портфелей на аккаунт (разные брокеры/счета). При двух и более
портфелях на фронте появляется виртуальный «Общий капитал» — сумма всех
(он не хранится в БД, считается при запросе с portfolio_id=all).
"""

from fastapi import APIRouter, HTTPException, Request

from app import audit
from app.db import get_db_connection
from app.logging_config import logger
from app.schemas import PortfolioCreate
from app.security import decode_access_token

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


@router.get("")
async def list_portfolios(token: str):
    """Список портфелей пользователя."""
    email = decode_access_token(token)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, description, currency, kind, broker_commission "
            "FROM portfolios WHERE email = %s ORDER BY id",
            (email,),
        )
        items = [
            {
                "id": r[0],
                "name": r[1],
                "description": r[2] or "",
                "currency": r[3],
                "kind": r[4],
                "broker_commission": r[5],
            }
            for r in cursor.fetchall()
        ]
        return {"portfolios": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка списка портфелей: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения портфелей.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("")
async def create_portfolio(data: PortfolioCreate, token: str, request: Request):
    """Создаёт новый портфель."""
    email = decode_access_token(token)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO portfolios (email, name, description, currency, kind, broker_commission) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (email, data.name, data.description, data.currency, data.kind, data.broker_commission),
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        audit.record_event(request, "portfolio_create", email=email, detail=f"id={new_id}")
        return {"id": new_id, "name": data.name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка создания портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка создания портфеля.") from e
    finally:
        if conn is not None:
            conn.close()


@router.delete("/{portfolio_id}")
async def delete_portfolio(portfolio_id: int, token: str, request: Request):
    """Удаляет портфель и все его данные (каскадом)."""
    email = decode_access_token(token)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM portfolios WHERE id = %s AND email = %s",
            (portfolio_id, email),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Портфель не найден.")
        conn.commit()
        audit.record_event(request, "portfolio_delete", email=email, detail=f"id={portfolio_id}")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка удаления портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка удаления портфеля.") from e
    finally:
        if conn is not None:
            conn.close()


def resolve_owned(cursor, email: str, portfolio_id: int) -> bool:
    """Проверяет, что портфель принадлежит пользователю."""
    cursor.execute(
        "SELECT 1 FROM portfolios WHERE id = %s AND email = %s",
        (portfolio_id, email),
    )
    return cursor.fetchone() is not None
