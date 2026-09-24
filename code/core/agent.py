"""HospitalIQ conversational agent.

Hybrid design (brief, section b — "Opción principal" + "Plan B"):

1. **Intent engine (rules)** — deterministic, instant and always available.
   Recognises the challenge's key questions (camas UCI, inventario < 5 días,
   espera en urgencias, servicio con más ingresos…) plus alerts, forecasts,
   surgery, demand and demographics. Answers are computed with the *same*
   KPI functions as the dashboard, scoped to the filters the user is seeing.
2. **NL2SQL (LLM)** — for open questions, a local Ollama model (llama3.2 by
   default) writes SQLite over anonymised views; the SQL is validated by
   :mod:`core.sql_guard` and executed read-only. The narrative is built
   deterministically from the returned rows (the model never writes the
   figures, so it cannot invent them). Any failure falls back to rules.

Privacy: the agent never returns names, documents, birth dates or patient
ids; requests for them are refused explicitly, whatever the other intent.

The public entry point is :meth:`HospitalAgent.ask`, which returns an
:class:`AgentAnswer` (text + optional table + optional chart spec + SQL),
consumed by both the Streamlit chat and the REST API.
"""

from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import pandas as pd
import requests

from core import config, insights, kpis
from core.data import get_data
from core.filters import FilterState, filter_admissions
from core.fmt import DAYS, es_date, minutes, num, pct, signed_pct
from core.sql_guard import UnsafeSQLError, extract_sql, sanitize


@dataclass
class AgentAnswer:
    text: str                                   # markdown, Spanish
    table: pd.DataFrame | None = None           # result visualizer
    chart: dict | None = None                   # {"type","x","y","color","title"}
    sql: str | None = None                      # shown in "¿Cómo lo calculé?"
    source: str = "reglas"                      # reglas | nl2sql | privacidad | ayuda
    intent: str = "ayuda"
    scope: str = ""                             # filters used, human readable
    followups: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


# ── Text normalisation & parsing ─────────────────────────────────────────────

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


