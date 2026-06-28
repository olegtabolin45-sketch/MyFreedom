"""Портфель: импорт брокерского отчёта и выдача позиций/сделок.

Данные привязаны к конкретному портфелю (portfolio_id). Запрос с
portfolio_id=all агрегирует все портфели пользователя («Общий капитал»).
"""

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app import audit, dividends, history, metrics, quotes, tbank
from app.broker_import import parse_broker_report
from app.db import get_db_connection
from app.logging_config import logger
from app.security import decode_access_token

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# Лимит размера загружаемого файла (5 МБ)
MAX_FILE_BYTES = 5 * 1024 * 1024

# Singleton для зависимости загрузки файлов (ruff B008: не вызывать File() в дефолтах)
_FILES = File(...)


def _scope(portfolio_id: str):
    """Возвращает (sql_filter, params_tail) для WHERE по портфелю или агрегату."""
    if portfolio_id and portfolio_id != "all":
        try:
            pid = int(portfolio_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Некорректный portfolio_id.") from e
        return " AND portfolio_id = %s", (pid,)
    # Агрегат «Общий капитал»: только данные, привязанные к портфелям
    # (исключаем осиротевшие строки portfolio_id IS NULL от старых импортов).
    return " AND portfolio_id IS NOT NULL", ()


@router.get("/calendar")
async def get_calendar(token: str, portfolio_id: str = "all"):
    """Календарь предстоящих выплат (дивиденды/купоны) на 12 мес вперёд."""
    email = decode_access_token(token)
    flt, tail = _scope(portfolio_id)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, ticker, isin, SUM(quantity) FROM portfolio_positions "
            "WHERE email = %s" + flt + " GROUP BY ticker, name, isin HAVING SUM(quantity) > 0",
            (email, *tail),
        )
        positions = [
            {"name": r[0], "ticker": r[1], "isin": r[2], "quantity": r[3]}
            for r in cursor.fetchall()
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка календаря выплат: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения календаря.") from e
    finally:
        if conn is not None:
            conn.close()

    events = dividends.payment_schedule(positions)

    # Обогащаем события: ISIN (для иконок) и доходность выплаты к стоимости позиции
    pos_by_ticker = {p["ticker"]: p for p in positions}
    qmap = quotes.get_quotes([p["ticker"] for p in positions])
    for ev in events:
        p = pos_by_ticker.get(ev["ticker"], {})
        ev["isin"] = p.get("isin")
        q = qmap.get(ev["ticker"])
        val = (q["price"] * ev["quantity"]) if (q and q.get("price") and ev["quantity"]) else 0
        ev["yield_pct"] = round(ev["amount"] / val * 100, 2) if val else None

    # Группировка по месяцам (YYYY-MM)
    by_month: dict[str, float] = {}
    for ev in events:
        m = ev["date"][:7]
        by_month[m] = round(by_month.get(m, 0.0) + ev["amount"], 2)
    months = [{"month": m, "total": t} for m, t in sorted(by_month.items())]
    total = round(sum(ev["amount"] for ev in events), 2)

    # Годовая доходность выплат к стоимости бумаг портфеля
    securities_value = sum(
        q["price"] * p["quantity"]
        for p in positions
        if (q := qmap.get(p["ticker"])) and q.get("price")
    )
    annual_yield = round(total / securities_value * 100, 2) if securities_value else None

    return {
        "events": events,
        "by_month": months,
        "total": total,
        "annual_yield": annual_yield,
        "currency": "RUB",
    }


@router.get("/history")
async def get_history(token: str, portfolio_id: str = "all"):
    """Динамика стоимости портфеля по месяцам + сравнение с индексом IMOEX."""
    email = decode_access_token(token)
    flt, tail = _scope(portfolio_id)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT trade_date, side, ticker, quantity, amount, is_fx "
            "FROM portfolio_trades WHERE email = %s" + flt + " ORDER BY id",
            (email, *tail),
        )
        trades = [
            {
                "date": r[0],
                "side": r[1],
                "ticker": r[2],
                "quantity": r[3],
                "amount": r[4],
                "is_fx": bool(r[5]),
            }
            for r in cursor.fetchall()
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка истории портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения истории.") from e
    finally:
        if conn is not None:
            conn.close()

    # Дата первой сделки → с какого момента тянуть котировки
    dates = [history._parse(t["date"]) for t in trades if not t["is_fx"]]
    dates = [d for d in dates if d]
    if not dates:
        return {"series": [], "currency": "RUB"}
    frm = min(dates).isoformat()

    tickers = {t["ticker"] for t in trades if t["ticker"] and not t["is_fx"]}
    # Параллельно тянем историю по тикерам и индексу (иначе на «Общем капитале» долго)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {tk: ex.submit(quotes.history_closes, tk, frm) for tk in tickers}
        idx_fut = ex.submit(quotes.index_history, "IMOEX", frm)
        price_hist = {tk: f.result() for tk, f in futs.items()}
        index_hist = idx_fut.result()

    result = history.build_series(trades, price_hist, index_hist)
    if result is None:
        return {"series": [], "currency": "RUB"}
    result["currency"] = "RUB"
    result["benchmark_name"] = "IMOEX"
    return result


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


def _norm_date(d: str) -> str:
    """DD.MM.YYYY или YYYY-MM-DD → сравнимый YYYY-MM-DD (или '')."""
    d = (d or "").strip()
    if len(d) >= 10 and d[4:5] == "-":
        return d[:10]
    parts = d.split(".")
    if len(parts) == 3 and len(parts[2]) >= 4:
        return f"{parts[2][:4]}-{parts[1]}-{parts[0]}"
    return ""


def _fmt_date(norm: str) -> str:
    """YYYY-MM-DD → DD.MM.YYYY для отображения."""
    if not norm:
        return ""
    y, m, d = norm.split("-")
    return f"{d}.{m}.{y}"


async def _read_files(files: list[UploadFile]) -> list[bytes]:
    """Читает и валидирует список .xlsx-файлов."""
    if not files:
        raise HTTPException(status_code=400, detail="Не выбрано ни одного файла.")
    contents = []
    for f in files:
        if not (f.filename or "").lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="Поддерживаются только файлы .xlsx.")
        content = await f.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой (максимум 5 МБ).")
        contents.append(content)
    return contents


