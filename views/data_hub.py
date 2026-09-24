"""Datos — catalog, upload of historical files, anonymised episodes, glossary.

Covers brief item 4 ("carga y análisis de datos históricos") and the dynamic
patient table without sensitive data (section 6).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import config, records
from core import data as data_module
from ui import auth, components, state

EXTRA_FILES = {config.INVENTORY_FILE.name: "Existencias reales (codigo_servicio;stock) → se importan al inventario"}
GLOSSARY = {
    "Ingreso / episodio": "Todo el paso del paciente por la institución, desde que llega hasta el egreso.",
    "Triage I–V": "Clasificación de urgencias por gravedad: I es inmediato, V no urgente.",
    "Censo de medianoche": "Pacientes en cama a las 00:00; base estándar para medir ocupación.",
    "Clase de ingreso": "Ambulatorio (sin internación) u Hospitalario (con internación).",
    "CIE-10": "Catálogo internacional de diagnósticos; se agrupa por capítulos (J = respiratorio…).",
    "CUPS": "Catálogo colombiano de procedimientos; se usa en servicios y cirugías.",
    "Régimen": "Contributivo, Subsidiado, Vinculado, Particular u Otro.",
    "Clase ABC": "Pareto de consumo: A = ítems que suman el 80 % del volumen.",
}


def _process(files, actor) -> None:
    """Save recognised HIS files into data/raw, rebuild parquet, import stock."""
    his_names = set(config.RAW_FILES.values())
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    saved, inventory = [], None
    for f in files:
        name = f.name.split("/")[-1].split("\\")[-1]  # never trust client paths
        if name == config.INVENTORY_FILE.name:
            inventory = pd.read_csv(f, sep=None, engine="python", dtype={"codigo_servicio": str})
            saved.append(name)
        elif name in his_names:
            (config.RAW_DIR / name).write_bytes(f.getvalue())
            saved.append(name)
    if not saved:
        st.error("Ningún archivo tiene un nombre reconocido.")
        return
    try:
        with st.spinner("Procesando archivos y recalculando indicadores…"):
            if any(n in his_names for n in saved):
                from scripts.cleaner import run_pipeline

                run_pipeline()
                data_module.reload()
            if inventory is not None and actor is not None:
                count = records.import_inventory(actor, inventory)
                st.toast(f"{count} existencias importadas.")
    except Exception as exc:  # surfaced to the user: bad delimiter, missing column…
        st.error(f"No se pudo procesar: {exc}")
        return
    st.success(f"Datos actualizados: {', '.join(saved)}.")
    st.session_state.pop("f_dates", None)
    st.rerun()


def uploader(actor=None, setup: bool = False) -> None:
    st.markdown(
        "Arrastra los archivos del HIS con su **nombre original** (delimitados por `|`). Opcionalmente, agrega "
        f"`{config.INVENTORY_FILE.name}` (columnas `codigo_servicio;stock`) para cargar existencias reales.")
    expected = {**{v: k for k, v in config.RAW_FILES.items()}, **EXTRA_FILES}
    st.dataframe([{"Archivo": k, "Contenido": v, "En disco": "✓" if (config.RAW_DIR / k).exists() else "—"}
                  for k, v in expected.items()], hide_index=True, width="stretch")
    if not setup and actor is None:
        return
    files = st.file_uploader("Archivos", type=["txt", "csv"], accept_multiple_files=True, label_visibility="collapsed")
    if st.button("Procesar y recargar", type="primary", icon=":material/sync:", disabled=not files):
        _process(files, actor)


def render_setup() -> None:
    """First run: no processed data yet, so there are no users to sign in."""
    components.page_header("Primer uso", "Carga los datos del HIS",
                           "No se encontraron archivos procesados en data/processed.")
    uploader(setup=True)


def render() -> None:
    from core import kpis  # needs data; imported lazily for the setup screen

    fs = state.filters()
    components.page_header("Datos", "Centro de datos",
                           "Catálogo, carga de históricos y listado anonimizado de episodios.", fs)
    tab_cat, tab_load, tab_ep, tab_glo = st.tabs(["Catálogo", "Cargar datos", "Episodios", "Glosario"])

    with tab_cat:
        catalog = data_module.dataset_catalog()
        st.dataframe(catalog, hide_index=True, width="stretch",
                     column_config={"filas": st.column_config.NumberColumn("Filas", format="localized"),
                                    "desde": st.column_config.DatetimeColumn("Desde", format="DD/MM/YYYY"),
                                    "hasta": st.column_config.DatetimeColumn("Hasta", format="DD/MM/YYYY")})
        st.caption("Fuente: extracto DateBaseHIS (7 tablas). Los datos se procesan con `scripts/cleaner.py` "
                   "y se enriquecen en `core/data.py` (edad, nivel de triage, espera, egreso estimado).")

    with tab_load:
        uploader(auth.guard("datos.cargar"))

    with tab_ep:
        table = kpis.episodes_table(fs)
        query = st.text_input("Buscar en diagnóstico, servicio o episodio", key="ep_q")
        if query:
            mask = table.astype(str).apply(lambda c: c.str.contains(query, case=False, regex=False)).any(axis=1)
            table = table[mask]
        st.dataframe(table, hide_index=True, width="stretch", height=460,
                     column_config={"Ingreso": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
        st.caption("🔒 Sin nombres, documentos ni fechas de nacimiento: el episodio usa un seudónimo irreversible.")
        st.download_button("Descargar CSV", table.to_csv(index=False).encode("utf-8"), "episodios_anonimizados.csv",
                           icon=":material/download:")

    with tab_glo:
        st.dataframe([{"Término": k, "Significado": v} for k, v in GLOSSARY.items()], hide_index=True, width="stretch")

    state.publish_context("Datos", {"Episodios listados": len(kpis.episodes_table(fs))},
                          ["Resumen de la situación", "Distribución de ingresos por edad"])
