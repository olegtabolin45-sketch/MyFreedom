"""Котировки Московской биржи (MOEX ISS API).

Бесплатный публичный источник, без ключа. Кэшируем цены на 30 минут
(требование: цена обновляется раз в 30 минут во время работы биржи).
Используем стандартный urllib, чтобы не тянуть новые зависимости.

Покрываем рынок акций/фондов (shares: TQBR, TQTF) и облигаций (bonds: TQOB).
Для облигаций цена в отчёте — процент от номинала, поэтому пересчитываем в рубли
по FACEVALUE.
"""

import json
import time
import urllib.parse
import urllib.request

from app import config
from app.logging_config import logger

_CACHE_TTL = 30 * 60  # 30 минут
# ticker -> (price, prev_close, currency, fetched_at)
_cache: dict[str, tuple[float, float | None, str, float]] = {}

_SHARES_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/securities.json"
    "?iss.meta=off&marketdata.columns=SECID,LAST,MARKETPRICE,WAPRICE,LCLOSEPRICE"
)
_BONDS_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/bonds/securities.json"
    "?iss.meta=off&securities.columns=SECID,FACEVALUE"
    "&marketdata.columns=SECID,LAST,LCLOSEPRICE,MARKETPRICE"
)


def _http_json(url: str, timeout: int = 5, retries: int = 1) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Aeterna/1.0"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (домен MOEX)
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # SSL/таймауты MOEX бывают плавающими — повторяем
            last = e
            if attempt < retries - 1:
                time.sleep(0.6)
    raise last


def _first_price(row: list, idxs: list[int]) -> float | None:
    """Первая ненулевая цена из перечня колонок (LAST → MARKETPRICE → ...)."""
    for i in idxs:
        if i is not None and i < len(row) and row[i] is not None:
            try:
                return float(row[i])
            except (TypeError, ValueError):
                continue
    return None


def _fetch_shares(tickers: list[str]) -> dict[str, tuple[float, float | None, str]]:
    url = _SHARES_URL + "&securities=" + urllib.parse.quote(",".join(tickers))
    data = _http_json(url)
    md = data.get("marketdata", {})
    cols = md.get("columns", [])
    price_idxs = [
        cols.index(c) for c in ("LAST", "MARKETPRICE", "WAPRICE", "LCLOSEPRICE") if c in cols
    ]
    i_sec = cols.index("SECID")
    i_close = cols.index("LCLOSEPRICE") if "LCLOSEPRICE" in cols else None
    out: dict[str, tuple[float, float | None, str]] = {}
    for row in md.get("data", []):
        price = _first_price(row, price_idxs)
        if price is not None:
            prev = _first_price(row, [i_close]) if i_close is not None else None
            out.setdefault(row[i_sec], (price, prev, "RUB"))
    return out


def _fetch_bonds(tickers: list[str]) -> dict[str, tuple[float, float | None, str]]:
    url = _BONDS_URL + "&securities=" + urllib.parse.quote(",".join(tickers))
    data = _http_json(url)
    # Номинал из секции securities
    sec = data.get("securities", {})
    scols = sec.get("columns", [])
    si_sec, si_face = scols.index("SECID"), scols.index("FACEVALUE")
    face: dict[str, float] = {}
    for row in sec.get("data", []):
        try:
            face[row[si_sec]] = float(row[si_face])
        except (TypeError, ValueError):
            continue
    # Цена (% номинала) из marketdata
    md = data.get("marketdata", {})
    cols = md.get("columns", [])
    price_idxs = [cols.index(c) for c in ("LAST", "LCLOSEPRICE", "MARKETPRICE") if c in cols]
    i_sec = cols.index("SECID")
    i_close = cols.index("LCLOSEPRICE") if "LCLOSEPRICE" in cols else None
    out: dict[str, tuple[float, float | None, str]] = {}
    for row in md.get("data", []):
        secid = row[i_sec]
        pct = _first_price(row, price_idxs)
        if pct is not None and secid in face:
            prev_pct = _first_price(row, [i_close]) if i_close is not None else None
            prev = round(face[secid] * prev_pct / 100, 2) if prev_pct is not None else None
            out.setdefault(secid, (round(face[secid] * pct / 100, 2), prev, "RUB"))
    return out


_fx_cache: tuple[dict[str, float], float] | None = None
_CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"


def get_fx_rates() -> dict[str, float]:
    """Курсы валют к рублю (ЦБ РФ), кэш 30 минут. RUB=1. Ошибки не пробрасываются."""
    global _fx_cache
    if not config.QUOTES_ENABLED:
        return {"RUB": 1.0}
    now = time.time()
    if _fx_cache and now - _fx_cache[1] < _CACHE_TTL:
        return _fx_cache[0]
    rates = {"RUB": 1.0}
    try:
        data = _http_json(_CBR_URL)
        for code, info in data.get("Valute", {}).items():
            nominal = info.get("Nominal", 1) or 1
            value = info.get("Value")
            if value:
                rates[code] = value / nominal
    except Exception as e:
        logger.warning("Не удалось получить курсы валют ЦБ: %s", e)
    _fx_cache = (rates, now)
    return rates


