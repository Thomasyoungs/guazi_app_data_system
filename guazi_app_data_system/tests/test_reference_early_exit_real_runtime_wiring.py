import inspect
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SRC_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_pricing_dispatcher import FeishuPricingDispatcher  # noqa: E402
from guazi_app_data_system.models import ReferenceCar  # noqa: E402
from guazi_app_data_system.pricing import ScoreResult  # noqa: E402
from guazi_app_data_system.reference_early_exit import EARLY_EXIT_RULE_ID  # noqa: E402


ACTIVE_FIELDS = {
    "rule_source_guard": {
        "active_page_contract_version": "V1.50",
        "active_scoring_rule_version": "V1.11",
        "active_reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "active_pricing_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "active_competition_coefficient_version": "V1.2.6",
    },
    "scoring": {"scoring_rule_version": "V1.11"},
    "reference_selection": {
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"
    },
    "pricing": {"pricing_rule_version": "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT"},
    "competition_coefficient": {"competition_coefficient_version": "V1.2.6"},
}


def _reference(index: int, score: float, price: float = 8.0) -> tuple[dict, ReferenceCar, ScoreResult]:
    car = ReferenceCar(
        reference_index=index,
        list_price_10k=price,
        list_year=2021,
        list_mileage_10k_km=4.2,
        transfer_count=0,
        accident_count=0,
        max_accident_amount=0,
        repair_counts={},
    )
    return (
        {
            "reference_index": index,
            "reference_score": score,
            "price_yuan": round(price * 10000),
            "list_price_10k": price,
        },
        car,
        ScoreResult(score=score, components={"body_score": score}, review_reasons=[]),
    )


def _runtime_context() -> dict:
    return {
        "configs": {"fields": ACTIVE_FIELDS},
        "current_reference_index": 4,
        "returned_list_source_verified": True,
        "first_stage_evidence": {
            "trisame_cards_count": 6,
            "same_source_cards": [
                {"title": "ref 1", "list_price_10k": 7.1},
                {"title": "ref 2", "list_price_10k": 7.4},
                {"title": "ref 3", "list_price_10k": 7.6},
                {"title": "ref 4", "list_price_10k": 7.8},
                {"title": "ref 5", "list_price_10k": 8.0},
                {"title": "ref 6", "list_price_10k": 8.2},
            ],
        },
        "startup_s10_reliable_evidence": {
            "reliable": True,
            "source_reliable": True,
            "selected_reference_card_gate_passed": True,
            "target_card_matches_expected": True,
        },
        "current_reference": {
            "reference_index": 4,
            "selected_card_title": "ref 4",
            "list_title": "ref 4",
            "list_price_10k": 7.8,
            "list_year": 2021,
            "list_mileage_10k_km": 4.2,
            "transfer_count": 0,
            "accident_count": 0,
            "max_accident_amount": 0,
        },
    }


