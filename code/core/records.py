"""Record management: create / edit / discharge / void, with audit trail.

Every public write function:

1. checks the actor's permission (:func:`core.security.require`),
2. validates and normalises the input (raises :class:`ValidationError` with a
   message ready to show to the user),
3. writes with parameterised SQL inside one transaction,
4. appends an immutable row to ``auditoria`` with the before/after state.

Records never store patient names or identity documents: an admission uses a
free pseudonymous reference (``paciente_ref``) plus sex, age and insurance
data — enough for every KPI, useless to re-identify someone.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta

import pandas as pd

from core import db
from core.security import ROLES, Actor, hash_pin, require, validate_pin, verify_pin

# ── Controlled vocabularies (match the HIS values so analytics stay consistent) ─
SERVICES = ["UCI", "Cuidado intermedio", "Cuidado básico neonatal", "Hospitalización", "Pediatría",
            "Urgencias", "Recuperación", "Ginecoobstetricia", "Sala de partos"]
ROUTES = ["Urgencias", "Remitido", "Cirugia Ambulatorias", "Hospitalizacion"]
CLASSES = ["Hospitalario", "Ambulatorio"]
RISKS = ["Enfermedad General", "Otro Tipo Accidente", "Atencion Poblacion Perinatal", "Accidente de Transito Comun",
         "Lesion por Agresion", "Accidente en el Hogar", "Accidente de Trabajo", "Atencion Inicial Urgencias",
         "Lesion auto inflingida", "Enfermedad profesional"]
REGIMES = ["Subsidiado", "Contributivo", "Vinculado", "Particular", "Otro"]
SEXES = ["Femenino", "Masculino"]
ZONES = ["Urbana", "Rural"]
DISCHARGE_TYPES = ["Alta médica", "Remisión a otra institución", "Alta voluntaria", "Fallecimiento", "Traslado interno"]
BED_STATES = ["Disponible", "Mantenimiento", "Fuera de servicio"]
MOVEMENTS = ["Entrada", "Salida", "Ajuste"]
OPERATING_ROOMS = ["Cirugia general", "Traumatologia y ortopedia", "Cirugia ginecobstetrica", "Sala de partos",
                   "Cirugia plastica", "Urologia", "Cirugia oftalmologica", "Otras especialidades"]
SURGERY_STATES = ["Programada", "Ejecutada", "Cancelada"]
CLINICIAN_ROLES = {"Médico", "Jefe de servicio"}

CIE10 = re.compile(r"^[A-Z]\d{2}[0-9X]?$")
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class ValidationError(ValueError):
    """Input rejected; the message is user-facing (Spanish)."""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ts(value) -> str | None:
    if value in (None, ""):
        return None
    return pd.Timestamp(value).strftime(DATETIME_FMT)


def _text(value, field: str, max_len: int, required: bool = False) -> str | None:
    text = (str(value).strip() if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    if required and not text:
        raise ValidationError(f"«{field}» es obligatorio.")
    if len(text) > max_len:
        raise ValidationError(f"«{field}» admite máximo {max_len} caracteres.")
    return text or None


def _choice(value, options: list, field: str):
    if value not in options:
        raise ValidationError(f"Valor no válido para «{field}».")
    return value


def _not_future(ts: str | None, field: str) -> None:
    if ts and pd.Timestamp(ts) > datetime.now() + timedelta(minutes=5):
        raise ValidationError(f"«{field}» no puede estar en el futuro.")


def _row(conn, table: str, key: str, value) -> dict | None:
    allowed = {("ingresos", "id"), ("camas", "codigo"), ("inventario", "codigo_servicio"),
               ("cirugias", "id"), ("personal", "id")}
    if (table, key) not in allowed:
        raise ValueError("tabla no permitida")
    row = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()
    return dict(row) if row else None


def _public(row: dict | None) -> dict | None:
    """Strip secrets before writing a row to the audit trail."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in {"pin_hash", "pin_salt"}}


