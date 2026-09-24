"""Personal y accesos — staff registry (doctors, nurses, pharmacy, admissions…).

Only administrators manage accounts; everyone can see the roles matrix to
know who is allowed to do what.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import records
from core.fmt import num
from core.security import PERMISSION_LABELS, PERMISSIONS, ROLES, PermissionDenied
from ui import auth, components, state


def _save(action, message: str) -> None:
    try:
        action()
    except (records.ValidationError, PermissionDenied, ValueError) as exc:
        st.error(str(exc), icon=":material/error:")
        return
    st.session_state["staff_flash"] = message
    st.rerun()


@st.dialog("Nuevo funcionario")
def _new_staff() -> None:
    actor = auth.guard("personal.gestionar")
    if actor is None:
        return
    name = st.text_input("Nombre completo", max_chars=80)
    c = st.columns(2)
    role = c[0].selectbox("Rol", ROLES, index=1)
    reg = c[1].text_input("Registro profesional (RETHUS)", max_chars=30)
    service = st.selectbox("Servicio principal", [None] + records.SERVICES, format_func=lambda s: s or "Transversal")
    c = st.columns(2)
    pin = c[0].text_input("PIN inicial (4–8 dígitos)", type="password", max_chars=8)
    pin2 = c[1].text_input("Repite el PIN", type="password", max_chars=8)
    st.caption("El PIN se guarda cifrado (PBKDF2). Pide al funcionario cambiarlo en su primer ingreso.")
    if st.button("Registrar funcionario", type="primary", icon=":material/person_add:"):
        if pin != pin2:
            st.error("Los PIN no coinciden.")
            return
        _save(lambda: records.create_staff(actor, name, role, reg, service, pin), f"{name} registrado.")


@st.dialog("Editar funcionario")
def _edit_staff(row: pd.Series) -> None:
    actor = auth.guard("personal.gestionar")
    if actor is None:
        return
    name = st.text_input("Nombre completo", value=row["nombre"], max_chars=80)
    c = st.columns(2)
    role = c[0].selectbox("Rol", ROLES, index=ROLES.index(row["rol"]))
    reg = c[1].text_input("Registro profesional", value=row["registro_profesional"] or "", max_chars=30)
    services = [None] + records.SERVICES
    service = st.selectbox("Servicio principal", services, index=services.index(row["servicio"]) if row["servicio"] in services else 0,
                           format_func=lambda s: s or "Transversal")
    active = st.toggle("Cuenta activa", value=bool(row["activo"]))
    if st.button("Guardar", type="primary", icon=":material/save:"):
        _save(lambda: records.update_staff(actor, int(row["id"]), name, role, reg, service, active), "Funcionario actualizado.")
    st.divider()
    new_pin = st.text_input("Restablecer PIN", type="password", max_chars=8, placeholder="Nuevo PIN")
    if st.button("Restablecer PIN", icon=":material/key:"):
        _save(lambda: records.reset_pin(actor, int(row["id"]), new_pin), "PIN restablecido.")


def render() -> None:
    components.page_header("Gestión", "Personal y accesos",
                           "Registro de médicos, enfermería, farmacia y personal administrativo con permisos por rol.")
    if message := st.session_state.pop("staff_flash", None):
        st.toast(message, icon=":material/check_circle:")

    staff = records.list_staff()
    k = st.columns(4)
    k[0].metric("Funcionarios activos", num(staff["activo"].sum()), border=True)
    k[1].metric("Médicos", num(((staff["rol"] == "Médico") & (staff["activo"] == 1)).sum()), border=True)
    k[2].metric("Enfermería", num(((staff["rol"] == "Enfermería") & (staff["activo"] == 1)).sum()), border=True)
    k[3].metric("Con acceso reciente", num(staff["ultimo_acceso"].notna().sum()), border=True)

    actor = auth.current_user()
    can_manage = actor is not None and actor.can("personal.gestionar")
    with st.container(border=True):
        bar = st.container(horizontal=True, vertical_alignment="bottom")
        role_filter = bar.multiselect("Rol", ROLES, key="staff_roles", placeholder="Todos los roles", width=360)
        if can_manage and bar.button("Nuevo funcionario", type="primary", icon=":material/person_add:", key="staff_new"):
            _new_staff()
        view = staff[staff["rol"].isin(role_filter)] if role_filter else staff
        view = view.reset_index(drop=True)
        event = st.dataframe(
            view.assign(activo=view["activo"].astype(bool)),
            hide_index=True, width="stretch", height=300, key="staff_table",
            on_select="rerun" if can_manage else "ignore", selection_mode="single-row",
            column_config={"id": "#", "nombre": "Nombre", "rol": "Rol", "registro_profesional": "Registro",
                           "servicio": "Servicio", "activo": st.column_config.CheckboxColumn("Activo"),
                           "creado_en": "Creado", "ultimo_acceso": "Último acceso"})
        if can_manage:
            rows = event.selection.rows
            if rows and st.button(f"Editar a {view.iloc[rows[0]]['nombre']}", icon=":material/edit:", key="staff_edit"):
                _edit_staff(view.iloc[rows[0]])
        else:
            st.caption(":material/lock: Solo el rol Administrador crea o edita cuentas.")

    with st.container(border=True):
        components.section("Matriz de permisos", "Qué puede hacer cada rol")
        matrix = pd.DataFrame({role: ["✓" if role in allowed else "" for allowed in PERMISSIONS.values()] for role in ROLES},
                              index=[PERMISSION_LABELS[p] for p in PERMISSIONS])
        st.dataframe(matrix, width="stretch")
        st.caption("La consulta de tableros y el asistente no requieren sesión; toda escritura exige sesión y queda auditada.")

    state.publish_context("Personal y accesos", {"Funcionarios activos": num(staff["activo"].sum())},
                          ["¿Qué cambios se registraron hoy?", "Resumen de la situación"])
