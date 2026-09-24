"""Assistant UI: the always-available dock and the full-page chat.

Both surfaces share one conversation (``st.session_state["messages"]``) and
the same agent. Every question is answered with:

* the **filters** the user has active in the sidebar, and
* the **view context** published by the open page (name, headline numbers,
  suggestions) — so "¿y aquí qué pasa?" means *what I'm looking at*.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from core.agent import AgentAnswer, HospitalAgent, llm_available
from core.config import OLLAMA_MODEL
from ui import charts, state

MODES = ["Automático", "Solo reglas", "Forzar NL2SQL"]
SOURCE_LABEL = {"reglas": "Motor de reglas", "nl2sql": f"NL2SQL · {OLLAMA_MODEL}",
                "privacidad": "Guardia de privacidad", "ayuda": "Ayuda"}
METHOD = {
    "camas": "Censo de medianoche por servicio: episodios en cama a las 00:00. Egreso estimado = último cargo "
             "del episodio (o egreso real si se registró en la app). Capacidad = máx(camas en servicio del "
             "registro de camas, p95 del censo).",
    "inventario": "Días de inventario = stock ÷ consumo diario promedio de los últimos 30 días.",
    "rotacion": "Unidades dispensadas en el periodo; clasificación ABC de Pareto (A = 80 % del volumen).",
    "espera": "Minutos entre la hora de triage y la primera atención médica (tabla Atención), por nivel 1–5.",
    "llegadas": "Ingresos por día de la semana × hora, divididos por el número de semanas del periodo.",
    "causa_espera": "Compara el volumen diario por turno × nivel de triage contra el periodo anterior de igual duración.",
    "cirugia": "Programadas = consecutivos únicos. Ejecutada = el ingreso tiene un servicio de quirófano o el código facturado.",
    "servicio_demanda": "Ingresos agrupados por el grupo de cama asignado al episodio.",
    "especialidad": "Suma de cantidades de la tabla Servicios por especialidad que ordenó.",
    "diagnosticos": "Diagnóstico principal CIE-10 del ingreso, agrupado por código y capítulo.",
    "pronostico": "Pronóstico estacional: nivel de los últimos 28 días × factor del día de la semana; banda ±1,28 σ.",
    "alertas": "Motor de reglas: ocupación ≥ 85 %, stock < 5 días, picos de consumo ≥ 25 %, tendencias ≥ 20 %.",
    "demografia": "Distribución de ingresos por atributo del paciente (sin datos identificables).",
    "ingresos": "Conteo de episodios (tabla Ingresos) en el periodo.",
    "resumen": "KPIs del tablero para los filtros activos.",
    "registros": "Conteo de eventos de la auditoría (sin nombres), excluyendo inicios y cierres de sesión.",
}


def agent() -> HospitalAgent:
    return HospitalAgent(use_llm=st.session_state.get("agent_mode", "Automático") != "Solo reglas")


def _answer(question: str) -> AgentAnswer:
    force = st.session_state.get("agent_mode") == "Forzar NL2SQL"
    return agent().ask(question, state.filters(), state.view_context(), force_llm=force)


def _queue_from(key: str) -> None:
    """Callback for suggestion pills: send the choice, then reset the widget."""
    value = st.session_state.get(key)
    if value:
        state.ask(value)
    st.session_state["pill_nonce"] = st.session_state.get("pill_nonce", 0) + 1


def render_answer(answer: AgentAnswer, key: str, compact: bool, last: bool) -> None:
    st.markdown(answer.text)
    if answer.chart is not None:
        fig = charts.from_spec(answer.chart, height=220 if compact else 320)
        if fig is not None:
            charts.show(fig, key=f"{key}_fig")
    if answer.table is not None and len(answer.table):
        with st.expander(f"Tabla de resultados · {len(answer.table)} filas", icon=":material/table_view:"):
            st.dataframe(answer.table, hide_index=True, width="stretch")
    meta = f"{SOURCE_LABEL.get(answer.source, answer.source)} · {answer.elapsed_ms} ms"
    with st.popover("¿Cómo lo calculé?", icon=":material/info:", type="tertiary"):
        st.caption(meta)
        st.markdown(f"**Alcance:** {answer.scope}")
        if answer.sql:
            st.code(answer.sql, language="sql")
        elif answer.intent in METHOD:
            st.markdown(METHOD[answer.intent])
    if last and answer.followups:
        pill_key = f"follow_{st.session_state.get('pill_nonce', 0)}_{key}"
        st.pills("Continuar con", answer.followups[:3], key=pill_key, on_change=_queue_from,
                 args=(pill_key,), label_visibility="collapsed")


def conversation(box, question: str | None, compact: bool, prefix: str) -> None:
    """Render history into ``box`` and, if given, answer ``question``."""
    history = state.messages()
    with box:
        for i, msg in enumerate(history):
            with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user" else ":material/health_and_safety:"):
                if msg.get("answer") is not None:
                    render_answer(msg["answer"], f"{prefix}{i}", compact, last=(i == len(history) - 1 and not question))
                else:
                    st.markdown(msg["content"])
        if question:
            history.append({"role": "user", "content": question, "answer": None})
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(question)
            with st.chat_message("assistant", avatar=":material/health_and_safety:"):
                with st.spinner("Analizando con los filtros de tu vista…"):
                    answer = _answer(question)
                render_answer(answer, f"{prefix}{len(history)}", compact, last=True)
            history.append({"role": "assistant", "content": answer.text, "answer": answer})


def _suggestions(prefix: str) -> None:
    ctx = state.view_context()
    options = ctx.get("suggestions") or []
    if options:
        key = f"{prefix}_sugg_{st.session_state.get('pill_nonce', 0)}"
        st.pills("Sugerencias para esta vista", options[:4], key=key, on_change=_queue_from, args=(key,),
                 label_visibility="collapsed")


def status_line() -> str:
    mode = st.session_state.get("agent_mode", "Automático")
    engine = f"NL2SQL {OLLAMA_MODEL}" if llm_available() and mode != "Solo reglas" else "motor de reglas"
    return f"En línea · {engine}"


def render_dock() -> None:
    """Compact assistant, docked on the right of every page."""
    ctx = state.view_context()
    with st.container(border=True, key="chat_dock"):
        head, actions = st.columns([5, 2], vertical_alignment="center")
        head.markdown(
            f'<div class="hiq-agent-head"><div class="hiq-avatar">IQ</div><div><div class="n">Asistente HospitalIQ</div>'
            f'<div class="s">● {status_line()}</div></div></div>', unsafe_allow_html=True)
        with actions:
            c1, c2 = st.columns(2, gap="small")
            c1.button("", icon=":material/add_comment:", key="dock_new", help="Nueva conversación",
                      on_click=state.reset_chat, type="tertiary")
            c2.button("", icon=":material/close:", key="dock_close", help="Ocultar asistente",
                      on_click=lambda: st.session_state.update(chat_open=False), type="tertiary")
        highlights = escape(" · ".join(f"{k}: {v}" for k, v in list(ctx.get("highlights", {}).items())[:3]))
        st.markdown(
            f'<div class="hiq-context">👁 Viendo <b>{escape(ctx.get("view", ""))}</b> · '
            f'{escape(state.filters().describe())}{"<br>" + highlights if highlights else ""}</div>',
            unsafe_allow_html=True)
        box = st.container(height=430, border=False)
        _suggestions("dock")
        prompt = st.chat_input("Pregunta sobre lo que estás viendo…", key="dock_input")
        conversation(box, prompt or state.pop_pending(), compact=True, prefix="d")
