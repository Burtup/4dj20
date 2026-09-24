"""Demanda — services, specialties, diagnoses, demographics and forecast."""

from __future__ import annotations

import streamlit as st

from core import config, insights, kpis
from core.data import get_data
from core.fmt import num, pct, signed_pct
from ui import charts, components, state

DIMENSIONS = {"Edad": "grupo_etario", "Sexo": "sexo", "Régimen": "regimen", "Zona": "zona"}


def render() -> None:
    fs = state.filters()
    data = get_data()
    components.page_header("Demanda", "Demanda de servicios y pronóstico",
                           "Qué se atiende, quién lo solicita y qué se espera en los próximos días.", fs)
    o = kpis.overview(fs)
    by_service = kpis.admissions_by_service(fs)
    sp = kpis.demand_by_specialty(fs)

    k = st.columns(4)
    k[0].metric("Ingresos", num(o["ingresos"]), border=True,
                delta=None if o["ingresos_delta"] is None else signed_pct(o["ingresos_delta"]), delta_color="off")
    k[1].metric("Pacientes únicos", num(o["pacientes"]), border=True)
    k[2].metric("Servicio con más ingresos", by_service.iloc[0]["servicio"] if len(by_service) else "—", border=True,
                delta=f"{num(by_service.iloc[0]['ingresos'])} ingresos" if len(by_service) else None, delta_color="off")
    k[3].metric("Especialidad más solicitada", str(sp.iloc[0]["especialidad"]).capitalize() if len(sp) else "—",
                border=True)

    left, right = st.columns(2, gap="medium")
    with left.container(border=True):
        components.section("Ingresos por servicio", "Grupo de cama del episodio")
        charts.show(charts.hbar(by_service, "ingresos", "servicio", height=320))
    with right.container(border=True):
        components.section("Servicios por especialidad", "Top 12")
        charts.show(charts.hbar(sp.assign(especialidad=sp["especialidad"].astype(str).str.capitalize()),
                                "servicios", "especialidad", color=charts.palette()[1], height=320))

    left, right = st.columns([1.4, 1], gap="medium")
    with left.container(border=True):
        components.section("Mapa de diagnósticos", "Servicio → capítulo CIE-10 → diagnóstico · clic para profundizar")
        charts.show(charts.sunburst(kpis.diagnosis_hierarchy(fs), height=470))
    with right.container(border=True):
        components.section("Perfil de los pacientes", "Distribución de ingresos")
        dim = st.segmented_control("Dimensión", list(DIMENSIONS), default="Edad", key="dm_dim",
                                   label_visibility="collapsed") or "Edad"
        series = kpis.demographics(fs)[DIMENSIONS[dim]]
        series = series[series > 0]
        charts.show(charts.donut(series.index.to_series(), series, height=300, center=dim))
        demand_area = kpis.demand_by_area(fs, top=5)
        st.caption("Áreas con más servicios: " + ", ".join(
            str(a).replace("APOYO DIAGNOSTICO - ", "").replace("APOYO TERAPEUTICO - ", "").capitalize()
            for a in demand_area["area_servicio"]))

    left, right = st.columns([1.4, 1], gap="medium")
    full = fs.with_dates(data.min_date.date(), fs.date_to)
    with left.container(border=True):
        components.section("Pronóstico de ingresos", "Próximos 14 días · modelo estacional semanal")
        charts.show(charts.forecast(kpis.forecast_admissions(full), height=320))
    with right.container(border=True, height=402):
        components.section("Alertas predictivas", f"Tendencias ≥ {pct(config.DEMAND_SPIKE)} por capítulo CIE-10")
        components.alert_cards(insights.generate(fs, "Predicción"), limit=3)
        trends = kpis.diagnosis_trends(fs)
        st.dataframe(trends.head(6).assign(variacion=trends["variacion"].map(signed_pct)),
                     hide_index=True, width="stretch",
                     column_config={"capitulo_dx": "Capítulo", "reciente_dia": st.column_config.NumberColumn("Reciente/día", format="%.1f"),
                                    "base_dia": st.column_config.NumberColumn("Base/día", format="%.1f"),
                                    "variacion": "Variación"})

    state.publish_context(
        "Demanda",
        {"Ingresos": num(o["ingresos"]),
         "Top servicio": by_service.iloc[0]["servicio"] if len(by_service) else "—",
         "Top especialidad": str(sp.iloc[0]["especialidad"]).capitalize() if len(sp) else "—"},
        ["¿Qué servicio tiene más pacientes ingresados este mes?", "¿Qué especialidades son las más solicitadas?",
         "¿Cuáles son los diagnósticos más frecuentes?", "¿Qué se espera para la próxima semana?"],
    )
