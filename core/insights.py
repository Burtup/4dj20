"""Alerts and recommendations engine (brief, section 7 and "valor añadido").

Turns KPIs into prioritised, actionable :class:`Insight` objects:

* early shortage alerts (stock < 5 days, consumption spikes),
* bed pressure → open beds / reassign staff / overflow service,
* waiting-time root cause (shift × triage level),
* surgery scheduling optimisation (low-use operating-room slots),
* predictive alerts (diagnosis groups and admissions trending up).

Rules are deliberately simple and explainable: each insight carries the
metric that triggered it so the UI and the agent can justify it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core import config, kpis
from core.data import get_data
from core.filters import FilterState
from core.fmt import DAYS, es_date

SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2, "info": 3}

# Diagnosis chapter → medication families worth reinforcing when it trends up.
CHAPTER_MEDS = {
    "Respiratorio": "antibióticos (amoxicilina, ceftriaxona), broncodilatadores y oxígeno",
    "Infecciosas": "sales de rehidratación, antibióticos y soluciones parenterales",
    "Digestivo": "inhibidores de bomba de protones y antieméticos",
    "Embarazo y parto": "oxitocina, sulfato de magnesio e insumos de sala de partos",
    "Traumatismos": "analgésicos, material de osteosíntesis e insumos de curación",
    "Circulatorio": "antihipertensivos y anticoagulantes",
    "Perinatal": "insumos neonatales y surfactante",
}


@dataclass(frozen=True)
class Insight:
    severity: str          # critical | serious | warning | info
    category: str          # Camas, Urgencias, Farmacia, Cirugía, Predicción
    title: str
    detail: str
    action: str
    metric: dict = field(default_factory=dict, hash=False, compare=False)


def _bed_insights(fs: FilterState) -> list[Insight]:
    occ = kpis.occupancy_by_service(fs)
    out = []
    spare = occ.sort_values("disponibles_hoy", ascending=False).iloc[0] if len(occ) else None
    for row in occ.itertuples():
        if row.ocupacion_hoy < config.OCCUPANCY_WARNING or row.camas < 5:
            continue
        severity = "critical" if row.ocupacion_hoy >= config.OCCUPANCY_CRITICAL else "serious"
        overflow = (f" Reubicar pacientes elegibles hacia {spare.servicio} ({int(spare.disponibles_hoy)} camas libres)."
                    if spare is not None and spare.servicio != row.servicio else "")
        out.append(Insight(
            severity, "Camas", f"{row.servicio} al {row.ocupacion_hoy:.0%} de ocupación",
            f"{row.ocupadas_hoy} de {int(row.camas)} camas ocupadas al corte; promedio del periodo {row.ocupacion_promedio:.0%}.",
            f"Abrir camas de expansión o reforzar personal de enfermería en {row.servicio}.{overflow}",
            {"servicio": row.servicio, "ocupacion": row.ocupacion_hoy},
        ))
    return out


def _wait_insights(fs: FilterState) -> list[Insight]:
    out = []
    waits = kpis.wait_by_triage(fs)
    for row in waits.itertuples():
        if row.nivel_triage <= 2 and row.mediana > config.WAIT_TARGET_MIN and row.pacientes >= 20:
            out.append(Insight(
                "serious" if row.nivel_triage == 1 else "warning", "Urgencias",
                f"Triage {row.nivel}: espera mediana {row.mediana:.0f} min",
                f"Meta {config.WAIT_TARGET_MIN} min; solo el {row.en_meta:.0%} de {row.pacientes} pacientes la cumple.",
                "Activar fast-track para triage I–II y revisar disponibilidad médica en consultorios de urgencias.",
                {"nivel": int(row.nivel_triage), "mediana": row.mediana},
            ))
    rc = kpis.wait_root_cause(fs)
    cause = rc.get("cause")
    if cause and pd.notna(rc["previous"]) and rc["current"] > rc["previous"]:
        pct = f" (+{cause['delta_pct']:.0%})" if pd.notna(cause["delta_pct"]) else ""
        out.append(Insight(
            "warning", "Urgencias",
            f"La espera subió a {rc['current']:.0f} min (antes {rc['previous']:.0f})",
            f"Causa probable: más pacientes triage {cause['nivel']} en turno {cause['turno'].lower()}"
            f" (+{cause['delta_diario']:.1f}/día{pct}).",
            f"Reforzar el turno {cause['turno'].lower()} con un médico adicional en consultorios de triage {cause['nivel']}.",
            cause,
        ))
    return out


def _pharmacy_insights(fs: FilterState) -> list[Insight]:
    data = get_data()
    out = []
    critical = kpis.inventory_status(config.STOCK_CRITICAL_DAYS)
    if len(critical):
        names = ", ".join(critical["nombre"].head(3))
        out.append(Insight(
            "critical", "Farmacia", f"{len(critical)} ítems con menos de {config.STOCK_CRITICAL_DAYS} días de inventario",
            f"Más urgentes: {names}." + (" (inventario simulado)" if data.inventory_is_simulated else ""),
            "Emitir orden de compra urgente y activar préstamo interinstitucional para los ítems críticos.",
            {"items": len(critical)},
        ))
    inv = data.inventory
    expiring = inv[inv["fecha_vencimiento"].notna()
                   & (inv["fecha_vencimiento"] <= pd.Timestamp.now() + pd.Timedelta(days=config.EXPIRY_WARNING_DAYS))]
    if len(expiring):
        out.append(Insight(
            "serious", "Farmacia", f"{len(expiring)} ítems vencen en menos de {config.EXPIRY_WARNING_DAYS} días",
            "Próximos: " + ", ".join(expiring.sort_values("fecha_vencimiento")["nombre_servicio"].astype(str).str.capitalize().head(3)) + ".",
            "Priorizar su dispensación (FEFO) o gestionar devolución al proveedor.",
            {"items": len(expiring)},
        ))
    trend = kpis.med_consumption_trend(fs)
    risky = trend[(trend["variacion"] >= config.CONSUMPTION_SPIKE) & (trend["dias_inventario"] < config.STOCK_LOW_DAYS)]
    for row in risky.head(3).itertuples():
        out.append(Insight(
            "serious", "Farmacia", f"Consumo de {row.nombre} +{row.variacion:.0%}",
            f"{row.reciente_dia:.1f} u/día últimos 7 días vs {row.base_dia:.1f} u/día; quedan {row.dias_inventario:.0f} días.",
            "Riesgo de desabastecimiento: adelantar el pedido y revisar prescripción.",
            {"codigo": row.codigo_servicio, "variacion": row.variacion},
        ))
    return out


def _surgery_insights(fs: FilterState) -> list[Insight]:
    out = []
    summary = kpis.surgery_summary(fs)
    if summary["programadas"] >= 10 and summary["cumplimiento"] < 0.8:
        out.append(Insight(
            "warning", "Cirugía", f"Cumplimiento quirúrgico {summary['cumplimiento']:.0%}",
            f"{summary['no_ejecutadas']} de {summary['programadas']} cirugías programadas sin ejecución registrada.",
            "Confirmar pacientes 48 h antes y sobre-agendar franjas de baja utilización.",
            {"cumplimiento": summary["cumplimiento"]},
        ))
    grid = kpis.or_usage_heatmap(fs)
    if grid.to_numpy().sum() > 0:
        business = grid.loc[kpis.WEEKDAYS[:5], list(range(7, 17))]
        per_day = business.sum(axis=1)
        low_day, high_day = per_day.idxmin(), per_day.idxmax()
        low_name, high_name = (DAYS[kpis.WEEKDAYS.index(d)] for d in (low_day, high_day))
        if per_day[high_day] > per_day[low_day] * 1.3:
            out.append(Insight(
                "info", "Cirugía", f"Quirófanos subutilizados los {low_name}",
                f"{per_day[low_day]:.0f} procedimientos/semana en horario 7–17 h vs {per_day[high_day]:.0f} los {high_name}.",
                f"Mover cirugías electivas del {high_name} al {low_name} para equilibrar la carga.",
                {"dia_bajo": low_name, "dia_alto": high_name},
            ))
    return out


def _predictive_insights(fs: FilterState) -> list[Insight]:
    out = []
    trends = kpis.diagnosis_trends(fs)
    relevant = trends[(trends["variacion"] >= config.DEMAND_SPIKE) & (trends["base_dia"] >= 2)]
    for row in relevant.head(2).itertuples():
        meds = CHAPTER_MEDS.get(row.capitulo_dx)
        action = f"Aumentar stock de {meds}." if meds else "Revisar capacidad del servicio receptor."
        out.append(Insight(
            "warning", "Predicción", f"Ingresos por causas {row.capitulo_dx.lower()} +{row.variacion:.0%}",
            f"{row.reciente_dia:.1f} ingresos/día en las últimas 2 semanas vs {row.base_dia:.1f} antes.",
            action, {"capitulo": row.capitulo_dx, "variacion": row.variacion},
        ))
    fc = kpis.forecast_admissions(fs)
    if len(fc) and fc["pronostico"].notna().any():
        nxt = fc["pronostico"].dropna().head(7).sum()
        last = fc["real"].dropna().tail(7).sum()
        peak = fc.loc[fc["pronostico"].idxmax()]
        change = (nxt - last) / last if last else 0
        if abs(change) >= 0.05:
            out.append(Insight(
                "info" if change < 0 else "warning", "Predicción",
                f"Se esperan {nxt:.0f} ingresos en 7 días ({change:+.0%})",
                f"Pico previsto el {es_date(peak['fecha'], weekday=True, year=False)} con ~{peak['pronostico']:.0f} ingresos.",
                "Ajustar la programación de turnos de urgencias al pico previsto.",
                {"proximos_7d": nxt, "cambio": change},
            ))
    return out


RULES = {
    "Farmacia": _pharmacy_insights,
    "Camas": _bed_insights,
    "Urgencias": _wait_insights,
    "Predicción": _predictive_insights,
    "Cirugía": _surgery_insights,
}


def generate(fs: FilterState, category: str | None = None) -> list[Insight]:
    """Insights for the current filters (optionally one category), most severe first."""
    rules = [RULES[category]] if category else RULES.values()
    items = [insight for rule in rules for insight in rule(fs)]
    return sorted(items, key=lambda i: SEVERITY_ORDER.get(i.severity, 9))