def _audit(conn, actor: Actor | None, action: str, entity: str, record_id, before=None, after=None,
           detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO auditoria (fecha, actor_id, actor_nombre, actor_rol, accion, entidad, registro_id, antes, despues, detalle)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (db.now(), actor.id if actor else None, actor.nombre if actor else "Sistema", actor.rol if actor else "Sistema",
         action, entity, None if record_id is None else str(record_id),
         json.dumps(_public(before), ensure_ascii=False, default=str) if before else None,
         json.dumps(_public(after), ensure_ascii=False, default=str) if after else None, detail),
    )


def _frame(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, db.connection(), params=params)


# ── Staff ────────────────────────────────────────────────────────────────────

DEMO_STAFF = [  # fictitious demo accounts — change the PINs in production
    ("Administrador del sistema", "Administrador", "ADM-001", None),
    ("Médico de turno (demo)", "Médico", "RM-10001", "Urgencias"),
    ("Enfermería de turno (demo)", "Enfermería", "RE-20001", "Hospitalización"),
    ("Jefatura de servicio (demo)", "Jefe de servicio", "RM-10002", "UCI"),
    ("Farmacia (demo)", "Químico farmacéutico", "QF-30001", None),
    ("Admisiones (demo)", "Admisiones", "AD-40001", None),
]
DEMO_PIN = "1234"


def seed_staff() -> None:
    if db.is_seeded("personal"):
        return
    with db.transaction() as conn:
        for name, role, reg, service in DEMO_STAFF:
            digest, salt = hash_pin(DEMO_PIN)
            cur = conn.execute(
                "INSERT INTO personal (nombre, rol, registro_profesional, servicio, pin_hash, pin_salt, creado_en)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", (name, role, reg, service, digest, salt, db.now()))
            _audit(conn, None, "CREAR", "personal", cur.lastrowid, after={"nombre": name, "rol": role},
                   detail="Cuenta de demostración creada al iniciar el sistema")


def list_staff(active_only: bool = False) -> pd.DataFrame:
    sql = ("SELECT id, nombre, rol, registro_profesional, servicio, activo, creado_en, ultimo_acceso "
           "FROM personal" + (" WHERE activo = 1" if active_only else "") + " ORDER BY nombre")
    return _frame(sql)


def authenticate(staff_id: int, pin: str) -> Actor | None:
    with db.transaction() as conn:
        row = _row(conn, "personal", "id", int(staff_id))
        ok = bool(row and row["activo"] and verify_pin(pin, row["pin_hash"], row["pin_salt"]))
        actor = Actor(row["id"], row["nombre"], row["rol"]) if ok else None
        if ok:
            conn.execute("UPDATE personal SET ultimo_acceso = ? WHERE id = ?", (db.now(), row["id"]))
        _audit(conn, actor or (Actor(row["id"], row["nombre"], row["rol"]) if row else None),
               "INICIO_SESION" if ok else "INICIO_FALLIDO", "personal", staff_id)
    return actor


def log_logout(actor: Actor) -> None:
    with db.transaction() as conn:
        _audit(conn, actor, "CIERRE_SESION", "personal", actor.id)


def create_staff(actor: Actor, nombre: str, rol: str, registro: str | None, servicio: str | None, pin: str) -> int:
    require(actor, "personal.gestionar")
    nombre = _text(nombre, "Nombre", 80, required=True)
    rol = _choice(rol, ROLES, "Rol")
    registro = _text(registro, "Registro profesional", 30)
    servicio = _choice(servicio, SERVICES, "Servicio") if servicio else None
    digest, salt = hash_pin(validate_pin(pin))
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO personal (nombre, rol, registro_profesional, servicio, pin_hash, pin_salt, creado_en)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)", (nombre, rol, registro, servicio, digest, salt, db.now()))
        _audit(conn, actor, "CREAR", "personal", cur.lastrowid, after=_row(conn, "personal", "id", cur.lastrowid))
        return cur.lastrowid


