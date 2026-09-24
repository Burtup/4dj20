"""Validation of LLM-generated SQL before it touches the database.

The brief requires sanitising generated SQL to prevent injection. Rules:

1. Exactly one statement, and it must be a ``SELECT`` (or ``WITH … SELECT``).
2. No write / DDL / engine keywords (``INSERT``, ``DROP``, ``PRAGMA``, ``ATTACH`` …).
3. Only whitelisted tables, and never a PII column.
4. A ``LIMIT`` is enforced so a bad query cannot flood the UI.

The connection is additionally opened with ``PRAGMA query_only`` (see
:mod:`core.agent`), so even a bypass could not modify data.
"""

from __future__ import annotations

import re

from core.config import PII_COLUMNS

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|"
    r"reindex|analyze|load_extension|trigger|grant|revoke|truncate)\b",
    re.IGNORECASE,
)
TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", re.IGNORECASE)
CTE_NAME = re.compile(r"(?:\bwith|,)\s*([a-zA-Z_]\w*)\s+as\s*\(", re.IGNORECASE)
MAX_ROWS = 200


class UnsafeSQLError(ValueError):
    """Raised when a generated query violates a rule."""


def extract_sql(text: str) -> str:
    """Pull the SQL out of an LLM reply (fenced block or bare statement)."""
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    sql = fenced.group(1) if fenced else text
    start = re.search(r"\b(select|with)\b", sql, re.IGNORECASE)
    return sql[start.start():].strip() if start else sql.strip()


def sanitize(sql: str, allowed_tables: set[str]) -> str:
    """Return a safe version of ``sql`` or raise :class:`UnsafeSQLError`."""
    sql = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL).strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("Consulta vacía.")
    if ";" in sql:
        raise UnsafeSQLError("Solo se permite una sentencia.")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise UnsafeSQLError("Solo se permiten consultas SELECT.")
    if FORBIDDEN.search(sql):
        raise UnsafeSQLError("La consulta contiene operaciones no permitidas.")

    ctes = {name.lower() for name in CTE_NAME.findall(sql)}
    tables = {t.lower() for t in TABLE_REF.findall(sql)} - ctes
    unknown = tables - {t.lower() for t in allowed_tables}
    if unknown:
        raise UnsafeSQLError(f"Tablas no permitidas: {', '.join(sorted(unknown))}.")

    lowered = sql.lower()
    leaked = [c for c in PII_COLUMNS if re.search(rf"\b{c}\b", lowered)]
    if leaked:
        raise UnsafeSQLError("La consulta intenta acceder a datos personales.")

    if not re.search(r"\blimit\s+\d+\s*$", sql, re.IGNORECASE):
        sql = f"{sql}\nLIMIT {MAX_ROWS}"
    return sql
