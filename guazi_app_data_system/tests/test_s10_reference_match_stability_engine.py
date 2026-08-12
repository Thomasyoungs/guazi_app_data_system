import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from s10_reference_match_stability_engine_v1 import (  # noqa: E402
    build_stable_vehicle_identity,
    match_s10_reference_identity,
)


class S10ReferenceMatchStabilityEngineTest(unittest.TestCase):
    def test_target_identity_uses_v2_simplified_key_when_available(self):
        identity = build_stable_vehicle_identity(
            {
                "brand": "欧拉",
                "series": "黑猫",
                "year_model": "2019款",
                "config_model": "351km 亲子版",
                "vehicle_model_identity_key_v2": "欧拉|黑猫|2019款",
            },
            source="target",
        )

        self.assertTrue(identity.ok)
        self.assertEqual(identity.identity_key_v2, "欧拉|黑猫|2019款")
        self.assertEqual(identity.canonical_config_model, "351km亲子版")
        self.assertEqual(identity.config_semantic_key, "351KM亲子版")

    def test_reference_title_parses_to_same_v2_identity_with_different_config(self):
        result = match_s10_reference_identity(
            {
                "brand": "欧拉",
                "series": "黑猫",
                "year_model": "2019款",
                "config_model": "351km 亲子版",
                "vehicle_model_identity_key_v2": "欧拉|黑猫|2019款",
            },
            {"vehicle_title": "2019款 欧拉黑猫 405km 长续航型"},
        )

        self.assertEqual(result["decision_code"], "S10_REFERENCE_CONFIG_SEMANTIC_MISMATCH")
        self.assertFalse(result["identity_match"])
        self.assertTrue(result["identity_key_v2_match"])
        self.assertEqual(result["config_semantic_decision_code"], "CONFIG_SEMANTIC_MISMATCH")
        self.assertFalse(result["config_semantic_match"])
        self.assertEqual(result["identity_key_v2_scope"], "brand_series_year")
        self.assertNotEqual(
            result["target_identity"]["strict_identity_key_v1"],
            result["reference_identity"]["strict_identity_key_v1"],
        )

    def test_config_semantic_match_tolerates_context_and_grade_suffix(self):
        result = match_s10_reference_identity(
            {
                "brand": "大众",
                "series": "迈腾",
                "year_model": "2018款",
                "config_model": "大众 迈腾2018款 改款 330TSI DSG 豪华型",
                "vehicle_model_identity_key_v2": "大众|迈腾|2018款",
            },
            {
                "brand": "大众",
                "series": "迈腾",
                "year_model": "2018款",
                "config_model": "2018款 330TSI DSG 豪华版",
                "vehicle_model_identity_key_v2": "大众|迈腾|2018款",
            },
        )

        self.assertEqual(result["decision_code"], "S10_REFERENCE_IDENTITY_MATCH")
        self.assertTrue(result["identity_match"])
        self.assertEqual(result["config_semantic_decision_code"], "CONFIG_SEMANTIC_MATCH")
        self.assertTrue(result["config_semantic_match"])

    def test_config_tier_mismatch_blocks_final_identity_match(self):
        result = match_s10_reference_identity(
            {
                "brand": "大众",
                "series": "迈腾",
                "year_model": "2018款",
                "config_model": "330TSI DSG 豪华",
                "vehicle_model_identity_key_v2": "大众|迈腾|2018款",
            },
            {
                "brand": "大众",
                "series": "迈腾",
                "year_model": "2018款",
                "config_model": "330TSI DSG 尊贵",
                "vehicle_model_identity_key_v2": "大众|迈腾|2018款",
            },
        )

        self.assertEqual(result["decision_code"], "S10_REFERENCE_CONFIG_SEMANTIC_MISMATCH")
        self.assertTrue(result["identity_key_v2_match"])
        self.assertFalse(result["identity_match"])
        self.assertEqual(result["config_semantic_decision_code"], "CONFIG_TIER_MISMATCH")
        self.assertFalse(result["config_semantic_match"])

    def test_powertrain_mismatch_blocks_final_identity_match(self):
        result = match_s10_reference_identity(
            {
                "brand": "大众",
                "series": "迈腾",
                "year_model": "2018款",
                "config_model": "330TSI DSG 豪华",
                "vehicle_model_identity_key_v2": "大众|迈腾|2018款",
            },
            {
                "brand": "大众",
                "series": "迈腾",
                "year_model": "2018款",
                "config_model": "380TSI DSG 豪华",
                "vehicle_model_identity_key_v2": "大众|迈腾|2018款",
            },
        )

        self.assertEqual(result["decision_code"], "S10_REFERENCE_CONFIG_SEMANTIC_MISMATCH")
        self.assertTrue(result["identity_key_v2_match"])
        self.assertFalse(result["identity_match"])
        self.assertEqual(result["config_semantic_decision_code"], "POWERTRAIN_TOKEN_MISMATCH")
        self.assertFalse(result["config_semantic_match"])

    def test_reference_title_mismatch_is_stable(self):
        result = match_s10_reference_identity(
            {"vehicle_model_identity_key_v2": "欧拉|黑猫|2019款"},
            {"vehicle_title": "2021款 比亚迪海豚 405km"},
        )

        self.assertEqual(result["decision_code"], "S10_REFERENCE_IDENTITY_MISMATCH")
        self.assertFalse(result["identity_match"])

    def test_missing_reference_identity_is_unknown_not_match(self):
        result = match_s10_reference_identity(
            {"vehicle_model_identity_key_v2": "欧拉|黑猫|2019款"},
            {"vehicle_title": "2021款 未知车型 400km"},
        )

        self.assertEqual(result["decision_code"], "S10_REFERENCE_IDENTITY_UNKNOWN")
        self.assertFalse(result["identity_match"])


if __name__ == "__main__":
    unittest.main()