def update_staff(actor: Actor, staff_id: int, nombre: str, rol: str, registro: str | None,
                 servicio: str | None, activo: bool) -> None:
    require(actor, "personal.gestionar")
    if staff_id == actor.id and (not activo or rol != "Administrador"):
        raise ValidationError("No puedes desactivarte ni quitarte el rol de administrador.")
    with db.transaction() as conn:
        before = _row(conn, "personal", "id", staff_id)
        if before is None:
            raise ValidationError("El funcionario no existe.")
        conn.execute("UPDATE personal SET nombre = ?, rol = ?, registro_profesional = ?, servicio = ?, activo = ? WHERE id = ?",
                     (_text(nombre, "Nombre", 80, required=True), _choice(rol, ROLES, "Rol"),
                      _text(registro, "Registro profesional", 30),
                      _choice(servicio, SERVICES, "Servicio") if servicio else None, int(bool(activo)), staff_id))
        _audit(conn, actor, "EDITAR", "personal", staff_id, before, _row(conn, "personal", "id", staff_id))


def reset_pin(actor: Actor, staff_id: int, new_pin: str) -> None:
    """Admin reset, or a user changing their own PIN."""
    if staff_id != actor.id:
        require(actor, "personal.gestionar")
    digest, salt = hash_pin(validate_pin(new_pin))
    with db.transaction() as conn:
        conn.execute("UPDATE personal SET pin_hash = ?, pin_salt = ? WHERE id = ?", (digest, salt, staff_id))
        _audit(conn, actor, "CAMBIO_PIN", "personal", staff_id, detail="PIN actualizado (valor no registrado)")


# ── Beds ─────────────────────────────────────────────────────────────────────

def seed_beds(beds: pd.DataFrame) -> None:
    """Initial bed registry from the HIS bed codes (codigo_cama, servicio)."""
    if db.is_seeded("camas"):
        return
    with db.transaction() as conn:
        conn.executemany("INSERT OR IGNORE INTO camas (codigo, servicio) VALUES (?, ?)",
                         list(beds[["codigo_cama", "servicio"]].astype(str).itertuples(index=False, name=None)))
        _audit(conn, None, "CREAR", "camas", None, detail=f"Registro inicial de {len(beds)} camas desde el HIS")


def list_beds() -> pd.DataFrame:
    return _frame("SELECT c.codigo, c.servicio, c.estado_operativo, c.notas, p.nombre AS actualizado_por, "
                  "c.actualizado_en FROM camas c LEFT JOIN personal p ON p.id = c.actualizado_por ORDER BY c.servicio, c.codigo")


def update_bed(actor: Actor, codigo: str, estado: str, notas: str | None) -> None:
    require(actor, "camas.editar")
    with db.transaction() as conn:
        before = _row(conn, "camas", "codigo", codigo)
        if before is None:
            raise ValidationError("La cama no existe.")
        conn.execute("UPDATE camas SET estado_operativo = ?, notas = ?, actualizado_por = ?, actualizado_en = ? WHERE codigo = ?",
                     (_choice(estado, BED_STATES, "Estado"), _text(notas, "Notas", 200), actor.id, db.now(), codigo))
        _audit(conn, actor, "EDITAR", "camas", codigo, before, _row(conn, "camas", "codigo", codigo))


def create_bed(actor: Actor, codigo: str, servicio: str) -> None:
    require(actor, "camas.crear")
    codigo = _text(codigo, "Código de cama", 12, required=True).upper()
    if not re.fullmatch(r"[A-Z0-9\-]+", codigo):
        raise ValidationError("El código solo admite letras, números y guiones.")
    with db.transaction() as conn:
        if _row(conn, "camas", "codigo", codigo):
            raise ValidationError(f"Ya existe la cama {codigo}.")
        conn.execute("INSERT INTO camas (codigo, servicio, actualizado_por, actualizado_en) VALUES (?, ?, ?, ?)",
                     (codigo, _choice(servicio, SERVICES, "Servicio"), actor.id, db.now()))
        _audit(conn, actor, "CREAR", "camas", codigo, after=_row(conn, "camas", "codigo", codigo))


# ── Admissions ───────────────────────────────────────────────────────────────

