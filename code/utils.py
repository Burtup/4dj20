from pathlib import Path
import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

EXPECTED_FILES = [
    "atencion",
    "ingresos",
    "medicamento_insumo",
    "paciente",
    "programacion_cirugia",
    "servicios",
    "triage",
]


@st.cache_data
def load_datasets() -> dict[str, pd.DataFrame]:
    datasets = {}
    for file_name in EXPECTED_FILES:
        file_path = PROCESSED_DIR / f"{file_name}.parquet"
        if file_path.exists():
            datasets[file_name] = pd.read_parquet(file_path)
        else:
            st.error(f"Archivo no encontrado: {file_path}")
    return datasets
