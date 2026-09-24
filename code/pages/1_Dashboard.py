import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import load_datasets

st.set_page_config(
    page_title="Dashboard Hospitalario",
    page_icon="📊",
    layout="wide",
)
st.title("Dashboard de Gestión Hospitalaria")

datasets = load_datasets()
if not datasets:
    st.error("No se encontraron datos. Ejecuta scripts/cleaner.py primero.")
    st.stop()

ingresos = datasets["ingresos"]
triage   = datasets["triage"]
paciente = datasets["paciente"]
prog_cx  = datasets["programacion_cirugia"]

BRAND_SCALE = ["#f2f2f2", "#d5e8a0", "#74b722", "#24732b", "#19205b"]

# ── KPIs ──────────────────────────────────────────────────────────────────
total_admissions = len(ingresos)
unique_patients  = paciente.shape[0]
top_dx           = ingresos["nombre_diagnostico"].value_counts().idxmax()
surgeries        = len(prog_cx)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Ingresos",        f"{total_admissions:,}")
c2.metric("Pacientes Registrados", f"{unique_patients:,}")
c3.metric("Diagnóstico Principal", top_dx[:30] + "…" if len(top_dx) > 30 else top_dx)
c4.metric("Cirugías Programadas",  f"{surgeries:,}")

st.divider()

# ── Fila 2: Pie + Line ─────────────────────────────────────────────────────
st.subheader("Distribución de Ingresos")
col_l, col_r = st.columns(2)

via_counts = ingresos["via_ingreso"].dropna().value_counts().reset_index()
via_counts.columns = ["via_ingreso", "total"]
fig_via = px.pie(
    via_counts, values="total", names="via_ingreso",
    title="Ingresos por Vía", hole=0.45,
    color_discrete_sequence=["#19205b", "#74b722"],
)
fig_via.update_traces(textposition="inside", textinfo="percent+label")
fig_via.update_layout(showlegend=False)
col_l.plotly_chart(fig_via, use_container_width=True)

weekly = (
    ingresos
    .assign(semana=ingresos["fecha_ingreso"].dt.to_period("W").dt.start_time)
    .groupby("semana").size()
    .reset_index(name="ingresos")
)
fig_time = px.line(
    weekly, x="semana", y="ingresos", title="Ingresos Semanales",
    markers=True, labels={"semana": "Semana", "ingresos": "Admisiones"},
    color_discrete_sequence=["#19205b"],
)
fig_time.update_layout(xaxis_tickangle=-30)
col_r.plotly_chart(fig_time, use_container_width=True)

st.divider()

# ── Fila 3: Triage + Top Diagnósticos ─────────────────────────────────────
st.subheader("Triage y Diagnósticos")
col3a, col3b = st.columns(2)

triage_counts = triage["clasificacion_triage"].value_counts().reset_index()
triage_counts.columns = ["clasificacion_triage", "total"]
fig_triage = px.bar(
    triage_counts, x="clasificacion_triage", y="total",
    title="Clasificación de Triage",
    labels={"clasificacion_triage": "Clasificación", "total": "Casos"},
    color="total", color_continuous_scale=BRAND_SCALE, text_auto=True,
)
fig_triage.update_layout(coloraxis_showscale=False, xaxis_tickangle=-20)
col3a.plotly_chart(fig_triage, use_container_width=True)

top10_dx = ingresos["nombre_diagnostico"].value_counts().head(10).reset_index()
top10_dx.columns = ["nombre_diagnostico", "total"]
fig_dx = px.bar(
    top10_dx, x="total", y="nombre_diagnostico", orientation="h",
    title="Top 10 Diagnósticos",
    labels={"total": "Ingresos", "nombre_diagnostico": "Diagnóstico"},
    color="total", color_continuous_scale=BRAND_SCALE, text_auto=True,
)
fig_dx.update_layout(
    yaxis={"categoryorder": "total ascending"},
    coloraxis_showscale=False,
)
col3b.plotly_chart(fig_dx, use_container_width=True)
