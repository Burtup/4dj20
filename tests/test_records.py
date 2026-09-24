"""Record management: validation, permissions, audit trail, analytics refresh.

Run from ``code/``::  python -m unittest discover -s tests -v
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config  # noqa: E402

config.DB_FILE = Path(tempfile.mkdtemp()) / "records.db"

from core import db, kpis, records  # noqa: E402
from core.data import get_data  # noqa: E402
from core.filters import FilterState  # noqa: E402
from core.security import PermissionDenied  # noqa: E402

BASE = {"paciente_ref": "PAC-T1", "sexo": "Femenino", "edad": 40, "regimen": "Subsidiado", "zona": "Urbana",
        "fecha_ingreso": "2026-09-23 09:00", "via_ingreso": "Urgencias", "clase_ingreso": "Hospitalario",
        "tipo_riesgo": "Enfermedad General", "servicio": "Pediatría"}


class RecordsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_data()  # seeds demo staff, beds and inventory
        cls.admin = records.authenticate(1, "1234")
        cls.doctor = records.authenticate(2, "1234")
        cls.nurse = records.authenticate(3, "1234")

    def test_login(self):
        self.assertIsNotNone(self.nurse)
        self.assertIsNone(records.authenticate(3, "9999"))
        self.assertEqual(records.audit_log()["accion"].iloc[0], "INICIO_FALLIDO")

    def test_admission_lifecycle_updates_analytics(self):
        before = get_data().version
        adm = records.create_admission(self.nurse, BASE | {"codigo_diagnostico": "J189", "nivel_triage": 3,
                                                           "fecha_triage": "2026-09-23 08:40",
                                                           "fecha_atencion": "2026-09-23 09:10"})
        data = get_data()
        self.assertGreater(data.version, before)
        row = data.admissions[data.admissions["origen"] == "Registro app"].iloc[-1]
        self.assertEqual(row["espera_min"], 30)
        self.assertEqual(row["capitulo_dx"], "Respiratorio")
        with self.assertRaises(PermissionDenied):
            records.discharge(self.nurse, adm, "2026-09-23 18:00", "Alta médica")
        records.discharge(self.doctor, adm, "2026-09-23 18:00", "Alta médica")
        with self.assertRaises(records.ValidationError):
            records.update_admission(self.nurse, adm, BASE)
        actions = records.audit_log("ingresos")["accion"].tolist()
        self.assertIn("CREAR", actions)
        self.assertIn("EGRESO", actions)

    def test_validation(self):
        for bad in ({"edad": 130}, {"codigo_diagnostico": "123"}, {"paciente_ref": "1098765432"},
                    {"fecha_ingreso": "2099-01-01 00:00"}, {"servicio": "Cafetería"}):
            with self.assertRaises(records.ValidationError, msg=str(bad)):
                records.create_admission(self.nurse, BASE | bad)
        with self.assertRaises(PermissionDenied):
            records.create_admission(None, BASE)

    def test_bed_cannot_be_double_booked(self):
        bed = get_data().beds.query("servicio == 'Sala de partos'")["codigo_cama"].iloc[0]
        records.create_admission(self.nurse, BASE | {"servicio": "Sala de partos", "codigo_cama": bed})
        with self.assertRaises(records.ValidationError):
            records.create_admission(self.nurse, BASE | {"servicio": "Sala de partos", "codigo_cama": bed})

    def test_stock_and_permissions(self):
        code = get_data().inventory["codigo_servicio"].iloc[0]
        stock = records.stock_movement(self.nurse, code, "Entrada", 10, "Compra")
        self.assertEqual(records.stock_movement(self.nurse, code, "Salida", 5, "Dispensación"), stock - 5)
        with self.assertRaises(records.ValidationError):
            records.stock_movement(self.nurse, code, "Salida", 10 ** 9, "x")
        with self.assertRaises(PermissionDenied):
            records.update_item(self.nurse, code, 5, None, None)
        inv = get_data().inventory.set_index("codigo_servicio")
        self.assertEqual(inv.at[code, "stock"], stock - 5)

    def test_surgery_overlap_and_kpi(self):
        first = records.schedule_surgery(self.doctor, {"fecha_programada": "2026-09-22 07:00", "quirofano": "Urologia",
                                                       "procedimiento": "RTU", "servicio": "Recuperación",
                                                       "duracion_min": 120})
        with self.assertRaises(records.ValidationError):
            records.schedule_surgery(self.doctor, {"fecha_programada": "2026-09-22 08:00", "quirofano": "Urologia",
                                                   "procedimiento": "Otra", "servicio": "Recuperación"})
        records.close_surgery(self.doctor, first, "Ejecutada")
        fs = FilterState.default(get_data())
        self.assertGreaterEqual(kpis.surgery_summary(fs)["ejecutadas"], 1)

    def test_staff_admin_only_and_audit_immutable(self):
        with self.assertRaises(PermissionDenied):
            records.create_staff(self.nurse, "X", "Médico", None, None, "1234")
        new = records.create_staff(self.admin, "Dra. Prueba", "Médico", "RM-1", "UCI", "5678")
        self.assertIsNotNone(records.authenticate(new, "5678"))
        self.assertNotIn("pin_hash", records.list_staff().columns)
        self.assertNotIn("5678", " ".join(records.audit_log("personal").fillna("").astype(str).sum()))
        with self.assertRaises(sqlite3.DatabaseError):
            db.connection().execute("UPDATE auditoria SET accion = 'X'")


if __name__ == "__main__":
    unittest.main()
