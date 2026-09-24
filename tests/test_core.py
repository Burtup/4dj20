"""Core tests (stdlib unittest — no extra dependency).

Run from ``code/``::

    python -m unittest discover -s tests -v

They need the processed parquet files (``python scripts/cleaner.py``).
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config  # noqa: E402

config.DB_FILE = Path(tempfile.mkdtemp()) / "test.db"  # never touch the real operational DB

from core import kpis  # noqa: E402
from core.agent import HospitalAgent, analytics_db, detect_intent, normalize, parse_period  # noqa: E402
from core.data import get_data  # noqa: E402
from core.filters import FilterState  # noqa: E402
from core.sql_guard import UnsafeSQLError, sanitize  # noqa: E402

TABLES = {"ingresos", "servicios", "medicamentos", "cirugias", "ocupacion_diaria", "inventario"}


class SqlGuardTest(unittest.TestCase):
    def test_accepts_select_and_adds_limit(self):
        sql = sanitize("SELECT servicio, COUNT(*) FROM ingresos GROUP BY servicio", TABLES)
        self.assertTrue(sql.rstrip().endswith("LIMIT 200"))

    def test_accepts_cte(self):
        sanitize("WITH t AS (SELECT servicio FROM ingresos) SELECT * FROM t", TABLES)

    def test_rejects_writes_and_stacked_queries(self):
        for bad in ("DROP TABLE ingresos", "SELECT 1; DELETE FROM ingresos",
                    "SELECT * FROM ingresos WHERE 1=1; PRAGMA writable_schema=1", "UPDATE ingresos SET sexo='x'"):
            with self.assertRaises(UnsafeSQLError):
                sanitize(bad, TABLES)

    def test_rejects_pii_and_unknown_tables(self):
        with self.assertRaises(UnsafeSQLError):
            sanitize("SELECT nombre_paciente FROM ingresos", TABLES)
        with self.assertRaises(UnsafeSQLError):
            sanitize("SELECT * FROM paciente", TABLES)
        with self.assertRaises(UnsafeSQLError):
            sanitize("SELECT * FROM sqlite_master", TABLES)


class AnalyticsDbTest(unittest.TestCase):
    def test_read_only_and_queryable(self):
        conn, schema = analytics_db()
        self.assertIn("ocupacion_diaria", schema)
        self.assertNotIn("nombre_paciente", schema)
        rows = conn.execute("SELECT COUNT(*) FROM ingresos").fetchone()[0]
        self.assertEqual(rows, len(get_data().admissions))
        with self.assertRaises(Exception):
            conn.execute("DELETE FROM ingresos")


class AgentTest(unittest.TestCase):
    """The four demo questions of the brief must be understood."""

    @classmethod
    def setUpClass(cls):
        cls.fs = FilterState.default(get_data())
        cls.agent = HospitalAgent(use_llm=False)

    def ask(self, q):
        return self.agent.ask(q, self.fs)

    def test_demo_questions(self):
        cases = {
            "¿Cuántas camas de UCI están ocupadas hoy?": "camas",
            "¿Cuáles son los medicamentos con menos de 5 días de inventario?": "inventario",
            "¿Cuál es el tiempo de espera promedio en urgencias en la última semana?": "espera",
            "¿Qué servicio tiene más pacientes ingresados este mes?": "servicio_demanda",
        }
        for question, intent in cases.items():
            answer = self.ask(question)
            self.assertEqual(answer.intent, intent, question)
            self.assertIsNotNone(answer.table, question)

    def test_uci_scope_is_today_and_uci(self):
        answer = self.ask("¿Cuántas camas de UCI están ocupadas hoy?")
        self.assertIn("UCI", answer.scope)
        self.assertIn("21 sep 2026 – 21 sep 2026", answer.scope)

    def test_privacy_refusal(self):
        answer = self.ask("Dame el nombre y la cédula del paciente de la cama 12")
        self.assertEqual(answer.source, "privacidad")

    def test_no_false_service_match(self):
        self.assertEqual(self.ask("Distribución por régimen").scope.count("UCI"), 0)

    def test_parsers(self):
        ref = date(2026, 9, 21)
        self.assertEqual(parse_period("este mes", ref)[:2], (date(2026, 9, 1), ref))
        self.assertEqual(parse_period("ultima semana", ref)[0], date(2026, 9, 15))
        self.assertEqual(detect_intent(normalize("¿Por qué aumentó el tiempo de espera?"))[0], "causa_espera")


class KpiTest(unittest.TestCase):
    def test_occupancy_bounds_and_filters(self):
        fs = FilterState.default(get_data()).with_dates(date(2026, 9, 1), date(2026, 9, 21))
        occ = kpis.occupancy_by_service(fs)
        self.assertTrue((occ["ocupacion_hoy"] >= 0).all())
        only_uci = kpis.occupancy_by_service(FilterState(fs.date_from, fs.date_to, services=("UCI",)))
        self.assertEqual(list(only_uci["servicio"]), ["UCI"])

    def test_episode_table_has_no_pii(self):
        table = kpis.episodes_table(FilterState.default(get_data()), limit=50)
        for column in ("nombre_paciente", "tipo_documento", "fecha_nacimiento", "id_paciente"):
            self.assertNotIn(column, table.columns)
        self.assertTrue(table["Episodio"].str.startswith("EP-").all())


if __name__ == "__main__":
    unittest.main()
