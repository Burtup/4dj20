"""Hospital KPIs requested by the challenge (section 5 of the brief).

Every public function takes a :class:`~core.filters.FilterState`, returns
plain pandas objects and is memoised, so the dashboard, the agent and the
REST API all compute *exactly* the same numbers.

KPI map (brief → function):

* Ocupación hospitalaria ........ :func:`daily_census`, :func:`occupancy_by_service`
* Tiempos de espera por triage .. :func:`wait_by_triage`, :func:`wait_root_cause`
* Eficiencia de quirófanos ...... :func:`surgery_summary`, :func:`or_usage_heatmap`
* Consumo de medicamentos ....... :func:`med_rotation`, :func:`med_consumption_trend`
* Demanda de servicios .......... :func:`demand_by_specialty`, :func:`top_diagnoses`
* Extra (predictivo) ............ :func:`forecast_admissions`, :func:`diagnosis_trends`
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache, wraps

import numpy as np
import pandas as pd

from core import config
from core.data import get_data, midnight_census, pseudonym
from core.filters import FilterState, filter_admissions, scope_lines

_CACHED = []
WEEKDAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _memo(func):
    """lru_cache that can be cleared globally via :func:`clear_cache`."""
    cached = lru_cache(maxsize=64)(func)
    _CACHED.append(cached)

    @wraps(func)
    def wrapper(*args, **kwargs):
        return cached(*args, **kwargs)

    return wrapper


def clear_cache() -> None:
    for fn in _CACHED:
        fn.cache_clear()


def _trend_window(fs: FilterState) -> FilterState:
    """Cap trend comparisons at the HIS cut-off.

    After the extract ends only app records exist, so "last 7 days vs
    baseline" windows reaching past it would show artificial drops.
    """
    cutoff = get_data().his_cutoff.date()
    if fs.date_to <= cutoff:
        return fs
    start = min(fs.date_from, cutoff)
    return fs.with_dates(start, cutoff)


def _previous(fs: FilterState) -> FilterState:
    """Same-length window immediately before ``fs`` (for deltas)."""
    span = (fs.date_to - fs.date_from).days + 1
    end = fs.date_from - timedelta(days=1)
    return fs.with_dates(end - timedelta(days=span - 1), end)


# ── Occupancy ────────────────────────────────────────────────────────────────

@_memo
def daily_census(fs: FilterState) -> pd.DataFrame:
    """Midnight census per service and day: occupied beds / capacity.

    A stay counts on day *d* if the patient is in the bed at 00:00 of *d+1*
    (standard midnight census). Stays that started before the window but are
    still active inside it are included.
    """
    data = get_data()
    out = midnight_census(filter_admissions(data, fs, ignore_dates=True))
    out = out[out["fecha"].between(pd.Timestamp(fs.date_from), pd.Timestamp(fs.date_to))].copy()
    out["camas"] = out["servicio"].map(data.bed_capacity).astype(float)
    out["ocupacion"] = (out["ocupadas"] / out["camas"]).clip(upper=1.5)
    return out


@_memo
def occupancy_by_service(fs: FilterState) -> pd.DataFrame:
    """Occupancy on the last day of the window and the window average."""
    census = daily_census(fs)
    cap = get_data().bed_capacity
    services = list(fs.services) or list(cap.index)
    last_day = census[census["fecha"] == census["fecha"].max()].set_index("servicio")["ocupadas"] if len(census) else pd.Series(dtype=float)
    avg = census.groupby("servicio", observed=True)["ocupadas"].mean()
    peak = census.groupby("servicio", observed=True)["ocupacion"].max()
    out = pd.DataFrame({"camas": cap.reindex(services)})
    out["ocupadas_hoy"] = last_day.reindex(services).fillna(0).astype(int)
    out["ocupacion_hoy"] = out["ocupadas_hoy"] / out["camas"]
    out["ocupacion_promedio"] = avg.reindex(services).fillna(0) / out["camas"]
    out["pico"] = peak.reindex(services).fillna(0)
    out["disponibles_hoy"] = (out["camas"] - out["ocupadas_hoy"]).clip(lower=0)
    return out.reset_index(names="servicio").sort_values("ocupacion_hoy", ascending=False)


@_memo
def bed_map(fs: FilterState) -> pd.DataFrame:
    """State of every known bed at midnight after ``fs.date_to``.

    ``estado``: Libre, Ocupada or Prolongada (stay longer than the service's
    90th-percentile length of stay → candidate for discharge planning).
    """
    data = get_data()
    a = filter_admissions(data, fs, ignore_dates=True)
    midnight = pd.Timestamp(fs.date_to) + pd.Timedelta(days=1)
    start = a["fecha_hospitalizacion"].fillna(a["fecha_ingreso"])
    active = a[(start < midnight) & (a["fecha_egreso_est"] >= midnight)]
    active = active.sort_values("fecha_ingreso").drop_duplicates("codigo_cama", keep="last")
    p90 = data.admissions.groupby("servicio", observed=True)["estancia_h"].quantile(0.9)
    days_in = (midnight - active["fecha_ingreso"]).dt.total_seconds() / 86400
    long_stay = days_in * 24 > active["servicio"].map(p90).astype(float)

    beds = data.beds.copy()  # editable bed registry (SQLite)
    if fs.services:
        beds = beds[beds["servicio"].isin(fs.services)]
    occupied = pd.DataFrame({
        "codigo_cama": active["codigo_cama"].astype(str), "dias": days_in.round(1),
        "estado": np.where(long_stay, "Prolongada", "Ocupada"),
        "episodio": active["oid_ingreso"].map(pseudonym), "capitulo_dx": active["capitulo_dx"].astype(str),
        "origen": active["origen"].astype(str),
    })
    out = beds.merge(occupied, on="codigo_cama", how="left")
    # Operational state wins for free beds: a bed in maintenance cannot be used.
    blocked = out["estado"].isna() & (out["estado_operativo"] != "Disponible")
    out.loc[blocked, "estado"] = out.loc[blocked, "estado_operativo"]
    out["estado"] = out["estado"].fillna("Libre")
    return out.sort_values(["servicio", "codigo_cama"]).reset_index(drop=True)


# ── Waiting times ────────────────────────────────────────────────────────────

@_memo
def wait_by_triage(fs: FilterState) -> pd.DataFrame:
    """Triage → first attention minutes per triage level."""
    a = filter_admissions(get_data(), fs).dropna(subset=["espera_min", "nivel_triage"])
    g = a.groupby("nivel_triage")["espera_min"]
    out = pd.DataFrame({
        "pacientes": g.size(),
        "promedio": g.mean(),
        "mediana": g.median(),
        "p90": g.quantile(0.9),
        "en_meta": g.apply(lambda s: (s <= config.WAIT_TARGET_MIN).mean()),
    }).reset_index()
    out["nivel"] = out["nivel_triage"].map(config.TRIAGE_LABELS)
    return out


@_memo
def wait_daily(fs: FilterState) -> pd.DataFrame:
    """Daily average wait (all levels) with a 7-day rolling mean."""
    a = filter_admissions(get_data(), fs).dropna(subset=["espera_min"])
    daily = a.groupby("fecha")["espera_min"].mean().rename("espera").to_frame()
    daily["media_7d"] = daily["espera"].rolling(7, min_periods=3).mean()
    return daily.reset_index()


@_memo
def wait_root_cause(fs: FilterState) -> dict:
    """Root-cause analysis of waiting time (brief, section h).

    Compares the current window with the previous one and attributes the
    change to the shift × triage-level cell whose volume or delay grew most.
    """
    data = get_data()
    fs = _trend_window(fs)
    # Long windows: compare the last 14 days with the 14 before, so the
    # "previous" period always exists inside the data.
    if (fs.date_to - fs.date_from).days >= 28:
        fs = fs.with_dates(fs.date_to - timedelta(days=13), fs.date_to)
    cur = filter_admissions(data, fs).dropna(subset=["espera_min", "nivel_triage"])
    prev = filter_admissions(data, _previous(fs)).dropna(subset=["espera_min", "nivel_triage"])
    matrix = cur.pivot_table(index="turno", columns="nivel_triage", values="espera_min",
                             aggfunc="mean", observed=True)
    volume = cur.pivot_table(index="turno", columns="nivel_triage", values="oid_ingreso",
                             aggfunc="count", observed=True)
    result = {"matrix": matrix, "volume": volume, "cause": None,
              "current": cur["espera_min"].mean(), "previous": prev["espera_min"].mean() if len(prev) else np.nan}
    if len(prev) and len(cur):
        span_cur = max(cur["fecha"].nunique(), 1)
        span_prev = max(prev["fecha"].nunique(), 1)
        vc = cur.groupby(["turno", "nivel_triage"], observed=True).size() / span_cur
        vp = prev.groupby(["turno", "nivel_triage"], observed=True).size() / span_prev
        growth = (vc - vp.reindex(vc.index).fillna(0)).sort_values(ascending=False)
        if len(growth) and growth.iloc[0] > 0:
            shift, level = growth.index[0]
            base = vp.get((shift, level), 0)
            pct = (growth.iloc[0] / base) if base else np.nan
            result["cause"] = {"turno": shift, "nivel": int(level), "delta_diario": growth.iloc[0], "delta_pct": pct}
    return result


# ── Admissions & demand ──────────────────────────────────────────────────────

@_memo
def overview(fs: FilterState) -> dict:
    """Headline numbers for the KPI cards, with deltas vs the previous window."""
    data = get_data()
    cur, prev = filter_admissions(data, fs), filter_admissions(data, _previous(fs))
    occ = occupancy_by_service(fs)
    surg = surgery_summary(fs)
    inv = data.inventory
    hosp = cur[cur["clase_ingreso"] == "Hospitalario"]

    def pct_change(a: float, b: float) -> float | None:
        return None if not b or np.isnan(b) else (a - b) / b

    days = max((fs.date_to - fs.date_from).days + 1, 1)
    return {
        "ingresos": len(cur),
        "ingresos_delta": pct_change(len(cur), len(prev)),
        "ingresos_dia": len(cur) / days,
        "pacientes": cur["id_paciente"].nunique(),
        "ocupacion": occ["ocupadas_hoy"].sum() / occ["camas"].sum() if occ["camas"].sum() else 0,
        "camas_ocupadas": int(occ["ocupadas_hoy"].sum()),
        "camas_total": int(occ["camas"].sum()),
        "espera": cur["espera_min"].mean(),
        "espera_delta": pct_change(cur["espera_min"].mean(), prev["espera_min"].mean()),
        "espera_en_meta": (cur["espera_min"].dropna() <= config.WAIT_TARGET_MIN).mean(),
        "cirugias_programadas": surg["programadas"],
        "cirugias_ejecutadas": surg["ejecutadas"],
        "cumplimiento_cx": surg["cumplimiento"],
        "meds_criticos": int((inv["estado"] == "Crítico").sum()),
        "meds_bajos": int((inv["estado"] == "Bajo").sum()),
        "estancia_media_d": hosp["estancia_h"].mean() / 24 if len(hosp) else np.nan,
    }


@_memo
def admissions_daily(fs: FilterState, by: str = "via_ingreso") -> pd.DataFrame:
    a = filter_admissions(get_data(), fs)
    return a.groupby(["fecha", by], observed=True).size().rename("ingresos").reset_index()


@_memo
def arrivals_heatmap(fs: FilterState) -> pd.DataFrame:
    """Average admissions per weekday × hour (staffing view)."""
    a = filter_admissions(get_data(), fs)
    weeks = max(a["fecha"].nunique() / 7, 1)
    grid = a.groupby([a["fecha_ingreso"].dt.dayofweek, a["fecha_ingreso"].dt.hour]).size() / weeks
    grid = grid.unstack(fill_value=0).reindex(index=range(7), columns=range(24), fill_value=0)
    grid.index = WEEKDAYS
    return grid


@_memo
def demand_by_specialty(fs: FilterState, top: int = 12) -> pd.DataFrame:
    data = get_data()
    lines = scope_lines(data.services, filter_admissions(data, fs), fs)
    out = lines.groupby("especialidad", observed=True).agg(
        servicios=("cantidad", "sum"), episodios=("oid_ingreso", "nunique")).reset_index()
    return out.sort_values("servicios", ascending=False).head(top)


@_memo
def demand_by_area(fs: FilterState, top: int = 12) -> pd.DataFrame:
    data = get_data()
    lines = scope_lines(data.services, filter_admissions(data, fs), fs)
    out = lines.groupby("area_servicio", observed=True).agg(
        servicios=("cantidad", "sum"), episodios=("oid_ingreso", "nunique")).reset_index()
    return out.sort_values("servicios", ascending=False).head(top)


@_memo
def admissions_by_service(fs: FilterState) -> pd.DataFrame:
    a = filter_admissions(get_data(), fs)
    out = a.groupby("servicio", observed=True).agg(
        ingresos=("oid_ingreso", "size"), pacientes=("id_paciente", "nunique"),
        estancia_media_h=("estancia_h", "mean")).reset_index()
    return out.sort_values("ingresos", ascending=False)


@_memo
def top_diagnoses(fs: FilterState, top: int = 10) -> pd.DataFrame:
    a = filter_admissions(get_data(), fs).dropna(subset=["codigo_diagnostico"])
    out = a.groupby(["codigo_diagnostico", "nombre_diagnostico", "capitulo_dx"], observed=True).size()
    out = out.rename("ingresos").reset_index().sort_values("ingresos", ascending=False).head(top)
    out["diagnostico"] = out["codigo_diagnostico"] + " · " + out["nombre_diagnostico"].str.capitalize().str[:60]
    return out


@_memo
def diagnosis_hierarchy(fs: FilterState, per_chapter: int = 6) -> pd.DataFrame:
    """Service → CIE-10 chapter → diagnosis counts (for the sunburst)."""
    a = filter_admissions(get_data(), fs).dropna(subset=["codigo_diagnostico"])
    g = a.groupby(["servicio", "capitulo_dx", "nombre_diagnostico"], observed=True).size().rename("n").reset_index()
    g = g.sort_values("n", ascending=False).groupby(["servicio", "capitulo_dx"], observed=True).head(per_chapter)
    g["nombre_diagnostico"] = g["nombre_diagnostico"].str.capitalize().str[:40]
    return g


@_memo
def demographics(fs: FilterState) -> dict[str, pd.Series]:
    a = filter_admissions(get_data(), fs)
    return {col: a[col].value_counts().sort_index() for col in ("grupo_etario", "sexo", "regimen", "zona")}


# ── Surgery ──────────────────────────────────────────────────────────────────

@_memo
def surgery_summary(fs: FilterState) -> dict:
    """Scheduled vs executed surgeries for admissions in the window."""
    data = get_data()
    oids = filter_admissions(data, fs)["oid_ingreso"]
    s = data.surgeries
    # HIS schedules have no date → scoped through their admission. App
    # schedules have a date → scoped by it (future ones are not due yet).
    lo, hi = pd.Timestamp(fs.date_from), pd.Timestamp(fs.date_to) + pd.Timedelta(days=1)
    dated = s["fecha"].notna()
    in_window = s["fecha"].between(lo, hi, inclusive="left") & (s["fecha"] <= pd.Timestamp.now())
    cx = s[(~dated & s["oid_ingreso"].isin(oids)) | (dated & in_window)]
    by_room = (cx[cx["ejecutada"]].groupby("quirofano").size().rename("ejecutadas")
               .sort_values(ascending=False).reset_index())
    programmed, executed = len(cx), int(cx["ejecutada"].sum())
    return {
        "programadas": programmed,
        "ejecutadas": executed,
        "no_ejecutadas": programmed - executed,
        "cumplimiento": executed / programmed if programmed else 0,
        "por_quirofano": by_room,
        "canceladas": int((cx["estado"] == "Cancelada").sum()),
        "sin_ingreso": int((s["oid_ingreso"].isna() & (s["origen"] == "HIS")).sum()),
    }


@_memo
def or_usage_heatmap(fs: FilterState) -> pd.DataFrame:
    """Operating-room activity (distinct procedures) per weekday × hour."""
    data = get_data()
    lines = scope_lines(data.services, filter_admissions(data, fs), fs)
    orl = lines[lines["area_servicio"].str.startswith("QUIROFANOS", na=False)]
    events = orl.drop_duplicates(["oid_ingreso", "fecha_prestacion"])
    weeks = max(events["fecha_prestacion"].dt.normalize().nunique() / 7, 1)
    grid = events.groupby([events["fecha_prestacion"].dt.dayofweek, events["fecha_prestacion"].dt.hour]).size() / weeks
    grid = grid.unstack(fill_value=0).reindex(index=range(7), columns=range(24), fill_value=0)
    grid.index = WEEKDAYS
    return grid


@_memo
def or_weekly(fs: FilterState) -> pd.DataFrame:
    data = get_data()
    lines = scope_lines(data.services, filter_admissions(data, fs), fs)
    orl = lines[lines["area_servicio"].str.startswith("QUIROFANOS", na=False)]
    ev = orl.drop_duplicates(["oid_ingreso", "fecha_prestacion"]).copy()
    ev["semana"] = ev["fecha_prestacion"].dt.to_period("W").dt.start_time
    ev["quirofano"] = ev["area_servicio"].astype(str).str.replace("QUIROFANOS - ", "", regex=False).str.capitalize()
    return ev.groupby(["semana", "quirofano"]).size().rename("procedimientos").reset_index()


# ── Pharmacy ─────────────────────────────────────────────────────────────────

@_memo
def med_rotation(fs: FilterState) -> pd.DataFrame:
    """Dispensed quantity per item with ABC (Pareto) class.

    A = items making up the first 80 % of volume, B = next 15 %, C = rest.
    """
    data = get_data()
    lines = scope_lines(data.meds, filter_admissions(data, fs), fs)
    out = lines.groupby(["codigo_servicio", "nombre_servicio"], observed=True).agg(
        cantidad=("cantidad", "sum"), dispensaciones=("cantidad", "size"),
        episodios=("oid_ingreso", "nunique")).reset_index()
    out = out[out["cantidad"] > 0].sort_values("cantidad", ascending=False)
    share = out["cantidad"].cumsum() / out["cantidad"].sum()
    out["clase_abc"] = np.where(share <= 0.8, "A", np.where(share <= 0.95, "B", "C"))
    out["nombre"] = out["nombre_servicio"].astype(str).str.capitalize().str[:55]
    return out.reset_index(drop=True)


@_memo
def med_consumption_trend(fs: FilterState, recent_days: int = 7, baseline_days: int = 28) -> pd.DataFrame:
    """Early shortage signal: recent daily use vs the previous baseline."""
    data = get_data()
    fs = _trend_window(fs)
    end = pd.Timestamp(fs.date_to) + pd.Timedelta(days=1)
    split = end - pd.Timedelta(days=recent_days)
    start = split - pd.Timedelta(days=baseline_days)
    admissions = filter_admissions(data, fs, ignore_dates=True)
    m = data.meds[data.meds["oid_ingreso"].isin(admissions["oid_ingreso"])]
    m = m[m["fecha_prestacion"].between(start, end)]
    recent = m[m["fecha_prestacion"] >= split].groupby("codigo_servicio", observed=True)["cantidad"].sum() / recent_days
    base = m[m["fecha_prestacion"] < split].groupby("codigo_servicio", observed=True)["cantidad"].sum() / baseline_days
    out = pd.DataFrame({"reciente_dia": recent, "base_dia": base}).fillna(0)
    out = out[(out["base_dia"] >= 1)]
    out["variacion"] = (out["reciente_dia"] - out["base_dia"]) / out["base_dia"]
    names = data.meds.drop_duplicates("codigo_servicio").set_index("codigo_servicio")["nombre_servicio"]
    out["nombre"] = names.reindex(out.index).astype(str).str.capitalize().str[:55].values
    inv = data.inventory.set_index("codigo_servicio")
    out["dias_inventario"] = inv["dias_inventario"].reindex(out.index).values
    return out.reset_index().sort_values("variacion", ascending=False)


def inventory_status(critical_days: float | None = None) -> pd.DataFrame:
    """Current stock table, optionally only items below ``critical_days``."""
    inv = get_data().inventory.copy()
    inv["nombre"] = inv["nombre_servicio"].astype(str).str.capitalize().str[:55]
    if critical_days is not None:
        inv = inv[inv["dias_inventario"] < critical_days]
    return inv


# ── Predictive ───────────────────────────────────────────────────────────────

@_memo
def forecast_admissions(fs: FilterState, horizon: int = 14) -> pd.DataFrame:
    """Seasonal-naive forecast: weekday profile × recent level.

    Lightweight and explainable (no ML dependency): level = mean of the last
    28 days, weekday factors from the whole window, band = ±1.28 σ of the
    in-sample residuals (≈ 80 % interval). Trained on the HIS series only:
    the few days captured manually after the extract would bias the level.
    """
    data = get_data()
    a = filter_admissions(data, fs)
    a = a[(a["origen"] == "HIS") & (a["fecha"] <= data.his_cutoff.normalize())]
    daily = a.groupby("fecha").size().asfreq("D", fill_value=0)
    if len(daily) < 21:
        return pd.DataFrame(columns=["fecha", "real", "pronostico", "bajo", "alto"])
    factors = daily.groupby(daily.index.dayofweek).mean() / daily.mean()
    level = daily.iloc[-28:].mean()
    fitted = daily.rolling(28, min_periods=7).mean().shift(1) * factors.reindex(daily.index.dayofweek).values
    sigma = (daily - fitted).std()
    future = pd.date_range(daily.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    pred = level * factors.reindex(future.dayofweek).values
    hist = pd.DataFrame({"fecha": daily.index, "real": daily.values})
    fut = pd.DataFrame({"fecha": future, "pronostico": pred,
                        "bajo": np.clip(pred - 1.28 * sigma, 0, None), "alto": pred + 1.28 * sigma})
    return pd.concat([hist.tail(60), fut], ignore_index=True)


@_memo
def diagnosis_trends(fs: FilterState, recent_days: int = 14) -> pd.DataFrame:
    """Admissions per CIE-10 chapter: last ``recent_days`` vs the prior 4×."""
    data = get_data()
    fs = _trend_window(fs)
    a = filter_admissions(data, fs, ignore_dates=True)
    end = pd.Timestamp(fs.date_to) + pd.Timedelta(days=1)
    split = end - pd.Timedelta(days=recent_days)
    start = split - pd.Timedelta(days=recent_days * 4)
    a = a[a["fecha_ingreso"].between(start, end)]
    recent = a[a["fecha_ingreso"] >= split].groupby("capitulo_dx", observed=True).size() / recent_days
    base = a[a["fecha_ingreso"] < split].groupby("capitulo_dx", observed=True).size() / (recent_days * 4)
    out = pd.DataFrame({"reciente_dia": recent, "base_dia": base}).fillna(0)
    out = out[out["base_dia"] >= 0.5]
    out["variacion"] = (out["reciente_dia"] - out["base_dia"]) / out["base_dia"]
    return out.reset_index().sort_values("variacion", ascending=False)


# ── Graphs ───────────────────────────────────────────────────────────────────

@_memo
def patient_flow(fs: FilterState) -> pd.DataFrame:
    """Links for the Sankey: vía de ingreso → clase → servicio → capítulo dx."""
    a = filter_admissions(get_data(), fs)
    steps = [("via_ingreso", "clase_ingreso"), ("clase_ingreso", "servicio"), ("servicio", "capitulo_dx")]
    frames = []
    for src, dst in steps:
        g = a.groupby([src, dst], observed=True).size().rename("valor").reset_index()
        g.columns = ["origen", "destino", "valor"]
        g["etapa"] = f"{src}→{dst}"
        frames.append(g)
    links = pd.concat(frames, ignore_index=True)
    # Keep the chart readable: fold small diagnosis chapters into "Otros".
    last = links["etapa"] == "servicio→capitulo_dx"
    top = links[last].groupby("destino")["valor"].sum().nlargest(8).index
    links.loc[last & ~links["destino"].isin(top), "destino"] = "Otros capítulos"
    return links.groupby(["etapa", "origen", "destino"], as_index=False, observed=True)["valor"].sum()


@_memo
def specialty_area_network(fs: FilterState, top_nodes: int = 10, min_weight: int = 50) -> pd.DataFrame:
    """Weighted bipartite edges especialidad ↔ área de servicio."""
    data = get_data()
    lines = scope_lines(data.services, filter_admissions(data, fs), fs)
    top_sp = lines.groupby("especialidad", observed=True)["cantidad"].sum().nlargest(top_nodes).index
    top_ar = lines.groupby("area_servicio", observed=True)["cantidad"].sum().nlargest(top_nodes).index
    sub = lines[lines["especialidad"].isin(top_sp) & lines["area_servicio"].isin(top_ar)]
    edges = sub.groupby(["especialidad", "area_servicio"], observed=True)["cantidad"].sum().rename("peso").reset_index()
    edges = edges[edges["peso"] >= min_weight]
    edges["area_servicio"] = (edges["area_servicio"].astype(str)
                              .str.replace(r"^APOYO (DIAGNOSTICO|TERAPEUTICO)\s*-\s*", "", regex=True).str.capitalize())
    edges["especialidad"] = edges["especialidad"].astype(str).str.capitalize()
    return edges


# ── Tables ───────────────────────────────────────────────────────────────────

@_memo
def episodes_table(fs: FilterState, limit: int = 2000) -> pd.DataFrame:
    """Anonymised episode list (brief: patients without sensitive data)."""
    a = filter_admissions(get_data(), fs).sort_values("fecha_ingreso", ascending=False).head(limit)
    return pd.DataFrame({
        "Episodio": a["oid_ingreso"].map(pseudonym),
        "Ingreso": a["fecha_ingreso"],
        "Servicio": a["servicio"].astype(str),
        "Vía": a["via_ingreso"].astype(str),
        "Triage": a["nivel_triage"],
        "Espera (min)": a["espera_min"].round(0),
        "Capítulo CIE-10": a["capitulo_dx"].astype(str),
        "Diagnóstico": (a["codigo_diagnostico"].fillna("") + " " + a["nombre_diagnostico"].fillna("").str.capitalize()).str.strip(),
        "Estancia (h)": a["estancia_h"].round(1),
        "Edad": a["grupo_etario"].astype(str),
        "Sexo": a["sexo"].astype(str),
        "Régimen": a["regimen"].astype(str),
        "Origen": a["origen"].astype(str),
    })
