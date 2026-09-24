## Script para dar revision y formato a la data entregada

from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

DELIMITER = "|"

def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = (
        df.columns.str.strip()
        .str.replace(r"(?<!^)(?=[A-Z])", "_", regex=True)
        .str.lower()
    )
    return df

def process_atencion():
    df = pd.read_csv(DATA_DIR / "Atencion.txt", sep=DELIMITER)
    df = clean_column_names(df)
    df["fecha_atencion"] = pd.to_datetime(df["fecha_atencion"])
    df.to_parquet(PROCESSED_DIR / "atencion.parquet", index=False)

def process_ingresos():
    df = pd.read_csv(DATA_DIR / "Ingresos.txt", sep=DELIMITER)
    df = clean_column_names(df)
    df["fecha_ingreso"] = pd.to_datetime(df["fecha_ingreso"])
    df["fecha_hospitalizacion"] = pd.to_datetime(df["fecha_hospitalizacion"])
    df.to_parquet(PROCESSED_DIR / "ingresos.parquet", index=False)

def process_medicamento_insumo():
    df = pd.read_csv(DATA_DIR / "MedicamentoInsumo.txt", sep=DELIMITER)
    df = clean_column_names(df)
    df["fecha_prestacion"] = pd.to_datetime(df["fecha_prestacion"])
    df.to_parquet(PROCESSED_DIR / "medicamento_insumo.parquet", index=False)

def process_paciente():
    df = pd.read_csv(DATA_DIR / "Paciente.txt", sep=DELIMITER)
    df = clean_column_names(df)
    df["fecha_nacimiento"] = pd.to_datetime(df["fecha_nacimiento"])
    df.to_parquet(PROCESSED_DIR / "paciente.parquet", index=False)

def process_programacion_cirugia():
    df = pd.read_csv(DATA_DIR / "ProgramacionCirugia.txt", sep=DELIMITER, dtype={"ConsecutivoProgramacion": str})
    df = clean_column_names(df)
    df.to_parquet(PROCESSED_DIR / "programacion_cirugia.parquet", index=False)

def process_servicios():
    df = pd.read_csv(DATA_DIR / "Servicios.txt", sep=DELIMITER)
    df = clean_column_names(df)
    df["fecha_prestacion"] = pd.to_datetime(df["fecha_prestacion"])
    df.to_parquet(PROCESSED_DIR / "servicios.parquet", index=False)

def process_triage():
    df = pd.read_csv(DATA_DIR / "Triage.txt", sep=DELIMITER)
    df = clean_column_names(df)
    df = df.rename(columns={"id_paciente2": "id_paciente"})
    df["fecha_triage"] = pd.to_datetime(df["fecha_triage"])
    df.to_parquet(PROCESSED_DIR / "triage.parquet", index=False)

def run_pipeline():
    process_atencion()
    process_ingresos()
    process_medicamento_insumo()
    process_paciente()
    process_programacion_cirugia()
    process_servicios()
    process_triage()
    print("Procesamiento finalizado. Archivos guardados en data/processed/")

if __name__ == "__main__":
    run_pipeline()