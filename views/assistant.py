"""Agente IA — full-page conversation (the core of the challenge)."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from core.agent import llm_available
from core.config import OLLAMA_MODEL, OLLAMA_URL
from ui import chat, components, state

# The four demo questions required by the brief (section "Demostración").
DEMO = [
    "¿Cuántas camas de UCI están ocupadas hoy?",
    "¿Cuáles son los medicamentos con menos de 5 días de inventario?",
    "¿Cuál es el tiempo de espera promedio en urgencias en la última semana?",
    "¿Qué servicio tiene más pacientes ingresados este mes?",
]
MORE = ["Resumen de la situación", "Dame las alertas activas", "¿Por qué aumentó el tiempo de espera?",
        "¿Qué se espera para la próxima semana?"]


def _transcript() -> str:
    lines = [f"# Conversación HospitalIQ · {datetime.now():%Y-%m-%d %H:%M}", ""]
    for m in state.messages():
        who = "Usuario" if m["role"] == "user" else "HospitalIQ"
        lines += [f"**{who}:** {m['content']}", ""]
    return "\n".join(lines)


def render() -> None:
    fs = state.filters()
    components.page_header("Agente IA", "Asistente conversacional",
                           "Pregunta en lenguaje natural; la respuesta usa los filtros activos del panel lateral.", fs)
    state.publish_context("Agente IA", {}, DEMO)

    main, side = st.columns([2.5, 1], gap="medium")
    with side:
        with st.container(border=True):
            components.section("Motor del agente")
            st.segmented_control("Modo", chat.MODES, key="agent_mode", label_visibility="collapsed", width="stretch")
            if llm_available():
                st.caption(f":material/check_circle: Ollama activo · `{OLLAMA_MODEL}`. Las preguntas abiertas se "
                           "traducen a SQL (NL2SQL), se validan y se ejecutan en modo solo lectura.")
            else:
                st.caption(f":material/info: Ollama no responde en `{OLLAMA_URL}`. Se usa el **motor de reglas** "
                           "(Plan B del reto): instantáneo y con los mismos KPIs del tablero.")
        with st.container(border=True):
            components.section("Preguntas del reto")
            for i, q in enumerate(DEMO):
                st.button(q, key=f"demo_{i}", on_click=state.ask, args=(q,), width="stretch", icon=":material/bolt:")
            components.section("Más ideas")
            for i, q in enumerate(MORE):
                st.button(q, key=f"more_{i}", on_click=state.ask, args=(q,), width="stretch", type="tertiary")
        with st.container(border=True):
            components.section("Privacidad y seguridad")
            st.caption("• Nunca entrega nombres, documentos, fechas de nacimiento ni diagnósticos de un paciente.  \n"
                       "• SQL generado: solo `SELECT`, tablas permitidas, sin columnas sensibles y con `LIMIT`.  \n"
                       "• Base analítica en memoria y en modo solo lectura.")
            c1, c2 = st.columns(2)
            c1.download_button("Exportar", _transcript(), file_name="conversacion_hospitaliq.md",
                               icon=":material/download:", width="stretch")
            c2.button("Nueva", on_click=state.reset_chat, icon=":material/add_comment:", width="stretch")

    with main:
        box = st.container(height=640, border=True)
    prompt = st.chat_input("Escribe tu consulta… ej. «¿cuántas camas de pediatría están libres hoy?»", key="page_input")
    chat.conversation(box, prompt or state.pop_pending(), compact=False, prefix="p")