def list_admissions(status: str | None = None) -> pd.DataFrame:
    sql = ("SELECT i.*, m.nombre AS medico, c.nombre AS creado_por_nombre, u.nombre AS actualizado_por_nombre "
           "FROM ingresos i LEFT JOIN personal m ON m.id = i.medico_id LEFT JOIN personal c ON c.id = i.creado_por "
           "LEFT JOIN personal u ON u.id = i.actualizado_por")
    params: tuple = ()
    if status:
        sql += " WHERE i.estado = ?"
        params = (status,)
    return _frame(sql + " ORDER BY i.fecha_ingreso DESC", params)


def occupied_beds_by_app(exclude_id: int | None = None) -> set[str]:
    rows = db.query("SELECT codigo_cama FROM ingresos WHERE estado = 'Activo' AND codigo_cama IS NOT NULL AND id != ?",
                    (exclude_id or -1,))
    return {r[0] for r in rows}


def _clean_admission(conn, data: dict, current_id: int | None = None) -> dict:
    ref = _text(data.get("paciente_ref"), "Referencia del paciente", 20) or f"PAC-{secrets.token_hex(3).upper()}"
    if re.fullmatch(r"\d{6,}", ref):
        raise ValidationError("La referencia parece un número de documento. Usa un código interno (p. ej. PAC-001).")
    clean = {
        "paciente_ref": ref,
        "sexo": _choice(data.get("sexo"), SEXES, "Sexo"),
        "regimen": _choice(data.get("regimen"), REGIMES, "Régimen"),
        "zona": _choice(data.get("zona"), ZONES, "Zona"),
        "municipio": _text(data.get("municipio"), "Municipio", 40),
        "via_ingreso": _choice(data.get("via_ingreso"), ROUTES, "Vía de ingreso"),
        "clase_ingreso": _choice(data.get("clase_ingreso"), CLASSES, "Clase de ingreso"),
        "tipo_riesgo": _choice(data.get("tipo_riesgo"), RISKS, "Tipo de riesgo"),
        "servicio": _choice(data.get("servicio"), SERVICES, "Servicio"),
        "observaciones": _text(data.get("observaciones"), "Observaciones", 500),
    }
    try:
        edad = int(data.get("edad"))
    except (TypeError, ValueError):
        raise ValidationError("La edad debe ser un número entero.") from None
    if not 0 <= edad <= 120:
        raise ValidationError("La edad debe estar entre 0 y 120 años.")
    clean["edad"] = edad

    clean["fecha_ingreso"] = _ts(data.get("fecha_ingreso"))
    if not clean["fecha_ingreso"]:
        raise ValidationError("La fecha de ingreso es obligatoria.")
    _not_future(clean["fecha_ingreso"], "Fecha de ingreso")
    clean["fecha_triage"] = _ts(data.get("fecha_triage"))
    clean["fecha_atencion"] = _ts(data.get("fecha_atencion"))
    _not_future(clean["fecha_triage"], "Hora de triage")
    _not_future(clean["fecha_atencion"], "Hora de primera atención")
    if clean["fecha_triage"] and clean["fecha_atencion"] and clean["fecha_atencion"] < clean["fecha_triage"]:
        raise ValidationError("La primera atención no puede ser anterior al triage.")
    level = data.get("nivel_triage")
    clean["nivel_triage"] = int(level) if level not in (None, "") else None
    if clean["nivel_triage"] is not None and clean["nivel_triage"] not in range(1, 6):
        raise ValidationError("El nivel de triage va de 1 (I) a 5 (V).")

    code = (_text(data.get("codigo_diagnostico"), "Código CIE-10", 4) or "").upper() or None
    if code and not CIE10.match(code):
        raise ValidationError("Código CIE-10 no válido (formato letra + 2–3 dígitos, p. ej. J189).")
    clean["codigo_diagnostico"] = code
    clean["nombre_diagnostico"] = _text(data.get("nombre_diagnostico"), "Diagnóstico", 200)

    bed = data.get("codigo_cama") or None
    if bed:
        bed_row = _row(conn, "camas", "codigo", bed)
        if bed_row is None or bed_row["servicio"] != clean["servicio"]:
            raise ValidationError("La cama no pertenece al servicio seleccionado.")
        if bed_row["estado_operativo"] != "Disponible":
            raise ValidationError(f"La cama {bed} está en «{bed_row['estado_operativo']}».")
        if bed in occupied_beds_by_app(current_id):
            raise ValidationError(f"La cama {bed} ya está asignada a otro ingreso activo.")
    clean["codigo_cama"] = bed

    medico = data.get("medico_id")
    if medico:
        row = _row(conn, "personal", "id", int(medico))
        if row is None or not row["activo"] or row["rol"] not in CLINICIAN_ROLES:
            raise ValidationError("El médico asignado debe ser personal médico activo.")
    clean["medico_id"] = int(medico) if medico else None
    return clean


