import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from config_semantic_normalization_v1 import (  # noqa: E402
    ENGINE_VERSION,
    match_config_semantics,
    normalize_config_semantics,
)


class ConfigSemanticNormalizationV1Test(unittest.TestCase):
    def test_model_context_facelift_and_grade_suffix_do_not_block_same_config(self):
        result = match_config_semantics(
            "大众 迈腾2018款 改款 330TSI DSG 豪华型",
            "2018款 330TSI DSG 豪华版",
        )

        self.assertTrue(result["semantic_match"], result)
        self.assertEqual(result["decision_code"], "CONFIG_SEMANTIC_MATCH")
        self.assertEqual(result["left"]["semantic_key"], "330TSIDSG豪华")
        self.assertEqual(result["right"]["semantic_key"], "330TSIDSG豪华")
        self.assertEqual(result["engine_version"], ENGINE_VERSION)

    def test_dsg_is_preserved_as_required_config_token(self):
        result = match_config_semantics("330TSI DSG 豪华型", "330TSI 豪华型")

        self.assertFalse(result["semantic_match"], result)
        self.assertEqual(result["decision_code"], "POWERTRAIN_TOKEN_MISMATCH")

    def test_emission_spelling_and_grade_suffix_are_normalized(self):
        result = match_config_semantics("330TSI 豪华型 国六", "330TSI 豪华版 国VI")

        self.assertTrue(result["semantic_match"], result)
        self.assertEqual(result["left"]["semantic_key"], "330TSI豪华国VI")

    def test_different_grade_is_not_tolerated(self):
        result = match_config_semantics("330TSI 领先型", "330TSI 豪华型")

        self.assertFalse(result["semantic_match"], result)
        self.assertEqual(result["decision_code"], "CONFIG_TIER_MISMATCH")

    def test_required_false_cases_for_tier_and_powertrain_guard(self):
        cases = [
            ("豪华", "尊贵", "CONFIG_TIER_MISMATCH"),
            ("豪华型", "尊贵型", "CONFIG_TIER_MISMATCH"),
            ("330TSI DSG 豪华", "330TSI DSG 尊贵", "CONFIG_TIER_MISMATCH"),
            ("330TSI DSG 豪华型", "330TSI DSG 尊贵型", "CONFIG_TIER_MISMATCH"),
            ("330TSI DSG 豪华", "380TSI DSG 豪华", "POWERTRAIN_TOKEN_MISMATCH"),
            ("1.5T 豪华", "2.0T 豪华", "POWERTRAIN_TOKEN_MISMATCH"),
            ("豪华", "旗舰", "CONFIG_TIER_MISMATCH"),
            ("尊贵", "尊享", "CONFIG_TIER_MISMATCH"),
            ("运动", "豪华", "CONFIG_TIER_MISMATCH"),
            ("舒适", "精英", "CONFIG_TIER_MISMATCH"),
        ]
        for left, right, decision_code in cases:
            with self.subTest(left=left, right=right):
                result = match_config_semantics(left, right)
                self.assertFalse(result["semantic_match"], result)
                self.assertEqual(result["decision_code"], decision_code)

    def test_required_true_cases_for_expression_variants(self):
        cases = [
            ("豪华型", "豪华版"),
            ("豪 华 型", "豪华"),
            ("330TSI DSG 豪华型", "330 TSI DSG 豪华"),
            ("DSG 330TSI 豪华", "330TSI DSG 豪华"),
            ("尊贵型", "尊贵版"),
            ("尊 贵 型", "尊贵"),
        ]
        for left, right in cases:
            with self.subTest(left=left, right=right):
                result = match_config_semantics(left, right)
                self.assertTrue(result["semantic_match"], result)
                self.assertEqual(result["decision_code"], "CONFIG_SEMANTIC_MATCH")

    def test_empty_side_is_unknown(self):
        result = match_config_semantics("", "330TSI 豪华型")

        self.assertFalse(result["semantic_match"])
        self.assertEqual(result["decision_code"], "CONFIG_SEMANTIC_UNKNOWN")

    def test_normalizer_keeps_observable_tokens(self):
        normalized = normalize_config_semantics("2018款 改款 330TSI DSG 豪华型")

        self.assertEqual(normalized.semantic_key, "330TSIDSG豪华")
        self.assertIn("DSG", normalized.tokens)
        self.assertEqual(normalized.tier_tokens, ("豪华",))
        self.assertEqual(normalized.powertrain_tokens, ("330TSI", "DSG"))


if __name__ == "__main__":
    unittest.main()
