# Arquitectura técnica de HospitalIQ

## 1. Capas

| Capa | Módulos | Regla |
|---|---|---|
| Presentación | `app.py`, `views/*`, `ui/*` | Solo dibuja y captura; no calcula KPIs ni escribe SQL |
| API | `api.py` | Traduce HTTP ↔ dominio; mismos permisos y validaciones que la UI |
| Dominio | `core/kpis.py`, `core/insights.py`, `core/agent.py`, `core/records.py` | Sin Streamlit; funciones puras sobre `FilterState` |
| Datos | `core/data.py`, `core/db.py`, `scripts/cleaner.py` | Histórico parquet (lectura) + SQLite operativo (escritura) |
| Transversal | `core/security.py`, `core/sql_guard.py`, `core/fmt.py`, `core/config.py` | Seguridad, formato, configuración |

Patrones: **repositorio/servicio** en `records.py` (validación + permiso + escritura + auditoría en una transacción),
**estrategia** en el agente (reglas vs. NL2SQL con *fallback*), **estado inmutable** (`FilterState`) para memoización.

## 2. Flujo de una pregunta al agente

```
usuario ─► chat (dock o página) ─► HospitalAgent.ask(pregunta, filtros, contexto_vista)
   1. normaliza texto (minúsculas, sin tildes)
   2. detecta intención (catálogo de patrones) y guardia de privacidad
   3. aplica alcance: periodo ("hoy", "última semana", "agosto"…) y servicio mencionado sobre los filtros activos
   4a. intención conocida ──► función KPI (misma del tablero) ──► texto + tabla + gráfico + recomendación
   4b. pregunta abierta / modo forzado ──► Ollama escribe SQL ──► sql_guard.sanitize ──► SQLite en memoria (solo lectura)
        └─► resumen determinístico de las filas (el LLM no redacta cifras) · si falla ─► reglas
   5. AgentAnswer {texto, tabla, gráfico, sql, motor, alcance, sugerencias, ms}
```

## 3. Composición de datos (`core/data.py`)

1. `_his()` (una vez por proceso, ~6 s): lee los 7 parquet, une paciente/triage/atención, deriva edad, nivel y punto
   de triage, egreso estimado (último cargo), marca episodios abiertos al corte, programación quirúrgica con bandera
   de ejecución.
2. `get_data()` compara `meta.version` de SQLite; si cambió (alguien guardó), `_compose()`:
   - agrega los ingresos registrados en la app (id = 900 000 000 + id local),
   - recalcula espera, turno, grupo etario, capítulo CIE-10, estancia,
   - capacidad por servicio = máx(camas en servicio del registro, p95 del censo),
   - inventario = stock SQLite + consumo diario (dispensaciones HIS 30 días + salidas registradas),
   - cirugías HIS + cirugías programadas en la app,
   - limpia la caché de KPIs y reconstruye la base analítica del NL2SQL.

## 4. Modelo de datos operativo (SQLite, `data/hospital.db`)

| Tabla | Campos clave |
|---|---|
| `personal` | nombre, rol, registro_profesional, servicio, pin_hash, pin_salt, activo, ultimo_acceso |
| `ingresos` | paciente_ref (seudónimo), sexo, edad, régimen, zona, fecha_ingreso, vía, clase, riesgo, servicio, cama, triage, horas de triage/atención, CIE-10, médico, estado, fecha/tipo de egreso, creado/actualizado por |
| `camas` | código, servicio, estado_operativo (Disponible / Mantenimiento / Fuera de servicio), notas |
| `inventario` | código, nombre, stock, stock_mínimo, vencimiento, lote, origen (Simulado / Registrado) |
| `movimientos_inventario` | tipo (Entrada / Salida / Ajuste), cantidad, stock resultante, motivo, responsable |
| `cirugias` | fecha, duración, quirófano, procedimiento, CUPS, servicio, cirujano, ingreso, estado, motivo de cancelación |
| `auditoria` | fecha, responsable, rol, acción, entidad, registro, antes (JSON), después (JSON), detalle — **inmutable por trigger** |

Cubre el "modelo de datos mínimo" del reto: *Admisiones* (id_paciente → seudónimo, fecha ingreso/salida, servicio,
diagnóstico, triage, médico asignado), *Camas* (id, servicio, estado, paciente vía ingreso) y *Medicamentos*
(id, nombre, stock, vencimiento, consumo diario promedio).

## 5. Metodología de los KPIs

| KPI | Cálculo |
|---|---|
| Ocupación | Censo de medianoche: episodio en cama a las 00:00 del día siguiente ÷ capacidad del servicio |
| Espera | `fecha_atencion − fecha_triage` en minutos (0–24 h); promedio, mediana, p90 y % ≤ meta (30 min) por nivel |
| Causa raíz de espera | Volumen diario por turno × nivel: últimos 14 días vs. 14 anteriores; se reporta la celda que más creció |
| Cumplimiento quirúrgico | Ejecutadas ÷ programadas (HIS por ingreso del periodo; app por fecha programada ya vencida) |
| Uso de quirófanos | Eventos distintos (ingreso, hora) en áreas de quirófano, promedio por semana, día × hora |
| Rotación | Unidades dispensadas; clasificación ABC (A = 80 % del volumen) |
| Días de inventario | Stock ÷ consumo diario promedio de 30 días; crítico < 5, bajo < 14 |
| Pico de consumo | Consumo diario 7 días vs. 28 previos ≥ +25 % con < 14 días de inventario |
| Tendencia por diagnóstico | Ingresos/día por capítulo CIE-10: 14 días vs. 56 previos ≥ +20 % (base ≥ 2/día) |
| Pronóstico | Nivel de los últimos 28 días × factor del día de la semana; banda ±1,28 σ de los residuos (≈ 80 %) |

Las comparaciones de tendencia se anclan al corte del HIS para que los días posteriores (solo registros manuales)
no generen caídas artificiales.

## 6. Diseño de la interfaz

- Tema nativo de Streamlit (`.streamlit/config.toml`) con `[theme.light]` y `[theme.dark]`; barra lateral azul
  institucional. Componentes propios usan variables CSS `--hiq-*` según el modo (`ui/theme.py`).
- Paletas categóricas validadas para daltonismo (claro y oscuro); secuencial verde de un solo tono; colores de estado
  y de triage reservados (nunca se reutilizan como series).
- El dock del asistente es una columna *sticky*: acompaña el desplazamiento en todas las páginas.
- Todas las inserciones HTML escapan el texto dinámico.
