"""Клиент T-Bank Invest API (REST) — дивиденды и купоны для пассивного дохода.

Токен берётся из переменной окружения TINKOFF_TOKEN (в коде не хранится).
Используется read-only: поиск инструмента, дивиденды, купоны. Кэш 6 часов.
"""

import datetime
import json
import time
import urllib.error
import urllib.request

from app import config
from app.logging_config import logger

_BASE = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService"
_CACHE_TTL = 6 * 3600
_uid_cache: dict[str, tuple] = {}  # ticker -> (uid, kind, ts)
_income_cache: dict[str, tuple[float, float]] = {}  # uid -> (annual_per_unit, ts)
_sched_cache: dict[str, tuple[list, float]] = {}  # uid -> (events, ts)


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


# ===== Доступ по ЛИЧНОМУ токену пользователя (read-only API) =====
_HOST = "https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1"
_inst_cache: dict[str, dict] = {}  # uid -> {ticker, name, isin}

_ACCOUNT_TYPE_RU = {
    "ACCOUNT_TYPE_TINKOFF": "Брокерский счёт",
    "ACCOUNT_TYPE_TINKOFF_IIS": "ИИС",
    "ACCOUNT_TYPE_INVEST_BOX": "Инвесткопилка",
}


class TBankAuthError(Exception):
    """Невалидный/просроченный токен или нет прав."""


