"""Расчёт XIRR и метрик портфеля."""

from datetime import date

from app.metrics import compute_metrics, xirr


def test_xirr_simple_annual_return():
    # Вложили 1000, через год стало 1100 -> ~10% годовых
    flows = [(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)]
    rate = xirr(flows)
    assert rate is not None
    assert abs(rate - 0.10) < 0.01


def test_xirr_requires_sign_change():
    assert xirr([(date(2025, 1, 1), -100.0), (date(2025, 6, 1), -50.0)]) is None


def test_compute_metrics_profit():
    trades = [
        {"date": "01.01.2025", "side": "Покупка", "amount": 1000.0, "commission": 0.0},
    ]
    m = compute_metrics(trades, total_value=1200.0)
    assert m["invested"] == 1000.0
    assert m["profit"] == 200.0
    assert m["profit_pct"] == 20.0
    assert m["xirr"] is not None


def test_compute_metrics_no_value():
    m = compute_metrics([], total_value=None)
    assert m["invested"] is None
    assert m["profit"] is None
    assert m["xirr"] is None