def _parse_many(contents: list[bytes]) -> dict:
    """Парсит несколько отчётов и объединяет: сделки/потоки накапливаются,
    позиции и кэш берутся из самого свежего отчёта."""
    all_trades: list[dict] = []
    all_cashflows: list[dict] = []
    best = None  # самый свежий отчёт с позициями
    for content in contents:
        try:
            parsed = parse_broker_report(content)
        except Exception as e:
            logger.warning("Не удалось разобрать отчёт: %s", e)
            raise HTTPException(
                status_code=400,
                detail="Не удалось распознать отчёт. Убедитесь, что это xlsx-отчёт Т-Банка.",
            ) from e
        all_trades.extend(parsed["trades"])
        all_cashflows.extend(parsed.get("cashflows", []))
        rd = parsed.get("report_date", "")
        if parsed["positions"] and (best is None or rd >= best["report_date"]):
            best = {
                "report_date": rd,
                "positions": parsed["positions"],
                "cash": parsed.get("cash", {}),
            }
    return {
        "trades": all_trades,
        "cashflows": all_cashflows,
        "positions": best["positions"] if best else [],
        "cash": best["cash"] if best else {},
        "report_date": best["report_date"] if best else "",
    }


def _summarize(cursor, email: str, portfolio_id: int, parsed: dict) -> dict:
    """Считает, что нового добавится (без записи в БД), период и предупреждения."""
    cursor.execute(
        "SELECT trade_date, trade_time, ticker, side, quantity, amount "
        "FROM portfolio_trades WHERE email = %s AND portfolio_id = %s",
        (email, portfolio_id),
    )
    seen_t = {tuple(r) for r in cursor.fetchall()}
    db_tickers = {r[2] for r in seen_t}
    new_trades = 0
    parsed_tickers = set()
    for t in parsed["trades"]:
        if not t.get("is_fx"):
            parsed_tickers.add(t["ticker"])
        key = (t["date"], t["time"], t["ticker"], t["side"], t["quantity"], t["amount"])
        if key in seen_t:
            continue
        seen_t.add(key)
        new_trades += 1

    cursor.execute(
        "SELECT flow_date, kind, amount FROM portfolio_cashflows "
        "WHERE email = %s AND portfolio_id = %s",
        (email, portfolio_id),
    )
    seen_cf = {tuple(r) for r in cursor.fetchall()}
    new_div = new_comm = 0
    for cf in parsed["cashflows"]:
        key = (cf["date"], cf["kind"], cf["amount"])
        if key in seen_cf:
            continue
        seen_cf.add(key)
        if cf["kind"] == "dividend":
            new_div += 1
        elif cf["kind"] == "commission":
            new_comm += 1

    # Предупреждение: позиции без единой сделки (нужны отчёты за прошлые периоды)
    all_tickers = parsed_tickers | db_tickers
    orphans = [p["ticker"] for p in parsed["positions"] if p["ticker"] not in all_tickers]
    warnings = []
    if orphans:
        warnings.append(
            "По некоторым позициям не найдено ни одной покупки или продажи: "
            + ", ".join(orphans)
            + ". Возможно, нужно загрузить отчёты за предыдущие периоды."
        )

    dates = [_norm_date(t["date"]) for t in parsed["trades"]]
    dates += [_norm_date(cf["date"]) for cf in parsed["cashflows"]]
    dates = [d for d in dates if d]
    period = None
    if dates:
        period = {"from": _fmt_date(min(dates)), "to": _fmt_date(max(dates))}

    return {
        "assets": len(parsed["positions"]),
        "new_trades": new_trades,
        "new_dividends": new_div,
        "new_commissions": new_comm,
        "period": period,
        "warnings": warnings,
        "report_date": parsed["report_date"],
    }


