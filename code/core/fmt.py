"""Spanish (es-CO) formatting helpers shared by UI, agent and API.

Kept dependency-free on purpose: Python's locale module is unreliable on
Windows, so month/day names and separators are handled explicitly.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MONTHS_LONG = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def es_date(value: date | datetime | pd.Timestamp, *, weekday: bool = False, year: bool = True) -> str:
    """``21 sep 2026`` / ``lunes 21 sep``."""
    if value is None or pd.isna(value):
        return "—"
    d = pd.Timestamp(value)
    text = f"{d.day} {MONTHS[d.month - 1]}"
    if year:
        text += f" {d.year}"
    return f"{DAYS[d.dayofweek]} {text}" if weekday else text


def num(value: float | int | None, decimals: int = 0) -> str:
    """Thousands with dot, decimals with comma: ``12.345,6``."""
    if value is None or pd.isna(value):
        return "—"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value: float | None, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{num(value * 100, decimals)} %"


def signed_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{'+' if value >= 0 else '−'}{num(abs(value) * 100)} %"


def minutes(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    if value >= 90:
        return f"{int(value // 60)} h {int(value % 60)} min"
    return f"{num(value)} min"
