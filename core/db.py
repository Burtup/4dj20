"""Operational database (SQLite) for everything users create or edit.

The HIS extract (parquet) is read-only history. Records captured in the app
— admissions, discharges, beds, stock, surgeries, staff — live here, and
every change is written to ``auditoria`` in the same transaction.

Why SQLite: recommended by the brief ("no requiere servidor, ligero y fácil de
reiniciar"). All queries use parameters (``?``), never string formatting.

Schema versioning is minimal: ``SCHEMA`` is idempotent (``IF NOT EXISTS``)
and ``meta.version`` is bumped on every write so cached analytics know when
to refresh (see :func:`data_version`).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from core import config

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Staff who can sign in and edit records.
CREATE TABLE IF NOT EXISTS personal (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre               TEXT NOT NULL,
    rol                  TEXT NOT NULL,
    registro_profesional TEXT,
    servicio             TEXT,
    pin_hash             TEXT NOT NULL,
    pin_salt             TEXT NOT NULL,
    activo               INTEGER NOT NULL DEFAULT 1,
    creado_en            TEXT NOT NULL,
    ultimo_acceso        TEXT
);

-- Admissions captured in the app (HIS-like fields, no patient names/documents).
CREATE TABLE IF NOT EXISTS ingresos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_ref         TEXT NOT NULL,
    sexo                 TEXT NOT NULL,
    edad                 INTEGER NOT NULL,
    regimen              TEXT NOT NULL,
    zona                 TEXT NOT NULL,
    municipio            TEXT,
    fecha_ingreso        TEXT NOT NULL,
    via_ingreso          TEXT NOT NULL,
    clase_ingreso        TEXT NOT NULL,
    tipo_riesgo          TEXT NOT NULL,
    servicio             TEXT NOT NULL,
    codigo_cama          TEXT,
    nivel_triage         INTEGER,
    fecha_triage         TEXT,
    fecha_atencion       TEXT,
    codigo_diagnostico   TEXT,
    nombre_diagnostico   TEXT,
    medico_id            INTEGER REFERENCES personal(id),
    estado               TEXT NOT NULL DEFAULT 'Activo',   -- Activo | Egresado | Anulado
    fecha_egreso         TEXT,
    tipo_egreso          TEXT,
    observaciones        TEXT,
    creado_por           INTEGER REFERENCES personal(id),
    creado_en            TEXT NOT NULL,
    actualizado_por      INTEGER REFERENCES personal(id),
    actualizado_en       TEXT
);

-- Bed registry (seeded from the HIS bed codes).
CREATE TABLE IF NOT EXISTS camas (
    codigo               TEXT PRIMARY KEY,
    servicio             TEXT NOT NULL,
    estado_operativo     TEXT NOT NULL DEFAULT 'Disponible', -- Disponible | Mantenimiento | Fuera de servicio
    notas                TEXT,
    actualizado_por      INTEGER REFERENCES personal(id),
    actualizado_en       TEXT
);

-- Stock per medication / supply (brief data model: stock, vencimiento, consumo).
CREATE TABLE IF NOT EXISTS inventario (
    codigo_servicio      TEXT PRIMARY KEY,
    nombre               TEXT NOT NULL,
    stock                REAL NOT NULL CHECK (stock >= 0),
    stock_minimo         REAL,
    fecha_vencimiento    TEXT,
    lote                 TEXT,
    origen               TEXT NOT NULL DEFAULT 'Simulado', -- Simulado | Registrado
    actualizado_por      INTEGER REFERENCES personal(id),
    actualizado_en       TEXT
);

CREATE TABLE IF NOT EXISTS movimientos_inventario (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo_servicio      TEXT NOT NULL REFERENCES inventario(codigo_servicio),
    tipo                 TEXT NOT NULL,     -- Entrada | Salida | Ajuste
    cantidad             REAL NOT NULL,
    stock_resultante     REAL NOT NULL,
    motivo               TEXT,
    actor_id             INTEGER REFERENCES personal(id),
    fecha                TEXT NOT NULL
);

-- Surgeries scheduled in the app.
CREATE TABLE IF NOT EXISTS cirugias (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_programada     TEXT NOT NULL,
    quirofano            TEXT NOT NULL,
    procedimiento        TEXT NOT NULL,
    codigo_cups          TEXT,
    servicio             TEXT NOT NULL,
    ingreso_id           INTEGER REFERENCES ingresos(id),
    cirujano_id          INTEGER REFERENCES personal(id),
    duracion_min         INTEGER NOT NULL DEFAULT 60,
    estado               TEXT NOT NULL DEFAULT 'Programada', -- Programada | Ejecutada | Cancelada
    motivo_cancelacion   TEXT,
    creado_por           INTEGER REFERENCES personal(id),
    creado_en            TEXT NOT NULL,
    actualizado_por      INTEGER REFERENCES personal(id),
    actualizado_en       TEXT
);

-- Immutable audit trail: who changed what, when, before/after.
CREATE TABLE IF NOT EXISTS auditoria (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha                TEXT NOT NULL,
    actor_id             INTEGER,
    actor_nombre         TEXT,
    actor_rol            TEXT,
    accion               TEXT NOT NULL,
    entidad              TEXT NOT NULL,
    registro_id          TEXT,
    antes                TEXT,
    despues              TEXT,
    detalle              TEXT
);
CREATE TRIGGER IF NOT EXISTS auditoria_no_update BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría es inmutable'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_no_delete BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría es inmutable'); END;

CREATE INDEX IF NOT EXISTS ix_ingresos_fecha ON ingresos(fecha_ingreso);
CREATE INDEX IF NOT EXISTS ix_auditoria_fecha ON auditoria(fecha);
CREATE INDEX IF NOT EXISTS ix_cirugias_fecha ON cirugias(fecha_programada);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    config.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_FILE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_CONN: sqlite3.Connection | None = None


def connection() -> sqlite3.Connection:
    """Process-wide connection; schema is created on first use."""
    global _CONN
    if _CONN is None:
        with _LOCK:
            if _CONN is None:
                conn = _connect()
                conn.executescript(SCHEMA)
                conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '0')")
                conn.commit()
                _CONN = conn
    return _CONN


@contextmanager
def transaction():
    """Serialised write transaction that bumps the data version on success."""
    conn = connection()
    with _LOCK:
        try:
            yield conn
            conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'version'")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return connection().execute(sql, params).fetchall()


def data_version() -> int:
    return int(connection().execute("SELECT value FROM meta WHERE key = 'version'").fetchone()[0])


def is_seeded(table: str) -> bool:
    if table not in {"personal", "camas", "inventario"}:
        raise ValueError(table)
    return connection().execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0] == 1
