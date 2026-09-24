"""Urgencias — waiting times by triage + root cause (brief KPI + section h)."""

from __future__ import annotations

import streamlit as st

from core import config, kpis
from core.data import get_data
from core.filters import filter_admissions
from core.fmt import minutes, num, pct
from ui import charts, components, state


def render() -> None:
    fs = state.filters()
    data = get_data()
    components.page_header("Urgencias", "Triage y tiempos de espera",
                           "Minutos entre la clasificación de triage y la primera atención médica.", fs)
    w = kpis.wait_by_triage(fs)
    if w.empty:
        components.empty("No hay registros de triage con atención para estos filtros.")
        state.publish_context("Urgencias", {}, ["¿Cuál es el tiempo de espera promedio en urgencias en la última semana?"])
        return

    cols = st.columns(len(w) + 1)
    total = (w["promedio"] * w["pacientes"]).sum() / w["pacientes"].sum()
    in_target = (w["en_meta"] * w["pacientes"]).sum() / w["pacientes"].sum()
    cols[0].metric("Espera promedio", minutes(total), border=True, delta=f"{pct(in_target)} en meta",
                   delta_color="off", help=f"Meta institucional: {config.WAIT_TARGET_MIN} min")
    for col, row in zip(cols[1:], w.itertuples()):
        col.metric(row.nivel, minutes(row.mediana), border=True, delta=f"{num(row.pacientes)} pacientes",
                   delta_color="off", help="Mediana de espera del nivel")

    left, right = st.columns([1, 1.3], gap="medium")
    with left.container(border=True):
        components.section("Espera por nivel de triage", "Mediana · bigote = percentil 90")
        charts.show(charts.triage_bars(w, target=config.WAIT_TARGET_MIN, height=300))
    with right.container(border=True):
        components.section("Tendencia diaria", "Promedio y media móvil 7 días")
        daily = kpis.wait_daily(fs).melt(id_vars="fecha", value_vars=["espera", "media_7d"],
                                         var_name="serie", value_name="min").dropna()
        daily["serie"] = daily["serie"].map({"espera": "Diario", "media_7d": "Media 7 días"})
        charts.show(charts.lines(daily, "fecha", "min", "serie", target=config.WAIT_TARGET_MIN, height=300))

    rc = kpis.wait_root_cause(fs)
    left, right = st.columns([1.3, 1], gap="medium")
    with left.container(border=True):
        components.section("Llegadas por día y hora", "Ingresos promedio por semana · para programar turnos")
        grid = kpis.arrivals_heatmap(fs).copy()
        grid.columns = [f"{h:02d}h" for h in grid.columns]
        charts.show(charts.heatmap(grid, fmt=".1f", height=300))
        peak_day, peak_hour = grid.stack().idxmax()
        st.caption(f"Pico de llegadas: **{peak_day} {peak_hour}** (~{num(grid.stack().max(), 1)} ingresos/semana en esa hora).")
    with right.container(border=True):
        components.section("Análisis de causa raíz", "Espera promedio (min) · turno × triage")
        matrix = rc["matrix"].copy()
        if not matrix.empty:
            matrix.columns = [f"Triage {int(c)}" for c in matrix.columns]
            charts.show(charts.heatmap(matrix, fmt=".0f", height=210))
        cause = rc.get("cause")
        if cause:
            st.markdown(
                f"**Hallazgo:** en las últimas 2 semanas crecieron los pacientes **triage {cause['nivel']} en turno "
                f"{cause['turno'].lower()}** (+{num(cause['delta_diario'], 1)}/día). Espera actual "
                f"{minutes(rc['current'])} vs {minutes(rc['previous'])} antes.")
        components.ask_button("Explicar la causa con el asistente", "¿Por qué aumentó el tiempo de espera?",
                              key="em_ask_cause")

    with st.container(border=True):
        components.section("Puntos de triage", "Pacientes clasificados por consultorio")
        a = filter_admissions(data, fs).dropna(subset=["punto_triage"])
        points = a["punto_triage"].value_counts()
        points = points[points > 0]
        c1, c2 = st.columns([1, 1.4])
        with c1:
            charts.show(charts.donut(points.index.to_series(), points, height=240, center=num(points.sum())))
        with c2:
            by_point = a.groupby("punto_triage", observed=True)["espera_min"].median().dropna().reset_index()
            charts.show(charts.hbar(by_point, "espera_min", "punto_triage", height=240, color=charts.palette()[2]))
            st.caption("Mediana de espera (min) por punto de triage.")

    slow = w.sort_values("mediana", ascending=False).iloc[0]
    state.publish_context(
        "Urgencias",
        {"Espera promedio": minutes(total), "En meta": pct(in_target), "Nivel más lento": f"{slow.nivel} {minutes(slow.mediana)}"},
        ["¿Cuál es el tiempo de espera promedio en urgencias en la última semana?", "¿Por qué aumentó el tiempo de espera?",
         "¿En qué horas llegan más pacientes?", "Dame las alertas activas"],
    )