def create_admission(actor: Actor, data: dict) -> int:
    require(actor, "ingresos.crear")
    with db.transaction() as conn:
        clean = _clean_admission(conn, data)
        clean |= {"creado_por": actor.id, "creado_en": db.now(), "estado": "Activo"}
        cols = ", ".join(clean)
        cur = conn.execute(f"INSERT INTO ingresos ({cols}) VALUES ({', '.join('?' * len(clean))})", tuple(clean.values()))
        _audit(conn, actor, "CREAR", "ingresos", cur.lastrowid, after=_row(conn, "ingresos", "id", cur.lastrowid))
        return cur.lastrowid


def update_admission(actor: Actor, admission_id: int, data: dict) -> None:
    require(actor, "ingresos.editar")
    with db.transaction() as conn:
        before = _row(conn, "ingresos", "id", admission_id)
        if before is None:
            raise ValidationError("El ingreso no existe.")
        if before["estado"] != "Activo":
            raise ValidationError(f"El ingreso está «{before['estado']}» y ya no se puede editar.")
        clean = _clean_admission(conn, data, current_id=admission_id)
        clean |= {"actualizado_por": actor.id, "actualizado_en": db.now()}
        assignments = ", ".join(f"{k} = ?" for k in clean)
        conn.execute(f"UPDATE ingresos SET {assignments} WHERE id = ?", (*clean.values(), admission_id))
        _audit(conn, actor, "EDITAR", "ingresos", admission_id, before, _row(conn, "ingresos", "id", admission_id))


def discharge(actor: Actor, admission_id: int, when, discharge_type: str, note: str | None = None) -> None:
    require(actor, "ingresos.egresar")
    with db.transaction() as conn:
        before = _row(conn, "ingresos", "id", admission_id)
        if before is None or before["estado"] != "Activo":
            raise ValidationError("Solo se puede dar egreso a un ingreso activo.")
        when_ts = _ts(when)
        _not_future(when_ts, "Fecha de egreso")
        if when_ts < before["fecha_ingreso"]:
            raise ValidationError("El egreso no puede ser anterior al ingreso.")
        conn.execute("UPDATE ingresos SET estado = 'Egresado', fecha_egreso = ?, tipo_egreso = ?, "
                     "observaciones = COALESCE(?, observaciones), actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                     (when_ts, _choice(discharge_type, DISCHARGE_TYPES, "Tipo de egreso"),
                      _text(note, "Nota", 500), actor.id, db.now(), admission_id))
        _audit(conn, actor, "EGRESO", "ingresos", admission_id, before, _row(conn, "ingresos", "id", admission_id))


