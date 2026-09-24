## Script para revisión y formato de la data entregada

from pathlib import Path
import pandas as pd


# -----------------------------------------------------------------------------
# 1. Configuración de rutas
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

DELIMITER = "|"


# -----------------------------------------------------------------------------
# 2. Limpieza de nombres de columnas
# -----------------------------------------------------------------------------

def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = (
        df.columns
        .str.strip()
        .str.replace(r"(?<!^)(?=[A-Z])", "_", regex=True)
        .str.lower()
    )

    return df


# -----------------------------------------------------------------------------
# 3. Conversión de columnas de fecha/hora
# -----------------------------------------------------------------------------

def convert_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte automáticamente a datetime las columnas cuyo nombre
    contiene 'fecha'.

    Los valores que no puedan convertirse se transforman en NaT.
    """

    for col in df.columns:
        if "fecha" in col:
            df[col] = pd.to_datetime(
                df[col],
                errors="coerce"
            )

    return df


# -----------------------------------------------------------------------------
# 4. Procesamiento de Atención
# -----------------------------------------------------------------------------

def process_atencion():
    df = pd.read_csv(
        DATA_DIR / "Atencion.txt",
        sep=DELIMITER
    )

    df = clean_column_names(df)
    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "atencion.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 5. Procesamiento de Ingresos
# -----------------------------------------------------------------------------

def process_ingresos():
    df = pd.read_csv(
        DATA_DIR / "Ingresos.txt",
        sep=DELIMITER
    )

    df = clean_column_names(df)
    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "ingresos.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 6. Procesamiento de Medicamentos e Insumos
# -----------------------------------------------------------------------------

def process_medicamento_insumo():
    df = pd.read_csv(
        DATA_DIR / "MedicamentoInsumo.txt",
        sep=DELIMITER
    )

    df = clean_column_names(df)
    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "medicamento_insumo.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 7. Procesamiento de Pacientes
# -----------------------------------------------------------------------------

def process_paciente():
    df = pd.read_csv(
        DATA_DIR / "Paciente.txt",
        sep=DELIMITER
    )

    df = clean_column_names(df)
    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "paciente.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 8. Procesamiento de Programación de Cirugía
# -----------------------------------------------------------------------------

def process_programacion_cirugia():
    df = pd.read_csv(
        DATA_DIR / "ProgramacionCirugia.txt",
        sep=DELIMITER,
        dtype={
            "ConsecutivoProgramacion": str
        }
    )

    df = clean_column_names(df)
    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "programacion_cirugia.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 9. Procesamiento de Servicios
# -----------------------------------------------------------------------------

def process_servicios():
    df = pd.read_csv(
        DATA_DIR / "Servicios.txt",
        sep=DELIMITER
    )

    df = clean_column_names(df)
    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "servicios.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 10. Procesamiento de Triage
# -----------------------------------------------------------------------------

def process_triage():
    df = pd.read_csv(
        DATA_DIR / "Triage.txt",
        sep=DELIMITER
    )

    df = clean_column_names(df)

    df = df.rename(
        columns={
            "id_paciente2": "id_paciente"
        }
    )

    df = convert_date_columns(df)

    df.to_parquet(
        PROCESSED_DIR / "triage.parquet",
        index=False
    )


# -----------------------------------------------------------------------------
# 11. Ejecución del pipeline completo
# -----------------------------------------------------------------------------

def run_pipeline():
    process_atencion()
    process_ingresos()
    process_medicamento_insumo()
    process_paciente()
    process_programacion_cirugia()
    process_servicios()
    process_triage()

    print(
        "Procesamiento finalizado. "
        "Archivos guardados en data/processed/."
    )


# -----------------------------------------------------------------------------
# 12. Punto de entrada
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    run_pipeline()