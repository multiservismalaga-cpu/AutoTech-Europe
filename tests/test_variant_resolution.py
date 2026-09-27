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

    def test_engine_code_alone_does_not_select_between_models(self):
        r = main.resolve_variant_signals({"engine_code": "G4LL"})
        self.assertIsNone(r)

    def test_conflicting_make_and_model_does_not_cross_match(self):
        r = main.resolve_variant_signals({
            "make": "Kia",
            "model": "KONA SX2 HEV",
            "engine_code": "G4LL",
        })
        self.assertIsNone(r)

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