def void_admission(actor: Actor, admission_id: int, reason: str) -> None:
    require(actor, "ingresos.anular")
    reason = _text(reason, "Motivo de anulación", 300, required=True)
    with db.transaction() as conn:
        before = _row(conn, "ingresos", "id", admission_id)
        if before is None or before["estado"] == "Anulado":
            raise ValidationError("El ingreso no existe o ya está anulado.")
        conn.execute("UPDATE ingresos SET estado = 'Anulado', actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                     (actor.id, db.now(), admission_id))
        conn.execute("UPDATE cirugias SET estado = 'Cancelada', motivo_cancelacion = 'Ingreso anulado' "
                     "WHERE ingreso_id = ? AND estado = 'Programada'", (admission_id,))
        _audit(conn, actor, "ANULAR", "ingresos", admission_id, before, _row(conn, "ingresos", "id", admission_id), reason)


# ── Inventory ────────────────────────────────────────────────────────────────

def seed_inventory(items: pd.DataFrame) -> None:
    """Initial stock (simulated, flagged) for items with recent consumption."""
    if db.is_seeded("inventario"):
        return
    with db.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO inventario (codigo_servicio, nombre, stock, origen) VALUES (?, ?, ?, 'Simulado')",
            [(str(r.codigo_servicio), str(r.nombre_servicio), float(r.stock)) for r in items.itertuples()])
        _audit(conn, None, "CREAR", "inventario", None,
               detail=f"Inventario inicial simulado para {len(items)} ítems a partir del consumo del HIS")


def list_inventory() -> pd.DataFrame:
    return _frame("SELECT i.*, p.nombre AS actualizado_por_nombre FROM inventario i "
                  "LEFT JOIN personal p ON p.id = i.actualizado_por ORDER BY i.nombre")


def list_movements(limit: int = 500) -> pd.DataFrame:
    return _frame("SELECT m.fecha, m.codigo_servicio, i.nombre, m.tipo, m.cantidad, m.stock_resultante, m.motivo, "
                  "p.nombre AS responsable FROM movimientos_inventario m JOIN inventario i USING (codigo_servicio) "
                  "LEFT JOIN personal p ON p.id = m.actor_id ORDER BY m.id DESC LIMIT ?", (limit,))


def stock_movement(actor: Actor, code: str, kind: str, quantity: float, reason: str | None) -> float:
    """Entrada adds, Salida subtracts, Ajuste sets the counted stock."""
    require(actor, "inventario.movimiento")
    kind = _choice(kind, MOVEMENTS, "Tipo de movimiento")
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad debe ser numérica.") from None
    if quantity < 0 or (kind != "Ajuste" and quantity == 0):
        raise ValidationError("La cantidad debe ser mayor que cero.")
    if kind == "Ajuste":
        reason = _text(reason, "Motivo del ajuste", 200, required=True)
    with db.transaction() as conn:
        before = _row(conn, "inventario", "codigo_servicio", code)
        if before is None:
            raise ValidationError("El ítem no existe en el inventario.")
        new_stock = {"Entrada": before["stock"] + quantity, "Salida": before["stock"] - quantity, "Ajuste": quantity}[kind]
        if new_stock < 0:
            raise ValidationError(f"Stock insuficiente: hay {before['stock']:.0f} unidades.")
        conn.execute("UPDATE inventario SET stock = ?, origen = 'Registrado', actualizado_por = ?, actualizado_en = ? "
                     "WHERE codigo_servicio = ?", (new_stock, actor.id, db.now(), code))
        conn.execute("INSERT INTO movimientos_inventario (codigo_servicio, tipo, cantidad, stock_resultante, motivo, actor_id, fecha)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)", (code, kind, quantity, new_stock, _text(reason, "Motivo", 200), actor.id, db.now()))
        _audit(conn, actor, f"STOCK_{kind.upper()}", "inventario", code, before,
               _row(conn, "inventario", "codigo_servicio", code), f"{kind} de {quantity:g} unidades")
        return new_stock


def update_item(actor: Actor, code: str, minimum: float | None, expiry, lot: str | None) -> None:
    require(actor, "inventario.editar")
    if minimum is not None and minimum < 0:
        raise ValidationError("El stock mínimo no puede ser negativo.")
    with db.transaction() as conn:
        before = _row(conn, "inventario", "codigo_servicio", code)
        if before is None:
            raise ValidationError("El ítem no existe.")
        conn.execute("UPDATE inventario SET stock_minimo = ?, fecha_vencimiento = ?, lote = ?, actualizado_por = ?, "
                     "actualizado_en = ? WHERE codigo_servicio = ?",
                     (minimum, pd.Timestamp(expiry).strftime("%Y-%m-%d") if expiry else None,
                      _text(lot, "Lote", 30), actor.id, db.now(), code))
        _audit(conn, actor, "EDITAR", "inventario", code, before, _row(conn, "inventario", "codigo_servicio", code))


