# -----------------------------------------------------------------------------
# 0. Importacion de Librerias y Dependencias
# -----------------------------------------------------------------------------
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_experimental.tools import PythonAstREPLTool
from langchain_ollama import ChatOllama
from prompts import SYSTEM_INSTRUCTIONS_TEMPLATE
# -----------------------------------------------------------------------------
# 1. Configuración Inicial de Streamlit
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Asistente de Gestión Hospitalaria", page_icon="🏥", layout="wide"
)

st.title("Asistente de Gestión Hospitalaria")
st.caption(
    "Consulta conversacional sobre registros hospitalarios usando Llama 3.2 (Ollama)."
)

# -----------------------------------------------------------------------------
# 2. Carga de Datos y Metadatos (Parquet, Diccionario y Glosario)
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROCESSED_DIR = DATA_DIR / "processed"


@st.cache_data
def load_datasets() -> dict[str, pd.DataFrame]:
    datasets = {}
    expected_files = [
        "atencion",
        "ingresos",
        "medicamento_insumo",
        "paciente",
        "programacion_cirugia",
        "servicios",
        "triage",
    ]

    for file_name in expected_files:
        file_path = PROCESSED_DIR / f"{file_name}.parquet"
        if file_path.exists():
            df = pd.read_parquet(file_path)

            # Convertir columnas de fecha/hora a datetime de Pandas
            for col in df.columns:
                if "fecha" in col:
                    df[col] = pd.to_datetime(
                        df[col],
                        errors="coerce"
                    )

            datasets[file_name] = df
        else:
            st.error(f"Archivo no encontrado: {file_path}")

    return datasets


@st.cache_data
def load_metadata() -> tuple[dict, dict]:
    diccionario_path = DATA_DIR / "diccionario_datos.json"
    glosario_path = DATA_DIR / "glosario_terminos.json"

    diccionario = {}
    glosario = {}

    if diccionario_path.exists():
        with open(diccionario_path, "r", encoding="utf-8") as f:
            diccionario = json.load(f)

    if glosario_path.exists():
        with open(glosario_path, "r", encoding="utf-8") as f:
            glosario = json.load(f)

    return diccionario, glosario


datasets = load_datasets()
diccionario_datos, glosario_terminos = load_metadata()

# Panel lateral
with st.sidebar:
    st.header("Bases de Datos Cargadas")
    for name, df in datasets.items():
        with st.expander(f"📊 {name} ({df.shape[0]} filas)"):
            st.caption(f"Columnas: {', '.join(df.columns)}")
            st.dataframe(df.head(2), width="stretch")

if not datasets:
    st.stop()

# -----------------------------------------------------------------------------
# 3. Formateo de Metadatos para el Prompt del Sistema
# -----------------------------------------------------------------------------
# Extraer únicamente los nombres de campos y descripciones para aplanar el prompt
diccionario_aplanado = {}

for tabla, info in diccionario_datos.items():
    if "campos" in info:
        diccionario_aplanado[tabla] = {
            c["campo"]: {
                "tipo": c.get("tipo"),
                "descripcion": c.get("descripcion"),
                "longitud_max": c.get("longitud_max"),
                "acepta_nulos": c.get("acepta_nulos"),
                "clave": c.get("clave"),
            }
            for c in info["campos"]
        }


diccionario_str = json.dumps(diccionario_aplanado, ensure_ascii=False, indent=2)
glosario_str = json.dumps(glosario_terminos, ensure_ascii=False, indent=2)

# Inspección de columnas reales cargadas en memoria
real_columns_info = "\n".join(
    [f"- `{name}`: {list(df.columns)}" for name, df in datasets.items()]
)

formatted_metadata_prompt = f"""
COLUMNAS REALES DE LOS DATAFRAMES EN MEMORIA:
{real_columns_info}

DICCIONARIO DE CAMPOS Y DESCRIPCIONES (SNAKE_CASE):
{diccionario_str}

GLOSARIO DE TÉRMINOS Y CONCEPTOS:
{glosario_str}

RELACIONES CLAVE (JOINS EN PANDAS):
- Entre Pacientes e Ingresos: `df_ingresos.merge(df_paciente, on='id_paciente')`
- Entre Triage y Pacientes: `df_triage.merge(df_paciente, on='id_paciente')`
- Entre Ingresos y Servicios/Atención/Medicamentos: `df_X.merge(df_ingresos, on='oid_ingreso')`
""".strip()

# -----------------------------------------------------------------------------
# 4. Motor de Consulta Directa con Python Tool
# -----------------------------------------------------------------------------
df_env = {
    "pd" : pd,
    "df_ingresos": datasets["ingresos"],
    "df_triage": datasets["triage"],
    "df_atencion": datasets["atencion"],
    "df_paciente": datasets["paciente"],
    "df_servicios": datasets["servicios"],
    "df_medicamento_insumo": datasets["medicamento_insumo"],
    "df_programacion_cirugia": datasets["programacion_cirugia"],
}

python_tool = PythonAstREPLTool(locals=df_env)
llm = ChatOllama(model="llama3.2", temperature=0.0)

# Formatear el prompt inyectando formatted_metadata_prompt
SYSTEM_INSTRUCTIONS = SYSTEM_INSTRUCTIONS_TEMPLATE.format(
    formatted_metadata=formatted_metadata_prompt
)


def query_hospital_data(user_query: str) -> str:
    messages = [
        SystemMessage(content=SYSTEM_INSTRUCTIONS),
        HumanMessage(content=user_query),
    ]
    response = llm.invoke(messages).content.strip()

    # Verificar si la respuesta es conceptual o de código
    has_code = "```python" in response or "```" in response

    if not has_code:
        return response

    if "```python" in response:
        python_code = response.split("```python")[1].split("```")[0].strip()
    else:
        python_code = response.split("```")[1].split("```")[0].strip()

    print(f"\n--- CÓDIGO GENERADO POR LLAMA ---\n{python_code}\n--------------------------------")

    try:
        execution_result = python_tool.run(python_code)
        print(f"--- RESULTADO EJECUCIÓN ---\n{execution_result}\n---------------------------")
    except Exception as e:
        return f"Error al ejecutar la consulta Pandas: {str(e)}"

    synthesis_messages = [
        SystemMessage(
            content=(
                "Eres un asistente analista de datos hospitalarios. "
                "Responde de forma clara y directa en español utilizando únicamente "
                "el resultado obtenido mediante Pandas. "
                "No inventes ni modifiques valores. "
                "Si el resultado contiene fechas, muéstralas en formato "
                "YYYY-MM-DD HH:MM:SS."
            )
        )
        ,
        HumanMessage(
            content=(
                f"Pregunta del usuario: {user_query}\n"
                f"Resultado obtenido del código: {execution_result}"
            )
        )
        ,
    ]
    final_answer = llm.invoke(synthesis_messages).content
    return final_answer

# -----------------------------------------------------------------------------
# 5. Interfaz de Chat (Streamlit)
# -----------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hola. Puedo responder consultas sobre admisiones, triage, servicios, pacientes y medicamentos. ¿Qué deseas consultar?",
        }
    ]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if user_input := st.chat_input("Escribe tu consulta..."):
    st.chat_message("user").markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    with st.chat_message("assistant"):
        with st.spinner("Ejecutando código Pandas y generando respuesta..."):
            response_text = query_hospital_data(user_input)
            st.markdown(response_text)
            st.session_state.messages.append(
                {"role": "assistant", "content": response_text}
            )