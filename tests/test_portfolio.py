"""Импорт брокерского отчёта: парсер и эндпоинты."""

from io import BytesIO

import openpyxl

from app.broker_import import parse_broker_report


def _build_sample_report() -> bytes:
    """Минимальный xlsx, повторяющий структуру отчёта Т-Банка (секции 1.1 и 3.1)."""
    wb = openpyxl.Workbook()
    ws = wb.active

    # --- Секция 1.1: совершённые сделки ---
    ws["A1"] = "1.1 Информация о совершенных сделках"
    headers = [
        "Дата заключения",
        "Время",
        "Вид сделки",
        "Наименование актива",
        "Код актива",
        "Цена за единицу",
        "Валюта цены",
        "Количество",
        "Сумма сделки",
        "Валюта расчетов",
        "Комиссия брокера",
    ]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=2, column=i, value=h)
    trade = [
        "27.11.2024",
        "12:24",
        "Покупка",
        "ГАЗПРОМ ао",
        "GAZP",
        114.57,
        "RUB",
        10,
        1145.7,
        "RUB",
        3.44,
    ]
    for i, v in enumerate(trade, start=1):
        ws.cell(row=3, column=i, value=v)

    # --- Секция 3.1: движение по ценным бумагам ---
    ws["A4"] = "3.1 Движение по ценным бумагам"
    pos_headers = ["Наименование актива", "Код актива", "ISIN", "Исходящий остаток"]
    for i, h in enumerate(pos_headers, start=1):
        ws.cell(row=5, column=i, value=h)
    ws.cell(row=6, column=1, value="ГАЗПРОМ ао")
    ws.cell(row=6, column=2, value="GAZP")
    ws.cell(row=6, column=3, value="RU0007661625")
    ws.cell(row=6, column=4, value=10)
    # закрытая позиция (остаток 0) — должна отфильтроваться
    ws.cell(row=7, column=1, value="Закрытая бумага")
    ws.cell(row=7, column=2, value="CLSD")
    ws.cell(row=7, column=3, value="RU000CLOSED0")
    ws.cell(row=7, column=4, value=0)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parser_extracts_positions_and_trades():
    res = parse_broker_report(_build_sample_report())
    assert len(res["positions"]) == 1  # закрытая позиция отфильтрована
    pos = res["positions"][0]
    assert pos["ticker"] == "GAZP"
    assert pos["quantity"] == 10
    assert pos["isin"] == "RU0007661625"

    assert len(res["trades"]) == 1
    t = res["trades"][0]
    assert t["side"] == "Покупка"
    assert t["ticker"] == "GAZP"
    assert t["price"] == 114.57
    assert t["quantity"] == 10


def test_import_and_get_portfolio(client, registered):
    token = registered["access_token"]
    report = _build_sample_report()

    resp = client.post(
        "/api/portfolio/import",
        params={"token": token},
        files={
            "file": (
                "report.xlsx",
                report,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["positions_count"] == 1
    assert body["trades_count"] == 1

    got = client.get("/api/portfolio", params={"token": token})
    assert got.status_code == 200
    data = got.json()
    assert data["has_data"] is True
    assert data["positions"][0]["ticker"] == "GAZP"
    assert data["trades"][0]["side"] == "Покупка"


def test_import_rejects_non_xlsx(client, registered):
    resp = client.post(
        "/api/portfolio/import",
        params={"token": registered["access_token"]},
        files={"file": ("report.txt", b"not a spreadsheet", "text/plain")},
    )
    assert resp.status_code == 400


def test_portfolio_requires_valid_token(client):
    resp = client.get("/api/portfolio", params={"token": "garbage.token"})
    assert resp.status_code == 401
