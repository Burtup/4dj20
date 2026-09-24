"""Reusable presentation components (HTML + native Streamlit widgets).

All custom HTML uses the ``--hiq-*`` CSS variables from :mod:`ui.theme`, so
every component follows light/dark mode automatically. Any dynamic text
inserted into HTML is escaped.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from core.filters import FilterState
from core.insights import Insight
from ui import state

SEVERITY_LABEL = {"critical": "Crítica", "serious": "Alta", "warning": "Media", "info": "Informativa"}


def page_header(kicker: str, title: str, subtitle: str, fs: FilterState | None = None) -> None:
    chips = ""
    if fs is not None:
        parts = fs.describe().split(" · ")
        chips = "".join(
            f'<span class="hiq-chip">{"<b>Periodo</b> " if i == 0 else ""}{escape(p)}</span>'
            for i, p in enumerate(parts))
        chips = f'<div class="hiq-chips">{chips}</div>'
    st.markdown(
        f'<p class="hiq-kicker">{escape(kicker)}</p><p class="hiq-title">{escape(title)}</p>'
        f'<p class="hiq-sub">{escape(subtitle)}</p>{chips}',
        unsafe_allow_html=True,
    )


def section(title: str, note: str = "") -> None:
    st.markdown(f'<div class="hiq-section"><h4>{escape(title)}</h4><span>{escape(note)}</span></div>',
                unsafe_allow_html=True)


def hero(title: str, subtitle: str, pills: list[tuple[str, str]]) -> None:
    """Brand banner. ``pills`` = [(text, css color)]."""
    items = "".join(f'<span class="hiq-pill"><span class="hiq-dot" style="background:{c}"></span>{escape(t)}</span>'
                    for t, c in pills)
    st.markdown(f'<div class="hiq-hero"><div><h2>{escape(title)}</h2><p>{escape(subtitle)}</p></div>'
                f'<div class="hiq-pills">{items}</div></div>', unsafe_allow_html=True)


def alert_cards(items: list[Insight], limit: int = 5) -> None:
    """Prioritised insight cards, each with a hand-off to the assistant."""
    if not items:
        st.success("Sin alertas activas con los filtros actuales.", icon=":material/verified:")
        return
    for item in items[:limit]:
        st.markdown(
            f'<div class="hiq-alert {item.severity}"><span class="tag">{escape(item.category)} · '
            f'{SEVERITY_LABEL.get(item.severity, "")}</span><div class="t">{escape(item.title)}</div>'
            f'<div class="d">{escape(item.detail)}</div><div class="a">→ {escape(item.action)}</div></div>',
            unsafe_allow_html=True,
        )
    if len(items) > limit:
        st.caption(f"+{len(items) - limit} alertas más · pregunta «Dame las alertas activas» al asistente.")


def ask_button(label: str, question: str, key: str, icon: str = ":material/forum:") -> None:
    """Button that sends ``question`` to the assistant dock."""
    st.button(label, key=key, icon=icon, type="tertiary", on_click=state.ask, args=(question,))


def empty(message: str) -> None:
    st.info(message, icon=":material/filter_alt_off:")