def create_item(actor: Actor, code: str, name: str, stock: float, minimum: float | None, expiry, lot: str | None) -> None:
    require(actor, "inventario.editar")
    code = _text(code, "Código", 20, required=True).upper()
    if stock < 0:
        raise ValidationError("El stock no puede ser negativo.")
    with db.transaction() as conn:
        if _row(conn, "inventario", "codigo_servicio", code):
            raise ValidationError(f"Ya existe el código {code}.")
        conn.execute("INSERT INTO inventario (codigo_servicio, nombre, stock, stock_minimo, fecha_vencimiento, lote, origen, "
                     "actualizado_por, actualizado_en) VALUES (?, ?, ?, ?, ?, ?, 'Registrado', ?, ?)",
                     (code, _text(name, "Nombre", 200, required=True), float(stock), minimum,
                      pd.Timestamp(expiry).strftime("%Y-%m-%d") if expiry else None, _text(lot, "Lote", 30),
                      actor.id, db.now()))
        _audit(conn, actor, "CREAR", "inventario", code, after=_row(conn, "inventario", "codigo_servicio", code))


def import_inventory(actor: Actor, stock: pd.DataFrame) -> int:
    """Bulk stock load from Inventario.csv (codigo_servicio;stock)."""
    require(actor, "inventario.editar")
    if not {"codigo_servicio", "stock"} <= set(stock.columns):
        raise ValidationError("El archivo debe tener las columnas codigo_servicio y stock.")
    rows = [(float(s), actor.id, db.now(), str(c)) for c, s in zip(stock["codigo_servicio"], stock["stock"])
            if pd.notna(s) and float(s) >= 0]
    with db.transaction() as conn:
        cur = conn.executemany("UPDATE inventario SET stock = ?, origen = 'Registrado', actualizado_por = ?, "
                               "actualizado_en = ? WHERE codigo_servicio = ?", rows)
        _audit(conn, actor, "IMPORTAR", "inventario", None, detail=f"{cur.rowcount} existencias cargadas desde archivo")
        return cur.rowcount


# ── Surgeries ────────────────────────────────────────────────────────────────

def list_surgeries() -> pd.DataFrame:
    return _frame("SELECT s.*, p.nombre AS cirujano, i.paciente_ref FROM cirugias s "
                  "LEFT JOIN personal p ON p.id = s.cirujano_id LEFT JOIN ingresos i ON i.id = s.ingreso_id "
                  "ORDER BY s.fecha_programada DESC")


def _clean_surgery(conn, data: dict, current_id: int | None = None) -> dict:
    when = _ts(data.get("fecha_programada"))
    if not when:
        raise ValidationError("La fecha y hora de la cirugía son obligatorias.")
    if pd.Timestamp(when) > datetime.now() + timedelta(days=365):
        raise ValidationError("Solo se puede programar hasta un año adelante.")
    duration = int(data.get("duracion_min") or 60)
    if not 15 <= duration <= 720:
        raise ValidationError("La duración debe estar entre 15 y 720 minutos.")
    room = _choice(data.get("quirofano"), OPERATING_ROOMS, "Quirófano")
    start = pd.Timestamp(when)
    end = start + pd.Timedelta(minutes=duration)
    for other in conn.execute("SELECT id, fecha_programada, duracion_min FROM cirugias WHERE quirofano = ? "
                              "AND estado = 'Programada' AND id != ?", (room, current_id or -1)):
        o_start = pd.Timestamp(other["fecha_programada"])
        if start < o_start + pd.Timedelta(minutes=other["duracion_min"]) and o_start < end:
            raise ValidationError(f"Cruce de agenda con la cirugía #{other['id']} en {room} ({o_start:%d/%m %H:%M}).")
    cups = _text(data.get("codigo_cups"), "Código CUPS", 10)
    if cups and not re.fullmatch(r"[0-9A-Z]{4,10}", cups.upper()):
        raise ValidationError("Código CUPS no válido.")
    surgeon = data.get("cirujano_id")
    if surgeon:
        row = _row(conn, "personal", "id", int(surgeon))
        if row is None or not row["activo"] or row["rol"] not in CLINICIAN_ROLES:
            raise ValidationError("El cirujano debe ser personal médico activo.")
    admission = data.get("ingreso_id")
    if admission:
        row = _row(conn, "ingresos", "id", int(admission))
        if row is None or row["estado"] != "Activo":
            raise ValidationError("El ingreso asociado debe estar activo.")
    return {
        "fecha_programada": when, "quirofano": room, "duracion_min": duration,
        "procedimiento": _text(data.get("procedimiento"), "Procedimiento", 200, required=True),
        "codigo_cups": cups.upper() if cups else None, "servicio": _choice(data.get("servicio"), SERVICES, "Servicio"),
        "cirujano_id": int(surgeon) if surgeon else None, "ingreso_id": int(admission) if admission else None,
    }


