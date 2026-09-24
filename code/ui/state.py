"""Session state helpers: filters, view context and assistant hand-off.

The *view context* is how the assistant "sees" what the user sees: every
view publishes its name, the headline numbers on screen and contextual
question suggestions. The chat reads it on the same script run, right after
the page renders, so it is never stale.
"""

from __future__ import annotations

import streamlit as st

from core.filters import FilterState

WELCOME = ("Hola, soy **HospitalIQ**. Veo lo mismo que tú: la vista abierta y los filtros activos. "
           "Pregúntame por camas, urgencias, cirugías, farmacia, demanda o pronósticos.")


def filters() -> FilterState:
    return st.session_state["filters"]


def publish_context(view: str, highlights: dict | None = None, suggestions: list[str] | None = None) -> None:
    st.session_state["view_ctx"] = {
        "view": view,
        "highlights": highlights or {},
        "suggestions": suggestions or [],
    }


def view_context() -> dict:
    return st.session_state.get("view_ctx", {"view": "", "highlights": {}, "suggestions": []})


def messages() -> list[dict]:
    if "messages" not in st.session_state:
        st.session_state["messages"] = [{"role": "assistant", "content": WELCOME, "answer": None}]
    return st.session_state["messages"]


def reset_chat() -> None:
    st.session_state.pop("messages", None)


def ask(question: str) -> None:
    """Queue a question for the assistant (used by 'Preguntar' buttons)."""
    st.session_state["pending_question"] = question
    st.session_state["chat_open"] = True


def pop_pending() -> str | None:
    return st.session_state.pop("pending_question", None)
