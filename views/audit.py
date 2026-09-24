"""Auditoría — immutable trail of every sign-in and every record change."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core import records
from core.fmt import num
from ui import auth, components, state

ENTITIES = {"ingresos": "Ingresos", "camas": "Camas", "inventario": "Inventario", "cirugias": "Cirugías", "personal": "Personal"}


def _diff(before: str | None, after: str | None) -> pd.DataFrame:
    """Field-level comparison of the before/after JSON snapshots."""
    b = json.loads(before) if before else {}
    a = json.loads(after) if after else {}
    rows = [{"Campo": k, "Antes": b.get(k), "Después": a.get(k)} for k in sorted(set(a) | set(b))
            if b.get(k) != a.get(k) and k not in {"actualizado_en", "actualizado_por"}]
    return pd.DataFrame(rows)


def render() -> None:
    components.page_header("Gestión", "Auditoría",
                           "Quién hizo qué y cuándo. El registro es inmutable: no se puede editar ni borrar.")
    actor = auth.guard("auditoria.ver")
    if actor is None:
        state.publish_context("Auditoría", {}, ["¿Qué cambios se registraron hoy?"])
        return

    staff = records.list_staff()
    bar = st.container(horizontal=True, vertical_alignment="bottom")
    entity = bar.selectbox("Entidad", [None] + list(ENTITIES), format_func=lambda e: "Todas" if e is None else ENTITIES[e],
                           key="aud_entity", width=200)
    who = bar.selectbox("Responsable", [None] + staff["id"].tolist(), key="aud_who", width=260,
                        format_func=lambda i: "Todos" if i is None else staff.set_index("id").at[i, "nombre"])
    since = bar.date_input("Desde", value=date.today() - timedelta(days=7), key="aud_since", format="DD/MM/YYYY", width=160)
    log = records.audit_log(entity, who, since.strftime("%Y-%m-%d") if since else None)

    k = st.columns(4)
    k[0].metric("Eventos", num(len(log)), border=True)
    k[1].metric("Registros creados", num((log["accion"] == "CREAR").sum()), border=True)
    k[2].metric("Ediciones / cierres", num(log["accion"].isin(["EDITAR", "EGRESO", "EJECUTAR", "CANCELAR"]).sum()), border=True)
    k[3].metric("Inicios fallidos", num((log["accion"] == "INICIO_FALLIDO").sum()), border=True,
                help="Intentos de acceso con PIN incorrecto")

    event = st.dataframe(log[["id", "fecha", "actor_nombre", "actor_rol", "accion", "entidad", "registro_id", "detalle"]],
                         hide_index=True, width="stretch", height=360, on_select="rerun", selection_mode="single-row",
                         key="aud_table",
                         column_config={"id": "#", "fecha": "Fecha", "actor_nombre": "Responsable", "actor_rol": "Rol",
                                        "accion": "Acción", "entidad": "Entidad", "registro_id": "Registro",
                                        "detalle": "Detalle"})
    rows = event.selection.rows
    if rows:
        entry = log.iloc[rows[0]]
        with st.container(border=True):
            components.section(f"Evento #{entry['id']} · {entry['accion']} {entry['entidad']} {entry['registro_id'] or ''}",
                               f"{entry['actor_nombre']} · {entry['fecha']}")
            diff = _diff(entry["antes"], entry["despues"])
            if len(diff):
                st.dataframe(diff.astype(str), hide_index=True, width="stretch")
            else:
                st.caption("Sin cambios de campos (evento de sesión o carga masiva).")
    st.download_button("Exportar auditoría (CSV)", log.to_csv(index=False).encode("utf-8"), "auditoria.csv",
                       icon=":material/download:")
    state.publish_context("Auditoría", {"Eventos (filtro)": num(len(log))}, ["¿Qué cambios se registraron hoy?"])
