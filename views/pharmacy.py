"""Farmacia — stock, rotation and early shortage alerts (brief KPI + section 7)."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core import config, insights, kpis
from core.data import get_data
from core.fmt import num, pct, signed_pct
from ui import charts, components, state


def _pareto(rot, height: int = 280) -> go.Figure:
    share = rot["cantidad"].cumsum() / rot["cantidad"].sum()
    x = (range(1, len(rot) + 1))
    fig = go.Figure(go.Scatter(x=list(x), y=share, mode="lines", line=dict(width=2, color=charts.palette()[1]),
                               fill="tozeroy", fillcolor="rgba(92,154,27,.12)",
                               hovertemplate="Top %{x} ítems: %{y:.0%} del volumen<extra></extra>"))
    fig.add_hline(y=0.8, line_dash="dot", line_width=1.5, line_color=config.STATUS["serious"],
                  annotation_text="80 % · clase A", annotation_font_size=10, annotation_font_color=config.STATUS["serious"])
    fig.update_yaxes(tickformat=".0%", range=[0, 1.02])
    fig.update_xaxes(title="Ítems ordenados por volumen", type="log")
    return charts.style(fig, height, legend=False)


def render() -> None:
    fs = state.filters()
    data = get_data()
    components.page_header("Farmacia", "Inventario y consumo",
                           "Días de inventario, rotación ABC y alertas tempranas de desabastecimiento.", fs)
    if data.inventory_is_simulated:
        st.warning("El extracto HIS no incluye existencias: el **stock inicial es simulado** a partir del consumo real. "
                   "Registra conteos en **Gestión → Registro clínico → Inventario** o importa `Inventario.csv` "
                   "(codigo_servicio;stock) en **Centro de datos**.", icon=":material/science:")

    inv = kpis.inventory_status()
    rot = kpis.med_rotation(fs)
    trend = kpis.med_consumption_trend(fs)
    k = st.columns(5)
    k[0].metric("Críticos", num((inv["estado"] == "Crítico").sum()), border=True,
                help=f"Menos de {config.STOCK_CRITICAL_DAYS} días de inventario")
    k[1].metric("Stock bajo", num((inv["estado"] == "Bajo").sum()), border=True,
                help=f"Entre {config.STOCK_CRITICAL_DAYS} y {config.STOCK_LOW_DAYS} días")
    k[2].metric("Unidades dispensadas", num(rot["cantidad"].sum()), border=True)
    k[3].metric("Ítems clase A", num((rot["clase_abc"] == "A").sum()), border=True,
                delta=f"de {num(len(rot))} ítems", delta_color="off", help="Concentran el 80 % del volumen")
    spikes = trend[trend["variacion"] >= config.CONSUMPTION_SPIKE]
    k[4].metric("Picos de consumo", num(len(spikes)), border=True, help="Consumo 7 días ≥ +25 % vs 28 días previos")

    with st.container(border=True):
        components.section("Inventario", "Ordenado por días restantes")
        c1, c2 = st.columns([3, 2])
        query = c1.text_input("Buscar medicamento o insumo", key="ph_q", placeholder="Ej. amoxicilina, gasa…")
        status = c2.segmented_control("Estado", ["Todos", "Crítico", "Bajo", "Normal", "Sin consumo"], default="Todos",
                                      key="ph_status")
        view = inv
        if status and status != "Todos":
            view = view[view["estado"] == status]
        if query:
            view = view[view["nombre"].str.lower().str.contains(query.lower(), regex=False)]
        st.dataframe(
            view[["nombre", "stock", "consumo_diario", "dias_inventario", "estado"]],
            hide_index=True, width="stretch", height=330,
            column_config={
                "nombre": "Medicamento / insumo",
                "stock": st.column_config.NumberColumn("Stock", format="%d"),
                "consumo_diario": st.column_config.NumberColumn("Consumo/día", format="%.1f"),
                "dias_inventario": st.column_config.ProgressColumn("Días de inventario", min_value=0, max_value=45,
                                                                   format="%.1f d"),
                "estado": "Estado",
            },
        )

    left, right = st.columns([1.3, 1], gap="medium")
    with left.container(border=True):
        components.section("Mayor rotación", "Unidades dispensadas en el periodo")
        charts.show(charts.hbar(rot.head(10), "cantidad", "nombre", height=360))
    with right.container(border=True):
        components.section("Curva de Pareto (ABC)", "Pocos ítems concentran el consumo")
        if len(rot):
            charts.show(_pareto(rot, height=360))

    left, right = st.columns([1.3, 1], gap="medium")
    with left.container(border=True):
        components.section("Alertas tempranas de consumo", "7 días recientes vs 28 días previos")
        st.dataframe(
            spikes.head(15)[["nombre", "base_dia", "reciente_dia", "variacion", "dias_inventario"]]
            .assign(variacion=lambda d: d["variacion"].map(signed_pct)),
            hide_index=True, width="stretch",
            column_config={"nombre": "Ítem", "base_dia": st.column_config.NumberColumn("Base/día", format="%.1f"),
                           "reciente_dia": st.column_config.NumberColumn("Reciente/día", format="%.1f"),
                           "variacion": "Variación",
                           "dias_inventario": st.column_config.NumberColumn("Días inv.", format="%.0f")},
        )
    with right.container(border=True):
        components.section("Menor rotación", "Candidatos a reducir compra o redistribuir")
        low = rot.tail(10).iloc[::-1]
        st.dataframe(low[["nombre", "cantidad", "dispensaciones"]], hide_index=True, width="stretch",
                     column_config={"nombre": "Ítem", "cantidad": "Unidades", "dispensaciones": "Dispensaciones"})
        components.alert_cards(insights.generate(fs, "Farmacia"), limit=1)

    worst = inv.iloc[0] if len(inv) else None
    state.publish_context(
        "Farmacia",
        {"Críticos": num((inv["estado"] == "Crítico").sum()),
         "Más urgente": f"{worst['nombre']} ({worst['dias_inventario']:.1f} d)" if worst is not None else "—",
         "Clase A": f"{pct((rot['clase_abc'] == 'A').mean())} de los ítems"},
        ["¿Cuáles son los medicamentos con menos de 5 días de inventario?", "¿Qué medicamentos tienen mayor rotación?",
         "¿Qué medicamentos tienen menor rotación?", "Dame las alertas activas"],
    )