def get_quotes(tickers: list[str]) -> dict[str, dict]:
    """Возвращает {ticker: {price, currency}} с кэшем. Сетевые ошибки не пробрасываются."""
    if not config.QUOTES_ENABLED or not tickers:
        return {}

    now = time.time()
    result: dict[str, dict] = {}
    missing = []
    for t in tickers:
        cached = _cache.get(t)
        if cached and now - cached[3] < _CACHE_TTL:
            result[t] = {"price": cached[0], "prev_close": cached[1], "currency": cached[2]}
        else:
            missing.append(t)

    if not missing:
        return result

    fetched: dict[str, tuple[float, float | None, str]] = {}
    try:
        fetched.update(_fetch_shares(missing))
        # Бумаги, не найденные среди акций/фондов, ищем на рынке облигаций
        still_missing = [t for t in missing if t not in fetched]
        if still_missing:
            fetched.update(_fetch_bonds(still_missing))
    except Exception as e:
        logger.warning("Не удалось получить котировки MOEX: %s", e)

    for t, (price, prev, currency) in fetched.items():
        _cache[t] = (price, prev, currency, now)
        result[t] = {"price": price, "prev_close": prev, "currency": currency}

    return result


# ===== Историческая динамика (для графика и бенчмарка) =====
_HIST_TTL = 12 * 3600
_hist_cache: dict[str, tuple[dict, float]] = {}  # key -> ({date: close}, fetched_at)


def _fetch_history(path: str, columns: str, frm: str, conv=None) -> dict:
    """Качает дневные закрытия из history-эндпоинта MOEX с пагинацией."""
    base = (
        f"https://iss.moex.com/iss/history/{path}.json"
        f"?iss.meta=off&from={frm}&history.columns={columns}"
    )
    out: dict[str, float] = {}
    start = 0
    for _ in range(60):  # предохранитель от бесконечного цикла
        data = _http_json(f"{base}&start={start}", timeout=10, retries=3)
        block = data.get("history", {})
        cols, rows = block.get("columns", []), block.get("data", [])
        if not rows:
            break
        i_date = cols.index("TRADEDATE")
        for row in rows:
            close = conv(row, cols) if conv else row[cols.index("CLOSE")]
            if row[i_date] and close:
                out[row[i_date]] = round(float(close), 4)
        start += len(rows)
        if len(rows) < 100:
            break
    return out


def _bond_close(row: list, cols: list) -> float | None:
    """Цена облигации в рублях = CLOSE (% номинала) × FACEVALUE / 100."""
    close = row[cols.index("CLOSE")]
    face = row[cols.index("FACEVALUE")] if "FACEVALUE" in cols else None
    if close is None:
        return None
    return float(close) * float(face) / 100 if face else float(close)


def history_closes(ticker: str, frm: str) -> dict:
    """История закрытий по бумаге (акции/фонды, иначе облигации). Кэш 12 ч."""
    if not config.QUOTES_ENABLED:
        return {}
    key = f"{ticker}:{frm}"
    cached = _hist_cache.get(key)
    if cached and time.time() - cached[1] < _HIST_TTL:
        return cached[0]
    out = {}
    try:
        out = _fetch_history(
            f"engines/stock/markets/shares/securities/{ticker}", "TRADEDATE,CLOSE", frm
        )
        if not out:
            out = _fetch_history(
                f"engines/stock/markets/bonds/securities/{ticker}",
                "TRADEDATE,CLOSE,FACEVALUE",
                frm,
                conv=_bond_close,
            )
    except Exception as e:
        logger.warning("MOEX история по %s: %s", ticker, e)
    _hist_cache[key] = (out, time.time())
    return out


def index_history(secid: str, frm: str) -> dict:
    """История значений индекса MOEX (например, IMOEX). Кэш 12 ч."""
    if not config.QUOTES_ENABLED:
        return {}
    key = f"idx:{secid}:{frm}"
    cached = _hist_cache.get(key)
    if cached and time.time() - cached[1] < _HIST_TTL:
        return cached[0]
    out = {}
    try:
        out = _fetch_history(
            f"engines/stock/markets/index/securities/{secid}", "TRADEDATE,CLOSE", frm
        )
    except Exception as e:
        logger.warning("MOEX история индекса %s: %s", secid, e)
    _hist_cache[key] = (out, time.time())
    return out
