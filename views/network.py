"""Flujos y redes — graph views of how patients and services connect."""

from __future__ import annotations

import streamlit as st

from core import kpis
from core.fmt import num, pct
from ui import charts, components, state


def render() -> None:
    fs = state.filters()
    components.page_header("Análisis de red", "Flujos de pacientes y red de servicios",
                           "Cómo entran los pacientes, a dónde van y qué especialidades dependen de qué áreas.", fs)

    links = kpis.patient_flow(fs)
    with st.container(border=True):
        components.section("Flujo de pacientes", "Vía de ingreso → clase → servicio → capítulo CIE-10")
        if links.empty:
            components.empty("Sin ingresos para estos filtros.")
        else:
            charts.show(charts.sankey(links, height=500))
            hosp = links[(links["etapa"] == "via_ingreso→clase_ingreso") & (links["destino"] == "Hospitalario")]["valor"].sum()
            total = links[links["etapa"] == "via_ingreso→clase_ingreso"]["valor"].sum()
            st.caption(f"{pct(hosp / total if total else 0)} de los ingresos terminan hospitalizados. "
                       "Pasa el cursor por un flujo para ver el volumen.")

    with st.container(border=True):
        components.section("Red especialidad ↔ área de servicio", "Tamaño = volumen · grosor implícito = co-ocurrencia")
        c1, c2 = st.columns(2)
        top = c1.slider("Nodos por tipo", 5, 15, 10, key="net_top")
        min_w = c2.select_slider("Peso mínimo de la relación", [10, 50, 100, 250, 500, 1000], value=100, key="net_w")
        edges = kpis.specialty_area_network(fs, top_nodes=top, min_weight=min_w)
        if edges.empty:
            components.empty("No hay relaciones con ese peso mínimo; bájalo para ver la red.")
            degree = None
        else:
            charts.show(charts.network(edges, height=540))
            degree = edges.groupby("area_servicio").size().sort_values(ascending=False)
            hub = degree.index[0]
            st.caption(f"Área más conectada: **{hub}** (atiende a {degree.iloc[0]} especialidades). "
                       "Es un punto crítico: su saturación impacta a todo el hospital.")
        with st.expander("Relaciones de la red", icon=":material/hub:"):
            st.dataframe(edges.sort_values("peso", ascending=False), hide_index=True, width="stretch",
                         column_config={"especialidad": "Especialidad", "area_servicio": "Área", "peso": "Servicios"})

    state.publish_context(
        "Flujos y redes",
        {"Relaciones": num(len(edges)), "Área hub": degree.index[0] if degree is not None else "—"},
        ["¿Qué especialidades son las más solicitadas?", "¿Qué servicio tiene más pacientes ingresados este mes?",
         "Distribución de ingresos por régimen"],
    )
