import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from langchain_experimental.agents.agent_toolkits import create_pandas_dataframe_agent
from langchain_ollama import ChatOllama

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import load_datasets

# -----------------------------------------------------------------------------
# 1. Configuración Inicial de Streamlit
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Asistente de Gestión Hospitalaria",
    page_icon="🏥",
    layout="wide"
)

st.title("Asistente de Gestión Hospitalaria")
st.caption("Consulta conversacional sobre registros hospitalarios usando Llama 3.2 (Ollama).")

# -----------------------------------------------------------------------------
# 2. Carga de Archivos Parquet
# -----------------------------------------------------------------------------
datasets = load_datasets()

# Panel lateral
with st.sidebar:
    st.header("Resumen de Datos")

    if st.button("🔄 Nueva Consulta", use_container_width=True, type="primary"):
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "Hola. Puedo responder consultas sobre admisiones, triage, "
                    "servicios, pacientes y medicamentos. ¿Qué deseas consultar?"
                ),
            }
        ]
        st.rerun()

    st.divider()

    for name, df in datasets.items():
        with st.expander(f"📊 {name}  ({df.shape[0]:,} filas)"):
            st.caption(f"Columnas: {df.shape[1]}")
            date_cols = [c for c in df.columns if "fecha" in c]
            if date_cols:
                col = date_cols[0]
                try:
                    st.caption(
                        f"Rango: {df[col].min().date()} – {df[col].max().date()}"
                    )
                except Exception:
                    pass


# -----------------------------------------------------------------------------
# 3. Inicialización del Agente Inteligente
#  TODO: revisar parser de errores
# -----------------------------------------------------------------------------
@st.cache_resource
def get_pandas_agent(dfs: dict[str, pd.DataFrame]):
    llm = ChatOllama(
        model="llama3.2",
        temperature=0.0
    )

    # Mapeo explícito de dataframes para que el agente reconozca df1, df2... por nombre
    df_list = [
        dfs["ingresos"],
        dfs["triage"],
        dfs["atencion"],
        dfs["paciente"],
        dfs["servicios"],
        dfs["medicamento_insumo"],
        dfs["programacion_cirugia"]
    ]

    prefix_prompt = """
    Trabajas con 7 DataFrames que contienen información hospitalaria:
    - df1 (ingresos): Registros de ingreso, vía de ingreso (Urgencias, Ambulatorio), fechas de ingreso y hospitalización, cama, diagnóstico.
    - df2 (triage): Clasificación y fechas de triage.
    - df3 (atencion): Fechas y registros de atenciones médicas.
    - df4 (paciente): Datos demográficos del paciente (edad, sexo, municipio, asegurador).
    - df5 (servicios): Servicios prestados, especialidad, área, fecha de prestación.
    - df6 (medicamento_insumo): Medicamentos e insumos entregados, cantidad, fecha.
    - df7 (programacion_cirugia): Cirugías programadas por paciente y servicio.

    SIEMPRE debes finalizar tu análisis escribiendo exactamente:
    Final Answer: <tu respuesta detallada en español>
    """

    agent = create_pandas_dataframe_agent(
        llm=llm,
        df=df_list,
        verbose=True,
        allow_dangerous_code=True,  #Para activar script de ejecucion local
        agent_type="zero-shot-react-description",
        prefix=prefix_prompt,
       # handle_parsing_errors=True,  POSIBLEMENTE DEPRECADO
        agent_executor_kwargs={
            "handle_parsing_errors": True,
        }
    )
    return agent


if datasets:
    agent = get_pandas_agent(datasets)
else:
    st.stop()

# -----------------------------------------------------------------------------
# 4. Chat e Historial
# -----------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hola. Puedo responder consultas sobre admisiones, triage, servicios, pacientes y medicamentos. ¿Qué deseas consultar?"
        }
    ]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if user_input := st.chat_input("Escribe tu consulta..."):

    st.chat_message("user").markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    with st.chat_message("assistant"):
        with st.spinner("Analizando registros..."):
            try:
                # Ejecutar el agente
                response = agent.invoke({"input": user_input})
                output_text = response.get("output", str(response))

                # Si el parser falló pero capturó la salida en el texto de error, limpiarla para el usuario
                if "Could not parse LLM output:" in output_text:
                    output_text = output_text.split("Could not parse LLM output:")[-1].strip(" `")

                st.markdown(output_text)
                st.session_state.messages.append({"role": "assistant", "content": output_text})

            except Exception as e:
                error_msg = f"Ocurrió un error al procesar la consulta: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})