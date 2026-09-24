"""HospitalIQ REST API (brief, section b: "Endpoints mínimos").

Run from ``code/``::

    uvicorn api:app --port 8000        # docs at http://localhost:8000/docs

Endpoints
---------
* ``GET  /api/health``                 – liveness + data cut-off
* ``GET  /api/kpis``                   – precalculated dashboard KPIs
* ``GET  /api/alerts``                 – prioritised alerts & recommendations
* ``POST /api/query``                  – natural-language question → agent (NL2SQL / rules)
* ``POST /api/auth/login``             – staff PIN → signed token (JWT HS256, 8 h)
* ``GET  /api/ingresos``               – admissions captured in the app (token)
* ``POST /api/ingresos``               – register an admission (token + role)
* ``POST /api/ingresos/{id}/egreso``   – discharge (token + role)
* ``POST /api/inventario/{code}/movimientos`` – stock in/out/adjust (token + role)

The API reuses exactly the same domain layer as the Streamlit app, so
validation, permissions and the audit trail are identical.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import date, datetime

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from core import insights, kpis, records
from core.agent import HospitalAgent
from core.data import get_data
from core.filters import FilterState
from core.security import Actor, PermissionDenied

app = FastAPI(title="HospitalIQ API", version="1.0",
              description="KPIs, agente conversacional y registro clínico del Hospital Susana López de Valencia.")

SECRET = os.getenv("API_SECRET") or secrets.token_hex(32)  # set API_SECRET in .env to keep tokens across restarts
TOKEN_TTL_S = 8 * 3600


# ── Helpers ──────────────────────────────────────────────────────────────────

def _records(df: pd.DataFrame, limit: int = 200) -> list[dict]:
    """DataFrame → JSON-safe records (NaN → null, timestamps → ISO)."""
    return json.loads(df.head(limit).to_json(orient="records", date_format="iso", force_ascii=False))


def _filters(date_from: date | None, date_to: date | None, service: list[str] | None) -> FilterState:
    fs = FilterState.default(get_data())
    fs = fs.with_dates(date_from or fs.date_from, date_to or fs.date_to)
    return FilterState(fs.date_from, fs.date_to, services=tuple(service or ()))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: dict) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(payload).encode())
    sig = _b64(hmac.new(SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def _verify(token: str) -> dict:
    try:
        header, body, sig = token.split(".")
        expected = _b64(hmac.new(SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        raise HTTPException(401, "Token inválido.") from None
    if payload.get("exp", 0) < time.time():
        raise HTTPException(401, "Token vencido.")
    return payload


def current_actor(authorization: str = Header(default="")) -> Actor:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Falta el encabezado Authorization: Bearer <token>.")
    payload = _verify(authorization[7:])
    staff = records.list_staff(active_only=True).set_index("id")
    if payload["sub"] not in staff.index:
        raise HTTPException(401, "Usuario inactivo.")
    return Actor(int(payload["sub"]), staff.at[payload["sub"], "nombre"], staff.at[payload["sub"], "rol"])


def _domain(call):
    try:
        return call()
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from None
    except records.ValidationError as exc:
        raise HTTPException(422, str(exc)) from None


# ── Read endpoints ───────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    data = get_data()
    return {"status": "ok", "corte": str(data.reference_date.date()), "ingresos": len(data.admissions)}


@app.get("/api/kpis")
def get_kpis(date_from: date | None = None, date_to: date | None = None,
             service: list[str] | None = Query(None, description="Servicio(s): UCI, Pediatría…")) -> dict:
    fs = _filters(date_from, date_to, service)
    overview = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in kpis.overview(fs).items()}
    surgery = kpis.surgery_summary(fs)
    return {
        "filtros": fs.describe(),
        "resumen": overview,
        "ocupacion_por_servicio": _records(kpis.occupancy_by_service(fs)),
        "espera_por_triage": _records(kpis.wait_by_triage(fs)),
        "cirugias": {k: v for k, v in surgery.items() if k != "por_quirofano"},
        "medicamentos_criticos": _records(kpis.inventory_status(5)[["codigo_servicio", "nombre", "stock", "consumo_diario", "dias_inventario"]], 50),
        "demanda_especialidades": _records(kpis.demand_by_specialty(fs)),
    }


@app.get("/api/alerts")
def get_alerts(date_from: date | None = None, date_to: date | None = None) -> list[dict]:
    fs = _filters(date_from, date_to, None)
    return [{"severidad": i.severity, "area": i.category, "titulo": i.title, "detalle": i.detail, "accion": i.action}
            for i in insights.generate(fs)]


class Query(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    date_from: date | None = None
    date_to: date | None = None
    services: list[str] | None = None
    force_llm: bool = False


@app.post("/api/query")
def query(body: Query) -> dict:
    """Question → agent → JSON (text, table, SQL used, engine)."""
    answer = HospitalAgent().ask(body.question, _filters(body.date_from, body.date_to, body.services),
                                 force_llm=body.force_llm)
    return {"respuesta": answer.text, "intencion": answer.intent, "motor": answer.source, "sql": answer.sql,
            "alcance": answer.scope, "tabla": _records(answer.table) if answer.table is not None else [],
            "sugerencias": answer.followups, "ms": answer.elapsed_ms}


# ── Auth & write endpoints ───────────────────────────────────────────────────

class Login(BaseModel):
    staff_id: int
    pin: str = Field(..., min_length=4, max_length=8)


@app.post("/api/auth/login")
def login(body: Login) -> dict:
    actor = records.authenticate(body.staff_id, body.pin)
    if actor is None:
        raise HTTPException(401, "Credenciales inválidas.")
    token = _sign({"sub": actor.id, "rol": actor.rol, "exp": int(time.time()) + TOKEN_TTL_S})
    return {"access_token": token, "token_type": "bearer", "rol": actor.rol, "nombre": actor.nombre}


class Admission(BaseModel):
    paciente_ref: str | None = Field(None, max_length=20)
    sexo: str
    edad: int = Field(..., ge=0, le=120)
    regimen: str
    zona: str
    municipio: str | None = None
    fecha_ingreso: datetime
    via_ingreso: str
    clase_ingreso: str
    tipo_riesgo: str
    servicio: str
    codigo_cama: str | None = None
    nivel_triage: int | None = Field(None, ge=1, le=5)
    fecha_triage: datetime | None = None
    fecha_atencion: datetime | None = None
    codigo_diagnostico: str | None = None
    nombre_diagnostico: str | None = None
    medico_id: int | None = None
    observaciones: str | None = Field(None, max_length=500)


@app.get("/api/ingresos")
def list_admissions(estado: str | None = None, actor: Actor = Depends(current_actor)) -> list[dict]:
    return _records(records.list_admissions(estado), 1000)


@app.post("/api/ingresos", status_code=201)
def create_admission(body: Admission, actor: Actor = Depends(current_actor)) -> dict:
    return {"id": _domain(lambda: records.create_admission(actor, body.model_dump()))}


class Discharge(BaseModel):
    fecha_egreso: datetime
    tipo_egreso: str
    nota: str | None = Field(None, max_length=500)


@app.post("/api/ingresos/{admission_id}/egreso")
def discharge(admission_id: int, body: Discharge, actor: Actor = Depends(current_actor)) -> dict:
    _domain(lambda: records.discharge(actor, admission_id, body.fecha_egreso, body.tipo_egreso, body.nota))
    return {"id": admission_id, "estado": "Egresado"}


class Movement(BaseModel):
    tipo: str
    cantidad: float = Field(..., ge=0)
    motivo: str | None = Field(None, max_length=200)


@app.post("/api/inventario/{code}/movimientos")
def stock_movement(code: str, body: Movement, actor: Actor = Depends(current_actor)) -> dict:
    stock = _domain(lambda: records.stock_movement(actor, code, body.tipo, body.cantidad, body.motivo))
    return {"codigo_servicio": code, "stock": stock}
