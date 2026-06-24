"""Парсер брокерского отчёта Т-Банка (xlsx).

Отчёт — единый широкий лист с логическими секциями. Каждая «колонка»
занимает несколько ячеек (объединённые), поэтому ориентируемся на тексты
заголовков: находим секцию по названию в колонке A, читаем строку заголовков,
строим карту «индекс колонки → поле» и затем читаем строки данных.

Извлекаем:
- positions — текущие позиции (секция 3.1, «Исходящий остаток»)
- trades    — совершённые сделки (секции 1.1 и 1.3)
"""

import re
from io import BytesIO

import openpyxl

# Заголовок секции: «1.1 Текст», «2. Текст», «3.1 Текст» — номер, пробел, затем не-цифра.
# Дата «27.11.2024» не подходит (нет пробела с текстом после номера).
_SECTION_RE = re.compile(r"^\d+(\.\d+)?\.?\s+\D")


def _norm(value) -> str:
    """Нормализует текст заголовка: убирает пробелы/переводы строк, в нижний регистр."""
    if value is None:
        return ""
    return "".join(str(value).split()).lower()


def _find_section_row(ws, title_substr: str) -> int | None:
    target = title_substr.lower()
    for r in range(1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if a and target in str(a).lower():
            return r
    return None


def _build_column_map(ws, header_row: int, targets: dict[str, set[str]]) -> dict[str, int]:
    """targets: поле → варианты нормализованного заголовка. Возвращает поле → индекс колонки."""
    col_map: dict[str, int] = {}
    for c in range(1, ws.max_column + 1):
        norm = _norm(ws.cell(row=header_row, column=c).value)
        if not norm:
            continue
        for field, variants in targets.items():
            if field not in col_map and norm in variants:
                col_map[field] = c
    return col_map


def _is_section_title(value) -> bool:
    """Строка вида «2. ...», «3.1 ...» — заголовок секции (конец данных)."""
    if not value:
        return False
    return bool(_SECTION_RE.match(str(value).strip()))


def _read_rows(ws, header_row: int, col_map: dict[str, int], required_field: str):
    """Читает строки данных после заголовка до следующей секции. Пустые/служебные пропускаются."""
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        if _is_section_title(a):
            break
        record = {}
        for field, col in col_map.items():
            record[field] = ws.cell(row=r, column=col).value
        if record.get(required_field) in (None, ""):
            continue  # служебная строка (разрыв страницы) или пустая
        rows.append(record)
    return rows


_TRADE_TARGETS = {
    "date": {"датазаключения"},
    "time": {"время"},
    "side": {"видсделки"},
    "name": {"наименованиеактива"},
    "ticker": {"кодактива"},
    "price": {"ценазаединицу"},
    "price_currency": {"валютацены"},
    "quantity": {"количество"},
    "amount": {"суммасделки"},
    "settle_currency": {"валютарасчетов"},
    "commission": {"комиссияброкера"},
}

_POSITION_TARGETS = {
    "name": {"наименованиеактива"},
    "ticker": {"кодактива"},
    "isin": {"isin"},
    "quantity": {"исходящийостаток"},
}


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_trades_section(ws, title: str) -> list[dict]:
    start = _find_section_row(ws, title)
    if start is None:
        return []
    header_row = start + 1
    col_map = _build_column_map(ws, header_row, _TRADE_TARGETS)
    if "ticker" not in col_map:
        return []
    out = []
    for rec in _read_rows(ws, header_row, col_map, "ticker"):
        out.append(
            {
                "date": str(rec.get("date") or "").strip(),
                "time": str(rec.get("time") or "").strip(),
                "side": str(rec.get("side") or "").strip(),
                "name": str(rec.get("name") or "").strip(),
                "ticker": str(rec.get("ticker") or "").strip(),
                "price": _to_float(rec.get("price")),
                "currency": str(
                    rec.get("settle_currency") or rec.get("price_currency") or ""
                ).strip(),
                "quantity": _to_float(rec.get("quantity")),
                "amount": _to_float(rec.get("amount")),
                "commission": _to_float(rec.get("commission")),
            }
        )
    return out


def parse_broker_report(file_bytes: bytes) -> dict:
    """Парсит xlsx-отчёт Т-Банка. Возвращает {positions, trades}."""
    wb = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True)
    ws = wb.active

    # --- Позиции (3.1 Движение по ценным бумагам) ---
    positions = []
    pos_start = _find_section_row(ws, "движение по ценным бумаг")
    if pos_start is not None:
        header_row = pos_start + 1
        col_map = _build_column_map(ws, header_row, _POSITION_TARGETS)
        if "ticker" in col_map:
            for rec in _read_rows(ws, header_row, col_map, "ticker"):
                qty = _to_float(rec.get("quantity"))
                if qty <= 0:
                    continue  # позиция закрыта
                positions.append(
                    {
                        "name": str(rec.get("name") or "").strip(),
                        "ticker": str(rec.get("ticker") or "").strip(),
                        "isin": str(rec.get("isin") or "").strip(),
                        "quantity": qty,
                    }
                )

    # --- Сделки (1.1 совершённые + 1.3 за расчётный период), дедуп по ключу ---
    trades = []
    seen = set()
    for title in ("информация о совершенных", "сделки за расчетный"):
        for t in _parse_trades_section(ws, title):
            key = (t["date"], t["time"], t["ticker"], t["side"], t["quantity"], t["amount"])
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)

    wb.close()
    return {"positions": positions, "trades": trades}
