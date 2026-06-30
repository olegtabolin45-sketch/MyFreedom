"""Интеграции с брокерами по API (read-only). Сейчас — T-Bank Invest API.

Пользователь вводит личный read-only токен. Мы валидируем его, получаем список
брокерских счетов и на каждый создаём портфель с текущими позициями. Токен
хранится только в зашифрованном виде (app/crypto) и не логируется.
"""

from fastapi import APIRouter, HTTPException, Request

from app import audit, crypto, tbank
from app.db import get_db_connection
from app.logging_config import logger
from app.schemas import TbankConnect
from app.security import decode_access_token

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
_PROVIDER = "tbank"


def _sync_accounts(cursor, email: str, token: str) -> list[dict]:
    """Создаёт/обновляет портфели по счетам T-Bank и подтягивает позиции."""
    accounts = tbank.get_accounts(token)
    result = []
    for acc in accounts:
        acc_id = acc["id"]
        # Портфель для этого счёта (по источнику и id счёта)
        cursor.execute(
            "SELECT id FROM portfolios WHERE email = %s AND source = %s AND broker_account_id = %s",
            (email, _PROVIDER, acc_id),
        )
        row = cursor.fetchone()
        if row:
            pid = row[0]
            cursor.execute("UPDATE portfolios SET name = %s WHERE id = %s", (acc["name"], pid))
        else:
            cursor.execute(
                "INSERT INTO portfolios (email, name, currency, kind, source, broker_account_id) "
                "VALUES (%s, %s, 'RUB', 'broker', %s, %s) RETURNING id",
                (email, acc["name"], _PROVIDER, acc_id),
            )
            pid = cursor.fetchone()[0]

        # Полная замена данных портфеля из API (API — авторитетный источник)
        port = tbank.get_account_portfolio(token, acc_id)
        cursor.execute(
            "DELETE FROM portfolio_positions WHERE email = %s AND portfolio_id = %s", (email, pid)
        )
        saved = 0
        for p in port["positions"]:
            info = tbank.instrument_by_uid(p["uid"], token)
            ticker = info.get("ticker")
            if not ticker:
                continue
            cursor.execute(
                "INSERT INTO portfolio_positions "
                "(email, portfolio_id, ticker, name, isin, quantity, fallback_price) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    email,
                    pid,
                    ticker,
                    info.get("name") or ticker,
                    info.get("isin"),
                    p["quantity"],
                    p.get("price") or None,
                ),
            )
            saved += 1

        # Денежные остатки
        cursor.execute(
            "DELETE FROM portfolio_cash WHERE email = %s AND portfolio_id = %s", (email, pid)
        )
        for cur, amt in port["cash"].items():
            cursor.execute(
                "INSERT INTO portfolio_cash (email, portfolio_id, currency, amount) "
                "VALUES (%s, %s, %s, %s)",
                (email, pid, cur, amt),
            )

        # История операций → сделки и денежные потоки
        parsed = tbank.parse_operations(token, acc_id, "2018-01-01T00:00:00Z")
        cursor.execute(
            "DELETE FROM portfolio_trades WHERE email = %s AND portfolio_id = %s", (email, pid)
        )
        for t in parsed["trades"]:
            cursor.execute(
                "INSERT INTO portfolio_trades "
                "(email, portfolio_id, trade_date, trade_time, side, ticker, name, price, "
                "currency, quantity, amount, commission, is_fx) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    email,
                    pid,
                    t["date"],
                    t["time"],
                    t["side"],
                    t["ticker"],
                    t["name"],
                    t["price"],
                    t["currency"],
                    t["quantity"],
                    t["amount"],
                    t["commission"],
                    bool(t["is_fx"]),
                ),
            )
        cursor.execute(
            "DELETE FROM portfolio_cashflows WHERE email = %s AND portfolio_id = %s", (email, pid)
        )
        for cf in parsed["cashflows"]:
            cursor.execute(
                "INSERT INTO portfolio_cashflows (email, portfolio_id, flow_date, kind, amount) "
                "VALUES (%s, %s, %s, %s, %s)",
                (email, pid, cf["date"], cf["kind"], cf["amount"]),
            )

        result.append(
            {
                "account": acc["name"],
                "portfolio_id": pid,
                "positions": saved,
                "trades": len(parsed["trades"]),
            }
        )
    return result


@router.get("/status")
def status(token: str):
    """Подключён ли провайдер у пользователя."""
    email = decode_access_token(token)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT created_at FROM broker_integrations WHERE email = %s AND provider = %s",
            (email, _PROVIDER),
        )
        row = cursor.fetchone()
        return {"connected": bool(row)}
    finally:
        if conn is not None:
            conn.close()


@router.post("/tbank/connect")
def connect(data: TbankConnect, token: str, request: Request):
    """Подключение T-Bank по личному read-only токену."""
    email = decode_access_token(token)
    user_token = data.token.strip()

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            result = _sync_accounts(cursor, email, user_token)
        except tbank.TBankAuthError as e:
            raise HTTPException(
                status_code=400,
                detail="Токен недействителен или без прав. Нужен read-only токен T-Bank.",
            ) from e
        if not result:
            raise HTTPException(status_code=400, detail="У токена нет доступных брокерских счетов.")

        # Сохраняем токен (зашифрованным), upsert
        enc = crypto.encrypt(user_token)
        cursor.execute(
            "INSERT INTO broker_integrations (email, provider, token_enc) VALUES (%s, %s, %s) "
            "ON CONFLICT (email, provider) DO UPDATE SET token_enc = EXCLUDED.token_enc",
            (email, _PROVIDER, enc),
        )
        conn.commit()
        audit.record_event(request, "tbank_connect", email=email, detail=f"accounts={len(result)}")
        return {"status": "success", "accounts": result}
    except HTTPException:
        if conn is not None:
            conn.rollback()
        raise
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error("Ошибка подключения T-Bank: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось подключить T-Bank.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/tbank/sync")
def sync(token: str, request: Request):
    """Повторная синхронизация позиций по сохранённому токену."""
    email = decode_access_token(token)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT token_enc FROM broker_integrations WHERE email = %s AND provider = %s",
            (email, _PROVIDER),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="T-Bank не подключён.")
        user_token = crypto.decrypt(row[0])
        try:
            result = _sync_accounts(cursor, email, user_token)
        except tbank.TBankAuthError as e:
            raise HTTPException(
                status_code=400,
                detail="Сохранённый токен больше не действителен. Подключите заново.",
            ) from e
        conn.commit()
        audit.record_event(request, "tbank_sync", email=email, detail=f"accounts={len(result)}")
        return {"status": "success", "accounts": result}
    except HTTPException:
        if conn is not None:
            conn.rollback()
        raise
    except Exception as e:
        if conn is not None:
            conn.rollback()
        logger.error("Ошибка синхронизации T-Bank: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Не удалось синхронизировать.") from e
    finally:
        if conn is not None:
            conn.close()


@router.delete("/tbank")
def disconnect(token: str, request: Request):
    """Отключение интеграции (удаляет сохранённый токен; портфели остаются)."""
    email = decode_access_token(token)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM broker_integrations WHERE email = %s AND provider = %s",
            (email, _PROVIDER),
        )
        conn.commit()
        audit.record_event(request, "tbank_disconnect", email=email)
        return {"status": "success"}
    finally:
        if conn is not None:
            conn.close()
