"""Реконструкция динамики стоимости портфеля (app/history.py)."""

from app.history import build_series


def test_build_series_value_and_benchmark():
    trades = [
        {
            "date": "01.01.2025",
            "side": "Покупка",
            "ticker": "GAZP",
            "quantity": 10,
            "amount": 1000.0,
            "is_fx": False,
        },
    ]
    price_hist = {"GAZP": {"2025-01-01": 100.0, "2025-02-01": 120.0}}
    index_hist = {"2025-01-01": 3000.0, "2025-02-01": 3300.0}

    res = build_series(trades, price_hist, index_hist)
    assert res is not None
    s = res["series"]
    # Январь: 10×100 = 1000; вложено 1000; в индекс 1000/3000 ед.
    assert s[0]["value"] == 1000.0
    assert s[0]["invested"] == 1000.0
    assert abs(s[0]["benchmark"] - 1000.0) < 0.5
    # Февраль: 10×120 = 1200; бенчмарк 0.333×3300 ≈ 1100
    assert s[1]["value"] == 1200.0
    assert abs(s[1]["benchmark"] - 1100.0) < 1.0
    assert res["portfolio_return"] is not None


def test_build_series_empty_without_trades():
    assert build_series([], {}, {}) is None


def test_build_series_skips_fx():
    trades = [
        {
            "date": "01.01.2025",
            "side": "Покупка",
            "ticker": "USDRUB",
            "quantity": 1,
            "amount": 90.0,
            "is_fx": True,
        }
    ]
    assert build_series(trades, {}, {}) is None
