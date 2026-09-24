# Cumplimiento del reto (sus.hackaton.pdf)

Estado: ✅ cumplido · 🟡 cumplido con supuesto documentado · ⏳ pendiente del equipo

## Alcance del reto

| # | Requisito | Estado | Dónde |
|---|---|---|---|
| 1 | Prototipo funcional (MVP) | ✅ | `streamlit run app.py` |
| 2 | Asistente conversacional (Agente IA) | ✅ | Dock en todas las páginas + página *Agente IA* (`core/agent.py`) |
| 3 | Visualizaciones con KPIs (Dashboard) | ✅ | Panorama, Camas, Urgencias, Cirugías, Farmacia, Demanda, Redes |
| 4 | Carga y análisis de datos históricos | ✅ | *Centro de datos* (carga → ETL → recarga) y `scripts/cleaner.py` |
| 5–6 | Ocupación diaria/mensual por servicio | 🟡 | Censo de medianoche; egreso y capacidad estimados (README §7) |
| 6 | Tiempos de espera por triage | ✅ | Urgencias; agente "espera" |
| 6 | Eficiencia de quirófanos (realizadas vs programadas) | ✅ | Cirugías; KPI de cumplimiento |
| 6 | Medicamentos de mayor y menor rotación | ✅ | Farmacia (ABC, top y bottom) |
| 6 | Servicios y especialidades más demandados | ✅ | Demanda y pronóstico |
| 6 | Otros KPIs | ✅ | Estancia media, estancias prolongadas, llegadas día × hora, uso de quirófano, pronóstico |
| 7 | Alertas tempranas de desabastecimiento | 🟡 | Stock < 5 días, picos de consumo, vencimientos (stock inicial simulado hasta registrar conteos) |
| 7 | Reasignar personal / abrir camas | ✅ | Alertas de ocupación ≥ 85 % con servicio de desborde; refuerzo por turno |
| 7 | Optimizar programación de cirugías | ✅ | Franjas subutilizadas; detección de cruces al programar |

## Recomendaciones técnicas

| Ítem | Estado | Implementación |
|---|---|---|
| Frontend Streamlit con chat integrado | ✅ | Chat siempre visible y con contexto de la vista |
| Visualizador de resultados (tablas y gráficos de la respuesta) | ✅ | Cada respuesta trae tabla, gráfico y "¿Cómo lo calculé?" |
| Backend Python (FastAPI) | ✅ | `api.py`: `POST /api/query`, `GET /api/kpis`, `/api/alerts`, login y registro |
| Agente NL2SQL con esquema y ejemplos (few-shot) | ✅ | Ollama llama3.2 local; esquema + valores válidos + 3 ejemplos |
| Plan B sin API | ✅ | Motor de intenciones con los mismos KPIs; *fallback* automático |
| Sanear SQL generado | ✅ | `core/sql_guard.py` + base en memoria `query_only` |
| SQLite | ✅ | Base operativa `data/hospital.db` + base analítica en memoria |
| Modelo de datos mínimo (Admisiones, Camas, Medicamentos) | ✅ | `core/db.py` (incluye médico asignado, fecha de salida, vencimiento, consumo diario) |
| Carga con pandas | ✅ | `scripts/cleaner.py`, `core/data.py` |
| README completo | ✅ | Reto, instalación desde cero, tecnologías, estructura |
| Roles del equipo y autores | ⏳ | Completar la tabla del README §10 |
| Ejecución local clara | ✅ | README §3 (manual); docker-compose listado como mejora |

## Seguridad

| Ítem | Estado | Implementación |
|---|---|---|
| Datos anónimos; el agente nunca devuelve PII | ✅ | Seudónimos, columnas sensibles excluidas, guardia de privacidad, rechazo explícito |
| Credenciales en `.env`, nunca en el repositorio | ✅ | `.env.example`; `.env` y `*.db` en `.gitignore` (ojo: `../.env` quedó en el primer commit; retirarlo con `git rm --cached code/.env`) |
| Sanear entradas y SQL | ✅ | Validaciones en `core/records.py`, SQL parametrizado, `sql_guard` |
| Autenticación con roles (opcional) | ✅ | PIN PBKDF2, 8 roles, matriz de permisos, token HS256 en la API |

## Buenas prácticas

| Ítem | Estado |
|---|---|
| Código comentado y estructurado; nombres en inglés | ✅ (textos de UI en español) |
| Modularización agente / BD / API / frontend | ✅ `core/agent.py`, `core/db.py` + `core/data.py`, `api.py`, `views/` + `ui/` |
| Patrones simples | ✅ Servicio/repositorio, estrategia con *fallback*, estado inmutable |
| Git con commits frecuentes | ⏳ Responsabilidad del equipo |

## Reportes visuales

| Ítem | Estado |
|---|---|
| KPIs en tarjetas: ocupación (%), espera (min), stock bajo | ✅ |
| Evolución de ocupación (línea) | ✅ Camas |
| Distribución por servicio / categoría (circular) | ✅ Triage, perfil de pacientes, puntos de triage |
| Consumo de medicamentos (barras) | ✅ Farmacia |
| Tabla dinámica de pacientes sin datos sensibles (diagnóstico, médico, fecha) | ✅ Centro de datos → Episodios; Registro clínico (médico asignado) |

## Innovación

| Ítem | Estado |
|---|---|
| Agente NL2SQL como núcleo | ✅ |
| Alertas predictivas (picos de demanda → reforzar stock) | ✅ Tendencias por capítulo CIE-10 + pronóstico 14 días |
| Análisis de causa raíz de la espera | ✅ Turno × triage vs periodo anterior |
| Extra | ✅ Chat con contexto de vista, mapa de camas, grafos, registro clínico auditado, modo oscuro |

## Demostración (4 preguntas)

| Pregunta | Respuesta del prototipo |
|---|---|
| ¿Cuántas camas de UCI están ocupadas hoy? | Ocupadas / capacidad / disponibles al corte + recomendación |
| ¿Medicamentos con menos de 5 días de inventario? | Lista ordenada con días, consumo/día y orden de compra sugerida |
| ¿Espera promedio en urgencias en la última semana? | Promedio global y mediana por nivel vs meta + causa probable |
| ¿Qué servicio tiene más pacientes ingresados este mes? | Servicio líder con ingresos, pacientes únicos, % y estancia media |
