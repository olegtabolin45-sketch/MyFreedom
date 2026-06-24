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
    # Сканируем диапазон ставок, чтобы найти интервал со сменой знака NPV
    grid = [-0.99 + i * 0.05 for i in range(int((10.0 + 0.99) / 0.05) + 1)]
    prev_r, prev_f = grid[0], _xnpv(grid[0], flows)
    low = high = None
    for r in grid[1:]:
        f = _xnpv(r, flows)
        if prev_f == 0:
            return prev_r
        if prev_f * f < 0:
            low, high, f_low = prev_r, r, prev_f
            break
        prev_r, prev_f = r, f
    if low is None:
        return None  # корень не найден в разумном диапазоне

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


def compute_metrics(
    trades: list[dict],
    total_value: float | None,
    cashflows: list[dict] | None = None,
) -> dict:
    """Метрики по сделкам бумаг, дивидендам/налогам и текущей стоимости.

    Валютные сделки (is_fx) исключаются — это конвертация валют, а не P&L бумаг.
    Прибыль = текущая стоимость − чистые вложения + дивиденды − налоги.
    """
    cashflows = cashflows or []
    invested_cash = 0.0  # потрачено на покупки (с комиссией)
    returned_cash = 0.0  # получено с продаж (за вычетом комиссии)
    first_date: date | None = None

    for t in trades:
        if t.get("is_fx"):
            continue  # конвертация валют — не учитываем
        amount = float(t.get("amount") or 0)
        commission = float(t.get("commission") or 0)
        d = _parse_date(t.get("date") or "")
        if d and (first_date is None or d < first_date):
            first_date = d
        if _is_buy(t.get("side", "")):
            invested_cash += amount + commission
        else:
            returned_cash += amount - commission

    dividends = sum(c["amount"] for c in cashflows if c.get("kind") == "dividend")
    taxes = sum(c["amount"] for c in cashflows if c.get("kind") == "tax")

    result = {
        "invested": None,
        "profit": None,
        "profit_pct": None,
        "xirr": None,
        "dividends": round(dividends, 2),
    }

    if total_value is not None:
        net_invested = invested_cash - returned_cash
        result["invested"] = round(net_invested, 2)
        profit = total_value - net_invested + dividends - taxes
        result["profit"] = round(profit, 2)
        if net_invested > 0:
            result["profit_pct"] = round(profit / net_invested * 100, 2)
            # Среднегодовая доходность (CAGR) на вложенный капитал за период владения
            if first_date is not None:
                years = (date.today() - first_date).days / 365.0
                terminal = total_value + dividends - taxes
                if years > 0.05 and terminal > 0:
                    cagr = (terminal / net_invested) ** (1 / years) - 1
                    result["xirr"] = round(cagr * 100, 2)

    return result
