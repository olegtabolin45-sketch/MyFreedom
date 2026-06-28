"""Оценка пассивного дохода (дивиденды акций/фондов + купоны облигаций) по MOEX.

Оценка за будущие 12 месяцев приблизительная: по акциям/фондам берём выплаты за
последние 12 мес (trailing), по облигациям — купоны на ближайший год вперёд.
Снежок прогнозирует объявленные будущие дивиденды по своей методике, поэтому
числа близки, но не идентичны. Кэш 6 часов (выплаты меняются редко).
"""

import datetime
import json
import time
import urllib.request

from app import config
from app.logging_config import logger

_CACHE_TTL = 6 * 3600
_cache: dict[str, tuple[float, float]] = {}  # ticker -> (annual_per_unit, fetched_at)


def _http_json(url: str):
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Aeterna/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(1)
    return None


def _annual_dividend(ticker: str) -> float | None:
    """Сумма дивидендов на акцию за последние 12 месяцев (trailing)."""
    data = _http_json(f"https://iss.moex.com/iss/securities/{ticker}/dividends.json?iss.meta=off")
    if not data or "dividends" not in data:
        return None
    cols = data["dividends"]["columns"]
    rows = data["dividends"]["data"]
    if "value" not in cols or "registryclosedate" not in cols:
        return None
    iv, idt = cols.index("value"), cols.index("registryclosedate")
    ago = str(datetime.date.today() - datetime.timedelta(days=365))
    return sum(r[iv] for r in rows if r[idt] and r[idt] >= ago and r[iv])


def _annual_coupon(ticker: str) -> float | None:
    """Сумма купонов на облигацию за ближайшие 12 месяцев."""
    data = _http_json(
        f"https://iss.moex.com/iss/securities/{ticker}/bondization.json?iss.meta=off&limit=100"
    )
    if not data or "coupons" not in data:
        return None
    cols = data["coupons"]["columns"]
    rows = data["coupons"]["data"]
    if "coupondate" not in cols or "value" not in cols:
        return None
    icd, icv = cols.index("coupondate"), cols.index("value")
    today = str(datetime.date.today())
    ahead = str(datetime.date.today() + datetime.timedelta(days=365))
    return sum(r[icv] for r in rows if r[icd] and today <= r[icd] <= ahead and r[icv])


def _annual_per_unit(ticker: str, is_bond: bool) -> float | None:
    now = time.time()
    cached = _cache.get(ticker)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    val = _annual_coupon(ticker) if is_bond else _annual_dividend(ticker)
    if val is None:
        return None
    _cache[ticker] = (val, now)
    return val


def _looks_like_bond(ticker: str, isin: str) -> bool:
    t = (ticker or "").upper()
    return t.startswith("SU") or "RMFS" in t or (isin or "").startswith("RU000A")


def _coupon_events_moex(ticker: str) -> list[dict]:
    """Будущие купоны по облигации с MOEX: [{date, per_unit}]."""
    data = _http_json(
        f"https://iss.moex.com/iss/securities/{ticker}/bondization.json?iss.meta=off&limit=100"
    )
    if not data or "coupons" not in data:
        return []
    cols = data["coupons"]["columns"]
    rows = data["coupons"]["data"]
    if "coupondate" not in cols or "value" not in cols:
        return []
    icd, icv = cols.index("coupondate"), cols.index("value")
    today = str(datetime.date.today())
    ahead = str(datetime.date.today() + datetime.timedelta(days=365))
    out = []
    for r in rows:
        if r[icd] and today <= r[icd] <= ahead and r[icv]:
            out.append({"date": r[icd], "kind": "coupon", "per_unit": r[icv]})
    return out


def payment_schedule(positions: list[dict]) -> list[dict]:
    """Календарь выплат на 12 мес вперёд: [{date, ticker, name, kind, per_unit, quantity, amount}].

    Приоритет — T-Bank (объявленные дивиденды + купоны с датами). Без токена —
    купоны облигаций с MOEX (будущие дивиденды акций MOEX бесплатно не отдаёт).
    """
    if not config.QUOTES_ENABLED:
        return []
    from app import tbank

    if tbank.is_enabled():
        events = tbank.payment_schedule(positions)
        if events:
            return events
    # Fallback: только купоны облигаций с MOEX
    events = []
    for p in positions:
        ticker = p.get("ticker") or ""
        qty = p.get("quantity") or 0
        if not ticker or qty <= 0 or not _looks_like_bond(ticker, p.get("isin") or ""):
            continue
        try:
            for ev in _coupon_events_moex(ticker):
                events.append(
                    {
                        "date": ev["date"],
                        "ticker": ticker,
                        "name": p.get("name") or ticker,
                        "kind": "coupon",
                        "per_unit": round(ev["per_unit"], 4),
                        "quantity": qty,
                        "amount": round(ev["per_unit"] * qty, 2),
                    }
                )
        except Exception as e:
            logger.warning("MOEX: купоны по %s: %s", ticker, e)
    events.sort(key=lambda e: e["date"])
    return events


def annual_income(positions: list[dict]) -> dict:
    """Прогноз годового пассивного дохода (в рублях) по позициям.

    positions: [{ticker, isin, quantity}]. Возвращает {total, by_ticker}.
    Сетевые ошибки не пробрасываются (вернётся то, что удалось получить).
    """
    if not config.QUOTES_ENABLED:
        return {"total": 0.0, "by_ticker": {}}
    # Приоритет — T-Bank Invest API (точные объявленные выплаты), иначе MOEX (с задержкой)
    from app import tbank

    if tbank.is_enabled():
        result = tbank.annual_income(positions)
        if result["total"] > 0:
            return result
    total = 0.0
    by_ticker = {}
    for p in positions:
        ticker = p.get("ticker") or ""
        qty = p.get("quantity") or 0
        if not ticker or qty <= 0:
            continue
        is_bond = _looks_like_bond(ticker, p.get("isin") or "")
        try:
            per = _annual_per_unit(ticker, is_bond)
        except Exception as e:
            logger.warning("Не удалось получить выплаты по %s: %s", ticker, e)
            per = None
        if per:
            amt = round(per * qty, 2)
            by_ticker[ticker] = amt
            total += amt
    return {"total": round(total, 2), "by_ticker": by_ticker}
