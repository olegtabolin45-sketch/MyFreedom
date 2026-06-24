"""Расчёт метрик портфеля: вложено, прибыль, среднегодовая доходность (XIRR).

Считаем по импортированным сделкам + текущей рыночной стоимости как конечному
денежному потоку. Если позиции были куплены до периода отчёта, картина неполная —
это честно отражается в подписи на дашборде. Точность растёт при загрузке всей
истории сделок (слияние нескольких отчётов — отдельный шаг).
"""

from datetime import date, datetime


def _parse_date(s: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _is_buy(side: str) -> bool:
    return "покуп" in (side or "").lower()


def _xnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    t0 = flows[0][0]
    return sum(a / (1.0 + rate) ** ((d - t0).days / 365.0) for d, a in flows)


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Среднегодовая доходность для нерегулярных потоков. Возвращает долю (0.1 = 10%)."""
    if len(flows) < 2:
        return None
    has_pos = any(a > 0 for _, a in flows)
    has_neg = any(a < 0 for _, a in flows)
    if not (has_pos and has_neg):
        return None

    flows = sorted(flows, key=lambda x: x[0])
    # Бисекция на широком диапазоне ставок — устойчивее метода Ньютона
    low, high = -0.9999, 10.0
    f_low = _xnpv(low, flows)
    f_high = _xnpv(high, flows)
    if f_low * f_high > 0:
        return None  # корень не локализуется в диапазоне
    for _ in range(200):
        mid = (low + high) / 2
        f_mid = _xnpv(mid, flows)
        if abs(f_mid) < 1e-6:
            return mid
        if f_low * f_mid < 0:
            high = mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def compute_metrics(trades: list[dict], total_value: float | None) -> dict:
    """Возвращает {invested, profit, profit_pct, xirr} по сделкам и текущей стоимости."""
    invested_cash = 0.0  # потрачено на покупки (с комиссией)
    returned_cash = 0.0  # получено с продаж (за вычетом комиссии)
    flows: list[tuple[date, float]] = []

    for t in trades:
        d = _parse_date(t.get("date") or "")
        amount = float(t.get("amount") or 0)
        commission = float(t.get("commission") or 0)
        if _is_buy(t.get("side", "")):
            invested_cash += amount + commission
            if d:
                flows.append((d, -(amount + commission)))
        else:
            returned_cash += amount - commission
            if d:
                flows.append((d, amount - commission))

    result = {"invested": None, "profit": None, "profit_pct": None, "xirr": None}

    if total_value is not None:
        net_invested = invested_cash - returned_cash
        result["invested"] = round(net_invested, 2)
        if net_invested > 0:
            profit = total_value - net_invested
            result["profit"] = round(profit, 2)
            result["profit_pct"] = round(profit / net_invested * 100, 2)
        # XIRR: потоки сделок + текущая стоимость как поступление сегодня
        if flows:
            terminal = list(flows) + [(date.today(), total_value)]
            rate = xirr(terminal)
            if rate is not None:
                result["xirr"] = round(rate * 100, 2)

    return result
