"""Портфель: импорт брокерского отчёта и выдача позиций/сделок.

Данные привязаны к конкретному портфелю (portfolio_id). Запрос с
portfolio_id=all агрегирует все портфели пользователя («Общий капитал»).
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile

from app import audit, dividends, metrics, quotes, tbank
from app.broker_import import parse_broker_report
from app.db import get_db_connection
from app.logging_config import logger
from app.security import decode_access_token

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# Лимит размера загружаемого файла (5 МБ)
MAX_FILE_BYTES = 5 * 1024 * 1024


def _scope(portfolio_id: str):
    """Возвращает (sql_filter, params_tail) для WHERE по портфелю или агрегату."""
    if portfolio_id and portfolio_id != "all":
        try:
            pid = int(portfolio_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Некорректный portfolio_id.") from e
        return " AND portfolio_id = %s", (pid,)
    return "", ()


@router.get("")
async def get_portfolio(token: str, portfolio_id: str = "all"):
    """Позиции/сделки портфеля (или агрегат всех при portfolio_id=all)."""
    email = decode_access_token(token)
    flt, tail = _scope(portfolio_id)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Позиции (агрегируем по тикеру — для «Общего капитала» одинаковые бумаги суммируются)
        cursor.execute(
            "SELECT name, ticker, isin, SUM(quantity) FROM portfolio_positions "
            "WHERE email = %s" + flt + " GROUP BY ticker, name, isin HAVING SUM(quantity) > 0 "
            "ORDER BY name",
            (email, *tail),
        )
        positions = [
            {"name": r[0], "ticker": r[1], "isin": r[2], "quantity": r[3]}
            for r in cursor.fetchall()
        ]

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
            "WHERE email = %s" + flt + " ORDER BY id",
            (email, *tail),
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

        # Средняя цена покупки по тикеру (для «вложено» и дохода позиции; приближённо)
        buy_amt: dict[str, float] = {}
        buy_qty: dict[str, float] = {}
        for t in trades:
            if t.get("is_fx") or "покуп" not in (t.get("side") or "").lower():
                continue
            tk = t.get("ticker") or ""
            buy_amt[tk] = buy_amt.get(tk, 0.0) + (t.get("amount") or 0)
            buy_qty[tk] = buy_qty.get(tk, 0.0) + (t.get("quantity") or 0)

        categories = {}
        try:
            categories = tbank.get_categories(positions) if tbank.is_enabled() else {}
        except Exception as e:
            logger.warning("Категории недоступны: %s", e)

        for p in positions:
            tk = p["ticker"]
            p["category"] = categories.get(tk, "Прочее")
            avg = (buy_amt[tk] / buy_qty[tk]) if buy_qty.get(tk) else None
            p["invested"] = round(avg * p["quantity"], 2) if avg else None
            if p.get("value") is not None and p["invested"] is not None:
                p["pos_profit"] = round(p["value"] - p["invested"], 2)
            else:
                p["pos_profit"] = None

        cursor.execute(
            "SELECT flow_date, kind, amount FROM portfolio_cashflows " "WHERE email = %s" + flt,
            (email, *tail),
        )
        cashflows = [{"date": r[0], "kind": r[1], "amount": r[2]} for r in cursor.fetchall()]

        cursor.execute(
            "SELECT currency, SUM(amount) FROM portfolio_cash "
            "WHERE email = %s" + flt + " GROUP BY currency",
            (email, *tail),
        )
        cash_rows = cursor.fetchall()
        cash = []
        cash_value = 0.0
        for currency, amount in cash_rows:
            value_rub = round(amount, 2) if currency == "RUB" else None
            if value_rub:
                cash_value += value_rub
            cash.append({"currency": currency, "amount": amount, "value": value_rub})

        tv = round(total_value, 2) if has_quotes else None
        portfolio_total = None
        if has_quotes or cash_value:
            portfolio_total = round(total_value + cash_value, 2)

        m = metrics.compute_metrics(trades, portfolio_total, cashflows)

        inc = dividends.annual_income(positions)
        passive_income = inc["total"]
        passive_yield = (
            round(passive_income / total_value * 100, 2)
            if has_quotes and total_value > 0 and passive_income
            else None
        )
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
            "passive_income": passive_income or None,
            "passive_yield": passive_yield,
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
async def import_report(token: str, portfolio_id: int, request: Request, file: UploadFile):
    """Загрузка брокерского отчёта Т-Банка (.xlsx) в конкретный портфель."""
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

        cursor.execute(
            "SELECT positions_asof FROM portfolios WHERE id = %s AND email = %s",
            (portfolio_id, email),
        )
        meta = cursor.fetchone()
        if meta is None:
            raise HTTPException(status_code=404, detail="Портфель не найден.")
        prev_asof = meta[0]

        # --- Сделки: накапливаем в этом портфеле, дедуп ---
        cursor.execute(
            "SELECT trade_date, trade_time, ticker, side, quantity, amount "
            "FROM portfolio_trades WHERE email = %s AND portfolio_id = %s",
            (email, portfolio_id),
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
                "(email, portfolio_id, trade_date, trade_time, side, ticker, name, price, "
                "currency, quantity, amount, commission, is_fx) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    email,
                    portfolio_id,
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

        # --- Денежные потоки: накапливаем, дедуп ---
        cursor.execute(
            "SELECT flow_date, kind, amount FROM portfolio_cashflows "
            "WHERE email = %s AND portfolio_id = %s",
            (email, portfolio_id),
        )
        existing_cf = {tuple(r) for r in cursor.fetchall()}
        for cf in cashflows:
            key = (cf["date"], cf["kind"], cf["amount"])
            if key in existing_cf:
                continue
            existing_cf.add(key)
            cursor.execute(
                "INSERT INTO portfolio_cashflows (email, portfolio_id, flow_date, kind, amount) "
                "VALUES (%s, %s, %s, %s, %s)",
                (email, portfolio_id, cf["date"], cf["kind"], cf["amount"]),
            )

        # --- Позиции и кэш: из самого свежего отчёта по этому портфелю ---
        if positions and (prev_asof is None or report_date >= prev_asof):
            cursor.execute(
                "DELETE FROM portfolio_positions WHERE email = %s AND portfolio_id = %s",
                (email, portfolio_id),
            )
            for p in positions:
                cursor.execute(
                    "INSERT INTO portfolio_positions "
                    "(email, portfolio_id, ticker, name, isin, quantity) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (email, portfolio_id, p["ticker"], p["name"], p["isin"], p["quantity"]),
                )
            cursor.execute(
                "DELETE FROM portfolio_cash WHERE email = %s AND portfolio_id = %s",
                (email, portfolio_id),
            )
            for currency, amount in parsed.get("cash", {}).items():
                cursor.execute(
                    "INSERT INTO portfolio_cash (email, portfolio_id, currency, amount) "
                    "VALUES (%s, %s, %s, %s)",
                    (email, portfolio_id, currency, amount),
                )
            cursor.execute(
                "UPDATE portfolios SET positions_asof = %s WHERE id = %s",
                (report_date, portfolio_id),
            )

        conn.commit()
        audit.record_event(
            request,
            audit.PORTFOLIO_IMPORT,
            email=email,
            detail=f"portfolio={portfolio_id} new_trades={new_trades}",
        )
        return {"status": "success", "new_trades": new_trades, "report_date": report_date}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка сохранения портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка сохранения портфеля.") from e
    finally:
        if conn is not None:
            conn.close()
