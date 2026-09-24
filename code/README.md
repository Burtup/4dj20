# HospitalIQ — Asistente de gestión hospitalaria

**Hospital Susana López de Valencia · Reto Hackathon Campus Party FUP (Ingeniería de Sistemas)**

HospitalIQ integra la información dispersa del hospital (admisiones, camas, triage, quirófanos, farmacia) en un
solo lugar y la pone al alcance de directivos y jefes de servicio mediante un **agente conversacional**, un
**tablero de indicadores** y un **registro clínico auditado** donde el personal ingresa y actualiza la operación diaria.

---

## 1. El reto

El hospital atiende ~500 pacientes diarios, pero la ocupación de camas, los tiempos de espera, el uso de quirófanos
y el consumo de medicamentos viven en sistemas y hojas de cálculo desconectados. Los directivos dependen de reportes
manuales de TI, lo que retrasa decisiones críticas (abrir camas, reforzar turnos, comprar medicamentos).

## 2. La solución

| Componente | Qué hace |
|---|---|
| **Agente IA** (siempre visible a la derecha) | Responde en lenguaje natural con los **filtros y la vista que el usuario tiene abiertos**. Motor de reglas instantáneo (Plan B del reto) + **NL2SQL** con un modelo local (Ollama · llama3.2). Muestra tabla, gráfico y "¿Cómo lo calculé?". |
| **Tablero** | KPIs del reto (ocupación, espera por triage, cirugías programadas vs. ejecutadas, rotación de medicamentos, demanda) con gráficos interactivos, mapa de camas, redes y pronóstico. |
| **Alertas y recomendaciones** | Desabastecimiento (< 5 días, picos de consumo, vencimientos), servicios > 85 % de ocupación, causa raíz de la espera, franjas de quirófano subutilizadas, alertas predictivas por diagnóstico. |
| **Registro clínico** | Ventanas para **registrar, editar, dar egreso y anular** ingresos; gestionar camas, inventario (entradas/salidas/ajustes) y programación quirúrgica. |
| **Personal y accesos** | Registro de médicos, enfermería, farmacia, admisiones… con **PIN cifrado y permisos por rol**. |
| **Auditoría** | Rastro **inmutable** de quién creó/editó qué, cuándo y el antes/después de cada campo. |
| **API REST** | `GET /api/kpis`, `POST /api/query` y endpoints de registro con token (FastAPI). |

---

## 3. Instalación paso a paso (Windows, macOS o Linux)

