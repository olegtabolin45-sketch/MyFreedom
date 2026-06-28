"""Реконструкция динамики стоимости портфеля по сделкам + бенчмарк (IMOEX).

Точные ежедневные снапшоты мы не храним, поэтому строим месячный ряд:
по сделкам восстанавливаем количество бумаг на каждую контрольную дату и
умножаем на исторические цены закрытия MOEX. Бенчмарк — те же денежные
потоки, вложенные в индекс IMOEX (money-weighted сравнение).
"""

import datetime

from app.metrics import xirr


def _parse(d: str):
    p = (d or "").split(".")
    if len(p) == 3:
        try:
            return datetime.date(int(p[2]), int(p[1]), int(p[0]))
        except ValueError:
            return None
    return None


def _month_points(first: datetime.date, last: datetime.date) -> list[datetime.date]:
    """Контрольные даты: 1-е число каждого месяца + последний день (сегодня)."""
    pts = []
    y, m = first.year, first.month
    while (y < last.year) or (y == last.year and m <= last.month):
        pts.append(datetime.date(y, m, 1))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    if not pts or pts[-1] != last:
        pts.append(last)
    return pts


def _price_on(sorted_hist: list[tuple], d: datetime.date):
    """Цена закрытия на дату d или последнюю известную до неё."""
    best = None
    for dd, c in sorted_hist:
        if dd <= d:
            best = c
        else:
            break
    return best


def _trade_events(trades: list[dict]) -> list[tuple]:
    """Сделки → события (дата, тикер, Δколичество, Δвложено). FX и прочее отбрасываем."""
    evs = []
    for t in trades:
        if t.get("is_fx"):
            continue
        d = _parse(t.get("date") or "")
        if not d:
            continue
        side = (t.get("side") or "").lower()
        sign = 1 if "покуп" in side else (-1 if "прод" in side else 0)
        if not sign:
            continue
        qty = t.get("quantity") or 0
        amt = t.get("amount") or 0
        evs.append((d, t.get("ticker"), sign * qty, sign * amt))
    evs.sort(key=lambda e: e[0])
    return evs


def build_series(trades: list[dict], price_hist: dict, index_hist: dict) -> dict | None:
    """Строит ряд {date, value, invested, benchmark} + доходности портфеля и индекса.

    price_hist: {ticker: {date_iso: close}}; index_hist: {date_iso: close}.
    """
    evs = _trade_events(trades)
    if not evs:
        return None

    ph = {
        tk: sorted((datetime.date.fromisoformat(d), c) for d, c in m.items())
        for tk, m in price_hist.items()
    }
    ih = sorted((datetime.date.fromisoformat(d), c) for d, c in index_hist.items())

    today = datetime.date.today()
    points = _month_points(evs[0][0], today)

    holdings: dict[str, float] = {}
    invested = 0.0
    bench_units = 0.0
    cashflows: list[tuple] = []  # для XIRR: вложения отрицательны
    series = []
    ev_i = 0
    for pt in points:
        while ev_i < len(evs) and evs[ev_i][0] <= pt:
            d, tk, dq, damt = evs[ev_i]
            holdings[tk] = holdings.get(tk, 0) + dq
            invested += damt
            ip = _price_on(ih, d)
            if ip and ip > 0:
                # Продажа бумаг, купленных до периода отчётов, не должна уводить
                # бенчмарк в минус — ограничиваем снизу нулём.
                bench_units = max(bench_units + damt / ip, 0.0)
            cashflows.append((d, -damt))  # покупка = отток (−), продажа = приток (+)
            ev_i += 1
        value = 0.0
        for tk, q in holdings.items():
            if q <= 0:
                continue
            p = _price_on(ph.get(tk, []), pt)
            if p:
                value += q * p
        bench = bench_units * (_price_on(ih, pt) or 0)
        series.append(
            {
                "date": pt.isoformat(),
                "value": round(value, 2),
                "invested": round(invested, 2),
                "benchmark": round(bench, 2),
            }
        )

    final_value = series[-1]["value"]
    final_bench = series[-1]["benchmark"]
    # Money-weighted доходность (XIRR): денежные потоки + финальная стоимость
    port_mwr = xirr(cashflows + [(today, final_value)])
    bench_mwr = xirr(cashflows + [(today, final_bench)])
    return {
        "series": series,
        "final_value": round(final_value, 2),
        "final_benchmark": round(final_bench, 2),
        "invested": round(invested, 2),
        "portfolio_return": round(port_mwr * 100, 2) if port_mwr is not None else None,
        "benchmark_return": round(bench_mwr * 100, 2) if bench_mwr is not None else None,
    }
