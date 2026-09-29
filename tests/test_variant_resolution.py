import os
import tempfile
import unittest

# Isolate SQLite before importing the application module.
_TMP = tempfile.TemporaryDirectory()
os.environ["AUTOTECH_DATA_DIR"] = _TMP.name

import main


class VariantResolutionRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        c = main.db()
        c.execute(
            "INSERT OR REPLACE INTO vehicles "
            "(id,make,model,kind,body_types,years,availability,popularity,sources,raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "test/hyundai-kona-sx2-hev",
                "Hyundai",
                "KONA SX2 HEV",
                "car",
                "",
                "2025",
                '["ES","EU"]',
                "test",
                "test",
                "{}",
            ),
        )
        c.execute(
            "INSERT OR REPLACE INTO vehicles "
            "(id,make,model,kind,body_types,years,availability,popularity,sources,raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "test/kia-niro-sg2-hev",
                "Kia",
                "Niro SG2 HEV",
                "car",
                "",
                "2024-2026",
                '["ES","EU"]',
                "test",
                "test",
                "{}",
            ),
        )
        c.commit()
        c.close()
        main.ensure_seed_variants()

    def test_engine_search_shared_code_returns_both_variants(self):
        rows = main.engine_search("G4LL", 30)
        ids = {row["id"] for row in rows if row.get("is_variant")}
        self.assertIn("car/hyundai/kona-sx2-hev-2025", ids)
        self.assertIn("car/kia/niro-sg2-hev-2024", ids)
        self.assertEqual(len(ids), 2)

    def test_engine_search_b47d20o1_returns_bmw_variant(self):
        rows = main.engine_search("B47D20O1", 30)
        self.assertTrue(any(
            row.get("id") == "car/bmw/3-series-320d"
            and row.get("engine_code") == "B47D20O1"
            and row.get("generation") == "G20"
            for row in rows
        ))

    def test_canonical_variant_id_returns_technical_records(self):
        rows = main.technical("car/kia/niro-sg2-hev-2024", "maintenance")
        self.assertTrue(rows)
        self.assertTrue(all(row["variant_id"] == "car/kia/niro-sg2-hev-2024" for row in rows))

    def test_variant_dashboard_reports_module_state(self):
        dashboard = main.variant_dashboard("car/bmw/3-series-320d")
        self.assertTrue(dashboard["ok"])
        self.assertEqual(dashboard["variant"]["variant_id"], "car/bmw/3-series-320d")
        categories = {row["category"]: row for row in dashboard["modules"]}
        self.assertEqual(categories["technical specifications"]["status"], "CONTRASTADO")
        self.assertIn(categories["timing"]["status"], {"SIN DATOS LOCALES", "EVIDENCIA WEB"})
    def test_hyundai_kona_2025_vin_crosscheck_resolves_expected_variant(self):
        result = main.decode_hyundai_vin_crosscheck("KMHHA8110SU155502")
        self.assertIsNotNone(result)
        self.assertEqual(result["manufacturer"], "Hyundai Motor Company")
        self.assertEqual(result["model"], "Kona SX2")
        self.assertEqual(result["variant"], "HEV")
        self.assertEqual(result["model_year"], 2025)
        self.assertEqual(result["engine_code"], "G4LL")
        self.assertEqual(result["transmission"], "Automática DCT de 6 velocidades")
        self.assertEqual(result["drive"], "Tracción delantera")
        self.assertEqual(result["plant_code"], "U")
        self.assertEqual(result["vds"], "HHA811")

    def test_kona_requires_compatible_signals(self):
        r = main.resolve_variant_signals({
            "make": "Hyundai",
            "model": "KONA SX2 HEV",
            "year": "2025",
            "engine_code": "G4LL",
            "transmission": "DCT 6",
            "drive": "FWD",
            "fuel": "Gasolina híbrido",
            "market": "ES/EU",
        })
        self.assertIsNotNone(r)
        self.assertEqual(r["variant_id"], "car/hyundai/kona-sx2-hev-2025")

    def test_niro_resolves_to_niro_not_kona(self):
        r = main.resolve_variant_signals({
            "make": "Kia",
            "model": "Niro SG2 HEV",
            "year": "2024",
            "engine_code": "G4LL",
            "transmission": "DCT 6",
            "drive": "FWD",
            "fuel": "Gasolina híbrido",
            "market": "ES/EU",
        })
        self.assertIsNotNone(r)
        self.assertEqual(r["variant_id"], "car/kia/niro-sg2-hev-2024")

    def test_transmission_token_order_does_not_break_resolution(self):
        r = main.resolve_variant_signals({
            "make": "Kia",
            "model": "Niro SG2 HEV",
            "year": "2024",
            "engine_code": "G4LL",
            "transmission": "6-speed DCT",
            "drive": "FWD",
            "fuel": "Gasolina híbrido",
            "market": "ES/EU",
        })
        self.assertIsNotNone(r)
        self.assertEqual(r["variant_id"], "car/kia/niro-sg2-hev-2024")

    def test_engine_code_alone_does_not_select_between_models(self):
        r = main.resolve_variant_signals({"engine_code": "G4LL"})
        self.assertIsNone(r)

    def test_shared_engine_code_with_make_but_no_model_stays_ambiguous(self):
        r = main.resolve_variant_signals({
            "make": "Hyundai",
            "engine_code": "G4LL",
        })
        self.assertIsNone(r)

    def test_conflicting_make_and_model_does_not_cross_match(self):
        r = main.resolve_variant_signals({
            "make": "Kia",
            "model": "KONA SX2 HEV",
            "engine_code": "G4LL",
        })
        self.assertIsNone(r)


    def test_evidence_is_linked_to_variant(self):
        c = main.db()
        c.execute(
            "INSERT INTO evidence(vehicle_id,query,category,title,url,domain,source_class,confidence,snippet,fetched_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "test/kia-niro-sg2-hev",
                "Niro SG2 HEV torque",
                "torque",
                "Kia source",
                "https://example.invalid/kia-niro",
                "example.invalid",
                "TEST",
                "PENDIENTE DE CONTRASTE",
                "test",
                "2026-09-27T00:00:00+00:00",
            ),
        )
        c.commit()
        c.close()
        main.ensure_evidence_variant_links()
        c = main.db()
        row = c.execute(
            "SELECT variant_id FROM evidence WHERE vehicle_id=?",
            ("test/kia-niro-sg2-hev",),
        ).fetchone()
        c.close()
        self.assertEqual(row["variant_id"], "car/kia/niro-sg2-hev-2024")

    def test_evidence_exposes_source_document_metadata(self):
        document_id = main.ensure_source_document(
            "Kia Niro SG2 technical manual",
            "https://example.invalid/kia-niro-manual",
            "car/kia/niro-sg2-hev-2024",
            "TEST",
        )
        c = main.db()
        c.execute(
            """UPDATE source_documents
               SET publisher=?, document_type=?, language=?, revision=?
               WHERE document_id=?""",
            ("Kia", "manual", "es-ES", "2024-01", document_id),
        )
        c.execute(
            """INSERT INTO evidence(
                 vehicle_id,variant_id,query,category,title,url,domain,source_class,
                 confidence,snippet,fetched_at,document_id,document_section,applicability
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "test/kia-niro-sg2-hev",
                "car/kia/niro-sg2-hev-2024",
                "Niro SG2 HEV torque",
                "torque",
                "Kia source",
                "https://example.invalid/kia-niro",
                "example.invalid",
                "TEST",
                "PENDIENTE DE CONTRASTE",
                "test",
                "2026-09-28T00:00:00+00:00",
                document_id,
                "Wheels",
                "SG2 HEV 2024 ES/EU",
            ),
        )
        c.commit()
        c.close()
        rows = main.evidence(limit=1)
        self.assertEqual(rows[0]["document_id"], document_id)
        self.assertEqual(rows[0]["document_title"], "Kia Niro SG2 technical manual")
        self.assertEqual(rows[0]["document_publisher"], "Kia")
        self.assertEqual(rows[0]["document_type"], "manual")
        self.assertEqual(rows[0]["document_language"], "es-ES")
        self.assertEqual(rows[0]["document_revision"], "2024-01")
        self.assertEqual(rows[0]["document_section"], "Wheels")
        self.assertEqual(rows[0]["applicability"], "SG2 HEV 2024 ES/EU")

    def test_technical_exposes_source_document_metadata(self):
        c = main.db()
        c.execute(
            """INSERT INTO technical_records(
                 vehicle_id,variant_id,category,field,value,unit,source_title,source_url,
                 source_class,confidence,applicable_from,applicable_to,notes,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "test/kia-niro-sg2-hev",
                "car/kia/niro-sg2-hev-2024",
                "torque",
                "Tuercas de rueda",
                "107–127",
                "Nm",
                "Kia Niro wheels",
                "https://example.invalid/kia-niro-wheels",
                "TEST",
                "CONTRASTADO",
                "2024",
                "2024",
                "test",
                "2026-09-28T00:00:00+00:00",
            ),
        )
        c.commit()
        c.close()
        main.ensure_technical_source_documents()
        rows = main.technical("test/kia-niro-sg2-hev", "torque")
        row = next(x for x in rows if x["field"] == "Tuercas de rueda")
        self.assertEqual(row["document_title"], "Kia Niro wheels")
        self.assertEqual(row["document_url"], "https://example.invalid/kia-niro-wheels")
        self.assertEqual(row["document_source_class"], "TEST")
        self.assertIsNotNone(row["source_document_id"])

    def test_source_document_schema_is_present(self):
        c = main.db()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(source_documents)").fetchall()}
        evidence_cols = {r["name"] for r in c.execute("PRAGMA table_info(evidence)").fetchall()}
        c.close()
        self.assertIn("document_id", evidence_cols)
        self.assertIn("document_section", evidence_cols)
        self.assertIn("applicability", evidence_cols)
        self.assertIn("variant_id", evidence_cols)
        self.assertIn("document_id", cols)
        self.assertIn("variant_id", cols)
        self.assertIn("url", cols)

    def test_catalog_mapping_uses_explicit_model_signals(self):
        self.assertEqual(
            main.get_vehicle_variant("test/hyundai-kona-sx2-hev")["variant_id"],
            "car/hyundai/kona-sx2-hev-2025",
        )
        self.assertEqual(
            main.get_vehicle_variant("test/kia-niro-sg2-hev")["variant_id"],
            "car/kia/niro-sg2-hev-2024",
        )


if __name__ == "__main__":
    unittest.main()
