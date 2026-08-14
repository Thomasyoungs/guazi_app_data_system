import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from current_target_task_builder import build_current_target_task  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc)


def valid_draft():
    return {
        "task_id": "FS20260609_0001",
        "source": "feishu",
        "status": "CONFIRMED",
        "brand": "本田",
        "series": "雅阁",
        "model_config": "2021款 260TURBO 豪华版",
        "license_date": "2021-06",
        "mileage_text": "5.8万公里",
        "color": "白色",
        "transfer_count_text": "1",
        "condition_text": "右前门喷漆，前杠喷漆",
        "accident_count_text": "1",
        "max_claim_amount_text": "3200",
        "city": "唐山",
        "remark": "客户着急卖",
        "raw_message_id": "om_xxx",
        "raw_sender_id": "ou_xxx",
        "raw_chat_id": "oc_xxx",
        "vehicle_model_identity_key": "本田|雅阁|2021款|260TURBO豪华版",
        "vehicle_model_identity_key_v2": "本田|雅阁|2021款",
        "vehicle_model_identity_key_v2_scope": "brand_series_year",
        "canonical_brand": "本田",
        "canonical_series": "雅阁",
        "canonical_year_model": "2021款",
        "canonical_config_model": "260TURBO豪华版",
        "created_at": "2026-06-09T08:30:00+00:00",
    }


class CurrentTargetTaskBuilderTest(unittest.TestCase):
    def test_builder_maps_all_supported_fields(self):
        result = build_current_target_task(valid_draft(), clock=fixed_clock)

        self.assertTrue(result.valid)
        task = result.current_target_task
        self.assertEqual(task["brand"], "本田")
        self.assertEqual(task["series"], "雅阁")
        self.assertEqual(task["model_config"], "2021款 260TURBO 豪华版")
        self.assertEqual(task["accident_count_text"], "1")
        self.assertEqual(task["max_claim_amount_text"], "3200")
        self.assertEqual(task["city"], "唐山")
        self.assertEqual(task["remark"], "客户着急卖")
        self.assertEqual(task["raw_message_id"], "om_xxx")
        self.assertEqual(task["raw_sender_id"], "ou_xxx")
        self.assertEqual(task["raw_chat_id"], "oc_xxx")
        self.assertEqual(task["vehicle_model_identity_key_v2"], "本田|雅阁|2021款")
        self.assertEqual(task["vehicle_model_identity_key_v2_scope"], "brand_series_year")

    def test_builder_outputs_mainline_compatible_fields(self):
        result = build_current_target_task(valid_draft(), clock=fixed_clock)
        task = result.current_target_task

        self.assertEqual(task["year_model"], "2021款")
        self.assertEqual(task["model_year"], "2021款")
        self.assertEqual(task["config_model"], "260TURBO 豪华版")
        self.assertEqual(task["trim"], "260TURBO 豪华版")
        self.assertEqual(task["license_date"], "2021.06")
        self.assertEqual(task["register_date"], "2021.06")
        self.assertEqual(task["registration_date"], "2021.06")
        self.assertEqual(task["register_year"], 2021)
        self.assertEqual(task["registration_date_year"], 2021)
        self.assertEqual(task["mileage_10k_km"], 5.8)
        self.assertEqual(task["display_mileage_wan_km"], 5.8)
        self.assertEqual(task["transfer_count"], 1)
        self.assertEqual(task["accident_count"], 1)
        self.assertEqual(task["max_accident_amount"], 3200)

    def test_missing_accident_count_does_not_default_to_zero(self):
        draft = valid_draft()
        draft.pop("accident_count_text")

        result = build_current_target_task(draft, clock=fixed_clock)

        self.assertTrue(result.valid)
        self.assertNotIn("accident_count_text", result.current_target_task)

    def test_missing_max_claim_amount_does_not_default_to_zero(self):
        draft = valid_draft()
        draft.pop("max_claim_amount_text")

        result = build_current_target_task(draft, clock=fixed_clock)

        self.assertTrue(result.valid)
        self.assertNotIn("max_claim_amount_text", result.current_target_task)

    def test_forbidden_reference_and_pricing_fields_are_ignored(self):
        draft = valid_draft()
        draft["final_reference_index"] = 2
        draft["boundary_reference_index"] = 3
        draft["competition_coefficient"] = 0.97
        draft["suggested_purchase_price"] = 120000

        result = build_current_target_task(draft, clock=fixed_clock)

        task = result.current_target_task
        self.assertNotIn("final_reference_index", task)
        self.assertNotIn("boundary_reference_index", task)
        self.assertNotIn("competition_coefficient", task)
        self.assertNotIn("suggested_purchase_price", task)
        self.assertIn("IGNORED_FORBIDDEN_FIELD:final_reference_index", result.warnings)

    def test_builder_does_not_calculate_price_or_scores(self):
        result = build_current_target_task(valid_draft(), clock=fixed_clock)

        for field in [
            "target_score",
            "reference_score",
            "competition_coefficient",
            "suggested_purchase_price",
        ]:
            self.assertNotIn(field, result.current_target_task)

    def test_short_registration_date_generates_mainline_year_fields(self):
        draft = valid_draft()
        draft["license_date"] = "22.8"

        result = build_current_target_task(draft, clock=fixed_clock)

        self.assertTrue(result.valid)
        task = result.current_target_task
        self.assertEqual(task["license_date"], "2022.08")
        self.assertEqual(task["register_date"], "2022.08")
        self.assertEqual(task["registration_date"], "2022.08")
        self.assertEqual(task["register_year"], 2022)
        self.assertEqual(task["registration_date_year"], 2022)

    def test_unrecognized_registration_date_blocks_app_payload(self):
        draft = valid_draft()
        draft["license_date"] = "not-a-date"

        result = build_current_target_task(draft, clock=fixed_clock)

        self.assertFalse(result.valid)
        self.assertIn("registration_date_year", result.missing_fields)


if __name__ == "__main__":
    unittest.main()
