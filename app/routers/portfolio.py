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
            "quantity, amount, commission, is_fx FROM portfolio_trades "
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
                "is_fx": bool(r[10]),
            }
            for r in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT flow_date, kind, amount FROM portfolio_cashflows WHERE email = %s",
            (email,),
        )
        cashflows = [{"date": r[0], "kind": r[1], "amount": r[2]} for r in cursor.fetchall()]

        # Денежные остатки → рубли по курсу ЦБ
        cursor.execute(
            "SELECT currency, amount FROM portfolio_cash WHERE email = %s",
            (email,),
        )
        cash_rows = cursor.fetchall()
        cash = []
        cash_value = 0.0
        # Учитываем только рублёвые остатки: валютные остатки в отчёте — это часто
        # неисполненные/расчётные позиции (T+), их корректная оценка требует учёта
        # сеттлмента. Иначе стоимость портфеля завышается (#snowball-mismatch).
        for currency, amount in cash_rows:
            value_rub = round(amount, 2) if currency == "RUB" else None
            if value_rub:
                cash_value += value_rub
            cash.append({"currency": currency, "amount": amount, "value": value_rub})

        # Итоговая стоимость портфеля = бумаги + свободные средства
        tv = round(total_value, 2) if has_quotes else None
        portfolio_total = None
        if has_quotes or cash_value:
            portfolio_total = round(total_value + cash_value, 2)

        # Метрики по модели Snowball: вложено = пополнения−выводы, прибыль = стоимость−вложено
        m = metrics.compute_metrics(trades, portfolio_total, cashflows)
        return {
            "has_data": bool(positions or trades),
            "positions": positions,
            "trades": trades,
            "total_value": tv,
            "cash": cash,
            "cash_value": round(cash_value, 2) if cash else None,
            "portfolio_total": portfolio_total,
            "day_change": round(day_change, 2) if has_quotes else None,
            "value_currency": "RUB",
            "invested": m["invested"],
            "profit": m["profit"],
            "profit_pct": m["profit_pct"],
            "xirr": m["xirr"],
            "dividends": m["dividends"],
            "commissions": m["commissions"],
            "taxes": m["taxes"],
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
    cashflows = parsed.get("cashflows", [])
    report_date = parsed.get("report_date", "")
    if not positions and not trades:
        raise HTTPException(status_code=400, detail="В отчёте не найдено позиций или сделок.")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # --- Сделки: накапливаем из всех отчётов, дедуп по ключу ---
        cursor.execute(
            "SELECT trade_date, trade_time, ticker, side, quantity, amount "
            "FROM portfolio_trades WHERE email = %s",
            (email,),
        )
        existing_trades = {tuple(r) for r in cursor.fetchall()}
        new_trades = 0
        for t in trades:
            key = (t["date"], t["time"], t["ticker"], t["side"], t["quantity"], t["amount"])
            if key in existing_trades:
                continue
            existing_trades.add(key)
            new_trades += 1
            cursor.execute(
                "INSERT INTO portfolio_trades "
                "(email, trade_date, trade_time, side, ticker, name, price, currency, "
                "quantity, amount, commission, is_fx) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    bool(t.get("is_fx")),
                ),
            )

        # --- Денежные потоки (дивиденды/налоги): накапливаем, дедуп ---
        cursor.execute(
            "SELECT flow_date, kind, amount FROM portfolio_cashflows WHERE email = %s",
            (email,),
        )
        existing_cf = {tuple(r) for r in cursor.fetchall()}
        for cf in cashflows:
            key = (cf["date"], cf["kind"], cf["amount"])
            if key in existing_cf:
                continue
            existing_cf.add(key)
            cursor.execute(
                "INSERT INTO portfolio_cashflows (email, flow_date, kind, amount) "
                "VALUES (%s, %s, %s, %s)",
                (email, cf["date"], cf["kind"], cf["amount"]),
            )

        # --- Позиции: берём из самого свежего отчёта ---
        cursor.execute("SELECT positions_asof FROM portfolio_meta WHERE email = %s", (email,))
        meta = cursor.fetchone()
        prev_asof = meta[0] if meta else None
        if positions and (prev_asof is None or report_date >= prev_asof):
            cursor.execute("DELETE FROM portfolio_positions WHERE email = %s", (email,))
            for p in positions:
                cursor.execute(
                    "INSERT INTO portfolio_positions (email, ticker, name, isin, quantity) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (email, p["ticker"], p["name"], p["isin"], p["quantity"]),
                )
            # Денежные остатки тоже из самого свежего отчёта
            cursor.execute("DELETE FROM portfolio_cash WHERE email = %s", (email,))
            for currency, amount in parsed.get("cash", {}).items():
                cursor.execute(
                    "INSERT INTO portfolio_cash (email, currency, amount) VALUES (%s, %s, %s)",
                    (email, currency, amount),
                )
            if meta:
                cursor.execute(
                    "UPDATE portfolio_meta SET positions_asof = %s WHERE email = %s",
                    (report_date, email),
                )
            else:
                cursor.execute(
                    "INSERT INTO portfolio_meta (email, positions_asof) VALUES (%s, %s)",
                    (email, report_date),
                )

        conn.commit()
        audit.record_event(
            request,
            audit.PORTFOLIO_IMPORT,
            email=email,
            detail=f"new_trades={new_trades} report_date={report_date}",
        )
        return {
            "status": "success",
            "new_trades": new_trades,
            "report_date": report_date,
        }
    except Exception as e:
        logger.error("Ошибка сохранения портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка сохранения портфеля.") from e
    finally:
        if conn is not None:
            conn.close()
