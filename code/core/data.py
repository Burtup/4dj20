"""Data access, enrichment and composition.

Two layers are combined into one :class:`HospitalData` snapshot:

1. **HIS history** (parquet from ``scripts/cleaner.py``) — read-only, heavy,
   loaded once per process (``_his``, ≈ 6 s).
2. **Operational records** (SQLite, :mod:`core.db`) — admissions, discharges,
   beds, stock and surgeries captured in the app. Light; re-composed each
   time the database version changes (someone saved a form).

Derived fields every KPI needs: demographics (anonymised), triage level,
waiting time, shift, CIE-10 chapter, estimated discharge and length of stay.

Design notes
------------
* The HIS extract has no discharge date. It is estimated as the last
  service / medication charged to the episode (standard proxy). Admissions
  registered in the app use their real ``fecha_egreso``.
* The HIS has no bed inventory: capacity per service = max(registered beds in
  service, 95th percentile of the daily census). The bed registry is editable.
* The HIS has no stock levels: the inventory table is seeded with a
  deterministic *simulated* stock (flagged ``origen = 'Simulado'``) until
  pharmacy records real counts.
* Returned DataFrames are shared between sessions: treat them as read-only.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from core import config, db

# Short, human labels for the HIS bed groups (used everywhere in the UI).
SERVICE_LABELS = {
    "UNIDAD DE CUIDADO INTENSIVO": "UCI",
    "UNIDAD DE CUIDADO INTERMEDIO": "Cuidado intermedio",
    "UNIDAD DE CUIDADO BASICO": "Cuidado básico neonatal",
    "HOSPITALIZACION": "Hospitalización",
    "PEDIATRIA": "Pediatría",
    "URGENCIAS": "Urgencias",
    "RECUPERACION": "Recuperación",
    "GINECO OBSTRETICIA": "Ginecoobstetricia",
    "SALA PARTOS": "Sala de partos",
}

AGE_BINS = [-1, 4, 17, 44, 64, 200]
AGE_LABELS = ["0–4", "5–17", "18–44", "45–64", "65+"]
SHIFT_ORDER = ["Mañana", "Tarde", "Noche"]
CATEGORY_COLUMNS = ("clase_ingreso", "via_ingreso", "tipo_riesgo", "servicio", "subservicio",
                    "sexo", "regimen", "zona", "capitulo_dx", "punto_triage", "origen")
APP_OID_OFFSET = 900_000_000  # ids of app admissions never collide with HIS ids


@dataclass(frozen=True)
class HospitalData:
    """Snapshot of every enriched table the app works with."""

    admissions: pd.DataFrame
    services: pd.DataFrame
    meds: pd.DataFrame
    surgeries: pd.DataFrame
    bed_capacity: pd.Series
    beds: pd.DataFrame              # bed registry: codigo_cama, servicio, estado_operativo
    inventory: pd.DataFrame
    inventory_is_simulated: bool    # True while most stock is still simulated
    reference_date: pd.Timestamp    # "today" for the data (last admission)
    min_date: pd.Timestamp
    his_cutoff: pd.Timestamp        # last timestamp present in the HIS extract
    version: int                    # operational DB version this snapshot reflects


# ── Helpers ──────────────────────────────────────────────────────────────────

def cie10_chapter(code: str | float) -> str:
    """Map a CIE-10 code (e.g. ``J189``) to a readable chapter name."""
    if not isinstance(code, str) or not code:
        return "Sin diagnóstico"
    letter, digits = code[0].upper(), code[1:3]
    num = int(digits) if digits.isdigit() else 0
    if letter in "AB":
        return "Infecciosas"
    if letter == "C" or (letter == "D" and num < 50):
        return "Neoplasias"
    if letter == "D":
        return "Sangre e inmunidad"
    if letter == "H":
        return "Ojo" if num < 60 else "Oído"
    if letter in "ST":
        return "Traumatismos"
    if letter in "VWXY":
        return "Causas externas"
    return {
        "E": "Endocrinas y metabólicas", "F": "Salud mental", "G": "Sistema nervioso",
        "I": "Circulatorio", "J": "Respiratorio", "K": "Digestivo", "L": "Piel",
        "M": "Osteomuscular", "N": "Genitourinario", "O": "Embarazo y parto",
        "P": "Perinatal", "Q": "Congénitas", "R": "Síntomas y signos",
        "Z": "Factores de salud",
    }.get(letter, "Otros")


def shift_of(hours: pd.Series) -> pd.Categorical:
    """Nursing shifts: Mañana 07–13, Tarde 13–19, Noche 19–07."""
    out = np.where((hours >= 7) & (hours < 13), "Mañana",
                   np.where((hours >= 13) & (hours < 19), "Tarde", "Noche"))
    return pd.Categorical(out, categories=SHIFT_ORDER, ordered=True)


def pseudonym(value: int | float) -> str:
    """Stable, non-reversible short id for an admission (for tables)."""
    return "EP-" + hashlib.sha1(str(int(value)).encode()).hexdigest()[:6].upper()


def _stable_int(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


def _read(name: str) -> pd.DataFrame:
    path = config.PROCESSED_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}. Ejecuta `python scripts/cleaner.py` primero.")
    return pd.read_parquet(path)


def midnight_census(stays: pd.DataFrame) -> pd.DataFrame:
    """Occupied beds per service and day (standard midnight census).

    A stay counts on day *d* when the patient is in bed at 00:00 of *d+1*.
    Returns columns ``fecha``, ``servicio``, ``ocupadas``.
    """
    start = stays["fecha_hospitalizacion"].fillna(stays["fecha_ingreso"])
    first_night = start.dt.normalize()
    nights = (stays["fecha_egreso_est"].dt.normalize() - first_night).dt.days.clip(lower=0).fillna(0)
    nights = nights.astype(int).to_numpy()
    keep = nights > 0
    reps = nights[keep]
    if not reps.size:
        return pd.DataFrame(columns=["fecha", "servicio", "ocupadas"])
    offsets = np.concatenate([np.arange(n) for n in reps])
    days = np.repeat(first_night[keep].to_numpy(), reps) + pd.to_timedelta(offsets, unit="D")
    census = pd.DataFrame({"fecha": days, "servicio": np.repeat(stays.loc[keep, "servicio"].astype(str).to_numpy(), reps)})
    return census.groupby(["fecha", "servicio"]).size().rename("ocupadas").reset_index()


# ── HIS layer (read-only, cached once) ───────────────────────────────────────

def _build_admissions(raw: dict[str, pd.DataFrame], last_activity: pd.Series) -> pd.DataFrame:
    ing = raw["ingresos"].copy()
    pac = raw["paciente"]
    tri = raw["triage"]
    att = raw["atencion"].drop_duplicates("oid_ingreso")

    # Patient demographics (no names, documents or birth dates leave this function).
    pac_cols = ["id_paciente", "fecha_nacimiento", "sexo", "regimen", "zona", "municipio", "asegurador"]
    ing = ing.merge(pac[pac_cols], on="id_paciente", how="left")
    age = (ing["fecha_ingreso"] - ing["fecha_nacimiento"]).dt.days / 365.25
    ing["edad"] = age.clip(lower=0).round().astype("Int64")
    ing = ing.drop(columns=["fecha_nacimiento"])

    # Triage: level (1–5) and triage point are parsed from the free-text label.
    tri = tri.dropna(subset=["oid_triage"]).drop_duplicates("oid_triage")
    tri = tri.assign(
        nivel_triage=tri["clasificacion_triage"].str.extract(r"TRIAGE\s*(\d)", expand=False).astype("Int64"),
        punto_triage=tri["clasificacion_triage"].str.extract(r"^\s*([A-ZÁÉÍÓÚÑ]+)", expand=False).str.title(),
    )
    ing = ing.merge(
        tri[["oid_triage", "fecha_triage", "nivel_triage", "punto_triage", "motivo_consulta"]],
        left_on="oid_triage_a", right_on="oid_triage", how="left",
    ).drop(columns=["oid_triage"])
    ing = ing.merge(att, on="oid_ingreso", how="left")

    ing["servicio"] = ing["nombre_grupo_cama"].map(SERVICE_LABELS).fillna(ing["nombre_grupo_cama"].str.title())
    ing["subservicio"] = ing["nombre_subgrupo_cama"].str.capitalize()
    start = ing["fecha_hospitalizacion"].fillna(ing["fecha_ingreso"])
    end = pd.concat([ing["oid_ingreso"].map(last_activity), start], axis=1).max(axis=1)
    # Right-censoring: hospitalised episodes with activity in the last 24 h of
    # the extract are considered still in bed at the data cut-off.
    cutoff = last_activity.max()
    ing["activo_al_corte"] = (ing["clase_ingreso"] == "Hospitalario") & (end >= cutoff - pd.Timedelta(hours=24))
    ing["fecha_egreso_est"] = end
    ing["origen"] = "HIS"
    return ing


def _build_his_surgeries(raw: dict[str, pd.DataFrame], services: pd.DataFrame) -> pd.DataFrame:
    """One row per scheduled HIS surgery (consecutivo) with an executed flag.

    Executed = the admission has an operating-room service, or the exact
    scheduled code was billed in it. The glossary warns both sets may differ.
    """
    cx = raw["programacion_cirugia"].copy()
    cx["codigo"] = cx["codigo_servicio"].astype(str).str.lstrip("0")
    billed = set(zip(services["oid_ingreso"], services["codigo_servicio"].astype(str).str.lstrip("0")))
    or_lines = services[services["area_servicio"].str.startswith("QUIROFANOS", na=False)]
    or_area = or_lines.groupby("oid_ingreso", observed=True)["area_servicio"].agg(lambda s: s.mode().iat[0])

    oid = cx["oid_ingreso"].fillna(-1).astype(int)
    cx["code_billed"] = [(o, c) in billed for o, c in zip(oid, cx["codigo"])]
    out = cx.groupby("consecutivo_programacion").agg(
        oid_ingreso=("oid_ingreso", "first"), procedimientos=("codigo", "nunique"),
        code_billed=("code_billed", "any")).reset_index()
    out["quirofano"] = out["oid_ingreso"].map(or_area)
    out["ejecutada"] = out["code_billed"] | out["quirofano"].notna()
    out["quirofano"] = (out["quirofano"].astype("string").str.replace("QUIROFANOS - ", "", regex=False)
                        .str.capitalize())
    out["estado"] = np.where(out["ejecutada"], "Ejecutada", "Sin ejecución registrada")
    out["fecha"] = pd.NaT
    out["origen"] = "HIS"
    return out.drop(columns=["code_billed"])


@lru_cache(maxsize=1)
def _his() -> dict:
    raw = {name: _read(name) for name in config.DATASETS}
    cols = ["oid_ingreso", "codigo_servicio", "nombre_servicio", "cantidad", "fecha_prestacion", "area_servicio", "especialidad"]
    services, meds = raw["servicios"][cols].copy(), raw["medicamento_insumo"][cols].copy()
    for df in (services, meds):
        for col in ("codigo_servicio", "nombre_servicio", "area_servicio", "especialidad"):
            df[col] = df[col].astype("category")
    last_activity = pd.concat([services[["oid_ingreso", "fecha_prestacion"]], meds[["oid_ingreso", "fecha_prestacion"]]]
                              ).groupby("oid_ingreso")["fecha_prestacion"].max()
    admissions = _build_admissions(raw, last_activity)
    return {
        "admissions": admissions,
        "services": services,
        "meds": meds,
        "surgeries": _build_his_surgeries(raw, services),
        "cutoff": last_activity.max(),
        "beds": admissions[["codigo_cama", "servicio"]].drop_duplicates("codigo_cama"),
    }


def _consumption(meds: pd.DataFrame, reference: pd.Timestamp) -> pd.DataFrame:
    window = meds[meds["fecha_prestacion"] > reference - pd.Timedelta(days=30)]
    daily = (window.groupby(["codigo_servicio", "nombre_servicio"], observed=True)["cantidad"]
             .sum().div(30).rename("consumo_diario").reset_index())
    return daily[daily["consumo_diario"] > 0]


def _seed(his: dict) -> None:
    """First run: demo staff, bed registry and a simulated stock baseline."""
    from core import records  # local import: records depends on db only

    records.seed_staff()
    records.seed_beds(his["beds"])
    if not db.is_seeded("inventario"):
        daily = _consumption(his["meds"], his["admissions"]["fecha_ingreso"].max().normalize())
        seed = daily["codigo_servicio"].astype(str).map(lambda c: int(hashlib.md5(c.encode()).hexdigest()[:6], 16))
        records.seed_inventory(daily.assign(stock=(daily["consumo_diario"] * (2 + seed % 44)).round().clip(lower=1)))


# ── Operational layer (SQLite) ───────────────────────────────────────────────

def _app_admissions(reference_fallback: pd.Timestamp) -> pd.DataFrame:
    rows = pd.read_sql_query("SELECT i.*, m.nombre AS medico FROM ingresos i LEFT JOIN personal m ON m.id = i.medico_id "
                             "WHERE i.estado != 'Anulado'", db.connection())
    if rows.empty:
        return rows
    for col in ("fecha_ingreso", "fecha_triage", "fecha_atencion", "fecha_egreso"):
        rows[col] = pd.to_datetime(rows[col])
    hosp = rows["clase_ingreso"] == "Hospitalario"
    return pd.DataFrame({
        "oid_ingreso": APP_OID_OFFSET + rows["id"],
        "id_paciente": APP_OID_OFFSET + rows["paciente_ref"].map(_stable_int) % 90_000_000,
        "clase_ingreso": rows["clase_ingreso"], "via_ingreso": rows["via_ingreso"], "tipo_riesgo": rows["tipo_riesgo"],
        "fecha_ingreso": rows["fecha_ingreso"],
        "fecha_hospitalizacion": rows["fecha_ingreso"].where(hosp),
        "codigo_cama": rows["codigo_cama"], "servicio": rows["servicio"], "subservicio": rows["servicio"],
        "codigo_diagnostico": rows["codigo_diagnostico"], "nombre_diagnostico": rows["nombre_diagnostico"],
        "sexo": rows["sexo"], "regimen": rows["regimen"], "zona": rows["zona"], "municipio": rows["municipio"],
        "edad": rows["edad"].astype("Int64"), "nivel_triage": rows["nivel_triage"].astype("Int64"),
        "punto_triage": np.where(rows["nivel_triage"].notna(), "Registro app", None),
        "fecha_triage": rows["fecha_triage"], "fecha_atencion": rows["fecha_atencion"],
        "activo_al_corte": rows["estado"] == "Activo",
        # Active stays remain in bed until "today"; discharged ones use the real date.
        "fecha_egreso_est": rows["fecha_egreso"].fillna(max(reference_fallback, pd.Timestamp.now()).normalize()
                                                        + pd.Timedelta(days=1)),
        "estado_registro": rows["estado"], "medico": rows["medico"], "origen": "Registro app",
    })


def _compose(his: dict, version: int) -> HospitalData:
    his_adm = his["admissions"]
    app_adm = _app_admissions(his_adm["fecha_ingreso"].max())
    admissions = pd.concat([his_adm, app_adm], ignore_index=True) if len(app_adm) else his_adm.copy()

    reference = admissions["fecha_ingreso"].max().normalize()
    # HIS stays still open at the extract cut-off stay in bed until the reference day.
    open_at_cut = admissions["activo_al_corte"].fillna(False).astype(bool) & (admissions["origen"] == "HIS")
    admissions.loc[open_at_cut, "fecha_egreso_est"] = max(his["cutoff"], reference + pd.Timedelta(days=1)) + pd.Timedelta(hours=1)

    age = admissions["edad"].astype(float)
    admissions["grupo_etario"] = pd.cut(age, AGE_BINS, labels=AGE_LABELS)
    wait = (admissions["fecha_atencion"] - admissions["fecha_triage"]).dt.total_seconds() / 60
    admissions["espera_min"] = wait.where((wait > 0) & (wait < 24 * 60))
    admissions["turno"] = shift_of(admissions["fecha_triage"].fillna(admissions["fecha_ingreso"]).dt.hour)
    admissions["capitulo_dx"] = admissions["codigo_diagnostico"].map(cie10_chapter)
    admissions["estancia_h"] = (admissions["fecha_egreso_est"] - admissions["fecha_ingreso"]).dt.total_seconds() / 3600
    admissions["fecha"] = admissions["fecha_ingreso"].dt.normalize()
    for col in CATEGORY_COLUMNS:
        admissions[col] = admissions[col].astype("category")

    # Bed registry and capacity.
    beds = pd.read_sql_query("SELECT codigo AS codigo_cama, servicio, estado_operativo FROM camas", db.connection())
    in_service = beds[beds["estado_operativo"] != "Fuera de servicio"].groupby("servicio").size()
    p95 = midnight_census(admissions).groupby("servicio")["ocupadas"].quantile(0.95).round()
    capacity = pd.concat([in_service, p95], axis=1).max(axis=1).rename("camas").sort_values(ascending=False)

    # Inventory: stock from the DB, consumption from HIS dispensations (+ app outflows).
    stock = pd.read_sql_query("SELECT * FROM inventario", db.connection())
    daily = _consumption(his["meds"], reference)[["codigo_servicio", "consumo_diario"]]
    daily["codigo_servicio"] = daily["codigo_servicio"].astype(str)
    outflow = pd.read_sql_query(
        "SELECT codigo_servicio, SUM(cantidad) / 30.0 AS app_dia FROM movimientos_inventario "
        "WHERE tipo = 'Salida' AND fecha >= ? GROUP BY codigo_servicio",
        db.connection(), params=((reference - pd.Timedelta(days=30)).strftime("%Y-%m-%d"),))
    inv = stock.merge(daily, on="codigo_servicio", how="left").merge(outflow, on="codigo_servicio", how="left")
    inv["consumo_diario"] = inv["consumo_diario"].fillna(0) + inv["app_dia"].fillna(0)
    inv = inv.rename(columns={"nombre": "nombre_servicio"}).drop(columns=["app_dia"])
    # Items without consumption in the last 30 days have no meaningful coverage.
    inv["dias_inventario"] = (inv["stock"] / inv["consumo_diario"].where(inv["consumo_diario"] > 0)).round(1)
    inv["estado"] = pd.cut(inv["dias_inventario"], [-np.inf, config.STOCK_CRITICAL_DAYS, config.STOCK_LOW_DAYS, np.inf],
                           labels=["Crítico", "Bajo", "Normal"], right=False).cat.add_categories("Sin consumo")
    inv["estado"] = inv["estado"].fillna("Sin consumo")
    inv["fecha_vencimiento"] = pd.to_datetime(inv["fecha_vencimiento"])

    # Surgeries: HIS schedule + surgeries scheduled in the app.
    app_cx = pd.read_sql_query("SELECT * FROM cirugias", db.connection())
    if len(app_cx):
        app_cx = pd.DataFrame({
            "consecutivo_programacion": "APP-" + app_cx["id"].astype(str),
            "oid_ingreso": (APP_OID_OFFSET + app_cx["ingreso_id"]).where(app_cx["ingreso_id"].notna()),
            "procedimientos": 1, "quirofano": app_cx["quirofano"].str.capitalize(),
            "ejecutada": app_cx["estado"] == "Ejecutada", "estado": app_cx["estado"],
            "fecha": pd.to_datetime(app_cx["fecha_programada"]), "origen": "Registro app",
        })
        surgeries = pd.concat([his["surgeries"], app_cx], ignore_index=True)
    else:
        surgeries = his["surgeries"]

    return HospitalData(
        admissions=admissions, services=his["services"], meds=his["meds"], surgeries=surgeries,
        bed_capacity=capacity, beds=beds, inventory=inv.sort_values("dias_inventario").reset_index(drop=True),
        inventory_is_simulated=bool((stock["origen"] == "Simulado").mean() > 0.5) if len(stock) else True,
        reference_date=reference, min_date=admissions["fecha_ingreso"].min().normalize(),
        his_cutoff=his["cutoff"], version=version,
    )


# ── Public API ───────────────────────────────────────────────────────────────

_STATE: dict = {"version": None, "data": None}
_COMPOSE_LOCK = threading.Lock()


def get_data() -> HospitalData:
    """Current snapshot. HIS loads once; the operational layer refreshes on change."""
    his = _his()
    version = db.data_version()
    if _STATE["version"] == version and _STATE["data"] is not None:
        return _STATE["data"]
    with _COMPOSE_LOCK:
        if _STATE["data"] is None:
            _seed(his)
            version = db.data_version()
        if _STATE["version"] != version:
            _STATE["data"] = _compose(his, version)
            _STATE["version"] = version
            from core import kpis  # local import avoids a cycle
            kpis.clear_cache()
    return _STATE["data"]


def reload() -> None:
    """Drop every cached table (call after processing new raw files)."""
    _his.cache_clear()
    _STATE.update(version=None, data=None)
    from core import kpis
    kpis.clear_cache()


def dataset_catalog() -> pd.DataFrame:
    """Rows, columns and date span of each processed file (for the data hub)."""
    rows = []
    for name in config.DATASETS:
        path = config.PROCESSED_DIR / f"{name}.parquet"
        if not path.exists():
            rows.append({"tabla": name, "filas": 0, "columnas": 0, "desde": None, "hasta": None, "estado": "Falta"})
            continue
        df = pd.read_parquet(path)
        dates = [c for c in df.columns if c.startswith("fecha") and c != "fecha_nacimiento"]
        rows.append({"tabla": name, "filas": len(df), "columnas": df.shape[1],
                     "desde": df[dates[0]].min() if dates else None,
                     "hasta": df[dates[0]].max() if dates else None, "estado": "OK"})
    for table in ("ingresos", "camas", "inventario", "cirugias", "personal", "auditoria"):
        count = db.connection().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        rows.append({"tabla": f"app · {table}", "filas": count, "columnas": None, "desde": None, "hasta": None,
                     "estado": "SQLite"})
    return pd.DataFrame(rows)