SERVICE_ALIASES = {
    "uci": "UCI", "cuidado intensivo": "UCI", "intensivo": "UCI",
    "intermedio": "Cuidado intermedio", "basico": "Cuidado básico neonatal", "neonat": "Cuidado básico neonatal",
    "hospitalizacion": "Hospitalización", "medicina interna": "Hospitalización",
    "pediatr": "Pediatría", "urgencia": "Urgencias", "recuperacion": "Recuperación",
    "gineco": "Ginecoobstetricia", "obstetr": "Ginecoobstetricia", "partos": "Sala de partos",
}
MONTH_IDX = {m: i + 1 for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"])}


def parse_services(q: str) -> tuple[str, ...]:
    found = []
    for alias, label in SERVICE_ALIASES.items():
        if re.search(rf"\b{alias}", q) and label not in found:
            found.append(label)
    return tuple(found)


def parse_period(q: str, ref: date) -> tuple[date, date, str] | None:
    """Relative period in the question, anchored on the data cut-off date."""
    if "hoy" in q or "actual" in q or "ahora" in q:
        return ref, ref, "hoy"
    if "ayer" in q:
        return ref - timedelta(days=1), ref - timedelta(days=1), "ayer"
    m = re.search(r"ultim[oa]s?\s+(\d+)\s+dias", q)
    if m:
        n = int(m.group(1))
        return ref - timedelta(days=n - 1), ref, f"últimos {n} días"
    if re.search(r"(ultima|esta|la)\s+semana|7 dias", q):
        return ref - timedelta(days=6), ref, "última semana"
    if "mes pasado" in q or "mes anterior" in q:
        first = ref.replace(day=1)
        prev_end = first - timedelta(days=1)
        return prev_end.replace(day=1), prev_end, "mes pasado"
    if re.search(r"(este|el|ultimo)\s+mes|mensual", q):
        return ref.replace(day=1), ref, "este mes"
    for name, idx in MONTH_IDX.items():
        if re.search(rf"\b{name}\b", q):
            start = date(ref.year, idx, 1)
            end = (pd.Timestamp(start) + pd.offsets.MonthEnd(0)).date()
            return start, min(end, ref), name
    return None


def parse_number(q: str, default: int) -> int:
    m = re.search(r"(?:menos de|<|bajo)\s*(\d+)", q)
    return int(m.group(1)) if m else default


# ── Intent catalogue ─────────────────────────────────────────────────────────
# Each intent: list of regex patterns; the one with most hits wins.

INTENTS: dict[str, list[str]] = {
    "privacidad": [r"\bnombre", r"cedula", r"documento", r"identidad", r"quien es", r"datos personales", r"telefono", r"direccion"],
    "inventario": [r"inventario", r"stock", r"desabast", r"agot", r"existencia", r"dias de (inventario|cobertura)"],
    "rotacion": [r"rotacion", r"mas (consumid|dispensad|usad)", r"menos (consumid|dispensad|usad)", r"consumo de (medicament|insumo)", r"medicamentos? (mas|menos)"],
    "camas": [r"\bcamas?\b", r"ocupacion", r"ocupad", r"disponib", r"censo", r"capacidad"],
    "causa_espera": [r"por ?que.*(espera|demora)", r"causa.*(espera|demora)", r"(espera|demora).*(aument|sub|crec)"],
    "espera": [r"espera", r"demora", r"tiempo (de|para) (atencion|ingreso)", r"triage"],
    "llegadas": [r"(a|en) que horas?", r"horas? (pico|de mayor)", r"llegan", r"afluencia", r"picos? de (llegada|ingreso)",
                 r"dia de la semana"],
    "cirugia": [r"cirug", r"quirofano", r"quirurg", r"programad"],
    "especialidad": [r"especialidad", r"especialista"],
    "servicio_demanda": [r"servicio.*(mas|mayor|menos).*(paciente|ingres|demanda)", r"(mas|mayor) demanda", r"servicios? (mas|con mas)"],
    "diagnosticos": [r"diagnostic", r"enfermedad", r"patolog", r"cie.?10", r"motivo"],
    "pronostico": [r"pronost", r"predic", r"proxim", r"se espera", r"tendencia", r"futur", r"pico"],
    "alertas": [r"alerta", r"recomend", r"accion", r"riesgo", r"que (hago|hacer|debo)", r"prioridad", r"sugerenc"],
    "demografia": [r"\bedad", r"\bsexo", r"regimen", r"\beps\b", r"asegurador", r"\bzona", r"municipio", r"mujeres|hombres"],
    "ingresos": [r"cuant[oa]s? (ingres|paciente|admision)", r"ingresos", r"admisiones", r"via de ingreso"],
    "registros": [r"cambios", r"se registr", r"auditori", r"quien (edito|modifico|registro)", r"registros? (clinico|de la app)"],
    "resumen": [r"resumen", r"situacion", r"como (estamos|va)", r"estado general", r"panorama", r"que (ves|estoy viendo)"],
}
PRIORITY = list(INTENTS)  # tie-break order
# "Urgencias" in these questions is the emergency process, not the bed group.
NO_SERVICE_SCOPE = {"espera", "causa_espera", "servicio_demanda", "inventario", "rotacion", "alertas", "registros"}


def detect_intent(q: str) -> tuple[str, int]:
    best, score = "ayuda", 0
    for name in PRIORITY:
        hits = sum(bool(re.search(p, q)) for p in INTENTS[name])
        if hits > score:
            best, score = name, hits
    return best, score


# ── Agent ────────────────────────────────────────────────────────────────────

class HospitalAgent:
    """Stateless agent; conversation history lives in the caller."""

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    # Public ---------------------------------------------------------------

    def ask(self, question: str, fs: FilterState, view_context: dict | None = None,
            force_llm: bool = False) -> AgentAnswer:
        started = time.perf_counter()
        q = normalize(question)
        ref = get_data().reference_date.date()
        intent, score = detect_intent(q)
        scoped, period_label = self._scope(q, fs, ref, parse_service=intent not in NO_SERVICE_SCOPE)

        asks_identity = re.search(r"\bnombres?\b|cedula|documento|identificacion|telefono|direccion|datos personales", q)
        if (intent == "privacidad" and score >= 2) or (asks_identity and re.search(r"paciente|persona|usuario", q)):
            answer = self._privacy()
        elif (force_llm or intent == "ayuda") and self.use_llm and llm_available():
            answer = self._nl2sql(question, scoped, view_context)
            if answer is None:  # invalid SQL, empty result or timeout → Plan B
                answer = self._rules(intent, q, scoped, period_label, view_context)
                answer.text = ("_NL2SQL no produjo un resultado válido; respondo con el motor de reglas._\n\n"
                               + answer.text)
        else:
            answer = self._rules(intent, q, scoped, period_label, view_context)

        answer.scope = answer.scope or scoped.describe()
        answer.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return answer

    # Scope ----------------------------------------------------------------

    @staticmethod
    def _scope(q: str, fs: FilterState, ref: date, parse_service: bool = True) -> tuple[FilterState, str | None]:
        """Question overrides (period / service) on top of the user's filters."""
        scoped = fs
        period = parse_period(q, ref)
        label = None
        if period:
            start, end, label = period
            scoped = scoped.with_dates(start, end)
        services = parse_services(q) if parse_service else ()
        if services:
            scoped = replace(scoped, services=services)
        return scoped, label

    # Rules ----------------------------------------------------------------

    def _rules(self, intent: str, q: str, fs: FilterState, period: str | None, ctx: dict | None) -> AgentAnswer:
        handler = getattr(self, f"_i_{intent}", None)
        if handler is None:
            return self._help(ctx)
        answer = handler(q, fs, period)
        answer.intent = intent
        return answer

    def _i_camas(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        # Bed questions default to the census on the last day of the window.
        occ = kpis.occupancy_by_service(fs)
        day = es_date(fs.date_to)
        if len(fs.services) == 1 and len(occ):
            r = occ.iloc[0]
            status = ("⚠️ Por encima del umbral de alerta." if r.ocupacion_hoy >= config.OCCUPANCY_WARNING
                      else "Dentro del rango operativo.")
            text = (f"**{r.servicio}: {int(r.ocupadas_hoy)} de {int(r.camas)} camas ocupadas** "
                    f"({pct(r.ocupacion_hoy)}) al corte del {day}. Quedan **{int(r.disponibles_hoy)} disponibles**. "
                    f"{status}\n\nPromedio del periodo: {pct(r.ocupacion_promedio)} · pico {pct(r.pico)}.")
        else:
            total_occ, total_beds = occ["ocupadas_hoy"].sum(), occ["camas"].sum()
            top = occ.iloc[0]
            text = (f"Ocupación global al {day}: **{pct(total_occ / total_beds)}** "
                    f"({num(total_occ)} de {num(total_beds)} camas). El servicio más presionado es "
                    f"**{top.servicio} ({pct(top.ocupacion_hoy)})**.")
            hot = occ[occ["ocupacion_hoy"] >= config.OCCUPANCY_WARNING]
            if len(hot):
                spare = occ.sort_values("disponibles_hoy", ascending=False).iloc[0]
                text += (f"\n\n**Recomendación:** {', '.join(hot['servicio'])} superan el "
                         f"{pct(config.OCCUPANCY_WARNING)}; considerar abrir camas o trasladar pacientes "
                         f"elegibles a {spare.servicio} ({int(spare.disponibles_hoy)} libres).")
        table = occ.assign(**{"Ocupación": occ["ocupacion_hoy"].map(pct), "Promedio": occ["ocupacion_promedio"].map(pct)})
        table = table.rename(columns={"servicio": "Servicio", "camas": "Camas", "ocupadas_hoy": "Ocupadas",
                                      "disponibles_hoy": "Disponibles"})[["Servicio", "Camas", "Ocupadas", "Disponibles", "Ocupación", "Promedio"]]
        chart = {"type": "barh", "data": occ, "x": "ocupacion_hoy", "y": "servicio", "title": "Ocupación por servicio", "format": "pct"}
        return AgentAnswer(text, table, chart, followups=[
            "¿Qué servicio tiene más pacientes ingresados este mes?", "Dame las alertas activas"])

    def _i_inventario(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        days = parse_number(q, config.STOCK_CRITICAL_DAYS)
        inv = kpis.inventory_status(days)
        simulated = get_data().inventory_is_simulated
        note = ("\n\n_Nota: el extracto HIS no trae niveles de stock; la mayoría de existencias aún son simuladas a partir "
                "del consumo real. Farmacia puede registrar conteos en **Registro clínico → Inventario**._") if simulated else ""
        if inv.empty:
            return AgentAnswer(f"No hay medicamentos ni insumos con menos de {days} días de inventario.{note}")
        worst = inv.iloc[0]
        text = (f"Hay **{len(inv)} medicamentos/insumos con menos de {days} días de inventario**. "
                f"El más crítico es **{worst['nombre']}** con {num(worst['dias_inventario'], 1)} días "
                f"(consumo {num(worst['consumo_diario'], 1)} u/día).\n\n**Recomendación:** emitir orden de compra "
                f"urgente para los {min(len(inv), 5)} primeros y revisar sustitutos terapéuticos.{note}")
        table = inv.head(25).rename(columns={"nombre": "Medicamento / insumo", "stock": "Stock",
                                             "consumo_diario": "Consumo/día", "dias_inventario": "Días", "estado": "Estado"})
        table = table[["Medicamento / insumo", "Stock", "Consumo/día", "Días", "Estado"]]
        chart = {"type": "barh", "data": inv.head(12), "x": "dias_inventario", "y": "nombre", "title": "Días de inventario restantes"}
        return AgentAnswer(text, table, chart, followups=["¿Qué medicamentos tienen mayor rotación?", "Dame las alertas activas"])

    def _i_rotacion(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        rot = kpis.med_rotation(fs)
        if rot.empty:
            return AgentAnswer("No hay dispensaciones en el periodo seleccionado.")
        low = "menos" in q or "menor" in q
        view = rot.tail(10).iloc[::-1] if low else rot.head(10)
        label = "menor" if low else "mayor"
        a_share = (rot["clase_abc"] == "A").mean()
        text = (f"Los ítems de **{label} rotación** en {fs.describe().split(' · ')[0]} están liderados por "
                f"**{view.iloc[0]['nombre']}** ({num(view.iloc[0]['cantidad'])} unidades). "
                f"El {pct(a_share)} de los ítems (clase A) concentra el 80 % del volumen dispensado: "
                "priorizar su control de inventario.")
        table = view.rename(columns={"nombre": "Ítem", "cantidad": "Unidades", "dispensaciones": "Dispensaciones",
                                     "clase_abc": "Clase ABC"})[["Ítem", "Unidades", "Dispensaciones", "Clase ABC"]]
        chart = {"type": "barh", "data": view, "x": "cantidad", "y": "nombre", "title": f"Ítems de {label} rotación"}
        return AgentAnswer(text, table, chart, followups=["¿Cuáles medicamentos tienen menos de 5 días de inventario?"])

    def _i_espera(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        w = kpis.wait_by_triage(fs)
        if w.empty:
            return AgentAnswer("No hay registros de triage con atención en el periodo seleccionado.")
        total = (w["promedio"] * w["pacientes"]).sum() / w["pacientes"].sum()
        slowest = w.sort_values("mediana", ascending=False).iloc[0]
        when = period or fs.describe().split(" · ")[0]
        text = (f"Tiempo promedio triage → primera atención en urgencias ({when}): **{minutes(total)}** "
                f"sobre {num(w['pacientes'].sum())} pacientes. Meta: {config.WAIT_TARGET_MIN} min.\n\n"
                + "\n".join(f"- {r.nivel}: mediana **{minutes(r.mediana)}** · {pct(r.en_meta)} dentro de meta"
                            for r in w.itertuples())
                + f"\n\nEl nivel con mayor demora es **{slowest.nivel}**.")
        rc = kpis.wait_root_cause(fs)
        if rc.get("cause"):
            c = rc["cause"]
            text += f" Posible causa: más pacientes triage {c['nivel']} en turno {c['turno'].lower()}."
        table = w.rename(columns={"nivel": "Nivel", "pacientes": "Pacientes"}).assign(
            **{"Promedio": w["promedio"].map(minutes), "Mediana": w["mediana"].map(minutes),
               "P90": w["p90"].map(minutes), "En meta": w["en_meta"].map(pct)})[["Nivel", "Pacientes", "Promedio", "Mediana", "P90", "En meta"]]
        chart = {"type": "bar", "data": w, "x": "nivel", "y": "mediana", "title": "Espera mediana por nivel (min)", "triage": True}
        return AgentAnswer(text, table, chart, followups=["¿Por qué aumentó el tiempo de espera?", "¿En qué horas llegan más pacientes?"])

    def _i_causa_espera(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        rc = kpis.wait_root_cause(fs)
        matrix = rc["matrix"].round(0)
        if matrix.empty:
            return AgentAnswer("No hay datos suficientes de triage para analizar la causa.")
        trend = ("aumentó" if pd.notna(rc["previous"]) and rc["current"] > rc["previous"] else "se mantuvo o bajó")
        text = (f"La espera promedio {trend}: **{minutes(rc['current'])}** vs {minutes(rc['previous'])} "
                "en el periodo anterior de igual duración.")
        if rc.get("cause"):
            c = rc["cause"]
            text += (f"\n\n**Causa raíz probable:** crecimiento de pacientes **triage {c['nivel']} en turno "
                     f"{c['turno'].lower()}** (+{num(c['delta_diario'], 1)} pacientes/día"
                     + (f", {signed_pct(c['delta_pct'])}" if pd.notna(c["delta_pct"]) else "") + ").\n\n"
                     f"**Recomendación:** reforzar el turno {c['turno'].lower()} con un médico adicional en "
                     f"consultorios de triage {c['nivel']}.")
        worst = matrix.stack().idxmax()
        text += f"\n\nLa celda más lenta es turno {worst[0].lower()} · triage {int(worst[1])} ({minutes(matrix.stack().max())})."
        table = matrix.rename(columns=lambda c: f"Triage {int(c)}").reset_index().rename(columns={"turno": "Turno"})
        chart = {"type": "heatmap", "data": rc["matrix"], "title": "Espera promedio (min) · turno × triage"}
        return AgentAnswer(text, table, chart, followups=["¿Cuál es el tiempo de espera promedio en urgencias en la última semana?"])

    def _i_llegadas(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        grid = kpis.arrivals_heatmap(fs).copy()  # cached object: never mutate in place
        grid.index = [DAYS[i] for i in range(7)]
        by_hour = grid.sum(axis=0)
        by_day = grid.sum(axis=1)
        peak_day, peak_hour = grid.stack().idxmax()
        top_hours = by_hour.nlargest(3).index
        text = (f"La mayor afluencia es el **{peak_day} a las {peak_hour:02d}:00** "
                f"(~{num(grid.stack().max(), 1)} ingresos por semana en esa hora). Las horas pico del día son "
                + ", ".join(f"{h:02d}:00" for h in sorted(top_hours))
                + f"; el día más cargado es **{by_day.idxmax()}** y el más tranquilo {by_day.idxmin()}.\n\n"
                "**Recomendación:** concentrar refuerzos de personal en triage entre "
                f"{min(top_hours):02d}:00 y {max(top_hours) + 1:02d}:00.")
        heat = grid.copy()
        heat.columns = [f"{h:02d}h" for h in heat.columns]
        chart = {"type": "heatmap_raw", "data": heat, "title": "Ingresos promedio por semana · día × hora"}
        table = by_hour.rename("Ingresos/semana").round(1).reset_index().rename(columns={"index": "Hora"})
        return AgentAnswer(text, table, chart, followups=["¿Cuál es el tiempo de espera promedio en urgencias en la última semana?"])

    def _i_cirugia(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        s = kpis.surgery_summary(fs)
        text = (f"En {period or fs.describe().split(' · ')[0]} se **programaron {num(s['programadas'])} cirugías** y "
                f"**{num(s['ejecutadas'])} se ejecutaron** (cumplimiento **{pct(s['cumplimiento'])}**).")
        if len(s["por_quirofano"]):
            top = s["por_quirofano"].iloc[0]
            text += f" El quirófano con más actividad es **{top['quirofano']}** ({num(top['ejecutadas'])})."
        extra = insights.generate(fs, "Cirugía")
        if extra:
            text += "\n\n**Recomendación:** " + " ".join(i.action for i in extra)
        table = s["por_quirofano"].rename(columns={"quirofano": "Quirófano", "ejecutadas": "Ejecutadas"})
        chart = {"type": "barh", "data": s["por_quirofano"], "x": "ejecutadas", "y": "quirofano", "title": "Cirugías ejecutadas por quirófano"}
        return AgentAnswer(text, table, chart, followups=["¿Qué días tienen quirófanos subutilizados?", "Dame las alertas activas"])

    def _i_servicio_demanda(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        by = kpis.admissions_by_service(fs)
        if by.empty:
            return AgentAnswer("No hay ingresos en el periodo seleccionado.")
        low = "menos" in q or "menor" in q
        top = by.iloc[-1] if low else by.iloc[0]
        text = (f"En {period or fs.describe().split(' · ')[0]}, el servicio con {'menos' if low else 'más'} pacientes ingresados es "
                f"**{top.servicio}** con **{num(top.ingresos)} ingresos** ({num(top.pacientes)} pacientes únicos), "
                f"{pct(top.ingresos / by['ingresos'].sum())} del total. Estancia media: {num(top.estancia_media_h / 24, 1)} días.")
        table = by.rename(columns={"servicio": "Servicio", "ingresos": "Ingresos", "pacientes": "Pacientes"})
        table["Estancia media (d)"] = (by["estancia_media_h"] / 24).round(1)
        table = table[["Servicio", "Ingresos", "Pacientes", "Estancia media (d)"]]
        chart = {"type": "barh", "data": by, "x": "ingresos", "y": "servicio", "title": "Ingresos por servicio"}
        return AgentAnswer(text, table, chart, followups=["¿Qué especialidades son las más solicitadas?", "¿Cuántas camas de UCI están ocupadas hoy?"])

    def _i_especialidad(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        sp = kpis.demand_by_specialty(fs)
        top = sp.iloc[0]
        text = (f"Las especialidades más solicitadas son **{str(top.especialidad).capitalize()}** "
                f"({num(top.servicios)} servicios en {num(top.episodios)} episodios), seguida de "
                + ", ".join(str(s).capitalize() for s in sp["especialidad"].iloc[1:4]) + ".")
        table = sp.rename(columns={"especialidad": "Especialidad", "servicios": "Servicios", "episodios": "Episodios"})
        chart = {"type": "barh", "data": sp.assign(especialidad=sp["especialidad"].astype(str).str.capitalize()),
                 "x": "servicios", "y": "especialidad", "title": "Servicios por especialidad"}
        return AgentAnswer(text, table, chart, followups=["¿Qué servicio tiene más pacientes ingresados este mes?"])

    def _i_diagnosticos(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        dx = kpis.top_diagnoses(fs)
        if dx.empty:
            return AgentAnswer("No hay diagnósticos registrados en el periodo.")
        chapters = filter_admissions(get_data(), fs)["capitulo_dx"].value_counts()
        text = (f"El diagnóstico más frecuente es **{dx.iloc[0]['diagnostico']}** ({num(dx.iloc[0]['ingresos'])} ingresos). "
                f"Por capítulo CIE-10 predominan **{chapters.index[0]}** ({pct(chapters.iloc[0] / chapters.sum())}) y "
                f"{chapters.index[1]} ({pct(chapters.iloc[1] / chapters.sum())}).\n\n_Datos agregados: no se muestran diagnósticos individuales._")
        table = dx.rename(columns={"diagnostico": "Diagnóstico", "capitulo_dx": "Capítulo", "ingresos": "Ingresos"})[["Diagnóstico", "Capítulo", "Ingresos"]]
        chart = {"type": "barh", "data": dx, "x": "ingresos", "y": "diagnostico", "title": "Top diagnósticos"}
        return AgentAnswer(text, table, chart, followups=["¿Qué se espera para las próximas dos semanas?"])

    def _i_pronostico(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        base = replace(fs, date_from=get_data().min_date.date())  # use full history for the model
        fc = kpis.forecast_admissions(base)
        tr = kpis.diagnosis_trends(base)
        future = fc.dropna(subset=["pronostico"])
        if future.empty:
            return AgentAnswer("No hay historia suficiente para pronosticar (mínimo 3 semanas).")
        nxt, last = future["pronostico"].head(7).sum(), fc["real"].dropna().tail(7).sum()
        peak = future.loc[future["pronostico"].idxmax()]
        text = (f"**Pronóstico próximos 7 días: ~{num(nxt)} ingresos** ({signed_pct((nxt - last) / last)} vs la última semana). "
                f"Pico esperado el **{es_date(peak['fecha'], weekday=True, year=False)}** (~{num(peak['pronostico'])} ingresos).")
        rising = tr[(tr["variacion"] >= config.DEMAND_SPIKE) & (tr["base_dia"] >= 2)].head(3)
        if len(rising):
            text += "\n\n**Alertas predictivas:** " + "; ".join(
                f"causas {r.capitulo_dx.lower()} {signed_pct(r.variacion)}" for r in rising.itertuples()) + "."
            meds = insights.CHAPTER_MEDS.get(rising.iloc[0]["capitulo_dx"])
            if meds:
                text += f" Recomendación: aumentar stock de {meds}."
        chart = {"type": "forecast", "data": fc, "title": "Ingresos diarios y pronóstico (banda 80 %)"}
        table = tr.head(8).rename(columns={"capitulo_dx": "Capítulo CIE-10"}).assign(
            **{"Reciente/día": tr["reciente_dia"].round(1), "Base/día": tr["base_dia"].round(1),
               "Variación": tr["variacion"].map(signed_pct)})[["Capítulo CIE-10", "Reciente/día", "Base/día", "Variación"]]
        return AgentAnswer(text, table, chart, followups=["Dame las alertas activas", "¿Cuántas camas de UCI están ocupadas hoy?"])

    def _i_alertas(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        items = insights.generate(fs)
        if not items:
            return AgentAnswer("No hay alertas activas con los filtros actuales. La operación está dentro de los umbrales.")
        icons = {"critical": "🔴", "serious": "🟠", "warning": "🟡", "info": "🔵"}
        text = f"Hay **{len(items)} alertas** con los filtros actuales. Las prioritarias:\n\n" + "\n".join(
            f"{icons[i.severity]} **{i.title}** — {i.action}" for i in items[:5])
        table = pd.DataFrame([{"Severidad": i.severity, "Área": i.category, "Alerta": i.title, "Acción sugerida": i.action} for i in items])
        return AgentAnswer(text, table, followups=["¿Por qué aumentó el tiempo de espera?", "¿Qué se espera para la próxima semana?"])

    def _i_demografia(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        demo = kpis.demographics(fs)
        key = next((k for k, pats in {"regimen": ["regimen", "eps", "asegurador"], "sexo": ["sexo", "mujer", "hombre"],
                                     "zona": ["zona", "rural", "urban"], "grupo_etario": ["edad"]}.items()
                    if any(p in q for p in pats)), "grupo_etario")
        series = demo[key]
        labels = {"regimen": "régimen", "sexo": "sexo", "zona": "zona", "grupo_etario": "grupo de edad"}
        text = (f"Distribución de ingresos por **{labels[key]}**: " + ", ".join(
            f"{k} {pct(v / series.sum())}" for k, v in series.sort_values(ascending=False).items()) + ".")
        df = series.rename("ingresos").reset_index().rename(columns={key: "categoria"})
        chart = {"type": "donut", "data": df, "x": "categoria", "y": "ingresos", "title": f"Ingresos por {labels[key]}"}
        return AgentAnswer(text, df.rename(columns={"categoria": labels[key].capitalize(), "ingresos": "Ingresos"}), chart)

    def _i_ingresos(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        a = filter_admissions(get_data(), fs)
        by_route = a["via_ingreso"].value_counts()
        by_route = by_route[by_route > 0]
        text = (f"En {period or fs.describe().split(' · ')[0]} hubo **{num(len(a))} ingresos** "
                f"({num(a['id_paciente'].nunique())} pacientes únicos). Por vía: "
                + ", ".join(f"{k} {num(v)}" for k, v in by_route.items()) + ".")
        daily = kpis.admissions_daily(fs, "clase_ingreso")
        chart = {"type": "line", "data": daily, "x": "fecha", "y": "ingresos", "color": "clase_ingreso", "title": "Ingresos diarios por clase"}
        table = by_route.rename("Ingresos").reset_index().rename(columns={"via_ingreso": "Vía de ingreso"})
        return AgentAnswer(text, table, chart, followups=["¿Qué servicio tiene más pacientes ingresados este mes?"])

    def _i_registros(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        """Activity captured in the app (from the audit trail, without names)."""
        from core import records  # local import: records is only needed here

        today = date.today()
        since = today if "hoy" in q else today - timedelta(days=6)
        log = records.audit_log(since=since.strftime("%Y-%m-%d"))
        log = log[~log["accion"].isin(["INICIO_SESION", "CIERRE_SESION", "INICIO_FALLIDO"])]
        label = "hoy" if since == today else "los últimos 7 días"
        if log.empty:
            return AgentAnswer(f"No hay cambios registrados en la aplicación {label}.", intent="registros")
        summary = log.groupby(["entidad", "accion"]).size().rename("eventos").reset_index()
        active = len(records.list_admissions("Activo"))
        text = (f"En {label} se registraron **{num(len(log))} cambios** en la aplicación: "
                + "; ".join(f"{r.entidad} {r.accion.lower()} ×{r.eventos}" for r in summary.itertuples())
                + f". Hay **{num(active)} ingresos activos** capturados en la app.\n\n"
                "_El detalle con responsables está en **Gestión → Auditoría** (requiere rol autorizado)._")
        table = summary.rename(columns={"entidad": "Entidad", "accion": "Acción", "eventos": "Eventos"})
        return AgentAnswer(text, table, followups=["Resumen de la situación"])

    def _i_resumen(self, q: str, fs: FilterState, period: str | None) -> AgentAnswer:
        o = kpis.overview(fs)
        items = insights.generate(fs)
        text = (f"**Resumen ({fs.describe()})**\n\n"
                f"- Ingresos: **{num(o['ingresos'])}** ({num(o['ingresos_dia'], 1)}/día, {signed_pct(o['ingresos_delta'])} vs periodo anterior)\n"
                f"- Ocupación al corte: **{pct(o['ocupacion'])}** ({num(o['camas_ocupadas'])}/{num(o['camas_total'])} camas)\n"
                f"- Espera triage → atención: **{minutes(o['espera'])}** ({pct(o['espera_en_meta'])} en meta)\n"
                f"- Cirugías: {num(o['cirugias_ejecutadas'])}/{num(o['cirugias_programadas'])} ejecutadas ({pct(o['cumplimiento_cx'])})\n"
                f"- Farmacia: **{o['meds_criticos']} ítems críticos** (< {config.STOCK_CRITICAL_DAYS} días)")
        if items:
            text += f"\n\n**Prioridad #1:** {items[0].title}. {items[0].action}"
        return AgentAnswer(text, followups=["Dame las alertas activas", "¿Qué se espera para la próxima semana?"])

    # Special answers ---------------------------------------------------------

    @staticmethod
    def _privacy() -> AgentAnswer:
        return AgentAnswer(
            "🔒 No puedo entregar información personal identificable (nombres, documentos, fechas de nacimiento "
            "o diagnósticos de un paciente específico). Puedo darte **datos agregados**: por ejemplo, ingresos por "
            "servicio, distribución por edad o los diagnósticos más frecuentes.",
            source="privacidad", intent="privacidad",
            followups=["¿Cuáles son los diagnósticos más frecuentes?", "Distribución de ingresos por edad"])

    @staticmethod
    def _help(ctx: dict | None) -> AgentAnswer:
        suggestions = (ctx or {}).get("suggestions") or [
            "¿Cuántas camas de UCI están ocupadas hoy?",
            "¿Cuáles son los medicamentos con menos de 5 días de inventario?",
            "¿Cuál es el tiempo de espera promedio en urgencias en la última semana?",
            "¿Qué servicio tiene más pacientes ingresados este mes?",
        ]
        extra = "" if llm_available() else (
            "\n\n_El modelo local (Ollama) no está disponible, así que respondo con el motor de reglas._")
        return AgentAnswer(
            "No identifiqué la consulta. Puedo responder sobre **camas y ocupación, tiempos de espera, cirugías, "
            "inventario y rotación de medicamentos, demanda de servicios, diagnósticos, pronósticos y alertas**." + extra,
            source="ayuda", intent="ayuda", followups=suggestions[:4])

    # NL2SQL ------------------------------------------------------------------

    def _nl2sql(self, question: str, fs: FilterState, ctx: dict | None) -> AgentAnswer | None:
        """LLM path. Returns ``None`` on any failure (or empty result) so the caller falls back.

        The model only *writes SQL*. The narrative is built deterministically
        from the returned rows, so the answer can never contain invented figures.
        """
        try:
            conn, schema = analytics_db()
            prompt = NL2SQL_PROMPT.format(
                schema=schema, ref=get_data().reference_date.date(),
                context=_context_text(fs, ctx), question=question)
            sql = sanitize(extract_sql(_llm(prompt)), set(ANALYTIC_TABLES))
            df = pd.read_sql_query(sql, conn)
        except (UnsafeSQLError, requests.RequestException, sqlite3.Error, ValueError, KeyError, TimeoutError, OSError):
            return None
        hidden = config.PII_COLUMNS | {"oid_ingreso", "paciente_ref"}
        df = df.drop(columns=[c for c in df.columns if c.lower() in hidden])
        if df.empty or df.shape[1] == 0:
            return None
        chart = None
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if len(df) > 1 and numeric and df.columns[0] not in numeric:
            chart = {"type": "barh" if len(df) <= 15 else "line", "data": df.head(30),
                     "x": numeric[-1], "y": df.columns[0], "title": "Resultado"}
        return AgentAnswer(describe_result(df), df, chart, sql=sql, source="nl2sql", intent="nl2sql",
                           followups=["Dame las alertas activas", "Resumen de la situación"])


def _cell(value) -> str:
    if isinstance(value, float):
        return num(value, 0 if abs(value) >= 100 else 1)
    if isinstance(value, int):
        return num(value)
    return str(value)


def describe_result(df: pd.DataFrame) -> str:
    """Plain, faithful narrative of a query result (no generated numbers)."""
    if len(df) == 1:
        pairs = ", ".join(f"**{c.replace('_', ' ')}**: {_cell(v)}" for c, v in df.iloc[0].items())
        return f"Resultado de la consulta: {pairs}."
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    text = f"La consulta devolvió **{num(len(df))} filas**."
    label = df.columns[0]
    if numeric and label not in numeric:
        value = numeric[-1]
        ranked = df.sort_values(value, ascending=False)
        top, low = ranked.iloc[0], ranked.iloc[-1]
        text += (f" Mayor **{value.replace('_', ' ')}**: {top[label]} ({_cell(top[value])}); "
                 f"menor: {low[label]} ({_cell(low[value])}).")
        if value.lower().startswith(("count", "ingresos", "total", "cantidad", "n_")):
            text += f" Total: {_cell(df[value].sum())}."
    return text + "\n\n_Consulta generada con NL2SQL y validada (solo lectura). Revisa el SQL en «¿Cómo lo calculé?»._"


# ── LLM plumbing (Ollama) ────────────────────────────────────────────────────

_LLM_STATE = {"checked": 0.0, "ok": False}


def llm_available() -> bool:
    """Ping Ollama at most every 30 s (cheap, non-blocking for the UI)."""
    now = time.time()
    if now - _LLM_STATE["checked"] > 30:
        try:
            r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=0.6)
            models = [m.get("name", "") for m in r.json().get("models", [])]
            _LLM_STATE["ok"] = any(m.startswith(config.OLLAMA_MODEL) for m in models)
        except (requests.RequestException, ValueError):
            _LLM_STATE["ok"] = False
        _LLM_STATE["checked"] = now
    return _LLM_STATE["ok"]


def _llm(prompt: str) -> str:
    from langchain_ollama import ChatOllama  # imported lazily: optional dependency path

    llm = ChatOllama(model=config.OLLAMA_MODEL, base_url=config.OLLAMA_URL, temperature=0,
                     num_predict=320, num_ctx=4096, keep_alive="30m",
                     client_kwargs={"timeout": config.OLLAMA_TIMEOUT_S})
    return llm.invoke(prompt).content


ANALYTIC_TABLES = ("ingresos", "servicios", "medicamentos", "cirugias", "ocupacion_diaria", "inventario")
_ANALYTICS: dict = {"version": None, "db": None}


def analytics_db() -> tuple[sqlite3.Connection, str]:
    """In-memory, read-only SQLite with anonymised analytic views.

    Rebuilt when the operational data changes, so NL2SQL also sees records
    captured in the app. The schema text includes the valid values of each
    categorical column: small models write far better filters with them.
    """
    data = get_data()
    if _ANALYTICS["version"] == data.version and _ANALYTICS["db"] is not None:
        return _ANALYTICS["db"]
    a = data.admissions
    tables = {
        "ingresos": pd.DataFrame({
            "oid_ingreso": a["oid_ingreso"], "paciente_ref": a["id_paciente"].rank(method="dense").astype(int),
            "fecha_ingreso": a["fecha_ingreso"].dt.strftime("%Y-%m-%d %H:%M"), "servicio": a["servicio"].astype(str),
            "via_ingreso": a["via_ingreso"].astype(str), "clase_ingreso": a["clase_ingreso"].astype(str),
            "tipo_riesgo": a["tipo_riesgo"].astype(str), "nivel_triage": a["nivel_triage"],
            "espera_min": a["espera_min"], "turno": a["turno"].astype(str), "capitulo_dx": a["capitulo_dx"].astype(str),
            "codigo_diagnostico": a["codigo_diagnostico"], "nombre_diagnostico": a["nombre_diagnostico"],
            "estancia_h": a["estancia_h"], "grupo_etario": a["grupo_etario"].astype(str), "sexo": a["sexo"].astype(str),
            "regimen": a["regimen"].astype(str), "zona": a["zona"].astype(str), "origen": a["origen"].astype(str),
        }),
        "servicios": data.services.assign(fecha_prestacion=data.services["fecha_prestacion"].dt.strftime("%Y-%m-%d %H:%M")),
        "medicamentos": data.meds.assign(fecha_prestacion=data.meds["fecha_prestacion"].dt.strftime("%Y-%m-%d %H:%M")),
        "cirugias": data.surgeries.rename(columns={"consecutivo_programacion": "cirugia"}).assign(
            fecha=lambda d: d["fecha"].dt.strftime("%Y-%m-%d %H:%M")),
        "ocupacion_diaria": kpis.daily_census(FilterState.default(data)).assign(
            fecha=lambda d: d["fecha"].dt.strftime("%Y-%m-%d")),
        "inventario": data.inventory[["codigo_servicio", "nombre_servicio", "stock", "consumo_diario",
                                      "dias_inventario", "estado", "fecha_vencimiento"]].assign(
            fecha_vencimiento=lambda d: d["fecha_vencimiento"].dt.strftime("%Y-%m-%d")),
    }
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    schema_lines = []
    for name, df in tables.items():
        df = df.copy()
        for col in df.columns:
            if isinstance(df[col].dtype, pd.CategoricalDtype):
                df[col] = df[col].astype(str)
        df.to_sql(name, conn, index=False)
        schema_lines.append(f"{name}({', '.join(df.columns)})")
        if name in ("ingresos", "cirugias", "inventario"):
            for col in df.columns:
                if df[col].dtype == object and 1 < df[col].nunique() <= 25 and not col.startswith("fecha"):
                    values = ", ".join(f"'{v}'" for v in sorted(df[col].dropna().astype(str).unique()))
                    schema_lines.append(f"  - {name}.{col} ∈ {{{values}}}")
    conn.execute("PRAGMA query_only = ON")
    _ANALYTICS.update(version=data.version, db=(conn, "\n".join(schema_lines)))
    return _ANALYTICS["db"]


def _context_text(fs: FilterState, ctx: dict | None) -> str:
    parts = [f"Filtros activos (úsalos si la pregunta no indica otro periodo): {fs.describe()}"]
    if ctx:
        parts.append(f"Vista actual: {ctx.get('view', '')}")
        for k, v in (ctx.get("highlights") or {}).items():
            parts.append(f"- {k}: {v}")
    return "\n".join(parts)


NL2SQL_PROMPT = """Eres un analista de datos hospitalarios. Escribe UNA consulta SQLite (solo SELECT) que responda la pregunta.

Esquema (tablas anonimizadas) y valores válidos:
{schema}

Reglas:
- Fecha de corte ("hoy"): {ref}. Las fechas son texto 'YYYY-MM-DD HH:MM'; compara siempre con date(columna).
- Si la pregunta menciona un servicio (UCI, Pediatría…) filtra por ingresos.servicio con el valor exacto de la lista.
- Usa exactamente los valores listados; para texto libre usa lower(col) LIKE '%palabra%'.
- espera_min = minutos de triage a primera atención; nivel_triage 1 (más urgente) a 5.
- servicios, medicamentos y cirugias se unen con ingresos por oid_ingreso.
- Devuelve agregados; nunca selecciones oid_ingreso ni paciente_ref.

Ejemplos:
P: ¿Cuántas camas de UCI están ocupadas hoy?
SQL: SELECT servicio, ocupadas, camas, ROUND(100.0*ocupadas/camas,1) AS pct FROM ocupacion_diaria WHERE servicio='UCI' AND fecha='{ref}'
P: ¿Qué servicio tiene más pacientes ingresados este mes?
SQL: SELECT servicio, COUNT(*) AS ingresos FROM ingresos WHERE date(fecha_ingreso) >= date('{ref}','start of month') GROUP BY servicio ORDER BY ingresos DESC
P: ¿Cuántos ingresos por accidentes de tránsito hubo en agosto por servicio?
SQL: SELECT servicio, COUNT(*) AS ingresos FROM ingresos WHERE lower(tipo_riesgo) LIKE '%transito%' AND date(fecha_ingreso) BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY servicio ORDER BY ingresos DESC

Contexto del usuario:
{context}

Pregunta: {question}
Responde solo con la consulta en un bloque ```sql```."""
