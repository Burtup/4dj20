"""Staff authentication and role-based permissions.

* PINs are stored as PBKDF2-SHA256 hashes with a per-user random salt
  (never in clear text) and compared in constant time.
* Every write in :mod:`core.records` calls :func:`require`, so permissions
  are enforced in the domain layer, not only hidden in the UI.

Brief, section 3: "Autenticación (Opcional): login con JWT y roles básicos".
The Streamlit session plays the token role; the API can reuse
:func:`authenticate` + :func:`require` the same way.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

ROLES = [
    "Administrador",
    "Médico",
    "Enfermería",
    "Jefe de servicio",
    "Químico farmacéutico",
    "Auxiliar de farmacia",
    "Admisiones",
    "Programación quirúrgica",
]

# permission → roles allowed. Reading dashboards needs no permission.
PERMISSIONS: dict[str, set[str]] = {
    "ingresos.crear": {"Administrador", "Médico", "Enfermería", "Jefe de servicio", "Admisiones"},
    "ingresos.editar": {"Administrador", "Médico", "Enfermería", "Jefe de servicio", "Admisiones"},
    "ingresos.egresar": {"Administrador", "Médico", "Jefe de servicio"},
    "ingresos.anular": {"Administrador", "Jefe de servicio"},
    "camas.editar": {"Administrador", "Enfermería", "Jefe de servicio", "Admisiones"},
    "camas.crear": {"Administrador", "Jefe de servicio"},
    "inventario.movimiento": {"Administrador", "Químico farmacéutico", "Auxiliar de farmacia", "Enfermería"},
    "inventario.editar": {"Administrador", "Químico farmacéutico"},
    "cirugias.programar": {"Administrador", "Médico", "Jefe de servicio", "Programación quirúrgica"},
    "cirugias.cerrar": {"Administrador", "Médico", "Jefe de servicio", "Programación quirúrgica"},
    "personal.gestionar": {"Administrador"},
    "auditoria.ver": {"Administrador", "Jefe de servicio"},
    "datos.cargar": {"Administrador"},
}

PERMISSION_LABELS = {
    "ingresos.crear": "Registrar ingresos", "ingresos.editar": "Editar ingresos",
    "ingresos.egresar": "Dar egreso", "ingresos.anular": "Anular ingresos",
    "camas.editar": "Cambiar estado de camas", "camas.crear": "Crear camas",
    "inventario.movimiento": "Entradas / salidas de stock", "inventario.editar": "Editar ficha de inventario",
    "cirugias.programar": "Programar cirugías", "cirugias.cerrar": "Ejecutar / cancelar cirugías",
    "personal.gestionar": "Gestionar personal", "auditoria.ver": "Ver auditoría", "datos.cargar": "Cargar archivos HIS",
}


@dataclass(frozen=True)
class Actor:
    """The signed-in staff member performing an action."""

    id: int
    nombre: str
    rol: str

    def can(self, permission: str) -> bool:
        return self.rol in PERMISSIONS.get(permission, set())


class PermissionDenied(PermissionError):
    pass


def require(actor: Actor | None, permission: str) -> Actor:
    if actor is None:
        raise PermissionDenied("Inicia sesión para registrar o modificar información.")
    if not actor.can(permission):
        raise PermissionDenied(f"El rol «{actor.rol}» no puede: {PERMISSION_LABELS.get(permission, permission)}.")
    return actor


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()
    return digest, salt


def verify_pin(pin: str, digest: str, salt: str) -> bool:
    return hmac.compare_digest(hash_pin(pin, salt)[0], digest)


def validate_pin(pin: str) -> str:
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ValueError("El PIN debe tener entre 4 y 8 dígitos.")
    return pin
