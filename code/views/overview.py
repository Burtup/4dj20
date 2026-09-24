"""Panorama — executive dashboard (brief, section 6: KPIs + charts + alerts)."""

from __future__ import annotations

import streamlit as st

from core import config, insights, kpis
from core.data import get_data
from core.filters import filter_admissions
from core.fmt import es_date, minutes, num, pct
from ui import charts, components, state


def _spark(series, n: int = 30) -> list[float]:
    return [round(float(v), 3) for v in series.tail(n).fillna(0)]


def render() -> None:
    fs = state.filters()
    data = get_data()
    o = kpis.overview(fs)
    alerts = insights.generate(fs)
    census = kpis.daily_census(fs)
    occ_daily = census.groupby("fecha")["ocupadas"].sum() / data.bed_capacity.sum()
    wait_daily = kpis.wait_daily(fs).set_index("fecha")["espera"]
    adm_daily = filter_admissions(data, fs).groupby("fecha").size()

    sev = {s: sum(a.severity == s for a in alerts) for s in ("critical", "serious", "warning")}
    components.hero(
        "Panorama de la operación",
        f"Hospital Susana López de Valencia · corte {es_date(data.reference_date)} · {fs.describe()}",
        [(f"{sev['critical']} críticas", config.STATUS["critical"]), (f"{sev['serious']} altas", config.STATUS["serious"]),
         (f"{sev['warning']} medias", config.STATUS["warning"])],
    )
    st.space("small")

    k = st.columns(3) + st.columns(3)
    k[0].metric("Ocupación al corte", pct(o["ocupacion"]), help="Camas ocupadas a medianoche ÷ capacidad",
                border=True, chart_data=_spark(occ_daily), chart_type="area",
                delta=f"{num(o['camas_ocupadas'])}/{num(o['camas_total'])} camas", delta_color="off")
    k[1].metric("Espera triage → atención", minutes(o["espera"]),
                delta=None if o["espera_delta"] is None else f"{o['espera_delta'] * 100:+.0f} %",
                delta_color="inverse", border=True, chart_data=_spark(wait_daily), chart_type="line",
                help=f"Promedio; {pct(o['espera_en_meta'])} de pacientes dentro de la meta de {config.WAIT_TARGET_MIN} min")
    k[2].metric("Ingresos por día", num(o["ingresos_dia"], 1),
                delta=None if o["ingresos_delta"] is None else f"{o['ingresos_delta'] * 100:+.0f} %",
                delta_color="off", border=True, chart_data=_spark(adm_daily), chart_type="bar",
                help=f"{num(o['ingresos'])} ingresos en el periodo vs el periodo anterior")
    k[3].metric("Cumplimiento quirúrgico", pct(o["cumplimiento_cx"]), border=True,
                delta=f"{num(o['cirugias_ejecutadas'])}/{num(o['cirugias_programadas'])} cirugías", delta_color="off")
    k[4].metric("Farmacia en riesgo", f"{o['meds_criticos']} críticos", border=True,
                delta=f"{o['meds_bajos']} con stock bajo", delta_color="off",
                help=f"Ítems con menos de {config.STOCK_CRITICAL_DAYS} días de inventario")
    k[5].metric("Estancia hospitalaria", f"{num(o['estancia_media_d'], 1)} días", border=True,
                help="Estancia media de ingresos hospitalarios (egreso estimado)")

    left, right = st.columns([1.35, 1], gap="medium")
    with left.container(border=True):
        components.section("Ocupación por servicio", f"Umbral de alerta {pct(config.OCCUPANCY_WARNING)}")
        occ = kpis.occupancy_by_service(fs)
        charts.show(charts.hbar(occ, "ocupacion_hoy", "servicio", fmt="pct",
                                threshold=config.OCCUPANCY_WARNING, highlight_above=config.OCCUPANCY_WARNING))
    with right.container(border=True, height=440):
        components.section("Alertas y recomendaciones", f"{len(alerts)} activas")
        components.alert_cards(alerts, limit=6)
        components.ask_button("Pedir plan de acción al asistente", "Dame las alertas activas", key="ov_ask_alerts")

    left, right = st.columns([1.35, 1], gap="medium")
    with left.container(border=True):
        components.section("Ingresos diarios y pronóstico", "Banda de confianza 80 %")
        charts.show(charts.forecast(kpis.forecast_admissions(fs.with_dates(data.min_date.date(), fs.date_to))))
    with right.container(border=True):
        components.section("Triage en urgencias", "Pacientes por nivel")
        w = kpis.wait_by_triage(fs)
        if len(w):
            charts.show(charts.donut(w["nivel"], w["pacientes"],
                                     colors=[config.TRIAGE_COLORS[int(n)] for n in w["nivel_triage"]],
                                     center=num(w["pacientes"].sum())))
        else:
            components.empty("Sin registros de triage en el periodo.")

    left, right = st.columns(2, gap="medium")
    with left.container(border=True):
        components.section("Diagnósticos más frecuentes", "CIE-10 · agregados")
        dx = kpis.top_diagnoses(fs, top=8)
        charts.show(charts.hbar(dx, "ingresos", "diagnostico"))
    with right.container(border=True):
        components.section("Especialidades más solicitadas", "Servicios prestados")
        sp = kpis.demand_by_specialty(fs, top=8)
        charts.show(charts.hbar(sp.assign(especialidad=sp["especialidad"].astype(str).str.capitalize()),
                                "servicios", "especialidad", color=charts.palette()[1]))

    top_occ = occ.iloc[0] if len(occ) else None
    state.publish_context(
        "Panorama",
        {"Ocupación": pct(o["ocupacion"]), "Espera": minutes(o["espera"]), "Ingresos/día": num(o["ingresos_dia"], 1),
         "Servicio más ocupado": f"{top_occ.servicio} {pct(top_occ.ocupacion_hoy)}" if top_occ is not None else "—"},
        ["Resumen de la situación", "Dame las alertas activas", "¿Qué se espera para la próxima semana?",
         "¿Cuántas camas de UCI están ocupadas hoy?"],
    )
