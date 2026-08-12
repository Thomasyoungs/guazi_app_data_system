import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from pricing_result_collector import (  # noqa: E402
    CONFIG_MISMATCH_HARD_STOP,
    CONTINUE_NEXT_REFERENCE,
    DEFAULT_RESULT_CANDIDATES,
    RESULT_MISSING_REQUIRED_PRICING_FIELDS,
    PricingResultCollector,
    _has_raw_terminal_success_signal,
    is_automatic_pricing_terminal_success,
    is_pricing_result_manual_review,
    normalize_pricing_result_fields,
    normalize_v33_low_score_continuation_fields,
    pricing_result_config_mismatch_reason,
    pricing_result_business_status,
    pricing_success_missing_required_fields,
    resolve_pricing_result_field,
    validate_pricing_result_payload,
)


class PricingResultCollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task_dir = self.root / "data" / "feishu_tasks" / "FS20260609_0001"
        self.task_dir.mkdir(parents=True)
        self.collector = PricingResultCollector(project_root=self.root, task_dir=self.task_dir)

    def tearDown(self):
        self.temp.cleanup()

    def test_collects_default_data_pricing_result(self):
        source = self.root / "data" / "pricing_result.json"
        self.write_json(source, full_success_payload())

        result = self.collector.collect()

        self.assertTrue(result.ok)
        self.assertTrue((self.task_dir / "pricing_result.json").exists())
        self.assertEqual(result.result["suggested_purchase_price_yuan"], 86308)
        self.assertTrue(result.pricing_success_ok)

    def test_default_candidates_include_confirmed_mainline_result_paths(self):
        self.assertIn(("output", "result_s10_to_s16.json"), DEFAULT_RESULT_CANDIDATES)
        self.assertIn(("output", "result.json"), DEFAULT_RESULT_CANDIDATES)

    def test_collects_actual_mainline_result_s10_to_s16_path(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        payload = full_success_payload()
        payload["source"] = "mainline"
        self.write_json(source, payload)

        result = self.collector.collect()

        self.assertTrue(result.ok)
        self.assertEqual(result.source_path, source)
        self.assertTrue(result.pricing_success_ok)

    def test_collects_explicit_result_path(self):
        source = self.root / "custom_result.json"
        self.write_json(source, {"manual_review_required": True})
        self.write_json(self.root / "output" / "result_s10_to_s16.json", {"manual_review_required": False})

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok)
        self.assertEqual(result.source_path, source)

    def test_missing_result_returns_error(self):
        result = self.collector.collect()

        self.assertFalse(result.ok)
        self.assertIn("RESULT_FILE_NOT_FOUND", result.errors)

    def test_invalid_json_returns_error(self):
        source = self.root / "data" / "pricing_result.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("{bad", encoding="utf-8")

        result = self.collector.collect()

        self.assertFalse(result.ok)
        self.assertIn("RESULT_JSON_INVALID", result.errors)

    def test_stale_result_file_returns_error(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(source, {"manual_review_required": False})
        old_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        source.touch()
        import os

        os.utime(source, (old_timestamp, old_timestamp))

        result = self.collector.collect(
            result_path=source,
            run_started_at=datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc),
        )

        self.assertFalse(result.ok)
        self.assertIn("STALE_RESULT_FILE", result.errors)
        self.assertFalse((self.task_dir / "pricing_result.json").exists())

    def test_contract_only_result_is_invalid_for_pricing(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(source, {"status": "CONTRACT_ONLY", "issue_code": "SOME_CONTRACT_STATUS"})

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result.errors)

    def test_blocked_not_at_s10_ready_result_is_failed(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(source, {"status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY"})

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result.errors)

    def test_second_stage_fast_handoff_failure_keeps_specific_error(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(
            source,
            {
                "status": "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
                "issue_code": "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
                "s10_fast_handoff_gate": {
                    "second_stage_fast_handoff_strong_error_signals": [
                        "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                    ],
                },
            },
        )

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn("SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED", result.errors)
        self.assertNotIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result.errors)

    def test_recovered_reference_card_binding_attempt_does_not_fail_manual_review_result(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        payload = fs20260624_0001_recovered_binding_payload()
        self.write_json(source, payload)

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.errors, [])
        self.assertTrue(is_pricing_result_manual_review(result.result))
        self.assertEqual(pricing_result_business_status(result.result), "NEEDS_REVIEW")
        self.assertTrue(result.result["binding_attempt_error_recovered"])
        self.assertIn("REFERENCE_CARD_BINDING_NOT_UNIQUE", result.result["ignored_stale_error_codes"])
        self.assertEqual(result.result["stale_reference_binding_failed_attempt_index"], 1)
        self.assertEqual(result.result["stale_reference_binding_recovered_by_attempt_index"], 2)
        copied = json.loads((self.task_dir / "pricing_result.json").read_text(encoding="utf-8"))
        self.assertTrue(copied["binding_attempt_error_recovered"])

    def test_latest_reference_card_binding_failure_is_still_reported(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(
            source,
            {
                "status": "S14_FULL_IMAGE_SEQUENCE_COLLECTED",
                "current_reference_index": 1,
                "s14_return_attempts": [
                    {
                        "attempt_index": 1,
                        "recognized_page": "S10",
                        "s10_reliable_list_evidence": {
                            "reliable": False,
                            "target_reference_index": 2,
                            "selected_reference_card_gate_passed": False,
                            "selected_reference_card_stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                        },
                    }
                ],
                "current_reference": {"s14_collect_done": True},
            },
        )

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn("REFERENCE_CARD_BINDING_NOT_UNIQUE", result.errors)

    def test_historical_diagnostics_reference_binding_code_is_not_promoted(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        payload = full_chain_manual_review_payload()
        payload["diagnostics"] = {
            "previous_attempt": {
                "stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                "selected_reference_card_gate_passed": False,
            }
        }
        self.write_json(source, payload)

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.errors, [])
        self.assertTrue(is_pricing_result_manual_review(result.result))

    def test_full_chain_manual_review_done_is_not_overridden_by_recovered_binding_error(self):
        payload = fs20260624_0001_recovered_binding_payload()

        self.assertEqual(pricing_result_business_status(payload), "NEEDS_REVIEW")
        self.assertNotIn(
            "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            PricingResultCollector(project_root=self.root, task_dir=self.task_dir)
            .collect(result_path=self._write_temp_result(payload))
            .errors,
        )

    def test_s13_repair_item_click_failure_keeps_specific_error(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(
            source,
            {
                "status": "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
                "final_status": "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
                "issue_code": "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
                "current_reference_index": 1,
            },
        )

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn("S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED", result.errors)
        self.assertNotIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result.errors)

    def test_collects_nested_full_chain_manual_review_result(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(source, full_chain_manual_review_payload())

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok, result.errors)
        self.assertTrue(is_pricing_result_manual_review(result.result))
        self.assertEqual(pricing_result_business_status(result.result), "NEEDS_REVIEW")
        self.assertEqual(resolve_pricing_result_field(result.result, "target_score"), 94.5)
        self.assertEqual(resolve_pricing_result_field(result.result, "final_reference_index"), 1)
        self.assertEqual(resolve_pricing_result_field(result.result, "final_reference_score"), 94.0)
        self.assertEqual(resolve_pricing_result_field(result.result, "final_reference_price_yuan"), 98400)
        self.assertEqual(resolve_pricing_result_field(result.result, "target_guazi_listing_price_yuan"), 96400)
        self.assertEqual(resolve_pricing_result_field(result.result, "suggested_purchase_price_yuan"), 86308)

    def test_manual_review_confirmed_result_maps_business_status(self):
        payload = full_chain_manual_review_payload()
        payload.update(
            {
                "status": "MANUAL_REVIEW_CONFIRMED",
                "manual_review_confirmed": True,
                "system_suggested_purchase_price_yuan": 86308,
                "manual_confirmed_purchase_price_yuan": 86000,
                "manual_adjustment_yuan": -308,
                "final_purchase_price_yuan": 86000,
            }
        )

        self.assertEqual(pricing_result_business_status(payload), "MANUAL_REVIEW_CONFIRMED")
        self.assertEqual(resolve_pricing_result_field(payload, "final_purchase_price_yuan"), 86000)

    def test_config_tier_mismatch_hard_stops_even_when_price_fields_exist(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(
            source,
            {
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
                "config_semantic_decision_code": "CONFIG_TIER_MISMATCH",
            },
        )

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn(CONFIG_MISMATCH_HARD_STOP, result.errors)
        self.assertEqual(pricing_result_config_mismatch_reason(result.result), "CONFIG_TIER_MISMATCH")
        self.assertEqual(pricing_result_business_status(result.result), "TARGET_INFO_NEEDS_CORRECTION")

    def test_powertrain_mismatch_hard_stops_even_when_price_fields_exist(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(
            source,
            {
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
                "config_semantic_result": {"decision_code": "POWERTRAIN_TOKEN_MISMATCH"},
            },
        )

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertIn(CONFIG_MISMATCH_HARD_STOP, result.errors)
        self.assertEqual(pricing_result_config_mismatch_reason(result.result), "POWERTRAIN_TOKEN_MISMATCH")

    def test_continue_next_reference_is_parseable_but_not_success(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(
            source,
            {
                "status": CONTINUE_NEXT_REFERENCE,
                "final_status": CONTINUE_NEXT_REFERENCE,
                "current_state": CONTINUE_NEXT_REFERENCE,
                "current_reference_index": 1,
                "next_reference_index": 2,
                "s14_collect_done": True,
                "target_score": 82,
                "reference_score": 68,
                "manual_review_required": False,
            },
        )

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok, result.errors)
        self.assertFalse(result.pricing_success_ok)
        self.assertEqual(result.non_terminal_status, CONTINUE_NEXT_REFERENCE)
        self.assertEqual(pricing_result_business_status(result.result), CONTINUE_NEXT_REFERENCE)

    def test_missing_price_chain_is_not_success(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(source, {"manual_review_required": False, "suggested_purchase_price_yuan": 100000})

        result = self.collector.collect(result_path=source)

        self.assertFalse(result.ok)
        self.assertFalse(result.pricing_success_ok)
        self.assertIn(RESULT_MISSING_REQUIRED_PRICING_FIELDS, result.errors)
        self.assertIn("final_reference_index", pricing_success_missing_required_fields(result.result))
        self.assertEqual(pricing_result_business_status(result.result), RESULT_MISSING_REQUIRED_PRICING_FIELDS)

    def test_fs0008_equivalent_priced_result_normalizes_final_price_and_profit_rate(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(self.root / "config" / "fields.yaml", {"pricing": {"profit_rate": 0.08}})
        self.write_json(source, fs20260625_0008_equivalent_payload())

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.pricing_success_ok, result.missing_required_fields)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.missing_required_fields, [])
        self.assertEqual(result.result["suggested_purchase_price_yuan"], 26200)
        self.assertEqual(result.result["system_suggested_price_yuan"], 26200)
        self.assertEqual(result.result["final_purchase_price_yuan"], 26200)
        self.assertEqual(result.result["final_price_source"], "SYSTEM_AUTOMATIC_PRICING")
        self.assertEqual(result.result["pricing_decision_source"], "AUTOMATIC_PRICING")
        self.assertEqual(result.result["profit_rate"], 0.08)
        self.assertNotIn(RESULT_MISSING_REQUIRED_PRICING_FIELDS, validate_pricing_result_payload(result.result))
        self.assertNotIn("REFERENCE_CARD_BINDING_NOT_UNIQUE", result.errors)
        copied = self.read_json(self.task_dir / "pricing_result.json")
        self.assertEqual(copied["final_purchase_price_yuan"], 26200)
        self.assertEqual(copied["profit_rate"], 0.08)

    def test_waiting_manual_price_is_not_normalized_to_automatic_success(self):
        source = self.root / "output" / "result_s10_to_s16.json"
        payload = fs20260625_0008_equivalent_payload()
        payload["status"] = "FULL_CHAIN_MANUAL_REVIEW_DONE"
        payload["final_status"] = "FULL_CHAIN_MANUAL_REVIEW_DONE"
        payload["current_state"] = "FULL_CHAIN_MANUAL_REVIEW_DONE"
        payload["manual_review_required"] = True
        payload["pricing"]["manual_review_required"] = True
        payload["s17_payload"]["manual_review_required"] = True
        self.write_json(source, payload)

        result = self.collector.collect(result_path=source)

        self.assertTrue(result.ok, result.errors)
        self.assertTrue(is_pricing_result_manual_review(result.result))
        self.assertEqual(pricing_result_business_status(result.result), "NEEDS_REVIEW")
        self.assertNotIn("final_purchase_price_yuan", result.result)
        self.assertNotEqual(result.result.get("pricing_decision_source"), "AUTOMATIC_PRICING")

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_temp_result(self, payload):
        source = self.root / "output" / "result_s10_to_s16.json"
        self.write_json(source, payload)
        return source


def full_chain_manual_review_payload():
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "s17_payload": {
            "final_reference_index": 1,
            "reference_price_10k": 9.84,
            "reference_score": 94.0,
            "target_score": 94.5,
            "manual_review_required": True,
            "manual_review_reasons": [
                "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
                "SAMPLE_SHORTAGE_MANUAL_REVIEW",
            ],
        },
        "pricing": {
            "base_reference_price_yuan": 98400,
            "target_guazi_listing_price_yuan": 96400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 94900,
            "guazi_return_price_yuan": 94900,
            "cost_yuan": 1000,
            "profit_yuan": 7592,
            "suggested_purchase_price_yuan": 86308,
            "manual_review_required": True,
        },
    }


class PricingResultCollectorTerminalSuccessTest(unittest.TestCase):
    def test_full_chain_priced_done_is_valid_terminal_success_even_with_historical_continue_trace(self):
        payload = fs20260625_0008_equivalent_payload()
        payload.setdefault("trace", []).append({"status": CONTINUE_NEXT_REFERENCE})

        self.assertTrue(is_automatic_pricing_terminal_success(payload))
        self.assertEqual(pricing_result_business_status(payload), "SUCCEEDED")
        self.assertEqual(validate_pricing_result_payload(payload), [])
        self.assertEqual(pricing_success_missing_required_fields(payload), [])
        self.assertEqual(resolve_pricing_result_field(payload, "final_reference_index"), 1)

    def test_terminal_success_with_stale_nested_low_score_continue_is_protected(self):
        payload = fs20260702_0001_terminal_with_stale_low_score_payload()

        self.assertTrue(_has_raw_terminal_success_signal(payload))
        self.assertFalse(normalize_v33_low_score_continuation_fields(payload))
        self.assertNotEqual(payload.get("status"), CONTINUE_NEXT_REFERENCE)

        self.assertTrue(normalize_pricing_result_fields(payload))

        self.assertTrue(is_automatic_pricing_terminal_success(payload))
        self.assertEqual(pricing_result_business_status(payload), "SUCCEEDED")
        self.assertEqual(payload["status"], "FULL_CHAIN_PRICED_DONE")
        self.assertEqual(payload["final_status"], "FULL_CHAIN_PRICED_DONE")
        self.assertEqual(payload["current_state"], "FULL_CHAIN_PRICED_DONE")
        self.assertEqual(payload["business_status"], "SUCCEEDED")
        self.assertEqual(payload["technical_status"], "SUCCEEDED")
        self.assertFalse(payload["dispatcher_should_continue"])
        self.assertFalse(payload["should_continue_reference_collection"])
        self.assertFalse(payload["continue_next_reference"])
        self.assertIsNone(payload["next_reference_index"])
        self.assertIsNone(payload["issue_code"])
        self.assertIsNone(payload["stop_code"])
        self.assertTrue(payload["terminal_success_result_exists"])
        self.assertTrue(payload["terminal_success_result_protected"])
        self.assertTrue(payload["terminal_success_normalization_precedence_applied"])
        self.assertTrue(payload["stale_low_score_continuation_ignored"])
        self.assertTrue(payload["stale_nested_low_score_continuation_present"])
        self.assertTrue(payload["stale_nested_low_score_continuation_ignored_due_to_terminal_success"])
        self.assertEqual(payload["final_reference_index"], 3)
        self.assertEqual(payload["final_purchase_price_yuan"], 140156)

    def test_non_terminal_low_score_continue_still_normalizes_to_continue(self):
        payload = v33_low_score_continue_payload()

        self.assertFalse(_has_raw_terminal_success_signal(payload))
        self.assertTrue(normalize_v33_low_score_continuation_fields(payload))

        self.assertEqual(payload["status"], CONTINUE_NEXT_REFERENCE)
        self.assertEqual(payload["issue_code"], "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE")
        self.assertTrue(payload["dispatcher_should_continue"])
        self.assertEqual(payload["next_reference_index"], 4)


def fs20260624_0001_recovered_binding_payload():
    payload = full_chain_manual_review_payload()
    payload.update(
        {
            "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "current_reference_index": 1,
            "returned_s10_snapshot_attempt_index": 2,
            "returned_s10_reliable_evidence": {
                "reliable": True,
                "source_reliable": True,
                "target_reference_index": 2,
                "target_card_visible": True,
                "target_card_matches_expected": True,
                "selected_reference_card_gate_passed": True,
                "selected_reference_card_gate_reason": "selected_reference_card_complete_safe_clickable",
                "vehicle_card_count": 4,
                "visible_cards": [
                    {
                        "reference_index": 1,
                        "list_title": "欧拉黑猫 2019款 351km 亲子版",
                        "list_price_text": "2.63万",
                        "raw_metadata": "2020年 | 12.39万公里 | 唐山",
                    },
                    {
                        "reference_index": 2,
                        "list_title": "欧拉黑猫 2019款 351km 亲子版",
                        "list_price_text": "3.14万",
                        "raw_metadata": "2020年 | 7.27万公里 | 长沙",
                    },
                ],
            },
            "s14_return_attempts": [
                {
                    "attempt_index": 1,
                    "recognized_page": "S10",
                    "s10_reliable_list_evidence": {
                        "reliable": False,
                        "source_reliable_reason": "missing_vehicle_cards;detail_report_page_signals_present",
                        "target_reference_index": 2,
                        "vehicle_card_count": 0,
                        "selected_reference_card_gate_passed": False,
                        "selected_reference_card_stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                        "selected_reference_card_gate_reason": "selected_reference_card_not_uniquely_bound",
                    },
                },
                {
                    "attempt_index": 2,
                    "recognized_page": "S10",
                    "s10_reliable_list_evidence": {
                        "reliable": True,
                        "source_reliable": True,
                        "target_reference_index": 2,
                        "target_card_visible": True,
                        "target_card_matches_expected": True,
                        "selected_reference_card_gate_passed": True,
                        "selected_reference_card_gate_reason": "selected_reference_card_complete_safe_clickable",
                        "vehicle_card_count": 4,
                    },
                },
            ],
            "current_reference": {
                "reference_index": 1,
                "s14_collect_done": True,
                "s15_entry_allowed": True,
                "s15_entry_reason": "S14_FULL_IMAGE_SEQUENCE_COLLECTED",
                "target_score_source": "score_target_runtime_s15",
            },
        }
    )
    return payload


def full_success_payload():
    return {
        "status": "SUCCEEDED",
        "final_status": "SUCCEEDED",
        "current_state": "SUCCEEDED",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "target_score": 94.5,
        "boundary_confirmed": True,
        "boundary_reference_index": 2,
        "boundary_reference_score": 95.0,
        "final_reference_index": 1,
        "final_reference_score": 94.0,
        "final_reference_price_yuan": 98400,
        "target_guazi_listing_price_yuan": 96400,
        "guazi_service_fee_yuan": 1500,
        "guazi_net_payout_yuan": 94900,
        "guazi_return_price_yuan": 94900,
        "cost_yuan": 1000,
        "profit_rate": 0.08,
        "profit_yuan": 7592,
        "suggested_purchase_price_yuan": 86308,
        "final_purchase_price_yuan": 86308,
        "manual_review_required": False,
    }


def fs20260625_0008_equivalent_payload():
    return {
        "status": "FULL_CHAIN_PRICED_DONE",
        "final_status": "FULL_CHAIN_PRICED_DONE",
        "current_state": "FULL_CHAIN_PRICED_DONE",
        "target_score": {"score": 92.0},
        "selected_reference": {
            "reference_index": 1,
            "list_price_10k": 3.14,
            "score": 88.5,
        },
        "selected_reference_score": {"score": 88.5},
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": True,
        "boundary_reference_index": 3,
        "boundary_reference_score": 95.5,
        "pre_boundary_reference_index": 1,
        "candidate_reference_pool": [
            {"reference_index": 1, "reference_score": 88.5, "price_yuan": 31400},
            {"reference_index": 3, "reference_score": 95.5, "price_yuan": 34200},
        ],
        "s17_payload": {
            "task_status": "priced",
            "suggested_acquisition_price_yuan": 26200,
            "final_reference_index": 1,
            "reference_score": 88.5,
            "target_score": 92.0,
            "manual_review_required": False,
            "boundary_confirmed": True,
            "boundary_reference_index": 3,
            "boundary_reference_score": 95.5,
            "pre_boundary_reference_index": 1,
            "target_guazi_listing_price_yuan": 30300,
            "guazi_service_fee_yuan": 1000,
            "guazi_net_payout_yuan": 29300,
            "guazi_return_price_yuan": 29300,
            "base_reference_price_yuan": 31400,
        },
        "pricing": {
            "status": "priced",
            "base_reference_price_yuan": 31400,
            "target_guazi_listing_price_yuan": 30300,
            "guazi_service_fee_yuan": 1000,
            "guazi_net_payout_yuan": 29300,
            "guazi_return_price_yuan": 29300,
            "cost_yuan": 600,
            "profit_yuan": 2500,
            "suggested_purchase_price_yuan": 26200,
            "manual_review_required": False,
        },
        "reference_history": [
            {"reference_index": 1, "s14_whole_vehicle_collection_complete": True},
            {
                "reference_index": 2,
                "s14_collect_done": False,
                "s14_has_uncollected_next_condition_signal": True,
                "s14_uncollected_items_count": 3,
                "reference_score_trustworthy": False,
                "reference_score_usable_for_boundary": False,
                "excluded_from_boundary": True,
                "excluded_from_boundary_reason": "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE",
            },
            {
                "reference_index": 3,
                "s14_whole_vehicle_collection_complete": True,
                "reference_score_trustworthy": True,
                "reference_score_usable_for_boundary": True,
                "returned_s10_reliable_evidence": {
                    "selected_reference_card_stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                    "selected_reference_card_gate_passed": False,
                },
            },
        ],
        "ignored_stale_error_codes": ["REFERENCE_CARD_BINDING_NOT_UNIQUE"],
        "binding_attempt_error_recovered": True,
        "recovered_attempt_error_codes": ["REFERENCE_CARD_BINDING_NOT_UNIQUE"],
    }


def fs20260702_0001_terminal_with_stale_low_score_payload():
    payload = fs20260625_0008_equivalent_payload()
    payload.update(
        {
            "selected_reference": {
                "reference_index": 3,
                "list_price_10k": 16.8,
                "score": 91.0,
            },
            "selected_reference_score": {"score": 91.0},
            "final_reference_index": 3,
            "final_purchase_price_yuan": 140156,
            "suggested_purchase_price_yuan": 140156,
            "system_suggested_price_yuan": 140156,
            "final_price_source": "SYSTEM_AUTOMATIC_PRICING",
            "pricing_decision_source": "AUTOMATIC_PRICING",
            "target_score": {"score": 92.0},
            "current_reference_index": 3,
            "current_reference": {
                "reference_index": 3,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "target_score": 92.0,
                "reference_score_upper_bound": 91.5,
                "s14_low_score_skip_triggered": True,
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "remaining_reference_count": 2,
                "early_exit_decision": {
                    "current_reference_index": 3,
                    "next_reference_index": 4,
                    "target_score": 92.0,
                    "reference_score_upper_bound": 91.5,
                    "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                    "s14_low_score_skip_triggered": True,
                    "early_exit_decision": "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE",
                    "return_to_s10_after_low_score_skip": True,
                    "returned_list_source_verified": True,
                    "remaining_reference_count": 2,
                },
            },
            "s17_payload": {
                **payload["s17_payload"],
                "task_status": "priced",
                "final_reference_index": 3,
                "suggested_acquisition_price_yuan": 140156,
            },
            "pricing": {
                **payload["pricing"],
                "status": "priced",
                "final_reference_index": 3,
                "suggested_purchase_price_yuan": 140156,
                "final_purchase_price_yuan": 140156,
                "pricing_decision_source": "AUTOMATIC_PRICING",
                "final_price_source": "SYSTEM_AUTOMATIC_PRICING",
            },
        }
    )
    return payload


def v33_low_score_continue_payload():
    return {
        "target_score": {"score": 92.0},
        "current_reference_index": 3,
        "current_reference": {
            "reference_index": 3,
            "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
            "target_score": 92.0,
            "reference_score_upper_bound": 91.5,
            "s14_low_score_skip_triggered": True,
            "return_to_s10_after_low_score_skip": True,
            "returned_list_source_verified": True,
            "remaining_reference_count": 2,
            "early_exit_decision": {
                "current_reference_index": 3,
                "next_reference_index": 4,
                "target_score": 92.0,
                "reference_score_upper_bound": 91.5,
                "reference_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
                "s14_low_score_skip_triggered": True,
                "early_exit_decision": "LOW_SCORE_SKIP_AND_CONTINUE_NEXT_REFERENCE",
                "return_to_s10_after_low_score_skip": True,
                "returned_list_source_verified": True,
                "remaining_reference_count": 2,
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
