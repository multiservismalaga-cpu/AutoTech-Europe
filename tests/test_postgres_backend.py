import os
import tempfile
import unittest

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    _TMP = tempfile.TemporaryDirectory()
    os.environ["AUTOTECH_DATA_DIR"] = _TMP.name
    import main


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL no configurada")
class PostgreSQLBackendSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()

    def test_schema_and_variant_persistence(self):
        main.ensure_seed_variants()
        variant = main.get_vehicle_variant("car/kia/niro-sg2-hev-2024")
        self.assertIsNotNone(variant)
        self.assertEqual(variant["variant_id"], "car/kia/niro-sg2-hev-2024")

    def test_source_document_round_trip(self):
        url = "https://example.invalid/postgres-smoke"
        document_id = main.ensure_source_document(
            "PostgreSQL persistence smoke test", url,
            "car/kia/niro-sg2-hev-2024", "TEST",
        )
        self.assertIsInstance(document_id, int)
        self.assertEqual(
            main.ensure_source_document(
                "PostgreSQL persistence smoke test", url,
                "car/kia/niro-sg2-hev-2024", "TEST",
            ),
            document_id,
        )

    def test_vin_round_trip_and_upsert(self):
        first = main.save_vin({"vin": "KMHHA8110SU155502", "vehicle_id": "car/hyundai/kona-sx2-hev-2025"})
        self.assertTrue(first["ok"])
        found = main.get_vin("KMHHA8110SU155502")
        self.assertEqual(found["vehicle_id"], "car/hyundai/kona-sx2-hev-2025")
        second = main.save_vin({"vin": "KMHHA8110SU155502", "vehicle_id": "car/kia/niro-sg2-hev-2024"})
        self.assertTrue(second["ok"])
        self.assertEqual(main.get_vin("KMHHA8110SU155502")["vehicle_id"], "car/kia/niro-sg2-hev-2024")

    def test_meta_upsert(self):
        main.meta_set("postgres_smoke", "one")
        self.assertEqual(main.meta_get("postgres_smoke"), "one")
        main.meta_set("postgres_smoke", "two")
        self.assertEqual(main.meta_get("postgres_smoke"), "two")

    def test_invalid_technical_year_is_rejected(self):
        with self.assertRaises(main.HTTPException) as ctx:
            main.technical("car/kia/niro-sg2-hev-2024", year=2101)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_technical_record_round_trip(self):
        c = main.db()
        c.execute(
            """INSERT INTO technical_records(
                 vehicle_id,variant_id,category,field,value,unit,source_title,source_url,
                 source_class,confidence,applicable_from,applicable_to,notes,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "car/kia/niro-sg2-hev-2024",
                "car/kia/niro-sg2-hev-2024",
                "postgres-smoke", "Persistencia", "OK", "text",
                "CI smoke", "https://example.invalid/postgres-smoke",
                "TEST", "CONTRASTADO", "2024", "2024",
                "PostgreSQL smoke test", "2026-10-01T00:00:00+00:00",
            ),
        )
        c.commit()
        c.close()
        rows = main.technical("car/kia/niro-sg2-hev-2024", "postgres-smoke", 2024)
        self.assertTrue(any(row["field"] == "Persistencia" for row in rows))


if __name__ == "__main__":
    unittest.main()
