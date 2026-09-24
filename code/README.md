# Asistente de Gestión Hospitalaria

Herramienta conversacional para consultar registros del Sistema de Información Hospitalaria (HIS). Permite hacer preguntas en lenguaje natural sobre admisiones, triage, servicios, pacientes y medicamentos, e incluye un dashboard visual con indicadores clave.

Desarrollado para el hackathon SUS 2026.

## Arquitectura

```
Datos/*.txt  ──►  scripts/cleaner.py  ──►  data/processed/*.parquet
                                                    │
                            ┌───────────────────────┤
                            ▼                       ▼
                  pages/1_Dashboard.py          app.py
                  KPIs + gráficas Plotly    Chat IA (LangChain + Ollama)
```

## Prerrequisitos

- Python 3.11+
- [Ollama](https://ollama.com/download) instalado y corriendo localmente

```bash
ollama pull llama3.2
```

## Instalación y uso

### Paso 1 — Copiar los datos crudos

Los archivos `.txt` deben quedar en `code/data/raw/`:

```
Datos/Atencion.txt            →  code/data/raw/Atencion.txt
Datos/Ingresos.txt            →  code/data/raw/Ingresos.txt
Datos/MedicamentoInsumo.txt   →  code/data/raw/MedicamentoInsumo.txt
Datos/Paciente.txt            →  code/data/raw/Paciente.txt
Datos/ProgramacionCirugia.txt →  code/data/raw/ProgramacionCirugia.txt
Datos/Servicios.txt           →  code/data/raw/Servicios.txt
Datos/Triage.txt              →  code/data/raw/Triage.txt
```

### Paso 2 — Instalar dependencias

```bash
cd code
pip install -r requerimientos.txt
```

### Paso 3 — Procesar los datos

```bash
python scripts/cleaner.py
```

Genera archivos `.parquet` en `code/data/processed/` (carpeta ignorada por git).

### Paso 4 — Ejecutar la aplicación

```bash
streamlit run app.py
```

Accede en: http://localhost:8501

## Páginas

| Página | Descripción |
|--------|-------------|
| Chat (app.py) | Consultas conversacionales al agente LLM sobre los datos hospitalarios |
| Dashboard (pages/1_Dashboard.py) | KPIs, distribución de ingresos, triage y top diagnósticos |

## Datasets

| Dataset | Descripción | Columnas clave |
|---------|-------------|----------------|
| `ingresos` | Registro de admisiones hospitalarias | `via_ingreso`, `fecha_ingreso`, `nombre_diagnostico`, `nombre_cama` |
| `triage` | Clasificación de urgencias | `clasificacion_triage`, `fecha_triage`, `motivo_consulta` |
| `atencion` | Atenciones médicas realizadas | `oid_ingreso`, `fecha_atencion` |
| `paciente` | Datos demográficos del paciente | `sexo`, `asegurador`, `regimen`, `municipio` |
| `servicios` | Servicios y procedimientos | `nombre_servicio`, `especialidad`, `area_servicio` |
| `medicamento_insumo` | Medicamentos e insumos entregados | `nombre_servicio`, `cantidad`, `fecha_prestacion` |
| `programacion_cirugia` | Cirugías programadas | `consecutivo_programacion`, `codigo_servicio` |

## Ejemplos de consultas

- ¿Cuántos ingresos hubo por urgencias en mayo?
- ¿Cuál es el diagnóstico más frecuente?
- Muestra la distribución de pacientes por asegurador
- ¿Cuántas cirugías están programadas esta semana?
- ¿Cuál es la edad promedio de los pacientes ingresados?
