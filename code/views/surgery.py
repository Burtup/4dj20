"""Cirugías — operating-room efficiency (brief KPI: realizadas vs programadas)."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import insights, kpis
from core.data import get_data, pseudonym
from core.filters import filter_admissions
from core.fmt import num, pct
from ui import charts, components, state


def _stacked(df, height: int = 300) -> go.Figure:
    fig = go.Figure()
    order = df.groupby("quirofano")["procedimientos"].sum().sort_values(ascending=False).index
    for i, room in enumerate(order):
        g = df[df["quirofano"] == room]
        fig.add_trace(go.Bar(x=g["semana"], y=g["procedimientos"], name=room,
                             marker_color=charts.palette()[i % 6],
                             hovertemplate="Semana %{x|%d %b}: %{y}<extra>" + room + "</extra>"))
    fig.update_layout(barmode="stack", bargap=0.2)
    return charts.style(fig, height, legend=True)


def render() -> None:
    fs = state.filters()
    data = get_data()
    components.page_header("Quirófanos", "Programación quirúrgica",
                           "Cirugías programadas vs ejecutadas, uso de quirófanos y franjas disponibles.", fs)
    s = kpis.surgery_summary(fs)

    k = st.columns(5)
    k[0].metric("Programadas", num(s["programadas"]), border=True)
    k[1].metric("Ejecutadas", num(s["ejecutadas"]), border=True)
    k[2].metric("Cumplimiento", pct(s["cumplimiento"]), border=True,
                help="Ejecutadas ÷ programadas, para ingresos del periodo")
    k[3].metric("Sin ejecución registrada", num(s["no_ejecutadas"]), border=True)
    k[4].metric("Programaciones sin ingreso", num(s["sin_ingreso"]), border=True,
                help="Cirugías programadas sin admisión asociada en el extracto (no se pueden fechar)")

    left, right = st.columns([1.4, 1], gap="medium")
    with left.container(border=True):
        components.section("Procedimientos por semana", "Eventos distintos en quirófano")
        weekly = kpis.or_weekly(fs)
        if len(weekly):
            charts.show(_stacked(weekly))
        else:
            components.empty("Sin actividad de quirófano en el periodo.")
    with right.container(border=True):
        components.section("Ejecutadas por quirófano", "Cirugías programadas cumplidas")
        if len(s["por_quirofano"]):
            charts.show(charts.hbar(s["por_quirofano"], "ejecutadas", "quirofano", height=300))

    left, right = st.columns([1.4, 1], gap="medium")
    with left.container(border=True):
        components.section("Uso de quirófanos por día y hora", "Procedimientos promedio por semana")
        grid = kpis.or_usage_heatmap(fs).copy()
        grid.columns = [f"{h:02d}h" for h in grid.columns]
        charts.show(charts.heatmap(grid, fmt=".1f", height=280))
    with right.container(border=True, height=372):
        components.section("Optimización de agenda", "Recomendaciones")
        components.alert_cards(insights.generate(fs, "Cirugía"), limit=3)
        components.ask_button("¿Cómo optimizo la programación?", "¿Cuántas cirugías fueron programadas vs ejecutadas?",
                              key="cx_ask")

    with st.container(border=True):
        components.section("Detalle de programaciones", "Episodios anonimizados")
        oids = filter_admissions(data, fs)[["oid_ingreso", "fecha_ingreso", "servicio"]]
        table = data.surgeries.merge(oids, on="oid_ingreso", how="inner")
        c1, c2 = st.columns([3, 2])
        query = c1.text_input("Buscar quirófano o servicio", key="cx_q", placeholder="Ej. ortopedia, UCI…")
        status = c2.segmented_control("Estado", ["Todas", "Ejecutada", "Sin ejecución"], default="Todas", key="cx_status")
        table["Estado"] = table["ejecutada"].map({True: "Ejecutada", False: "Sin ejecución"})
        if status and status != "Todas":
            table = table[table["Estado"] == status]
        if query:
            q = query.lower()
            table = table[table["quirofano"].fillna("").str.lower().str.contains(q)
                          | table["servicio"].astype(str).str.lower().str.contains(q)]
        st.dataframe(
            table.assign(Episodio=table["oid_ingreso"].map(pseudonym))
                 .rename(columns={"fecha_ingreso": "Ingreso", "servicio": "Servicio", "quirofano": "Quirófano",
                                  "procedimientos": "Procedimientos"})
                 [["Episodio", "Ingreso", "Servicio", "Quirófano", "Procedimientos", "Estado"]]
                 .sort_values("Ingreso", ascending=False),
            hide_index=True, width="stretch", height=320,
            column_config={"Ingreso": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")},
        )

    state.publish_context(
        "Cirugías",
        {"Programadas": num(s["programadas"]), "Ejecutadas": num(s["ejecutadas"]), "Cumplimiento": pct(s["cumplimiento"])},
        ["¿Cuántas cirugías fueron programadas vs ejecutadas este mes?", "¿Qué días tienen quirófanos subutilizados?",
         "Dame las alertas activas"],
    )