def schedule_surgery(actor: Actor, data: dict) -> int:
    require(actor, "cirugias.programar")
    with db.transaction() as conn:
        clean = _clean_surgery(conn, data) | {"creado_por": actor.id, "creado_en": db.now()}
        cur = conn.execute(f"INSERT INTO cirugias ({', '.join(clean)}) VALUES ({', '.join('?' * len(clean))})",
                           tuple(clean.values()))
        _audit(conn, actor, "CREAR", "cirugias", cur.lastrowid, after=_row(conn, "cirugias", "id", cur.lastrowid))
        return cur.lastrowid


def update_surgery(actor: Actor, surgery_id: int, data: dict) -> None:
    require(actor, "cirugias.programar")
    with db.transaction() as conn:
        before = _row(conn, "cirugias", "id", surgery_id)
        if before is None or before["estado"] != "Programada":
            raise ValidationError("Solo se pueden editar cirugías en estado «Programada».")
        clean = _clean_surgery(conn, data, surgery_id) | {"actualizado_por": actor.id, "actualizado_en": db.now()}
        conn.execute(f"UPDATE cirugias SET {', '.join(f'{k} = ?' for k in clean)} WHERE id = ?", (*clean.values(), surgery_id))
        _audit(conn, actor, "EDITAR", "cirugias", surgery_id, before, _row(conn, "cirugias", "id", surgery_id))


def close_surgery(actor: Actor, surgery_id: int, state: str, reason: str | None = None) -> None:
    require(actor, "cirugias.cerrar")
    state = _choice(state, ["Ejecutada", "Cancelada"], "Estado")
    if state == "Cancelada":
        reason = _text(reason, "Motivo de cancelación", 200, required=True)
    with db.transaction() as conn:
        before = _row(conn, "cirugias", "id", surgery_id)
        if before is None or before["estado"] != "Programada":
            raise ValidationError("Solo se pueden cerrar cirugías programadas.")
        if state == "Ejecutada" and pd.Timestamp(before["fecha_programada"]) > datetime.now() + timedelta(minutes=5):
            raise ValidationError("No se puede marcar como ejecutada una cirugía futura.")
        conn.execute("UPDATE cirugias SET estado = ?, motivo_cancelacion = ?, actualizado_por = ?, actualizado_en = ? WHERE id = ?",
                     (state, reason, actor.id, db.now(), surgery_id))
        _audit(conn, actor, "EJECUTAR" if state == "Ejecutada" else "CANCELAR", "cirugias", surgery_id,
               before, _row(conn, "cirugias", "id", surgery_id), reason)


# ── Audit ────────────────────────────────────────────────────────────────────

def audit_log(entity: str | None = None, actor_id: int | None = None, since: str | None = None,
              limit: int = 2000) -> pd.DataFrame:
    sql, params = "SELECT * FROM auditoria WHERE 1 = 1", []
    if entity:
        sql += " AND entidad = ?"
        params.append(entity)
    if actor_id:
        sql += " AND actor_id = ?"
        params.append(actor_id)
    if since:
        sql += " AND fecha >= ?"
        params.append(since)
    return _frame(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
