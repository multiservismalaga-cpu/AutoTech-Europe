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