@router.post("/import/preview")
async def import_preview(token: str, portfolio_id: int, files: list[UploadFile] = _FILES):
    """Разбирает отчёты и возвращает сводку (без записи) — для подтверждения."""
    email = decode_access_token(token)
    contents = await _read_files(files)
    parsed = _parse_many(contents)
    if not parsed["positions"] and not parsed["trades"]:
        raise HTTPException(status_code=400, detail="В отчётах не найдено позиций или сделок.")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM portfolios WHERE id = %s AND email = %s", (portfolio_id, email)
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Портфель не найден.")
        return _summarize(cursor, email, portfolio_id, parsed)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка предпросмотра импорта: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка обработки отчёта.") from e
    finally:
        if conn is not None:
            conn.close()


@router.post("/import")
async def import_report(
    token: str, portfolio_id: int, request: Request, files: list[UploadFile] = _FILES
):
    """Загрузка одного или нескольких отчётов Т-Банка (.xlsx) в портфель."""
    email = decode_access_token(token)
    contents = await _read_files(files)
    parsed = _parse_many(contents)
    positions = parsed["positions"]
    trades = parsed["trades"]
    cashflows = parsed["cashflows"]
    report_date = parsed["report_date"]
    if not positions and not trades:
        raise HTTPException(status_code=400, detail="В отчётах не найдено позиций или сделок.")

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

        # Сводка считается до записи (после неё «новых» уже не будет)
        summary = _summarize(cursor, email, portfolio_id, parsed)

        # --- Сделки: накапливаем в этом портфеле, дедуп ---
        cursor.execute(
            "SELECT trade_date, trade_time, ticker, side, quantity, amount "
            "FROM portfolio_trades WHERE email = %s AND portfolio_id = %s",
            (email, portfolio_id),
        )
        existing_trades = {tuple(r) for r in cursor.fetchall()}
        for t in trades:
            key = (t["date"], t["time"], t["ticker"], t["side"], t["quantity"], t["amount"])
            if key in existing_trades:
                continue
            existing_trades.add(key)
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
            for currency, amount in parsed["cash"].items():
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
            detail=f"pf={portfolio_id} files={len(contents)} new={summary['new_trades']}",
        )
        summary["status"] = "success"
        return summary
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка сохранения портфеля: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка сохранения портфеля.") from e
    finally:
        if conn is not None:
            conn.close()
