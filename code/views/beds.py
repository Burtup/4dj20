"""Camas — hospital occupancy (brief KPI: ocupación diaria/mensual por servicio)."""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from core import config, kpis
from core.data import get_data
from core.fmt import es_date, num, pct
from ui import charts, components, state

STATES = {"Ocupada": "busy", "Prolongada": "long", "Libre": "", "Mantenimiento": "maint", "Fuera de servicio": "maint"}


def _bed_grid(beds: pd.DataFrame) -> str:
    tiles = []
    for b in beds.itertuples():
        tip = f"{b.codigo_cama} · {b.estado}"
        if b.estado in ("Ocupada", "Prolongada"):
            tip += f" · {b.episodio} · {b.dias:.1f} días · {b.capitulo_dx}"
        label = str(b.codigo_cama)[-3:]
        tiles.append(f'<div class="hiq-bed {STATES[b.estado]}" title="{escape(tip)}">{escape(label)}</div>')
    return f'<div class="hiq-bedgrid">{"".join(tiles)}</div>'


def render() -> None:
    fs = state.filters()
    data = get_data()
    components.page_header("Capacidad", "Camas y ocupación",
                           "Censo de medianoche por servicio, evolución y mapa de camas al corte.", fs)

    occ = kpis.occupancy_by_service(fs)
    census = kpis.daily_census(fs)
    beds = kpis.bed_map(fs)
    total, busy = occ["camas"].sum(), occ["ocupadas_hoy"].sum()
    long_stays = int((beds["estado"] == "Prolongada").sum())

    k = st.columns(5)
    k[0].metric("Capacidad", num(total), border=True, help="Camas por servicio (ver metodología)")
    k[1].metric("Ocupadas al corte", num(busy), border=True, delta=pct(busy / total if total else 0), delta_color="off")
    k[2].metric("Disponibles", num(total - busy), border=True)
    k[3].metric("Ocupación promedio", pct((census["ocupadas"].sum() / census["fecha"].nunique() / total)
                                         if len(census) and total else 0), border=True)
    k[4].metric("Estancias prolongadas", num(long_stays), border=True,
                help="Pacientes con estancia mayor al percentil 90 de su servicio: candidatos a plan de egreso")

    left, right = st.columns([1, 1.25], gap="medium")
    with left.container(border=True):
        components.section("Ocupación al corte", f"{es_date(fs.date_to)} · umbral {pct(config.OCCUPANCY_WARNING)}")
        charts.show(charts.hbar(occ, "ocupacion_hoy", "servicio", fmt="pct",
                                threshold=config.OCCUPANCY_WARNING, highlight_above=config.OCCUPANCY_WARNING))
    with right.container(border=True):
        components.section("Evolución de la ocupación", "% de camas ocupadas por día")
        default = list(occ["servicio"].head(4))
        chosen = st.multiselect("Servicios", list(occ["servicio"]), default=default, key="beds_series",
                                label_visibility="collapsed", placeholder="Elige servicios", max_selections=6)
        series = census[census["servicio"].isin(chosen)]
        if len(series):
            charts.show(charts.lines(series, "fecha", "ocupacion", "servicio", fmt="pct",
                                     target=config.OCCUPANCY_WARNING, height=280))
        else:
            components.empty("Elige al menos un servicio.")

    with st.container(border=True):
        components.section("Mapa de calor semanal", "Ocupación promedio por servicio y semana")
        weekly = census.assign(semana=census["fecha"].dt.to_period("W").dt.start_time)
        grid = weekly.pivot_table(index="servicio", columns="semana", values="ocupacion", aggfunc="mean", observed=True)
        grid.columns = [es_date(c, year=False) for c in grid.columns]
        charts.show(charts.heatmap(grid * 100, fmt=".0f", height=320, colorbar_title="%"))

    with st.container(border=True):
        components.section("Mapa de camas al corte", "Pasa el cursor sobre una cama para ver el episodio (anonimizado)")
        c1, c2 = st.columns([2, 3])
        service = c1.selectbox("Servicio", sorted(beds["servicio"].astype(str).unique()), key="beds_map_service")
        status = c2.segmented_control("Estado", ["Todas", "Ocupada", "Prolongada", "Libre", "Mantenimiento"],
                                      default="Todas", key="beds_map_status")
        view = beds[beds["servicio"] == service]
        if status == "Mantenimiento":
            view = view[view["estado"].isin(["Mantenimiento", "Fuera de servicio"])]
        elif status and status != "Todas":
            view = view[view["estado"] == status]
        st.markdown('<div class="hiq-legend"><span><i style="background:var(--hiq-bed-busy)"></i>Ocupada</span>'
                    '<span><i style="background:var(--hiq-bed-long)"></i>Estancia prolongada</span>'
                    '<span><i style="background:var(--hiq-bed-free)"></i>Libre</span>'
                    '<span><i class="hiq-maint-swatch"></i>Mantenimiento / fuera de servicio</span></div>',
                    unsafe_allow_html=True)
        st.markdown(_bed_grid(view), unsafe_allow_html=True)
        st.caption(f"{len(view)} camas mostradas del registro de camas de {service}. "
                   "El estado operativo se gestiona en Gestión → Registro clínico → Camas.")

    prolonged = beds[beds["estado"] == "Prolongada"].sort_values("dias", ascending=False)
    if len(prolonged):
        with st.expander(f"Plan de egreso · {len(prolonged)} estancias prolongadas", icon=":material/exit_to_app:"):
            st.dataframe(prolonged.rename(columns={"codigo_cama": "Cama", "servicio": "Servicio", "episodio": "Episodio",
                                                   "dias": "Días", "capitulo_dx": "Capítulo CIE-10"})
                         [["Episodio", "Servicio", "Cama", "Días", "Capítulo CIE-10"]], hide_index=True, width="stretch")

    top = occ.iloc[0] if len(occ) else None
    state.publish_context(
        "Camas",
        {"Ocupación al corte": pct(busy / total if total else 0), "Disponibles": num(total - busy),
         "Más ocupado": f"{top.servicio} {pct(top.ocupacion_hoy)}" if top is not None else "—",
         "Estancias prolongadas": num(long_stays)},
        ["¿Cuántas camas de UCI están ocupadas hoy?", "¿Qué servicio tiene más pacientes ingresados este mes?",
         "¿Dónde conviene abrir camas?", "Dame las alertas activas"],
    )