class ReferenceEarlyExitRealRuntimeWiringTests(unittest.TestCase):
    def test_runtime_calls_early_exit_helper_in_s15_path(self):
        source = inspect.getsource(runtime.handle_s15)
        self.assertIn("_evaluate_reference_early_exit_for_runtime", source)
        wrapper = inspect.getsource(runtime._evaluate_reference_early_exit_for_runtime)
        self.assertIn("evaluate_reference_early_exit_max_possible_score", wrapper)
        self.assertIn("calculate_reference_score_upper_bound_for_early_exit", wrapper)

    def test_early_exit_allowed_marks_current_reference_for_continue_next_reference(self):
        context = _runtime_context()
        decision = runtime._evaluate_reference_early_exit_for_runtime(
            context,
            target_score=ScoreResult(score=92.0, components={}, review_reasons=[]),
            reference_score=ScoreResult(score=80.0, components={"body_score": 60.0}, review_reasons=[]),
            previous_valid_lows=[_reference(2, 88.0)],
            repair_completion={
                "missing_repair_count": 2,
                "collected_repair_items": ["left front fender paint"],
                "uncollected_condition_tabs": ["right rear door paint"],
            },
            rule_guard={"rule_source_guard_passed": True},
        )

        self.assertTrue(decision["early_exit_allowed"])
        self.assertEqual(EARLY_EXIT_RULE_ID, decision["early_exit_rule_id"])
        self.assertEqual(80.0, decision["max_possible_reference_score"])
        runtime._apply_reference_early_exit_decision_to_runtime(context, decision)

        current = context["current_reference"]
        self.assertTrue(current["reference_early_exit"])
        self.assertEqual("LOW_SCORE_SKIPPED_INCOMPLETE", current["reference_status"])
        self.assertFalse(current["usable_for_boundary"])
        self.assertFalse(current["usable_for_pre_boundary"])
        self.assertFalse(current["reference_score_trustworthy"])
        self.assertEqual(1, len(context["early_rejected_reference_history"]))
        self.assertEqual(1, len(context["excluded_reference_history"]))

    def test_early_exit_without_pre_boundary_evidence(self):
        context = _runtime_context()
        decision = runtime._evaluate_reference_early_exit_for_runtime(
            context,
            target_score=ScoreResult(score=92.0, components={}, review_reasons=[]),
            reference_score=ScoreResult(score=80.0, components={}, review_reasons=[]),
            previous_valid_lows=[],
            repair_completion={"missing_repair_count": 0},
            rule_guard={"rule_source_guard_passed": True},
        )

        self.assertTrue(decision["early_exit_allowed"])
        self.assertNotIn("PRE_BOUNDARY_EVIDENCE_NOT_REQUIRED", decision["early_exit_blockers"])

    def test_s14_in_flight_early_exit_writes_trace_before_s15(self):
        context = _runtime_context()
        context["configs"]["fields"] = json.loads((ROOT / "config" / "fields.yaml").read_text(encoding="utf-8-sig"))
        context["target_car"] = runtime.TargetCar(
            task_id="FS_TEST",
            brand="test",
            series="series",
            model_year="2021款",
            trim="trim",
            color="black",
            registration_date="2021.01",
            mileage_10k_km=5.0,
            transfer_count=0,
            condition_text="",
        )
        context["reference_history"] = [_reference(2, 88.0)[0] | {
            "reference_score_trustworthy": True,
            "reference_score_usable_for_boundary": True,
            "usable_for_boundary": True,
            "usable_for_pre_boundary": True,
            "list_year": 2021,
            "list_mileage_10k_km": 4.2,
            "transfer_count": 0,
            "accident_count": 0,
            "max_accident_amount": 0,
            "repair_counts": {},
        }]
        context["damage_by_part"] = {}
        context["s14_image_records"] = [{"normalized_part": "左前翼子板"}]
        context["current_reference"].update(
            {
                "repair_counts": {"驾驶侧": 3},
                "repair_items": [{"part": "左前翼子板", "normalized_damage": "喷漆"}],
                "s13_enter_s14_required": True,
                "current_s14_item_done": True,
            }
        )

        decision = runtime._evaluate_s14_in_flight_early_exit_for_runtime(
            context,
            selected_tab={"label": "左前翼子板"},
            semantic_state={"s14_key": "左前翼子板--喷漆"},
            snapshot={"screenshot_path": "s14.png", "xml_path": "s14.xml"},
        )

        self.assertIn("s14_in_flight_early_exit_trace", context)
        self.assertTrue(context["current_reference"]["s14_in_flight_early_exit_checked"])
        self.assertEqual(1, context["current_reference"]["s14_in_flight_early_exit_check_count"])
        self.assertEqual(EARLY_EXIT_RULE_ID, decision["early_exit_rule_id"])
        self.assertIn("partial_confirmed_score", decision)
        self.assertIn("remaining_max_possible_score", decision)
        self.assertIn("s14_items_skipped_due_to_early_exit", decision)

    def test_s14_in_flight_early_exit_still_enabled(self):
        source = inspect.getsource(runtime.handle_s14)

        self.assertIn("_evaluate_s14_in_flight_early_exit_for_runtime", source)
        self.assertIn("s14_in_flight_early_exit_pending_return", source)
        self.assertIn("_return_from_s14_to_s10_then_s15", source)

    def test_early_exit_reference_not_used_for_no_boundary_closest_low(self):
        history = [
            {
                "reference_index": 1,
                "reference_score": 88.0,
                "reference_score_trustworthy": True,
                "reference_score_usable_for_boundary": True,
                "usable_for_boundary": True,
                "usable_for_pre_boundary": True,
                "list_price_10k": 7.2,
                "list_year": 2021,
                "list_mileage_10k_km": 4.2,
                "transfer_count": 0,
                "accident_count": 0,
                "max_accident_amount": 0,
                "repair_counts": {},
            },
            {
                "reference_index": 2,
                "reference_score": 91.0,
                "reference_early_exit": True,
                "excluded_from_final_reference_selection": True,
                "reference_score_trustworthy": False,
                "reference_score_usable_for_boundary": False,
                "usable_for_boundary": False,
                "usable_for_pre_boundary": False,
                "list_price_10k": 7.4,
                "list_year": 2021,
                "list_mileage_10k_km": 4.1,
                "transfer_count": 0,
                "accident_count": 0,
                "max_accident_amount": 0,
                "repair_counts": {},
            },
        ]

        selection = runtime._select_v3_reference_from_history(history, {"score": 92.0})

        self.assertIsNone(selection["final_reference_index"])
        self.assertTrue(selection["manual_review_required"])
        self.assertFalse(selection["auto_pricing_allowed"])
        self.assertEqual(selection["manual_review_reason"], "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING")
        self.assertEqual([1], [item["reference_index"] for item in selection["candidate_reference_pool"]])

    def test_dispatcher_uses_early_exit_continue_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_root = root / "feishu_tasks"
            data_dir = root / "data"
            task_id = "FS_TEST_EARLY_EXIT"
            task_dir = task_root / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "first_stage_result.json").write_text(
                '{"trisame_cards_count": 6, "same_source_cards": [{}, {}, {}, {}, {}, {}]}\n',
                encoding="utf-8",
            )
            dispatcher = FeishuPricingDispatcher(
                task_root=task_root,
                data_dir=data_dir,
                runtime_lock_path=root / "pricing.lock",
                clock=lambda: datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            )
            result = {
                "ok": True,
                "status": "CONTINUE_NEXT_REFERENCE",
                "current_reference_index": 4,
                "next_reference_index": 5,
                "remaining_reference_count": 2,
                "should_continue_reference_collection": True,
                "continue_reason": "EARLY_EXIT_CONTINUE_NEXT_REFERENCE",
                "reference_early_exit": True,
                "early_exit_allowed": True,
                "early_exit_rule_id": EARLY_EXIT_RULE_ID,
            }

            state = dispatcher._resolve_second_stage_reference_loop_state(
                task_id,
                {"status": "S10_READY"},
                last_second_stage_result=result,
                second_stage_results=[result],
            )

        self.assertTrue(state["dispatcher_continue_allowed"])
        self.assertEqual("EARLY_EXIT_CONTINUE_NEXT_REFERENCE", state["dispatcher_continue_reason"])
        self.assertTrue(state["reference_early_exit"])
        self.assertEqual(EARLY_EXIT_RULE_ID, state["early_exit_rule_id"])


if __name__ == "__main__":
    unittest.main()
