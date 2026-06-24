"""Котировки Московской биржи (MOEX ISS API).

Бесплатный публичный источник, без ключа. Кэшируем цены на 30 минут
(требование: цена обновляется раз в 30 минут во время работы биржи).
Используем стандартный urllib, чтобы не тянуть новые зависимости.
"""

import json
import time
import urllib.parse
import urllib.request

from app import config
from app.logging_config import logger

_CACHE_TTL = 30 * 60  # 30 минут
_cache: dict[str, tuple[float, str, float]] = {}  # ticker -> (price, currency, fetched_at)

_MOEX_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/securities.json"
    "?iss.meta=off&securities.columns=SECID&marketdata.columns=SECID,LAST,CURRENCYID"
)


def _fetch_from_moex(tickers: list[str]) -> dict[str, tuple[float, str]]:
    """Запрашивает последние цены с MOEX. Возвращает {ticker: (price, currency)}."""
    url = _MOEX_URL + "&securities=" + urllib.parse.quote(",".join(tickers))
    req = urllib.request.Request(url, headers={"User-Agent": "Aeterna/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (доверенный домен MOEX)
        data = json.loads(resp.read().decode("utf-8"))

    md = data.get("marketdata", {})
    cols = md.get("columns", [])
    rows = md.get("data", [])
    i_sec = cols.index("SECID")
    i_last = cols.index("LAST")
    i_cur = cols.index("CURRENCYID") if "CURRENCYID" in cols else None

    out: dict[str, tuple[float, str]] = {}
    for row in rows:
        secid = row[i_sec]
        last = row[i_last]
        if last is None:
            continue
        currency = "RUB"
        if i_cur is not None and row[i_cur]:
            # MOEX отдаёт SUR для рублёвых инструментов — нормализуем
            currency = "RUB" if row[i_cur] in ("SUR", "RUB") else row[i_cur]
        # Берём первую доступную цену по тикеру (первичный борд идёт раньше)
        out.setdefault(secid, (float(last), currency))
    return out


def get_quotes(tickers: list[str]) -> dict[str, dict]:
    """Возвращает {ticker: {price, currency}} с кэшем. Сетевые ошибки не пробрасываются."""
    if not config.QUOTES_ENABLED or not tickers:
        return {}

    now = time.time()
    result: dict[str, dict] = {}
    missing = []
    for t in tickers:
        cached = _cache.get(t)
        if cached and now - cached[2] < _CACHE_TTL:
            result[t] = {"price": cached[0], "currency": cached[1]}
        else:
            missing.append(t)

    if missing:
        try:
            fetched = _fetch_from_moex(missing)
            for t, (price, currency) in fetched.items():
                _cache[t] = (price, currency, now)
                result[t] = {"price": price, "currency": currency}
        except Exception as e:
            logger.warning("Не удалось получить котировки MOEX: %s", e)

    return result
