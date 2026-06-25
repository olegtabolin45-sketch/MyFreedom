"""Клиент T-Bank Invest API (REST) — дивиденды и купоны для пассивного дохода.

Токен берётся из переменной окружения TINKOFF_TOKEN (в коде не хранится).
Используется read-only: поиск инструмента, дивиденды, купоны. Кэш 6 часов.
"""

import datetime
import json
import time
import urllib.request

from app import config
from app.logging_config import logger

_BASE = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService"
_CACHE_TTL = 6 * 3600
_uid_cache: dict[str, tuple] = {}  # ticker -> (uid, kind, ts)
_income_cache: dict[str, tuple[float, float]] = {}  # uid -> (annual_per_unit, ts)


def is_enabled() -> bool:
    return bool(config.TINKOFF_TOKEN)


def _post(method: str, payload: dict):
    if not config.TINKOFF_TOKEN:
        return None
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_BASE}/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {config.TINKOFF_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 (доверенный API)
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(1)
    return None


def _money(m: dict) -> float:
    if not m:
        return 0.0
    return float(m.get("units", 0) or 0) + float(m.get("nano", 0) or 0) / 1e9


def _resolve(ticker: str):
    """Тикер → (uid, kind). kind: 'share' | 'bond' | 'etf' | None. Кэш."""
    now = time.time()
    c = _uid_cache.get(ticker)
    if c and now - c[2] < _CACHE_TTL:
        return c[0], c[1]
    d = _post("FindInstrument", {"query": ticker})
    uid = kind = None
    if d:
        for ins in d.get("instruments", []):
            if ins.get("ticker") == ticker:
                uid = ins.get("uid")
                k = (ins.get("instrumentKind") or "").upper()
                kind = "bond" if "BOND" in k else ("etf" if "ETF" in k else "share")
                break
    _uid_cache[ticker] = (uid, kind, now)
    return uid, kind


def _annual_dividend(uid: str) -> float:
    """Сумма дивидендов на бумагу за последние 12 месяцев (годовой run-rate)."""
    today = datetime.date.today()
    frm = (today - datetime.timedelta(days=365)).isoformat() + "T00:00:00Z"
    to = today.isoformat() + "T23:59:59Z"
    d = _post("GetDividends", {"instrumentId": uid, "from": frm, "to": to})
    if not d:
        return 0.0
    return sum(_money(x.get("dividendNet") or x.get("dividend")) for x in d.get("dividends", []))


def _annual_coupon(uid: str) -> float:
    """Сумма купонов на облигацию за ближайшие 12 месяцев."""
    today = datetime.date.today()
    frm = today.isoformat() + "T00:00:00Z"
    to = (today + datetime.timedelta(days=365)).isoformat() + "T23:59:59Z"
    d = _post("GetBondCoupons", {"instrumentId": uid, "from": frm, "to": to})
    if not d:
        return 0.0
    return sum(_money(x.get("payOneBond")) for x in d.get("events", []))


def annual_income(positions: list[dict]) -> dict:
    """Прогноз годового пассивного дохода (₽) по позициям через T-Bank API."""
    total = 0.0
    by_ticker = {}
    now = time.time()
    for p in positions:
        ticker = p.get("ticker") or ""
        qty = p.get("quantity") or 0
        if not ticker or qty <= 0:
            continue
        try:
            uid, kind = _resolve(ticker)
            if not uid:
                continue
            cached = _income_cache.get(uid)
            if cached and now - cached[1] < _CACHE_TTL:
                per = cached[0]
            else:
                per = _annual_coupon(uid) if kind == "bond" else _annual_dividend(uid)
                _income_cache[uid] = (per, now)
        except Exception as e:
            logger.warning("T-Bank: не удалось получить выплаты по %s: %s", ticker, e)
            continue
        if per:
            amt = round(per * qty, 2)
            by_ticker[ticker] = amt
            total += amt
    return {"total": round(total, 2), "by_ticker": by_ticker}
