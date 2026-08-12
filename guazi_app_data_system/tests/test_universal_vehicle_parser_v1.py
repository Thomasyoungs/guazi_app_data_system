import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from universal_vehicle_parser_v1 import parse_vehicle_model, parse_vehicle_model_text  # noqa: E402


class UniversalVehicleParserV1Test(unittest.TestCase):
    def assert_model(self, text, brand, series, year_model, config_model):
        result = parse_vehicle_model(text)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.brand, brand)
        self.assertEqual(result.series, series)
        self.assertEqual(result.year_model, year_model)
        self.assertEqual(result.config_model, config_model)

    def test_parse_ora_black_cat_range_trim(self):
        result = parse_vehicle_model("2019款 欧拉黑猫 351km 亲子版")
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.brand, "欧拉")
        self.assertEqual(result.series, "黑猫")
        self.assertEqual(result.year_model, "2019款")
        self.assertEqual(result.config_model, "351km 亲子版")
        self.assertEqual(result.canonical_brand, "欧拉")
        self.assertEqual(result.canonical_series, "黑猫")
        self.assertEqual(result.canonical_year_model, "2019款")
        self.assertEqual(result.canonical_config_model, "351km亲子版")
        self.assertEqual(result.config_semantic_key, "351KM亲子版")
        self.assertEqual(result.config_semantic_version, "FEISHU_CONFIG_SEMANTIC_NORMALIZATION_V1")
        self.assertEqual(result.vehicle_model_identity_key, "欧拉|黑猫|2019款|351km亲子版")
        self.assertEqual(result.vehicle_model_identity_key_v2, "欧拉|黑猫|2019款")
        self.assertEqual(result.vehicle_model_identity_key_v2_scope, "brand_series_year")
        self.assertEqual(result.decision_code, "VEHICLE_MODEL_PARSE_OK")

    def test_parse_byd_dolphin_range(self):
        self.assert_model("2021款 比亚迪海豚 405km", "比亚迪", "海豚", "2021款", "405km")

    def test_parse_wuling_bingo_range(self):
        self.assert_model("2020款 五菱缤果 333km", "五菱", "缤果", "2020款", "333km")

    def test_parse_leapmotor_t03_trim(self):
        self.assert_model("2022款 零跑T03 400轻享版", "零跑", "T03", "2022款", "400轻享版")

    def test_parse_keluze_without_year(self):
        self.assert_model(
            "科鲁泽 320 自动悦享天窗版",
            "雪佛兰",
            "科鲁泽",
            None,
            "320 自动悦享天窗版",
        )

    def test_unresolved_unknown_model(self):
        result = parse_vehicle_model("2022款 未知车 400km")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "MODEL_BRAND_SERIES_UNRESOLVED")
        self.assertEqual(result.decision_code, "MODEL_BRAND_SERIES_UNRESOLVED")

    def test_legacy_name_delegates_to_unified_entry(self):
        unified = parse_vehicle_model("2019款 欧拉黑猫 351km 亲子版")
        legacy = parse_vehicle_model_text("2019款 欧拉黑猫 351km 亲子版")
        self.assertEqual(legacy.as_dict(), unified.as_dict())

    def test_canonical_identity_is_stable_for_equivalent_ora_black_cat_inputs(self):
        first = parse_vehicle_model("2019款 欧拉黑猫 351km 亲子版")
        second = parse_vehicle_model("2019款欧拉 黑猫 351KM亲子版")
        third = parse_vehicle_model("2019款 黑猫 351 km 亲子版")

        self.assertEqual(first.vehicle_model_identity_key, second.vehicle_model_identity_key)
        self.assertEqual(first.vehicle_model_identity_key, third.vehicle_model_identity_key)
        self.assertEqual(first.canonical_config_model, second.canonical_config_model)
        self.assertEqual(first.canonical_config_model, third.canonical_config_model)

    def test_v2_identity_key_ignores_config_for_reference_stability(self):
        short_range = parse_vehicle_model("2019款 欧拉黑猫 351km 亲子版")
        long_range = parse_vehicle_model("2019款 欧拉黑猫 405km 长续航型")

        self.assertNotEqual(short_range.vehicle_model_identity_key, long_range.vehicle_model_identity_key)
        self.assertEqual(short_range.vehicle_model_identity_key_v2, long_range.vehicle_model_identity_key_v2)


if __name__ == "__main__":
    unittest.main()
