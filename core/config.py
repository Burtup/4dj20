"""Central configuration: paths, brand palette, clinical thresholds.

Change values here instead of hard-coding them in views, so the whole app
(dashboard, agent, API) stays consistent.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
INVENTORY_FILE = RAW_DIR / "Inventario.csv"  # optional import: codigo_servicio;stock
DB_FILE = BASE_DIR / "data" / "hospital.db"  # operational records + audit trail (SQLite)

DATASETS = (
    "atencion",
    "ingresos",
    "medicamento_insumo",
    "paciente",
    "programacion_cirugia",
    "servicios",
    "triage",
)

# Raw file name expected by scripts/cleaner.py for each dataset.
RAW_FILES = {
    "atencion": "Atencion.txt",
    "ingresos": "Ingresos.txt",
    "medicamento_insumo": "MedicamentoInsumo.txt",
    "paciente": "Paciente.txt",
    "programacion_cirugia": "ProgramacionCirugia.txt",
    "servicios": "Servicios.txt",
    "triage": "Triage.txt",
}

# ── Brand (hospital logo) ────────────────────────────────────────────────────
BRAND = {
    "navy": "#19205b",   # logo lettering → headings, primary ink
    "lime": "#74b722",   # logo light green → accents, highlights
    "green": "#24732b",  # logo dark green → positive / primary actions
    "paper": "#f2f2f2",  # institutional background
}

# Categorical order validated with the dataviz validator (light / dark surfaces).
# Order is fixed: series keep their color regardless of rank or filters.
CATEGORICAL_LIGHT = ["#3b47a8", "#5c9a1b", "#8a4fc7", "#c0781a", "#0a8f86", "#d0506a"]
CATEGORICAL_DARK = ["#6f7bd8", "#6aa82a", "#a070e0", "#c28226", "#1a9e94", "#dd5f7a"]

# Single-hue (brand green) ramps for magnitude, light → dark ink.
SEQUENTIAL_LIGHT = ["#f3f9ec", "#e2f1d0", "#cde6b0", "#b3d98c", "#96c966",
                    "#7cb838", "#62992a", "#4a7a1f", "#355d17", "#23410f"]
SEQUENTIAL_DARK = ["#172312", "#1f3316", "#29451a", "#34581d", "#416c21",
                   "#4f8126", "#61972c", "#77ad37", "#90c34c", "#b0d975"]

# Reserved status colors — never reused as a data series.
STATUS = {
    "critical": "#c62828",
    "serious": "#e0701b",
    "warning": "#c99700",
    "good": "#2e7d32",
    "info": "#3b47a8",
}

# Colombian 5-level triage scale (I = most urgent). Semantic, not brand.
TRIAGE_COLORS = {1: "#c62828", 2: "#e0701b", 3: "#c99700", 4: "#2e7d32", 5: "#3b47a8"}
TRIAGE_LABELS = {
    1: "I · Inmediato",
    2: "II · Urgente",
    3: "III · Prioritario",
    4: "IV · Menos urgente",
    5: "V · No urgente",
}

# ── Operational thresholds (used by alerts & recommendations) ───────────────
OCCUPANCY_WARNING = 0.85      # ≥ 85 % → recommend opening beds / staff
OCCUPANCY_CRITICAL = 0.95
WAIT_TARGET_MIN = 30          # triage → first attention target (minutes)
STOCK_CRITICAL_DAYS = 5       # < 5 days of supply → critical (PDF demo question 2)
STOCK_LOW_DAYS = 14
EXPIRY_WARNING_DAYS = 30      # items expiring sooner → FEFO alert
CONSUMPTION_SPIKE = 0.25      # +25 % recent vs baseline → early shortage alert
DEMAND_SPIKE = 0.20           # +20 % admissions of a diagnosis group → predictive alert

# Columns that must never reach the agent, the API or any exported table.
PII_COLUMNS = {"nombre_paciente", "tipo_documento", "fecha_nacimiento", "id_paciente"}

# ── LLM (optional). Configure through .env — never commit credentials. ──────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "120"))
