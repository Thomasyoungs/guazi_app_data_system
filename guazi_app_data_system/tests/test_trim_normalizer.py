import json
import unittest
from pathlib import Path

from guazi_app_data_system.feishu_sync import validate_current_target_task
from guazi_app_data_system.trim_normalizer import (
    emission_normalization_used,
    exact_trim_match_with_emission_normalization,
    normalize_emission_standard,
    normalize_trim_for_match,
)


class TrimNormalizerTest(unittest.TestCase):
    def test_china_5_variants_normalize_to_guo_v(self):
        for value in ["国5", "国Ⅴ", "国V", "国五"]:
            self.assertEqual(normalize_emission_standard(f"330TSI 尊贵版 {value}"), "330TSI 尊贵版 国V")

    def test_china_6_variants_normalize_to_guo_vi(self):
        for value in ["国6", "国Ⅵ", "国VI", "国六"]:
            self.assertEqual(normalize_emission_standard(f"330TSI 尊贵版 {value}"), "330TSI 尊贵版 国VI")

    def test_emission_only_variants_match(self):
        self.assertTrue(exact_trim_match_with_emission_normalization("330TSI 尊贵版 国Ⅵ", "330TSI 尊贵版 国VI"))
        self.assertTrue(exact_trim_match_with_emission_normalization("330TSI 尊贵版 国6", "330TSI 尊贵版 国VI"))
        self.assertTrue(exact_trim_match_with_emission_normalization("330TSI 尊贵版 国六", "330TSI 尊贵版 国VI"))
        self.assertTrue(exact_trim_match_with_emission_normalization("330TSI 尊贵版 国5", "330TSI 尊贵版 国V"))
        self.assertTrue(exact_trim_match_with_emission_normalization("330TSI 尊贵版 国Ⅴ", "330TSI 尊贵版 国V"))
        self.assertTrue(exact_trim_match_with_emission_normalization("330TSI 尊贵版 国五", "330TSI 尊贵版 国V"))

    def test_non_emission_differences_do_not_match(self):
        self.assertFalse(exact_trim_match_with_emission_normalization("330TSI 豪华版 国VI", "330TSI 尊贵版 国VI"))
        self.assertFalse(exact_trim_match_with_emission_normalization("330TSI 尊荣版 国VI", "330TSI 尊贵版 国VI"))
        self.assertFalse(exact_trim_match_with_emission_normalization("330TSI DSG 尊贵版 国VI", "330TSI 尊贵版 国VI"))
        self.assertFalse(exact_trim_match_with_emission_normalization("2020款 330TSI 尊贵版 国VI", "330TSI 尊贵版 国VI"))

    def test_no_fuzzy_or_auto_alias_behavior(self):
        self.assertFalse(exact_trim_match_with_emission_normalization("330TSI 尊贵 国VI", "330TSI 尊贵版 国VI"))
        self.assertFalse(exact_trim_match_with_emission_normalization("330TSI 尊贵版", "330TSI 尊贵版 国VI"))
        self.assertEqual(normalize_trim_for_match("330TSI DSG 尊贵版 国6"), "330TSI DSG 尊贵版 国VI")

    def test_emission_normalization_used_flag(self):
        self.assertTrue(emission_normalization_used("330TSI 尊贵版 国6", "330TSI 尊贵版 国VI"))
        self.assertFalse(emission_normalization_used("330TSI 尊贵版 国VI", "330TSI 尊贵版 国VI"))
        self.assertFalse(emission_normalization_used("330TSI 豪华版 国6", "330TSI 尊贵版 国VI"))

    def test_current_target_task_trim_is_updated_and_valid(self):
        root = Path(__file__).resolve().parents[1]
        current = json.loads((root / "input" / "current_target_task.json").read_text(encoding="utf-8"))

        self.assertEqual(current["source"], "feishu_export")
        self.assertFalse(current["simulation_only"])
        self.assertEqual(current["task_id"], "FS-GZ-20260421-001")
        self.assertEqual(current["trim"], "330TSI 尊贵版 国VI")
        self.assertNotIn("reference_index", current)
        self.assertNotIn("尊荣版", json.dumps(current, ensure_ascii=False))
        self.assertNotIn("330TSI DSG 尊贵版", json.dumps(current, ensure_ascii=False))

        result = validate_current_target_task(root / "input" / "current_target_task.json")
        self.assertEqual(result["status"], "TASK_IMPORT_VERIFIED")
        self.assertTrue(result["allow_real_device_operation"])
        self.assertEqual(result["app_operation_params"]["trim"], "330TSI 尊贵版 国VI")


if __name__ == "__main__":
    unittest.main()
