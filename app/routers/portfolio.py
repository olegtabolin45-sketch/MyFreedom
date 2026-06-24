"""Портфель: импорт брокерского отчёта и выдача позиций/сделок."""

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app import audit, metrics, quotes
from app.broker_import import parse_broker_report
from app.db import get_db_connection
from app.logging_config import logger
from app.security import decode_access_token

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# Лимит размера загружаемого файла (5 МБ)
MAX_FILE_BYTES = 5 * 1024 * 1024


@router.get("")
async def get_portfolio(token: str):
    """Текущие позиции и сделки пользователя."""
    email = decode_access_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, ticker, isin, quantity FROM portfolio_positions "
            "WHERE email = %s ORDER BY name",
            (email,),
        )
        positions = [
            {"name": r[0], "ticker": r[1], "isin": r[2], "quantity": r[3]}
            for r in cursor.fetchall()
        ]

        # Подмешиваем живые котировки MOEX, считаем стоимость и изменение за день
        quote_map = quotes.get_quotes([p["ticker"] for p in positions])
        total_value = 0.0
        day_change = 0.0
        has_quotes = False
        for p in positions:
            q = quote_map.get(p["ticker"])
            if q:
                has_quotes = True
                p["price"] = q["price"]
                p["currency"] = q["currency"]
                p["value"] = round(q["price"] * p["quantity"], 2)
                total_value += p["value"]
                prev = q.get("prev_close")
                if prev:
                    day_change += (q["price"] - prev) * p["quantity"]
            else:
                p["price"] = None
                p["currency"] = None
                p["value"] = None

        cursor.execute(
            "SELECT trade_date, trade_time, side, name, ticker, price, currency, "
            "quantity, amount, commission FROM portfolio_trades "
            "WHERE email = %s ORDER BY id",
            (email,),
        )
        trades = [
            {
                "date": r[0],
                "time": r[1],
                "side": r[2],
                "name": r[3],
                "ticker": r[4],
                "price": r[5],
                "currency": r[6],
                "quantity": r[7],
                "amount": r[8],
                "commission": r[9],
            }
            for r in cursor.fetchall()
        ]
        tv = round(total_value, 2) if has_quotes else None
        m = metrics.compute_metrics(trades, tv)
        return {
            "has_data": bool(positions or trades),
            "positions": positions,
            "trades": trades,
            "total_value": tv,
            "day_change": round(day_change, 2) if has_quotes else None,
            "value_currency": "RUB",
            "invested": m["invested"],
            "profit": m["profit"],
            "profit_pct": m["profit_pct"],
            "xirr": m["xirr"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка получения портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения портфеля.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/import")
async def import_report(token: str, request: Request, file: UploadFile):
    """Загрузка брокерского отчёта Т-Банка (.xlsx): парсинг и сохранение."""
    email = decode_access_token(token)

    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Поддерживаются только файлы .xlsx.")

    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой (максимум 5 МБ).")

    try:
        parsed = parse_broker_report(content)
    except Exception as e:
        logger.warning("Не удалось разобрать отчёт: %s", e)
        raise HTTPException(
            status_code=400,
            detail="Не удалось распознать отчёт. Убедитесь, что это xlsx-отчёт Т-Банка.",
        ) from e

    positions = parsed["positions"]
    trades = parsed["trades"]
    if not positions and not trades:
        raise HTTPException(status_code=400, detail="В отчёте не найдено позиций или сделок.")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Текущий отчёт замещает предыдущие данные пользователя
        cursor.execute("DELETE FROM portfolio_positions WHERE email = %s", (email,))
        cursor.execute("DELETE FROM portfolio_trades WHERE email = %s", (email,))

        for p in positions:
            cursor.execute(
                "INSERT INTO portfolio_positions (email, ticker, name, isin, quantity) "
                "VALUES (%s, %s, %s, %s, %s)",
                (email, p["ticker"], p["name"], p["isin"], p["quantity"]),
            )
        for t in trades:
            cursor.execute(
                "INSERT INTO portfolio_trades "
                "(email, trade_date, trade_time, side, ticker, name, price, currency, "
                "quantity, amount, commission) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    email,
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
                ),
            )
        conn.commit()
        audit.record_event(
            request,
            audit.PORTFOLIO_IMPORT,
            email=email,
            detail=f"positions={len(positions)} trades={len(trades)}",
        )
        return {
            "status": "success",
            "positions_count": len(positions),
            "trades_count": len(trades),
            "positions": positions,
            "trades": trades,
        }
    except Exception as e:
        logger.error("Ошибка сохранения портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка сохранения портфеля.") from e
    finally:
        if conn is not None:
            conn.close()
