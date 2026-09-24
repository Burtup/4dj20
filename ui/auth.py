"""Staff session in the sidebar: sign in with PIN, change PIN, sign out.

Dashboards are readable without signing in; any create/edit action needs a
session and the right role. The actor is re-validated against the database
on every run, so deactivating a user or changing their role takes effect
immediately.
"""

from __future__ import annotations

import time
from html import escape

import streamlit as st

from core import db, records
from core.security import PERMISSION_LABELS, Actor, validate_pin

MAX_ATTEMPTS = 5
LOCK_SECONDS = 300


def current_user() -> Actor | None:
    actor: Actor | None = st.session_state.get("actor")
    if actor is None:
        return None
    row = db.query("SELECT nombre, rol, activo FROM personal WHERE id = ?", (actor.id,))
    if not row or not row[0]["activo"]:
        st.session_state.pop("actor", None)
        return None
    fresh = Actor(actor.id, row[0]["nombre"], row[0]["rol"])
    st.session_state["actor"] = fresh
    return fresh


def _login(staff_id: int, pin: str) -> None:
    ss = st.session_state
    if ss.get("login_locked_until", 0) > time.time():
        ss["login_error"] = "Demasiados intentos. Espera unos minutos."
        return
    actor = records.authenticate(staff_id, pin)
    if actor is None:
        ss["login_attempts"] = ss.get("login_attempts", 0) + 1
        if ss["login_attempts"] >= MAX_ATTEMPTS:
            ss["login_locked_until"] = time.time() + LOCK_SECONDS
            ss["login_attempts"] = 0
        ss["login_error"] = "PIN incorrecto o usuario inactivo."
        return
    ss.update(actor=actor, login_attempts=0, login_error=None)


def _logout() -> None:
    actor = st.session_state.pop("actor", None)
    if actor:
        records.log_logout(actor)


def render_session_box() -> Actor | None:
    """Sidebar widget. Returns the signed-in actor (or None)."""
    actor = current_user()
    if actor:
        st.markdown(f":material/badge: **{escape(actor.nombre)}**  \n"
                    f"<span style='opacity:.75;font-size:.8rem'>{escape(actor.rol)}</span>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1.popover("Mi PIN", icon=":material/key:", width="stretch"):
            with st.form("own_pin", clear_on_submit=True, border=False):
                new = st.text_input("Nuevo PIN", type="password", max_chars=8)
                repeat = st.text_input("Repite el PIN", type="password", max_chars=8)
                if st.form_submit_button("Cambiar", type="primary"):
                    try:
                        if new != repeat:
                            raise records.ValidationError("Los PIN no coinciden.")
                        records.reset_pin(actor, actor.id, validate_pin(new))
                        st.success("PIN actualizado.")
                    except ValueError as exc:
                        st.error(str(exc))
        c2.button("Salir", icon=":material/logout:", on_click=_logout, width="stretch", key="logout_btn")
        return actor

    with st.popover("Iniciar sesión", icon=":material/login:", width="stretch"):
        staff = records.list_staff(active_only=True)
        with st.form("login", border=False):
            who = st.selectbox("Funcionario", staff["id"].tolist(),
                               format_func=lambda i: f"{staff.set_index('id').at[i, 'nombre']} · {staff.set_index('id').at[i, 'rol']}")
            pin = st.text_input("PIN", type="password", max_chars=8)
            if st.form_submit_button("Entrar", type="primary", width="stretch"):
                _login(int(who), pin)
                if st.session_state.get("actor"):
                    st.rerun()
        if st.session_state.get("login_error"):
            st.error(st.session_state["login_error"])
        st.caption("Cuentas demo con PIN 1234 (cámbialo en «Mi PIN»).")
    return None


def guard(permission: str) -> Actor | None:
    """Actor if allowed to ``permission``; otherwise shows why and returns None."""
    actor = current_user()
    if actor is None:
        st.info("Inicia sesión en el panel lateral para registrar o modificar información.", icon=":material/lock:")
        return None
    if not actor.can(permission):
        st.warning(f"Tu rol ({actor.rol}) no tiene permiso para: {PERMISSION_LABELS.get(permission, permission)}.",
                   icon=":material/block:")
        return None
    return actor
