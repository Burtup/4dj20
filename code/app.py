"""HospitalIQ — Hospital Susana López de Valencia.

Entry point of the Streamlit frontend::

    streamlit run app.py

Layout of every page::

    ┌──────────── sidebar ───────────┐┌────────── main ──────────┐┌─ assistant dock ─┐
    │ logo · navigation (4 groups)   ││ page header + KPIs       ││ context chip     │
    │ staff session (PIN)            ││ charts / tables / maps   ││ conversation     │
    │ global filters (3 layers)      ││ forms (Gestión group)    ││ suggestions      │
    │ system status                  ││                          ││                  │
    └────────────────────────────────┘└──────────────────────────┘└──────────────────┘

The dock is sticky (always at hand) and receives the page's context, so the
assistant answers about exactly what the user is looking at. On the
"Agente IA" page the conversation takes the whole width instead.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.data import get_data
from ui import chat, sidebar, theme
from views import (assistant, audit, beds, data_hub, demand, emergency, network, overview, pharmacy, staff,
                   surgery)
from views import records as records_view

st.set_page_config(page_title="HospitalIQ · HSLV", page_icon=":material/local_hospital:", layout="wide",
                   initial_sidebar_state="expanded")
theme.inject()
ASSETS = Path(__file__).resolve().parent / "assets"
st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"), size="large")

ss = st.session_state
ss.setdefault("chat_open", True)
ss.setdefault("agent_mode", "Automático")

# First run without processed data → onboarding screen with the uploader.
try:
    with st.spinner("Cargando y enriqueciendo los datos del HIS…"):
        data = get_data()
except FileNotFoundError:
    st.navigation([st.Page(data_hub.render_setup, title="Primer uso", icon=":material/upload:")]).run()
    st.stop()

Page = st.Page
pages = {
    "Operación": [
        Page(overview.render, title="Panorama", icon=":material/dashboard:", default=True),
        Page(beds.render, title="Camas", icon=":material/bed:", url_path="camas"),
        Page(emergency.render, title="Urgencias", icon=":material/emergency:", url_path="urgencias"),
        Page(surgery.render, title="Cirugías", icon=":material/surgical:", url_path="cirugias"),
        Page(pharmacy.render, title="Farmacia", icon=":material/medication:", url_path="farmacia"),
    ],
    "Análisis": [
        Page(demand.render, title="Demanda y pronóstico", icon=":material/trending_up:", url_path="demanda"),
        Page(network.render, title="Flujos y redes", icon=":material/hub:", url_path="redes"),
    ],
    "Asistente": [Page(assistant.render, title="Agente IA", icon=":material/smart_toy:", url_path="agente")],
    "Gestión": [
        Page(records_view.render, title="Registro clínico", icon=":material/edit_note:", url_path="registro"),
        Page(staff.render, title="Personal y accesos", icon=":material/badge:", url_path="personal"),
        Page(audit.render, title="Auditoría", icon=":material/policy:", url_path="auditoria"),
        Page(data_hub.render, title="Centro de datos", icon=":material/database:", url_path="datos"),
    ],
}
page = st.navigation(pages)
sidebar.render(data)

full_chat = page.url_path == "agente"
show_dock = ss["chat_open"] and not full_chat

if show_dock:
    main, dock = st.columns([7, 3], gap="medium")
else:
    main, dock = st.container(), None

with main:
    if not ss["chat_open"] and not full_chat:
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button("Asistente IA", icon=":material/forum:", type="primary",
                      on_click=lambda: ss.update(chat_open=True), key="open_dock")
    page.run()

if dock is not None:
    with dock:
        chat.render_dock()