### Requisitos
- Python **3.11+** (probado con 3.14)
- Opcional: [Ollama](https://ollama.com/download) para el modo NL2SQL. Sin Ollama, el agente funciona con el motor de reglas.

### 3.1 Entorno e instalación
```bash
cd code
python -m venv .venv
```
Activa el entorno (`.venv\Scripts\activate` en Windows, `source .venv/bin/activate` en macOS/Linux) y luego:
```bash
pip install -r requerimientos.txt
```

### 3.2 Configuración
Copia `.env.example` como `.env` y ajusta los valores (el archivo `.env` **nunca** se sube al repositorio).

### 3.3 Datos del HIS
Copia los 7 archivos de `Datos/` (delimitados por `|`) a `code/data/raw/` con su nombre original
(`Atencion.txt`, `Ingresos.txt`, `MedicamentoInsumo.txt`, `Paciente.txt`, `ProgramacionCirugia.txt`,
`Servicios.txt`, `Triage.txt`) y procésalos:
```bash
python scripts/cleaner.py
```
> También puedes cargarlos desde la app: si no hay datos procesados, se abre la pantalla **Primer uso** con el cargador.

### 3.4 Modelo local (opcional, recomendado)
```bash
ollama pull llama3.2
```

### 3.5 Ejecutar
```bash
streamlit run app.py
```
Abre http://localhost:8501. La primera carga tarda ~6 s (enriquecimiento del HIS); luego es instantánea.

API REST (opcional, en otra terminal):
```bash
uvicorn api:app --port 8000
```
Documentación interactiva en http://localhost:8000/docs.

### 3.6 Cuentas de demostración
Al primer arranque se crean cuentas ficticias, todas con **PIN `1234`** (cámbialo en *Mi PIN*):

| Cuenta | Rol |
|---|---|
| Administrador del sistema | Administrador |
| Médico de turno (demo) | Médico |
| Enfermería de turno (demo) | Enfermería |
| Jefatura de servicio (demo) | Jefe de servicio |
| Farmacia (demo) | Químico farmacéutico |
| Admisiones (demo) | Admisiones |

### 3.7 Pruebas
```bash
python -m unittest discover -s tests -v
```
19 pruebas: validador SQL, base analítica de solo lectura, las 4 preguntas del reto, privacidad, KPIs,
ciclo de vida de ingresos, permisos por rol, cruces de agenda quirúrgica, stock y auditoría inmutable.

---

## 4. Uso

### Navegación
| Grupo | Página | Contenido |
|---|---|---|
| Operación | **Panorama** | 6 KPIs con tendencia, ocupación por servicio, alertas, pronóstico, triage, diagnósticos y especialidades |
| | **Camas** | Ocupación al corte, evolución diaria, mapa de calor semanal, **mapa de camas** (libre/ocupada/prolongada/mantenimiento) y plan de egreso |
| | **Urgencias** | Espera por nivel de triage vs. meta, tendencia, llegadas día × hora, **causa raíz** turno × triage, puntos de triage |
| | **Cirugías** | Programadas vs. ejecutadas, procedimientos por semana, uso de quirófanos día × hora, recomendaciones de agenda |
| | **Farmacia** | Inventario con días de cobertura, rotación, curva ABC, alertas tempranas de consumo |
| Análisis | **Demanda y pronóstico** | Servicios, especialidades, mapa de diagnósticos (sunburst), perfil de pacientes, pronóstico 14 días, alertas predictivas |
| | **Flujos y redes** | Sankey del flujo de pacientes y grafo especialidad ↔ área de servicio |
| Asistente | **Agente IA** | Conversación a pantalla completa, modo del motor, preguntas del reto, exportar conversación |
| Gestión | **Registro clínico** | Ingresos y egresos · Camas · Inventario · Cirugías (crear / editar / cerrar / anular) |
| | **Personal y accesos** | Funcionarios, roles y matriz de permisos |
| | **Auditoría** | Eventos con filtros y comparación antes/después, exportable a CSV |
| | **Centro de datos** | Catálogo, carga de archivos del HIS, episodios anonimizados, glosario |

### Filtros en capas (panel lateral)
1. **Periodo**: atajos 7 d / 30 d / 90 d / Todo o rango libre (los atajos siguen al corte de datos).
2. **Servicio**: UCI, Pediatría, Hospitalización…
3. **Más filtros**: vía y clase de ingreso, régimen, sexo, grupo de edad y zona.

Los filtros se aplican a **todas** las vistas y al asistente.

### El asistente "ve" lo que ves
Cada página publica su contexto (nombre, cifras visibles, sugerencias). Si preguntas "¿y aquí qué pasa?" desde
Urgencias con el filtro *UCI · últimos 30 días*, la respuesta usa exactamente ese alcance. Las preguntas pueden
sobrescribirlo ("…en agosto", "…hoy", "…de pediatría").

**Preguntas de la demostración del reto** (botones en *Agente IA*):
1. ¿Cuántas camas de UCI están ocupadas hoy?
2. ¿Cuáles son los medicamentos con menos de 5 días de inventario?
3. ¿Cuál es el tiempo de espera promedio en urgencias en la última semana?
4. ¿Qué servicio tiene más pacientes ingresados este mes?

Otras: *¿Por qué aumentó el tiempo de espera?*, *Dame las alertas activas*, *¿Qué se espera para la próxima semana?*,
*¿Qué cambios se registraron hoy?*, *¿En qué horas llegan más pacientes?*

**Modos del motor**: *Automático* (reglas para preguntas conocidas, NL2SQL para las abiertas), *Solo reglas*
y *Forzar NL2SQL*. En CPU, llama3.2 tarda ~40 s por consulta; el motor de reglas responde en < 100 ms.

### Registro clínico (ingreso de datos)
1. Inicia sesión en el panel lateral (**Iniciar sesión** → funcionario → PIN).
2. En **Gestión → Registro clínico**, usa **Nuevo ingreso**: datos del paciente *sin nombre ni documento*
   (referencia interna, sexo, edad, régimen, zona), vía, clase, servicio y **cama libre**, triage, horas de triage
   y primera atención, CIE-10 (autocompleta el diagnóstico con el catálogo del HIS) y médico asignado.
3. Selecciona una fila para **Editar**, **Dar egreso** (libera la cama) o **Anular** (con motivo).
4. Igual para **Camas** (estado operativo, nuevas camas), **Inventario** (entrada, salida, ajuste por conteo,
   ficha con stock mínimo, lote y vencimiento) y **Cirugías** (programar con detección de cruces, ejecutar, cancelar).

Cada guardado actualiza al instante todos los tableros, el mapa de camas y el agente.

---

## 5. Arquitectura

```
            ┌──────────────── Frontend (Streamlit) ────────────────┐     ┌──── API REST (FastAPI) ────┐
            │ app.py · views/* · ui/* (tema, filtros, chat, auth)  │     │ api.py  /api/kpis /query…  │
            └───────────────────────────┬──────────────────────────┘     └──────────────┬─────────────┘
                                        │           misma capa de dominio              │
            ┌───────────────────────────▼───────────────────────────────────────────────▼────────────┐
            │ core/  kpis · insights (alertas) · agent (reglas + NL2SQL) · records (CRUD + auditoría) │
            │        filters · security (roles, PIN) · sql_guard · fmt                                 │
            └───────────────┬───────────────────────────────────────────────┬───────────────────────┘
                            │ core/data.py compone ambas capas             │
          ┌─────────────────▼─────────────────┐              ┌──────────────▼──────────────┐
          │ Histórico HIS (solo lectura)       │              │ Base operativa SQLite        │
          │ Datos/*.txt → scripts/cleaner.py   │              │ data/hospital.db             │
          │ → data/processed/*.parquet         │              │ ingresos · camas · inventario│
          └────────────────────────────────────┘              │ cirugías · personal · auditoría
                                                              └──────────────────────────────┘
          Agente NL2SQL → Ollama (llama3.2, local) → SQL validado → SQLite en memoria, solo lectura
```

Detalle técnico, modelo de datos y metodología de cada KPI: [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md).
Cumplimiento punto por punto del reto: [`docs/CUMPLIMIENTO_RETO.md`](docs/CUMPLIMIENTO_RETO.md).

### Estructura
```
code/
├── app.py                 # Entrada Streamlit: navegación, filtros, dock del asistente
├── api.py                 # API REST (FastAPI)
├── core/                  # Dominio (sin Streamlit): reutilizable por app y API
│   ├── config.py          # Rutas, paleta de marca, umbrales, .env
│   ├── data.py            # Carga y enriquecimiento HIS + composición con SQLite
│   ├── db.py              # Esquema SQLite, transacciones, versión de datos
│   ├── records.py         # Registro clínico: validación + permisos + auditoría
│   ├── security.py        # Roles, permisos, PIN (PBKDF2)
│   ├── filters.py         # Estado de filtros (inmutable, cacheable)
│   ├── kpis.py            # Indicadores del reto
│   ├── insights.py        # Alertas y recomendaciones
│   ├── agent.py           # Agente: intenciones + NL2SQL
│   ├── sql_guard.py       # Validación de SQL generado
│   └── fmt.py             # Formato es-CO
├── ui/                    # Presentación: tema, componentes, gráficas, chat, sesión, filtros
├── views/                 # Una página por archivo
├── scripts/cleaner.py     # ETL .txt → parquet
├── tests/                 # unittest
├── assets/                # Logo e ícono
└── .streamlit/config.toml # Tema claro/oscuro con la paleta del hospital
```

---

## 6. Seguridad y privacidad

- **Datos anónimos**: los tableros, tablas y el agente nunca muestran nombre, documento ni fecha de nacimiento.
  Los episodios se identifican con un seudónimo irreversible (`EP-XXXXXX`). El registro clínico no pide nombre ni
  documento y rechaza referencias con forma de cédula.
- **El agente** rechaza pedidos de datos personales y nunca devuelve diagnósticos de un paciente específico.
- **SQL generado**: solo una sentencia `SELECT`, tablas permitidas, sin columnas sensibles, con `LIMIT`, sobre una
  base en memoria con `PRAGMA query_only` (`core/sql_guard.py`).
- **Consultas propias**: siempre parametrizadas (`?`); nunca se concatena texto del usuario.
- **Autenticación**: PIN de 4–8 dígitos con **PBKDF2-SHA256** (200 000 iteraciones, sal por usuario), bloqueo temporal
  (5 min) tras 5 intentos fallidos en la sesión, intentos fallidos registrados en auditoría, permisos por rol aplicados en el dominio (no solo ocultos en la UI). API con token firmado HS256.
- **Auditoría inmutable**: triggers de SQLite impiden modificar o borrar eventos. Las anulaciones no borran datos.
- **Validación de entradas**: vocabularios controlados, rangos, fechas no futuras, CIE-10/CUPS con formato,
  cama libre y del servicio, cruces de agenda, stock no negativo, textos acotados y sin caracteres de control.
- **Credenciales** en `.env` (excluido por `.gitignore`), igual que `data/` y `*.db`.

## 7. Supuestos de los datos (importante para interpretar)

- **Egreso**: el extracto HIS no trae fecha de salida; se estima como el último cargo (servicio o medicamento) del
  episodio. Los episodios activos al corte se consideran aún hospitalizados. Los registrados en la app usan su egreso real.
- **Capacidad de camas**: el HIS no trae inventario de camas; capacidad = máx(camas en servicio del registro de camas,
  percentil 95 del censo diario). El registro de camas es editable.
- **Existencias**: el HIS registra dispensaciones pero no stock. El inventario arranca con un **stock simulado
  determinístico** (marcado *Simulado*) hasta que farmacia registre conteos o importe `Inventario.csv`.
- **Espera**: minutos entre la hora de triage y la primera atención médica (tabla Atención); el nivel 1–5 se extrae
  del texto de la clasificación.
- **Cirugía ejecutada**: la programación tiene un servicio de quirófano o el código facturado en su ingreso.
- **"Hoy"** es el corte de los datos (último ingreso), no la fecha del computador.

## 8. Tecnologías

| Capa | Tecnología | Por qué |
|---|---|---|
| Frontend | Streamlit 1.64, Plotly | Recomendado por el reto; tema claro/oscuro nativo, diálogos, filtros |
| Dominio | Python, pandas, numpy | Cálculo de KPIs sobre 1,2 M de filas en memoria |
| Agente | Reglas + LangChain/Ollama (llama3.2) | Local y privado (mejora sugerida por el reto), con Plan B siempre disponible |
| Base operativa | SQLite | Recomendado por el reto: sin servidor, transaccional |
| Histórico | Parquet (pyarrow) | Lectura rápida del extracto HIS |
| API | FastAPI + Uvicorn | Documentación automática en `/docs` |
| Pruebas | unittest + Streamlit AppTest | Sin dependencias extra |

Paleta: colores del logo del hospital — azul `#19205b`, verde `#24732b`, lima `#74b722`, fondo `#f2f2f2`.
Las paletas de gráficos se validaron para daltonismo en modo claro y oscuro.

## 9. Limitaciones y mejoras

- NL2SQL en CPU es lento (~40 s) y un modelo de 3B puede fallar en consultas complejas → se mitiga con reglas y
  validación. Mejora: modelo especializado (SQLCoder) o GPU.
- Egreso, capacidad y stock son estimaciones mientras no exista integración en tiempo real con el HIS.
- Mejoras: integración HL7/FHIR con la historia clínica, modelos de ML para pronóstico, notificaciones push de
  alertas, despliegue con docker-compose (app + API + Ollama) y SSO institucional.

## 10. Equipo y autores

| Integrante | Rol en el proyecto | Aportes |
|---|---|---|
| _(completar)_ | _(completar)_ | _(completar)_ |

Metodología: Design Thinking (Idear → Prototipar → Testear → Presentar), con tablero To Do / Doing / Done.