def _post_user(service: str, method: str, payload: dict, token: str) -> dict:
    """POST к произвольному сервису T-Bank API с пользовательским токеном."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_HOST}.{service}/{method}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (домен T-Bank)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise TBankAuthError() from e
            last = e
            time.sleep(0.8)
        except Exception as e:
            last = e
            time.sleep(0.8)
    raise last


def get_accounts(token: str) -> list[dict]:
    """Открытые брокерские счета пользователя."""
    d = _post_user("UsersService", "GetAccounts", {}, token)
    out = []
    for a in d.get("accounts", []):
        status = a.get("status", "")
        if status and status != "ACCOUNT_STATUS_OPEN":
            continue
        atype = a.get("type", "")
        name = a.get("name") or _ACCOUNT_TYPE_RU.get(atype, "Счёт Т-Банк")
        out.append({"id": a["id"], "name": name, "type": atype})
    return out


def get_account_positions(token: str, account_id: str) -> list[dict]:
    """Позиции по счёту (ценные бумаги, без валюты): uid, количество, средняя цена."""
    d = _post_user("OperationsService", "GetPortfolio", {"accountId": account_id}, token)
    out = []
    for p in d.get("positions", []):
        if p.get("instrumentType") == "currency":
            continue
        qty = _money(p.get("quantity"))
        if qty <= 0:
            continue
        out.append(
            {
                "uid": p.get("instrumentUid"),
                "figi": p.get("figi"),
                "quantity": qty,
                "avg": _money(p.get("averagePositionPrice")),
            }
        )
    return out


def instrument_by_uid(uid: str, token: str) -> dict:
    """uid инструмента → {ticker, name, isin}. Кэш в памяти."""
    if not uid:
        return {}
    if uid in _inst_cache:
        return _inst_cache[uid]
    info = {}
    try:
        d = _post_user(
            "InstrumentsService",
            "GetInstrumentBy",
            {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid},
            token,
        )
        ins = (d or {}).get("instrument", {}) or {}
        info = {
            "ticker": ins.get("ticker"),
            "name": ins.get("name"),
            "isin": ins.get("isin"),
        }
    except Exception as e:
        logger.warning("T-Bank: инструмент по uid %s: %s", uid, e)
    _inst_cache[uid] = info
    return info


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


def _dividend_events(uid: str) -> list[dict]:
    """Объявленные дивиденды на бумагу на 12 мес вперёд: [{date, per_unit}]."""
    today = datetime.date.today()
    frm = today.isoformat() + "T00:00:00Z"
    to = (today + datetime.timedelta(days=365)).isoformat() + "T23:59:59Z"
    d = _post("GetDividends", {"instrumentId": uid, "from": frm, "to": to})
    out = []
    if d:
        for x in d.get("dividends", []):
            date = (x.get("paymentDate") or x.get("lastBuyDate") or "")[:10]
            per = _money(x.get("dividendNet") or x.get("dividend"))
            if date and per:
                out.append({"date": date, "kind": "dividend", "per_unit": per})
    return out


def _coupon_events(uid: str) -> list[dict]:
    """Купоны на облигацию на 12 мес вперёд: [{date, per_unit}]."""
    today = datetime.date.today()
    frm = today.isoformat() + "T00:00:00Z"
    to = (today + datetime.timedelta(days=365)).isoformat() + "T23:59:59Z"
    d = _post("GetBondCoupons", {"instrumentId": uid, "from": frm, "to": to})
    out = []
    if d:
        for x in d.get("events", []):
            date = (x.get("couponDate") or "")[:10]
            per = _money(x.get("payOneBond"))
            if date and per:
                out.append({"date": date, "kind": "coupon", "per_unit": per})
    return out


def payment_schedule(positions: list[dict]) -> list[dict]:
    """Предстоящие выплаты (дивиденды/купоны) на 12 мес вперёд по позициям.

    Возвращает список событий [{date, ticker, name, kind, per_unit, quantity, amount}].
    """
    events = []
    now = time.time()
    for p in positions:
        ticker = p.get("ticker") or ""
        qty = p.get("quantity") or 0
        name = p.get("name") or ticker
        if not ticker or qty <= 0:
            continue
        try:
            uid, kind = _resolve(ticker)
            if not uid:
                continue
            cached = _sched_cache.get(uid)
            if cached and now - cached[1] < _CACHE_TTL:
                raw = cached[0]
            else:
                raw = _coupon_events(uid) if kind == "bond" else _dividend_events(uid)
                _sched_cache[uid] = (raw, now)
        except Exception as e:
            logger.warning("T-Bank: расписание выплат по %s: %s", ticker, e)
            continue
        for ev in raw:
            events.append(
                {
                    "date": ev["date"],
                    "ticker": ticker,
                    "name": name,
                    "kind": ev["kind"],
                    "per_unit": round(ev["per_unit"], 4),
                    "quantity": qty,
                    "amount": round(ev["per_unit"] * qty, 2),
                }
            )
    events.sort(key=lambda e: e["date"])
    return events


# Сектора T-Bank → русские категории
_SECTOR_RU = {
    "financial": "Финансы",
    "energy": "Энергетика",
    "materials": "Материалы",
    "utilities": "Коммунальные",
    "telecom": "Телеком",
    "it": "IT",
    "consumer": "Потребительский",
    "industrials": "Промышленность",
    "health_care": "Здравоохранение",
    "real_estate": "Недвижимость",
    "transport": "Транспорт",
    "green_energy": "Энергетика",
    "electrocars": "Транспорт",
    "ecomaterials": "Материалы",
    "metals": "Материалы",
    "other": "Прочее",
}
_cat_cache: dict[str, tuple[str, float]] = {}  # ticker -> (category, ts)


def _share_sector(uid: str) -> str | None:
    d = _post("ShareBy", {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})
    if not d:
        return None
    return (d.get("instrument", {}) or {}).get("sector")


def get_categories(positions: list[dict]) -> dict[str, str]:
    """Тикер → категория (сектор для акций; Облигации/Фонды для прочего)."""
    out = {}
    now = time.time()
    for p in positions:
        ticker = p.get("ticker") or ""
        if not ticker:
            continue
        c = _cat_cache.get(ticker)
        if c and now - c[1] < _CACHE_TTL:
            out[ticker] = c[0]
            continue
        cat = "Прочее"
        try:
            uid, kind = _resolve(ticker)
            if kind == "bond":
                cat = "Облигации"
            elif kind == "etf":
                cat = "Фонды"
            elif uid:
                sec = _share_sector(uid)
                cat = _SECTOR_RU.get((sec or "").lower(), "Акции")
        except Exception as e:
            logger.warning("T-Bank: сектор по %s: %s", ticker, e)
        _cat_cache[ticker] = (cat, now)
        out[ticker] = cat
    return out


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
