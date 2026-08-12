import unittest

from guazi_app_data_system.config_loader import load_config
from guazi_app_data_system.models import TargetCar
from guazi_app_data_system.pricing import (
    score_target,
    standardize_target_condition_repairs,
    standardize_target_condition_repairs_with_evidence,
)


OULA_BLACKCAT_CONDITION = "右后叶板金，右后门板喷，左后叶剐蹭变形，右前门凹陷，右前门下坎凹陷，两大灯更换"


def _record_pairs(condition_text: str) -> list[tuple[str, str]]:
    records, _reasons = standardize_target_condition_repairs(condition_text)
    return [(record.part, record.damage_type) for record in records]


class TargetConditionDamageBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fields = load_config("fields.yaml")

    def test_oula_blackcat_condition_does_not_bind_headlight_replace_to_pending_panels(self):
        records, reasons, evidence = standardize_target_condition_repairs_with_evidence(OULA_BLACKCAT_CONDITION)
        pairs = [(record.part, record.damage_type) for record in records]

        self.assertIn(("右后翼子板", "钣金"), pairs)
        self.assertIn(("右后门", "喷漆"), pairs)
        self.assertIn(("左后翼子板", "剐蹭变形"), pairs)
        self.assertIn(("右前门", "凹陷"), pairs)
        self.assertIn(("右前门下坎", "凹陷"), pairs)
        self.assertIn(("左大灯", "更换"), pairs)
        self.assertIn(("右大灯", "更换"), pairs)
        self.assertNotIn(("右后翼子板", "更换"), pairs)
        self.assertNotIn(("右后门", "更换"), pairs)
        self.assertNotIn(("左后翼子板", "更换"), pairs)
        self.assertNotIn(("右前门", "更换"), pairs)
        self.assertIn("TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW", reasons)
        self.assertTrue(all(item["from_pending_binding"] is False for item in evidence))

        target = TargetCar(
            task_id="target-condition-regression",
            brand="欧拉",
            series="黑猫",
            model_year="2019款",
            trim="351km 亲子版",
            color="白",
            registration_date="2020.08",
            mileage_10k_km=4.5,
            transfer_count=2,
            condition_text=OULA_BLACKCAT_CONDITION,
        )

        score = score_target(target, self.fields, current_year=2026)

        self.assertEqual(score.components["body_score"], 68.0)
        self.assertEqual(score.score, 92.0)
        self.assertFalse(score.hard_reject)

    def test_two_headlights_replace_is_independent_from_previous_clause(self):
        pairs = _record_pairs("右前门凹陷，两大灯更换")

        self.assertIn(("右前门", "凹陷"), pairs)
        self.assertIn(("左大灯", "更换"), pairs)
        self.assertIn(("右大灯", "更换"), pairs)
        self.assertNotIn(("右前门", "更换"), pairs)

    def test_banjin_and_banpen_normalize_to_scoring_damage_types(self):
        pairs = _record_pairs("右后叶板金，右后门板喷")

        self.assertIn(("右后翼子板", "钣金"), pairs)
        self.assertIn(("右后门", "喷漆"), pairs)

    def test_leaf_aliases_normalize_to_rear_fenders(self):
        pairs = _record_pairs("右后叶板金，左后叶板金")

        self.assertIn(("右后翼子板", "钣金"), pairs)
        self.assertIn(("左后翼子板", "钣金"), pairs)

    def test_sill_depression_does_not_become_structure_or_hard_reject(self):
        records, reasons, evidence = standardize_target_condition_repairs_with_evidence("右前门下坎凹陷")
        pairs = [(record.part, record.damage_type) for record in records]

        self.assertEqual(pairs, [("右前门下坎", "凹陷")])
        self.assertIn("TARGET_CONDITION_SILL_SCORING_REVIEW", reasons)
        self.assertEqual(evidence[0]["parse_warning"], "TARGET_CONDITION_SILL_SCORING_REVIEW")

        target = TargetCar(
            task_id="sill-regression",
            brand="欧拉",
            series="黑猫",
            model_year="2019款",
            trim="351km 亲子版",
            color="白",
            registration_date="2020.08",
            mileage_10k_km=4.5,
            transfer_count=2,
            condition_text="右前门下坎凹陷",
        )

        score = score_target(target, self.fields, current_year=2026)

        self.assertEqual(score.components["body_score"], 70.0)
        self.assertFalse(score.hard_reject)

    def test_replace_does_not_cross_clause_to_cosmetic_damage_parts(self):
        pairs = _record_pairs("左后叶剐蹭变形，右前门凹陷，两大灯更换")

        self.assertIn(("左后翼子板", "剐蹭变形"), pairs)
        self.assertIn(("右前门", "凹陷"), pairs)
        self.assertIn(("左大灯", "更换"), pairs)
        self.assertIn(("右大灯", "更换"), pairs)
        self.assertNotIn(("左后翼子板", "更换"), pairs)
        self.assertNotIn(("右前门", "更换"), pairs)

    def test_parse_evidence_contains_required_fields(self):
        _records, _reasons, evidence = standardize_target_condition_repairs_with_evidence("右前门凹陷，两大灯更换")
        required = {
            "source_span",
            "clause_text",
            "matched_part",
            "matched_damage",
            "normalized_part",
            "normalized_damage",
            "binding_reason",
            "binding_scope",
            "confidence",
            "from_pending_binding",
            "parse_warning",
        }

        self.assertTrue(evidence)
        for item in evidence:
            self.assertTrue(required.issubset(item))


if __name__ == "__main__":
    unittest.main()
