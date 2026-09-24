"""Sidebar: staff session, global filter layers and system status.

Filter layers (applied in this order, all cascading to every view and to the
assistant):
1. Period — quick presets (segmented control) or a custom date range.
2. Service — bed group (UCI, Pediatría, …).
3. More filters — route, class, regime, sex, age group, zone.
"""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from core.agent import llm_available
from core.config import OLLAMA_MODEL
from core.data import HospitalData
from core.filters import FilterState
from core.fmt import es_date
from ui import auth

PRESETS = {"7 d": 7, "30 d": 30, "90 d": 90, "Todo": None}
DIMENSIONS = [  # (session key, label, admissions column)
    ("f_routes", "Vía de ingreso", "via_ingreso"),
    ("f_classes", "Clase de ingreso", "clase_ingreso"),
    ("f_regimes", "Régimen", "regimen"),
    ("f_sexes", "Sexo", "sexo"),
    ("f_ages", "Grupo de edad", "grupo_etario"),
    ("f_zones", "Zona", "zona"),
]


def _apply_preset(ref, first) -> None:
    days = PRESETS.get(st.session_state.get("f_preset"))
    start = first if days is None else max(first, ref - timedelta(days=days - 1))
    st.session_state["f_dates"] = (start, ref)


def _reset(ref, first) -> None:
    for key, *_ in DIMENSIONS:
        st.session_state[key] = []
    st.session_state["f_services"] = []
    st.session_state["f_preset"] = "30 d"
    _apply_preset(ref, first)


def _options(data: HospitalData, column: str) -> list[str]:
    series = data.admissions[column]
    return [str(v) for v in (series.cat.categories if hasattr(series, "cat") else sorted(series.dropna().unique()))]


def render(data: HospitalData) -> FilterState:
    ref, first = data.reference_date.date(), data.min_date.date()
    ss = st.session_state
    if "f_dates" not in ss:
        ss["f_preset"] = "30 d"
        _apply_preset(ref, first)
    elif ss.get("f_ref") != ref and ss.get("f_preset"):
        _apply_preset(ref, first)  # the cut-off moved (new records): presets follow it
    ss["f_ref"] = ref

    with st.sidebar:
        auth.render_session_box()
        st.divider()
        st.markdown("**Filtros globales**")
        st.segmented_control("Periodo", list(PRESETS), key="f_preset", on_change=_apply_preset,
                             args=(ref, first), label_visibility="collapsed", width="stretch")
        st.date_input("Rango de fechas", key="f_dates", min_value=first, max_value=ref, format="DD/MM/YYYY",
                      on_change=lambda: ss.update(f_preset=None))
        st.multiselect("Servicio", list(data.bed_capacity.index), key="f_services", placeholder="Todos los servicios")

        active = sum(bool(ss.get(k)) for k, *_ in DIMENSIONS)
        with st.expander(f"Más filtros{f' · {active} activos' if active else ''}", icon=":material/tune:"):
            for key, label, column in DIMENSIONS:
                st.multiselect(label, _options(data, column), key=key, placeholder="Todos")
        st.button("Limpiar filtros", icon=":material/restart_alt:", on_click=_reset, args=(ref, first),
                  type="tertiary")

        st.divider()
        engine = f"NL2SQL · {OLLAMA_MODEL}" if llm_available() else "Motor de reglas (Plan B)"
        inventory = "mayormente simulado" if data.inventory_is_simulated else "registrado"
        st.caption(
            f":material/database: Corte de datos **{es_date(ref)}**  \n"
            f":material/history: HIS hasta {es_date(data.his_cutoff)}; después, registros de la app  \n"
            f":material/smart_toy: Agente: **{engine}**  \n"
            f":material/inventory_2: Inventario: **{inventory}**  \n"
            f":material/lock: Datos anonimizados"
        )

    dates = ss["f_dates"]
    start, end = (dates[0], dates[1]) if len(dates) == 2 else (dates[0], dates[0])
    fs = FilterState(
        date_from=start, date_to=end,
        services=tuple(ss.get("f_services", [])),
        routes=tuple(ss.get("f_routes", [])), classes=tuple(ss.get("f_classes", [])),
        regimes=tuple(ss.get("f_regimes", [])), sexes=tuple(ss.get("f_sexes", [])),
        age_groups=tuple(ss.get("f_ages", [])), zones=tuple(ss.get("f_zones", [])),
    )
    ss["filters"] = fs
    return fs
