"""Registro clínico — capture and edit operational records.

Tabs: admissions & discharges, beds, inventory, surgeries. Pattern for every
entity:

1. a filterable table (select a row to act on it),
2. action buttons that open a modal window (``st.dialog``),
3. the form calls :mod:`core.records`, which validates, enforces the role
   and writes the audit trail. Errors are shown inside the window; on
   success the window closes and every dashboard refreshes automatically.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from core import kpis, records
from core.data import get_data
from core.filters import FilterState
from core.fmt import num
from core.security import PermissionDenied
from ui import auth, charts, components, state

TRIAGE_OPTIONS = [1, 2, 3, 4, 5]  # no selection = sin triage
TRIAGE_TEXT = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}


# ── Shared helpers ───────────────────────────────────────────────────────────

def _flash(message: str) -> None:
    st.session_state["records_flash"] = message
    st.rerun()


def _run(action, success: str) -> None:
    """Execute a domain action and translate its errors into UI messages."""
    try:
        action()
    except (records.ValidationError, PermissionDenied, ValueError) as exc:
        st.error(str(exc), icon=":material/error:")
        return
    _flash(success)


def _datetime_input(label: str, default: datetime | None, key: str, optional: bool = False) -> datetime | None:
    c1, c2 = st.columns([3, 2])
    if optional:
        enabled = c1.checkbox(f"Registrar {label.lower()}", value=default is not None, key=f"{key}_on")
        if not enabled:
            return None
    value = default or datetime.now().replace(second=0, microsecond=0)
    d = c1.date_input(label, value=value.date(), key=f"{key}_d", format="DD/MM/YYYY", max_value=date.today())
    t = c2.time_input("Hora", value=value.time(), key=f"{key}_t", step=timedelta(minutes=5))
    return datetime.combine(d, t)


def _ts(value) -> datetime | None:
    return None if value is None or pd.isna(value) else pd.Timestamp(value).to_pydatetime()


def _selected(event, df: pd.DataFrame) -> pd.Series | None:
    rows = event.selection.rows if event is not None else []
    return df.iloc[rows[0]] if rows else None


@st.cache_data(show_spinner=False)
def _dx_catalog(version: int) -> dict[str, str]:
    a = get_data().admissions.dropna(subset=["codigo_diagnostico"])
    a = a.drop_duplicates("codigo_diagnostico")
    return dict(zip(a["codigo_diagnostico"], a["nombre_diagnostico"].astype(str).str.capitalize()))


def _clinicians() -> pd.DataFrame:
    staff = records.list_staff(active_only=True)
    return staff[staff["rol"].isin(records.CLINICIAN_ROLES)]


def _free_beds(service: str, keep: str | None = None) -> list[str]:
    data = get_data()
    today = FilterState.default(data).with_dates(data.reference_date.date(), data.reference_date.date())
    beds = kpis.bed_map(today)
    free = beds[(beds["servicio"] == service) & (beds["estado"] == "Libre")]["codigo_cama"].tolist()
    free = [b for b in free if b not in records.occupied_beds_by_app()]
    return ([keep] if keep and keep not in free else []) + free


# ── Admissions ───────────────────────────────────────────────────────────────

def _admission_form(row: pd.Series | None, prefix: str) -> dict:
    """Fields shared by 'new' and 'edit'. Returns the payload for core.records."""
    g = (lambda k, d=None: d if row is None or pd.isna(row.get(k)) else row.get(k))
    st.markdown("**Paciente** · sin nombres ni documentos")
    c = st.columns([2, 1.4, 1, 1.4, 1.2])
    ref = c[0].text_input("Referencia interna", value=g("paciente_ref", ""), key=f"{prefix}ref", max_chars=20,
                          placeholder="PAC-001 (se genera si se deja vacío)",
                          help="Código interno del hospital. No uses número de documento ni nombre.")
    sexo = c[1].selectbox("Sexo", records.SEXES, index=records.SEXES.index(g("sexo", "Femenino")), key=f"{prefix}sexo")
    edad = c[2].number_input("Edad", 0, 120, int(g("edad", 30)), key=f"{prefix}edad")
    regimen = c[3].selectbox("Régimen", records.REGIMES, index=records.REGIMES.index(g("regimen", "Subsidiado")),
                             key=f"{prefix}reg")
    zona = c[4].selectbox("Zona", records.ZONES, index=records.ZONES.index(g("zona", "Urbana")), key=f"{prefix}zona")
    municipio = st.text_input("Municipio", value=g("municipio", "") or "", key=f"{prefix}mun", max_chars=40)

    st.markdown("**Ingreso**")
    fecha = _datetime_input("Fecha de ingreso", _ts(g("fecha_ingreso")), f"{prefix}fi")
    c = st.columns(3)
    via = c[0].selectbox("Vía de ingreso", records.ROUTES, index=records.ROUTES.index(g("via_ingreso", "Urgencias")),
                         key=f"{prefix}via")
    clase = c[1].selectbox("Clase", records.CLASSES, index=records.CLASSES.index(g("clase_ingreso", "Hospitalario")),
                           key=f"{prefix}clase")
    riesgo = c[2].selectbox("Tipo de riesgo", records.RISKS,
                            index=records.RISKS.index(g("tipo_riesgo", "Enfermedad General")), key=f"{prefix}riesgo")
    c = st.columns(2)
    servicio = c[0].selectbox("Servicio", records.SERVICES, index=records.SERVICES.index(g("servicio", "Urgencias")),
                              key=f"{prefix}serv")
    beds = _free_beds(servicio, keep=g("codigo_cama"))
    current_bed = g("codigo_cama")
    cama = c[1].selectbox("Cama disponible", [None] + beds, key=f"{prefix}cama",
                          index=(beds.index(current_bed) + 1) if current_bed in beds else 0,
                          format_func=lambda b: "Sin cama asignada" if b is None else b,
                          help="Solo camas libres y en estado «Disponible» del servicio.")

    st.markdown("**Triage y atención**")
    level = g("nivel_triage")
    nivel = st.segmented_control("Nivel de triage (vacío = sin triage)", TRIAGE_OPTIONS, format_func=lambda v: TRIAGE_TEXT[v],
                                 default=None if level is None else int(level), key=f"{prefix}triage")
    triage_at = _datetime_input("Hora de triage", _ts(g("fecha_triage")), f"{prefix}ft", optional=True)
    care_at = _datetime_input("Primera atención médica", _ts(g("fecha_atencion")), f"{prefix}fa", optional=True)

    st.markdown("**Diagnóstico y responsable**")
    catalog = _dx_catalog(get_data().version)
    c = st.columns([1, 3])
    code = c[0].text_input("CIE-10", value=g("codigo_diagnostico", "") or "", key=f"{prefix}cie", max_chars=4,
                           placeholder="J189").strip().upper()
    suggested = catalog.get(code, "")
    name = c[1].text_input("Diagnóstico", value=g("nombre_diagnostico", "") or suggested, key=f"{prefix}dx_{code}",
                           max_chars=200, help="Se completa con el catálogo del HIS cuando el código existe.")
    if code and not suggested:
        st.caption("Código no visto en el HIS: verifica que sea correcto.")
    doctors = _clinicians()
    medico = st.selectbox("Médico asignado", [None] + doctors["id"].tolist(), key=f"{prefix}med",
                          index=(doctors["id"].tolist().index(int(g("medico_id"))) + 1)
                          if g("medico_id") is not None and int(g("medico_id")) in doctors["id"].tolist() else 0,
                          format_func=lambda i: "Sin asignar" if i is None else doctors.set_index("id").at[i, "nombre"])
    obs = st.text_area("Observaciones", value=g("observaciones", "") or "", key=f"{prefix}obs", max_chars=500,
                       height=70, placeholder="Información clínica relevante (sin datos identificables)")
    return {"paciente_ref": ref, "sexo": sexo, "edad": edad, "regimen": regimen, "zona": zona, "municipio": municipio,
            "fecha_ingreso": fecha, "via_ingreso": via, "clase_ingreso": clase, "tipo_riesgo": riesgo,
            "servicio": servicio, "codigo_cama": cama, "nivel_triage": nivel, "fecha_triage": triage_at,
            "fecha_atencion": care_at, "codigo_diagnostico": code or None, "nombre_diagnostico": name or None,
            "medico_id": medico, "observaciones": obs}


@st.dialog("Registrar ingreso", width="large")
def _new_admission() -> None:
    actor = auth.guard("ingresos.crear")
    if actor is None:
        return
    payload = _admission_form(None, "new_")
    if st.button("Guardar ingreso", type="primary", icon=":material/save:"):
        _run(lambda: records.create_admission(actor, payload), "Ingreso registrado.")


@st.dialog("Editar ingreso", width="large")
def _edit_admission(row: pd.Series) -> None:
    actor = auth.guard("ingresos.editar")
    if actor is None:
        return
    st.caption(f"Ingreso #{row['id']} · creado por {row.get('creado_por_nombre') or '—'} el {row['creado_en']}")
    payload = _admission_form(row, f"edit{row['id']}_")
    if st.button("Guardar cambios", type="primary", icon=":material/save:"):
        _run(lambda: records.update_admission(actor, int(row["id"]), payload), "Cambios guardados.")


@st.dialog("Dar egreso")
def _discharge(row: pd.Series) -> None:
    actor = auth.guard("ingresos.egresar")
    if actor is None:
        return
    st.markdown(f"Ingreso **#{row['id']}** · {row['paciente_ref']} · {row['servicio']}")
    when = _datetime_input("Fecha de egreso", None, f"dis{row['id']}")
    kind = st.selectbox("Tipo de egreso", records.DISCHARGE_TYPES, key=f"dis_type{row['id']}")
    note = st.text_area("Nota de egreso", max_chars=500, key=f"dis_note{row['id']}", height=70)
    if st.button("Confirmar egreso", type="primary", icon=":material/exit_to_app:"):
        _run(lambda: records.discharge(actor, int(row["id"]), when, kind, note or None), "Egreso registrado; la cama quedó libre.")


@st.dialog("Anular ingreso")
def _void(row: pd.Series) -> None:
    actor = auth.guard("ingresos.anular")
    if actor is None:
        return
    st.warning("La anulación no borra el registro: lo excluye de los indicadores y queda en auditoría.")
    reason = st.text_area("Motivo de anulación (obligatorio)", max_chars=300, key=f"void{row['id']}")
    sure = st.checkbox("Confirmo que el ingreso fue registrado por error", key=f"void_ok{row['id']}")
    if st.button("Anular", type="primary", icon=":material/block:", disabled=not sure):
        _run(lambda: records.void_admission(actor, int(row["id"]), reason), "Ingreso anulado.")


def _admissions_tab() -> None:
    df = records.list_admissions()
    active = df[df["estado"] == "Activo"]
    k = st.columns(4)
    k[0].metric("Ingresos activos", num(len(active)), border=True)
    k[1].metric("Egresos registrados", num((df["estado"] == "Egresado").sum()), border=True)
    k[2].metric("Sin cama asignada", num(active["codigo_cama"].isna().sum()), border=True)
    k[3].metric("Anulados", num((df["estado"] == "Anulado").sum()), border=True)

    bar = st.container(horizontal=True, vertical_alignment="bottom")
    status = bar.segmented_control("Estado", ["Activo", "Egresado", "Anulado", "Todos"], default="Activo", key="adm_status")
    query = bar.text_input("Buscar", placeholder="Referencia, servicio, CIE-10…", key="adm_q", width=260)
    if bar.button("Nuevo ingreso", type="primary", icon=":material/person_add:", key="adm_new_btn"):
        _new_admission()

    view = df if status in (None, "Todos") else df[df["estado"] == status]
    if query:
        blob = view[["paciente_ref", "servicio", "codigo_diagnostico", "nombre_diagnostico", "medico"]].astype(str).agg(" ".join, axis=1)
        view = view[blob.str.contains(query, case=False, regex=False)]
    view = view.reset_index(drop=True)
    shown = view.assign(fecha_ingreso=pd.to_datetime(view["fecha_ingreso"]), fecha_egreso=pd.to_datetime(view["fecha_egreso"]))
    event = st.dataframe(
        shown[["id", "estado", "paciente_ref", "fecha_ingreso", "servicio", "codigo_cama", "nivel_triage",
               "codigo_diagnostico", "medico", "fecha_egreso", "creado_por_nombre", "actualizado_por_nombre"]],
        hide_index=True, width="stretch", height=320, on_select="rerun", selection_mode="single-row", key="adm_table",
        column_config={"id": "#", "estado": "Estado", "paciente_ref": "Paciente", "servicio": "Servicio",
                       "fecha_ingreso": st.column_config.DatetimeColumn("Ingreso", format="DD/MM/YYYY HH:mm"),
                       "fecha_egreso": st.column_config.DatetimeColumn("Egreso", format="DD/MM/YYYY HH:mm"),
                       "codigo_cama": "Cama", "nivel_triage": "Triage", "codigo_diagnostico": "CIE-10",
                       "medico": "Médico", "creado_por_nombre": "Registró", "actualizado_por_nombre": "Última edición"})
    row = _selected(event, view)
    if row is None:
        st.caption("Selecciona una fila para editar, dar egreso o anular.")
        return
    actions = st.container(horizontal=True)
    if actions.button("Editar", icon=":material/edit:", key="adm_edit", disabled=row["estado"] != "Activo"):
        _edit_admission(row)
    if actions.button("Dar egreso", icon=":material/exit_to_app:", key="adm_dis", disabled=row["estado"] != "Activo"):
        _discharge(row)
    if actions.button("Anular", icon=":material/block:", key="adm_void", disabled=row["estado"] == "Anulado"):
        _void(row)
    if row["estado"] != "Activo":
        actions.caption(f"Ingreso «{row['estado']}»: solo lectura.")


# ── Beds ─────────────────────────────────────────────────────────────────────

@st.dialog("Estado de la cama")
def _bed_state(row: pd.Series) -> None:
    actor = auth.guard("camas.editar")
    if actor is None:
        return
    st.markdown(f"Cama **{row['codigo']}** · {row['servicio']} · ocupación actual: **{row['ocupacion']}**")
    state_ = st.segmented_control("Estado operativo", records.BED_STATES, default=row["estado_operativo"], key=f"bed_s{row['codigo']}")
    notes = st.text_input("Notas", value=row["notas"] or "", max_chars=200, key=f"bed_n{row['codigo']}",
                          placeholder="Ej. cambio de colchón, aislamiento…")
    if row["ocupacion"] != "Libre" and state_ != "Disponible":
        st.warning("La cama tiene un paciente: el cambio aplica cuando quede libre.")
    if st.button("Guardar", type="primary", icon=":material/save:"):
        _run(lambda: records.update_bed(actor, row["codigo"], state_, notes), f"Cama {row['codigo']} actualizada.")


@st.dialog("Nueva cama")
def _new_bed() -> None:
    actor = auth.guard("camas.crear")
    if actor is None:
        return
    code = st.text_input("Código", max_chars=12, placeholder="UCI-A15")
    service = st.selectbox("Servicio", records.SERVICES)
    if st.button("Crear cama", type="primary", icon=":material/add:"):
        _run(lambda: records.create_bed(actor, code, service), "Cama creada; la capacidad del servicio se actualizó.")


def _beds_tab() -> None:
    data = get_data()
    beds = records.list_beds()
    today = FilterState.default(data).with_dates(data.reference_date.date(), data.reference_date.date())
    occupancy = kpis.bed_map(today).set_index("codigo_cama")["estado"]
    beds["ocupacion"] = beds["codigo"].map(occupancy).fillna("Libre")
    beds.loc[beds["ocupacion"].isin(records.BED_STATES), "ocupacion"] = "Libre"

    k = st.columns(4)
    k[0].metric("Camas registradas", num(len(beds)), border=True)
    k[1].metric("Disponibles", num((beds["estado_operativo"] == "Disponible").sum()), border=True)
    k[2].metric("En mantenimiento", num((beds["estado_operativo"] == "Mantenimiento").sum()), border=True)
    k[3].metric("Fuera de servicio", num((beds["estado_operativo"] == "Fuera de servicio").sum()), border=True)

    bar = st.container(horizontal=True, vertical_alignment="bottom")
    service = bar.selectbox("Servicio", ["Todos"] + records.SERVICES, key="bed_service", width=240)
    op = bar.segmented_control("Estado operativo", ["Todos"] + records.BED_STATES, default="Todos", key="bed_op")
    if bar.button("Nueva cama", icon=":material/add:", key="bed_new"):
        _new_bed()
    view = beds if service == "Todos" else beds[beds["servicio"] == service]
    view = view if op in (None, "Todos") else view[view["estado_operativo"] == op]
    view = view.reset_index(drop=True)
    event = st.dataframe(view[["codigo", "servicio", "ocupacion", "estado_operativo", "notas", "actualizado_por", "actualizado_en"]],
                         hide_index=True, width="stretch", height=320, on_select="rerun", selection_mode="single-row",
                         key="bed_table",
                         column_config={"codigo": "Cama", "servicio": "Servicio", "ocupacion": "Ocupación (al corte)",
                                        "estado_operativo": "Estado operativo", "notas": "Notas",
                                        "actualizado_por": "Actualizó", "actualizado_en": "Fecha"})
    row = _selected(event, view)
    if row is not None and st.button(f"Cambiar estado de {row['codigo']}", icon=":material/edit:", key="bed_edit"):
        _bed_state(row)


# ── Inventory ────────────────────────────────────────────────────────────────

@st.dialog("Movimiento de inventario")
def _movement(row: pd.Series) -> None:
    actor = auth.guard("inventario.movimiento")
    if actor is None:
        return
    st.markdown(f"**{row['nombre']}** · stock actual **{num(row['stock'])}**")
    kind = st.segmented_control("Tipo", records.MOVEMENTS, default="Salida", key=f"mv_k{row['codigo_servicio']}")
    qty = st.number_input("Cantidad" if kind != "Ajuste" else "Stock contado", min_value=0.0, step=1.0,
                          key=f"mv_q{row['codigo_servicio']}")
    reason = st.text_input("Motivo", max_chars=200, key=f"mv_r{row['codigo_servicio']}",
                           placeholder="Compra, dispensación a UCI, inventario físico…")
    if st.button("Registrar", type="primary", icon=":material/save:"):
        _run(lambda: records.stock_movement(actor, row["codigo_servicio"], kind, qty, reason), "Movimiento registrado.")


@st.dialog("Ficha del ítem")
def _item(row: pd.Series) -> None:
    actor = auth.guard("inventario.editar")
    if actor is None:
        return
    st.markdown(f"**{row['nombre']}** · `{row['codigo_servicio']}`")
    minimum = st.number_input("Stock mínimo", min_value=0.0, value=float(row["stock_minimo"] or 0), step=1.0)
    expiry = st.date_input("Fecha de vencimiento", value=_ts(row["fecha_vencimiento"]), format="DD/MM/YYYY")
    lot = st.text_input("Lote", value=row["lote"] or "", max_chars=30)
    if st.button("Guardar", type="primary", icon=":material/save:"):
        _run(lambda: records.update_item(actor, row["codigo_servicio"], minimum or None, expiry, lot), "Ficha actualizada.")


@st.dialog("Nuevo ítem de inventario")
def _new_item() -> None:
    actor = auth.guard("inventario.editar")
    if actor is None:
        return
    code = st.text_input("Código", max_chars=20)
    name = st.text_input("Nombre", max_chars=200)
    c = st.columns(2)
    stock = c[0].number_input("Stock inicial", min_value=0.0, step=1.0)
    minimum = c[1].number_input("Stock mínimo", min_value=0.0, step=1.0)
    expiry = st.date_input("Vencimiento", value=None, format="DD/MM/YYYY")
    lot = st.text_input("Lote", max_chars=30)
    if st.button("Crear", type="primary", icon=":material/add:"):
        _run(lambda: records.create_item(actor, code, name, stock, minimum or None, expiry, lot), "Ítem creado.")


def _inventory_tab() -> None:
    inv = records.list_inventory()
    stats = get_data().inventory.set_index("codigo_servicio")
    inv["consumo_diario"] = inv["codigo_servicio"].map(stats["consumo_diario"])
    inv["dias"] = inv["codigo_servicio"].map(stats["dias_inventario"])
    inv["estado"] = inv["codigo_servicio"].map(stats["estado"]).astype(str)
    soon = pd.to_datetime(inv["fecha_vencimiento"]) <= pd.Timestamp.now() + pd.Timedelta(days=30)

    k = st.columns(4)
    k[0].metric("Ítems", num(len(inv)), border=True)
    k[1].metric("Críticos", num((inv["estado"] == "Crítico").sum()), border=True)
    k[2].metric("Con stock registrado", num((inv["origen"] == "Registrado").sum()), border=True,
                help="El resto conserva el stock simulado inicial")
    k[3].metric("Vencen en 30 días", num(soon.sum()), border=True)

    bar = st.container(horizontal=True, vertical_alignment="bottom")
    query = bar.text_input("Buscar", placeholder="Nombre o código", key="inv_q", width=280)
    status = bar.segmented_control("Estado", ["Todos", "Crítico", "Bajo", "Normal", "Sin consumo"], default="Todos", key="inv_s")
    if bar.button("Nuevo ítem", icon=":material/add:", key="inv_new"):
        _new_item()
    view = inv
    if query:
        view = view[(view["nombre"] + " " + view["codigo_servicio"]).str.contains(query, case=False, regex=False)]
    if status not in (None, "Todos"):
        view = view[view["estado"] == status]
    view = view.sort_values("dias", na_position="last").reset_index(drop=True)
    event = st.dataframe(
        view[["codigo_servicio", "nombre", "stock", "consumo_diario", "dias", "estado", "stock_minimo",
              "fecha_vencimiento", "lote", "origen", "actualizado_por_nombre"]],
        hide_index=True, width="stretch", height=330, on_select="rerun", selection_mode="single-row", key="inv_table",
        column_config={"codigo_servicio": "Código", "nombre": "Ítem", "stock": st.column_config.NumberColumn("Stock", format="%d"),
                       "consumo_diario": st.column_config.NumberColumn("Consumo/día", format="%.1f"),
                       "dias": st.column_config.ProgressColumn("Días", min_value=0, max_value=45, format="%.1f"),
                       "estado": "Estado", "stock_minimo": "Mínimo", "fecha_vencimiento": "Vence", "lote": "Lote",
                       "origen": "Origen", "actualizado_por_nombre": "Actualizó"})
    row = _selected(event, view)
    if row is not None:
        actions = st.container(horizontal=True)
        if actions.button("Entrada / salida / ajuste", icon=":material/swap_vert:", key="inv_mv"):
            _movement(row)
        if actions.button("Editar ficha", icon=":material/edit:", key="inv_edit"):
            _item(row)
    with st.expander("Movimientos recientes", icon=":material/history:"):
        st.dataframe(records.list_movements(200), hide_index=True, width="stretch")


# ── Surgeries ────────────────────────────────────────────────────────────────

def _surgery_form(row: pd.Series | None, prefix: str) -> dict:
    g = (lambda k, d=None: d if row is None or pd.isna(row.get(k)) else row.get(k))
    default_when = _ts(g("fecha_programada")) or datetime.combine(date.today() + timedelta(days=1), time(8, 0))
    c1, c2, c3 = st.columns([2, 1.2, 1])
    d = c1.date_input("Fecha", value=default_when.date(), key=f"{prefix}d", format="DD/MM/YYYY")
    t = c2.time_input("Hora", value=default_when.time(), key=f"{prefix}t", step=timedelta(minutes=15))
    duration = c3.number_input("Duración (min)", 15, 720, int(g("duracion_min", 60)), step=15, key=f"{prefix}dur")
    c1, c2 = st.columns(2)
    room = c1.selectbox("Quirófano", records.OPERATING_ROOMS,
                        index=records.OPERATING_ROOMS.index(g("quirofano", "Cirugia general")), key=f"{prefix}room")
    service = c2.selectbox("Servicio", records.SERVICES, index=records.SERVICES.index(g("servicio", "Recuperación")),
                           key=f"{prefix}serv")
    c1, c2 = st.columns([3, 1])
    procedure = c1.text_input("Procedimiento", value=g("procedimiento", ""), max_chars=200, key=f"{prefix}proc")
    cups = c2.text_input("CUPS", value=g("codigo_cups", "") or "", max_chars=10, key=f"{prefix}cups")
    doctors = _clinicians()
    surgeon = st.selectbox("Cirujano", [None] + doctors["id"].tolist(), key=f"{prefix}sur",
                           index=(doctors["id"].tolist().index(int(g("cirujano_id"))) + 1)
                           if g("cirujano_id") is not None and int(g("cirujano_id")) in doctors["id"].tolist() else 0,
                           format_func=lambda i: "Sin asignar" if i is None else doctors.set_index("id").at[i, "nombre"])
    active = records.list_admissions("Activo")
    options = [None] + active["id"].tolist()
    current = g("ingreso_id")
    admission = st.selectbox("Ingreso asociado", options, key=f"{prefix}adm",
                             index=options.index(int(current)) if current is not None and int(current) in options else 0,
                             format_func=lambda i: "Ninguno (ambulatoria)" if i is None else
                             f"#{i} · {active.set_index('id').at[i, 'paciente_ref']} · {active.set_index('id').at[i, 'servicio']}")
    return {"fecha_programada": datetime.combine(d, t), "duracion_min": duration, "quirofano": room, "servicio": service,
            "procedimiento": procedure, "codigo_cups": cups, "cirujano_id": surgeon, "ingreso_id": admission}


@st.dialog("Programar cirugía", width="large")
def _new_surgery() -> None:
    actor = auth.guard("cirugias.programar")
    if actor is None:
        return
    payload = _surgery_form(None, "cxn_")
    if st.button("Programar", type="primary", icon=":material/event_available:"):
        _run(lambda: records.schedule_surgery(actor, payload), "Cirugía programada.")


@st.dialog("Editar programación", width="large")
def _edit_surgery(row: pd.Series) -> None:
    actor = auth.guard("cirugias.programar")
    if actor is None:
        return
    payload = _surgery_form(row, f"cxe{row['id']}_")
    if st.button("Guardar", type="primary", icon=":material/save:"):
        _run(lambda: records.update_surgery(actor, int(row["id"]), payload), "Programación actualizada.")


@st.dialog("Cerrar cirugía")
def _close_surgery(row: pd.Series) -> None:
    actor = auth.guard("cirugias.cerrar")
    if actor is None:
        return
    st.markdown(f"**{row['procedimiento']}** · {row['quirofano']} · {row['fecha_programada']}")
    result = st.segmented_control("Resultado", ["Ejecutada", "Cancelada"], default="Ejecutada", key=f"cxc{row['id']}")
    reason = st.text_input("Motivo de cancelación", max_chars=200, key=f"cxr{row['id']}") if result == "Cancelada" else None
    if st.button("Confirmar", type="primary", icon=":material/task_alt:"):
        _run(lambda: records.close_surgery(actor, int(row["id"]), result, reason), f"Cirugía marcada como {result.lower()}.")


def _surgery_tab() -> None:
    cx = records.list_surgeries()
    cx["fecha_programada"] = pd.to_datetime(cx["fecha_programada"])
    upcoming = cx[(cx["estado"] == "Programada") & (cx["fecha_programada"] >= pd.Timestamp.now())]
    k = st.columns(4)
    k[0].metric("Programadas (próximas)", num(len(upcoming)), border=True)
    k[1].metric("Ejecutadas", num((cx["estado"] == "Ejecutada").sum()), border=True)
    k[2].metric("Canceladas", num((cx["estado"] == "Cancelada").sum()), border=True)
    overdue = cx[(cx["estado"] == "Programada") & (cx["fecha_programada"] < pd.Timestamp.now())]
    k[3].metric("Pendientes de cierre", num(len(overdue)), border=True, help="Fecha pasada y aún sin marcar ejecutada/cancelada")

    if len(upcoming):
        with st.container(border=True):
            components.section("Agenda de quirófanos", "Próximos 14 días")
            agenda = upcoming[upcoming["fecha_programada"] <= pd.Timestamp.now() + pd.Timedelta(days=14)].assign(
                fin=lambda d: d["fecha_programada"] + pd.to_timedelta(d["duracion_min"], unit="m"))
            fig = px.timeline(agenda, x_start="fecha_programada", x_end="fin", y="quirofano", color="servicio",
                              hover_data={"procedimiento": True, "cirujano": True}, color_discrete_sequence=charts.palette())
            charts.show(charts.style(fig, 240, legend=True))

    bar = st.container(horizontal=True, vertical_alignment="bottom")
    status = bar.segmented_control("Estado", ["Programada", "Ejecutada", "Cancelada", "Todas"], default="Programada", key="cx_state")
    if bar.button("Programar cirugía", type="primary", icon=":material/event_available:", key="cx_new"):
        _new_surgery()
    view = cx if status in (None, "Todas") else cx[cx["estado"] == status]
    view = view.reset_index(drop=True)
    event = st.dataframe(view[["id", "estado", "fecha_programada", "duracion_min", "quirofano", "procedimiento", "codigo_cups",
                               "servicio", "cirujano", "paciente_ref", "motivo_cancelacion"]],
                         hide_index=True, width="stretch", height=300, on_select="rerun", selection_mode="single-row",
                         key="cx_table",
                         column_config={"id": "#", "estado": "Estado",
                                        "fecha_programada": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY HH:mm"),
                                        "duracion_min": "Min", "quirofano": "Quirófano", "procedimiento": "Procedimiento",
                                        "codigo_cups": "CUPS", "servicio": "Servicio", "cirujano": "Cirujano",
                                        "paciente_ref": "Paciente", "motivo_cancelacion": "Motivo cancelación"})
    row = _selected(event, view)
    if row is not None and row["estado"] == "Programada":
        actions = st.container(horizontal=True)
        if actions.button("Editar", icon=":material/edit:", key="cx_edit"):
            _edit_surgery(row)
        if actions.button("Marcar ejecutada / cancelar", icon=":material/task_alt:", key="cx_close"):
            _close_surgery(row)


# ── Page ─────────────────────────────────────────────────────────────────────

def render() -> None:
    components.page_header("Gestión", "Registro clínico",
                           "Ingresa, edita y cierra registros. Cada cambio queda firmado por el funcionario en la auditoría.")
    if message := st.session_state.pop("records_flash", None):
        st.toast(message, icon=":material/check_circle:")
    actor = auth.current_user()
    if actor is None:
        st.info("Estás en modo consulta. Inicia sesión en el panel lateral para registrar o editar.", icon=":material/lock:")
    else:
        st.caption(f":material/verified_user: Registrando como **{actor.nombre}** ({actor.rol}).")

    tabs = st.tabs(["Ingresos y egresos", "Camas", "Inventario", "Cirugías"], key="records_tabs")
    with tabs[0]:
        _admissions_tab()
    with tabs[1]:
        _beds_tab()
    with tabs[2]:
        _inventory_tab()
    with tabs[3]:
        _surgery_tab()

    active = len(records.list_admissions("Activo"))
    state.publish_context("Registro clínico", {"Ingresos activos registrados": num(active)},
                          ["¿Cuántas camas de UCI están ocupadas hoy?", "¿Cuáles son los medicamentos con menos de 5 días de inventario?",
                           "¿Qué cambios se registraron hoy?"])
