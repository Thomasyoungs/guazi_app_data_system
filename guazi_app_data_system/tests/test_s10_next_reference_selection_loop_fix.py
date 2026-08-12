import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s10_to_s16_mainline.py"
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s10_to_s16_mainline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def card(index: int, *, title: str = "NIO ES6 2024 75kWh", price: float = 15.79, mileage: float = 2.1) -> dict:
    left = 40
    top = 320 + (index - 1) * 220
    return {
        "reference_index": index,
        "canonical_reference_index": index,
        "live_display_order": index,
        "list_title": title,
        "list_price_text": f"{price:.2f}万",
        "list_price_10k": price,
        "list_year": 2024,
        "list_mileage_10k_km": mileage,
        "city": "上海",
        "raw_metadata": f"2024年 | {mileage:.1f}万公里 | 上海",
        "card_complete": True,
        "card_fully_visible": True,
        "has_price": True,
        "has_metadata": True,
        "clicked_card_bounds": (left, top, left + 600, top + 120),
        "card_bounds": (left, top - 40, left + 640, top + 160),
    }


class S10NextReferenceSelectionLoopFixTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_runtime_module()
        self.snapshot = {"target_car": {}, "nodes": [], "visible_blob": "price asc list"}

    def test_invalid_partial_reference_1_recovers_next_reference_2(self):
        result = {
            "status": "CONTINUE_NEXT_REFERENCE",
            "task_id": "FS20260630_0001",
            "current_reference": {
                "reference_index": 1,
                "list_title": "NIO ES6 2024 75kWh",
                "list_price_text": "15.79万",
                "raw_metadata": "2024年 | 2.1万公里 | 上海",
                "reference_score": None,
            },
            "next_reference_index": 1,
            "reference_history": [],
        }

        with (
            mock.patch.object(
                self.runtime,
                "_safe_read_json",
                side_effect=lambda path: result if "result_s10_to_s16" in str(path) else {},
            ),
            mock.patch.object(
                self.runtime,
                "validate_second_stage_continuation_state_for_current_task",
                return_value={"continue_allowed": True},
            ),
            mock.patch.object(self.runtime, "_load_first_stage_s10_ready_evidence", return_value={}),
        ):
            plan = self.runtime._load_reference_continuation_plan({"task_id": "FS20260630_0001"})

        self.assertTrue(plan["continuation_mode"])
        self.assertTrue(plan["invalid_partial_reference_detected"])
        self.assertEqual(plan["invalid_partial_reference_index"], 1)
        self.assertEqual(plan["continuation_recovered_next_reference_index"], 2)
        self.assertEqual(plan["next_reference_index"], 2)
        self.assertIn(1, plan["processed_reference_indices"])

    def test_selects_reference_2_after_reference_1_processed(self):
        cards = [card(1), card(2, price=16.2, mileage=1.8)]
        reference_history = [dict(cards[0], reference_score=88, reference_status="LOW_SCORE_SKIPPED_INCOMPLETE")]

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                2,
                reference_history=reference_history,
            )

        self.assertEqual(selected["selected_reference_index"], 2)
        self.assertEqual(selected["selected_card_price"], "16.20万")
        self.assertEqual(selected["selected_reference_identity"]["reference_index"], 2)
        self.assertFalse(selected["duplicate_reference_detected"])
        self.assertGreater(point[0], 0)

    def test_duplicate_reference_1_click_is_blocked_after_processed(self):
        cards = [card(1), card(2, price=16.2, mileage=1.8)]
        reference_history = [dict(cards[0], reference_score=88, reference_status="LOW_SCORE_SKIPPED_INCOMPLETE")]

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                1,
                reference_history=reference_history,
            )

        self.assertFalse(selected["ok"])
        self.assertEqual(selected["stop_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertTrue(selected["duplicate_reference_detected"])
        self.assertIn(1, selected["processed_reference_indices"])
        self.assertEqual(point, (0, 0))

    def test_duplicate_reference_allowed_only_for_boundary_previous_recollect(self):
        cards = [card(1), card(2, price=16.2, mileage=1.8)]
        reference_history = [dict(cards[0], reference_score=88, reference_status="BOUNDARY_PREVIOUS_INCOMPLETE")]
        expected_card = {
            "boundary_previous_recollect_required": True,
            "recollect_reference_index": 1,
            "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE",
        }

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                1,
                expected_card=expected_card,
                reference_history=reference_history,
            )

        self.assertEqual(selected["selected_reference_index"], 1)
        self.assertTrue(selected["duplicate_reference_detected"])
        self.assertEqual(
            selected["duplicate_reference_allowed_reason"],
            "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_RECOLLECT",
        )
        self.assertGreater(point[0], 0)

    def test_v33_boundary_previous_low_score_skipped_recollect_is_allowed(self):
        cards = [
            card(1, price=15.1, mileage=2.4),
            card(2, price=15.5, mileage=2.2),
            card(3, price=15.9, mileage=2.0),
            card(4, price=16.2, mileage=1.8),
        ]
        reference_history = [
            dict(cards[0], reference_score=86, reference_status="LOW_SCORE_SKIPPED_INCOMPLETE"),
            dict(cards[1], reference_score=87, reference_status="LOW_SCORE_SKIPPED_INCOMPLETE"),
            dict(
                cards[2],
                reference_score=89,
                reference_status="LOW_SCORE_SKIPPED_INCOMPLETE",
                low_score_skipped_incomplete=True,
                reference_score_trustworthy=False,
                reference_score_usable_for_boundary=False,
            ),
            dict(cards[3], reference_score=93, reference_status="BOUNDARY_REFERENCE"),
        ]
        expected_card = {
            "final_reference_recollect_required": True,
            "boundary_previous_recollect_required": True,
            "recollect_mode": True,
            "recollect_reference_index": 3,
            "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
            "boundary_reference_index": 4,
            "boundary_reference_score": 93,
            "target_score": 92,
            "final_reference_candidate_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
        }

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                3,
                expected_card=expected_card,
                reference_history=reference_history,
            )

        self.assertEqual(selected["selected_reference_index"], 3)
        self.assertTrue(selected["duplicate_reference_detected"])
        self.assertTrue(selected["duplicate_reference_allowed_for_recollect"])
        self.assertEqual(selected["duplicate_reference_boundary_reference_index"], 4)
        self.assertEqual(selected["duplicate_reference_candidate_previous_status"], "LOW_SCORE_SKIPPED_INCOMPLETE")
        self.assertEqual(
            selected["duplicate_reference_allowed_reason"],
            "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_RECOLLECT",
        )
        self.assertGreater(point[0], 0)

    def test_v33_recollect_index_must_be_previous_of_boundary(self):
        cards = [card(1), card(2, price=16.2, mileage=1.8), card(3, price=16.8, mileage=1.6)]
        reference_history = [dict(cards[1], reference_score=89, reference_status="LOW_SCORE_SKIPPED_INCOMPLETE")]
        expected_card = {
            "final_reference_recollect_required": True,
            "boundary_previous_recollect_required": True,
            "recollect_reference_index": 2,
            "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
            "boundary_reference_index": 4,
            "final_reference_candidate_status": "LOW_SCORE_SKIPPED_INCOMPLETE",
        }

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                2,
                expected_card=expected_card,
                reference_history=reference_history,
            )

        self.assertFalse(selected["ok"])
        self.assertEqual(selected["stop_code"], "RECOLLECT_REFERENCE_INDEX_NOT_PREVIOUS_OF_BOUNDARY")
        self.assertFalse(selected["duplicate_reference_allowed_for_recollect"])
        self.assertEqual(point, (0, 0))

    def test_v33_recollect_does_not_allow_already_trusted_duplicate(self):
        cards = [card(1), card(2, price=16.2, mileage=1.8), card(3, price=16.8, mileage=1.6)]
        reference_history = [
            dict(
                cards[1],
                reference_score=91,
                reference_status="FULLY_COLLECTED_TRUSTED",
                fully_collected_trusted=True,
                reference_score_trustworthy=True,
                reference_score_usable_for_boundary=True,
            )
        ]
        expected_card = {
            "final_reference_recollect_required": True,
            "boundary_previous_recollect_required": True,
            "recollect_reference_index": 2,
            "recollect_reason": "BOUNDARY_PREVIOUS_REFERENCE_INCOMPLETE_OR_SKIPPED",
            "boundary_reference_index": 3,
            "final_reference_candidate_status": "FULLY_COLLECTED_TRUSTED",
        }

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                2,
                expected_card=expected_card,
                reference_history=reference_history,
            )

        self.assertFalse(selected["ok"])
        self.assertEqual(selected["stop_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertFalse(selected["duplicate_reference_allowed_for_recollect"])
        self.assertEqual(selected["duplicate_reference_allowed_reason"], "DUPLICATE_REFERENCE_CLICK_BLOCKED_FULLY_COLLECTED_TRUSTED")
        self.assertEqual(point, (0, 0))

    def test_missing_next_reference_reports_precise_s10_stop_code(self):
        cards = [card(1), card(2, price=16.2, mileage=1.8)]

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(self.snapshot, 3)

        self.assertFalse(selected["ok"])
        self.assertEqual(selected["stop_code"], "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND")
        self.assertEqual(point, (0, 0))

    def test_absolute_reference_index_uses_expected_identity_when_viewport_renumbered(self):
        visible_cards = [
            card(1, title="别克 君越 2021款 652T 豪华型", price=8.51, mileage=7.97),
            card(2, title="别克 君越 2021款 652T 豪华型", price=8.55, mileage=11.22),
            card(3, title="别克 君越 2021款 652T 豪华型", price=9.12, mileage=4.95),
            card(4, title="别克 君越 2021款 652T 豪华型", price=9.13, mileage=5.82),
            card(5, title="别克 君越 2021款 652T 豪华型", price=9.15, mileage=7.69),
            card(6, title="别克 君越 2021款 652T 豪华型", price=9.19, mileage=6.6),
            card(7, title="别克 君越 2021款 652T 豪华型", price=9.24, mileage=3.89),
        ]
        expected_ref9 = dict(visible_cards[5])
        expected_ref9.update(
            {
                "reference_index": 9,
                "canonical_reference_index": 9,
                "first_stage_card_order": 9,
            }
        )

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=visible_cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                9,
                expected_card=expected_ref9,
                reference_history=[],
            )

        self.assertEqual(selected["selected_reference_index"], 9)
        self.assertEqual(selected["viewport_reference_index"], 6)
        self.assertEqual(selected["selected_card_live_display_order"], 6)
        self.assertTrue(selected["s10_absolute_reference_binding_success"])
        self.assertTrue(selected["s10_viewport_renumbering_detected"])
        self.assertEqual(selected["s10_reference_index_scope"], "canonical")
        self.assertTrue(selected["s10_binding_identity_matched"])
        self.assertGreater(point[0], 0)

    def test_absolute_reference_index_multiple_identity_matches_are_rejected(self):
        cards = [
            card(1, title="别克 君越 2021款 652T 豪华型", price=9.19, mileage=6.6),
            card(2, title="别克 君越 2021款 652T 豪华型", price=9.19, mileage=6.6),
        ]
        expected_ref9 = dict(cards[0], reference_index=9, canonical_reference_index=9)

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=cards):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                9,
                expected_card=expected_ref9,
                reference_history=[],
            )

        self.assertFalse(selected["ok"])
        self.assertEqual(selected["stop_code"], "S10_NEXT_REFERENCE_CARD_NOT_UNIQUE")
        self.assertEqual(point, (0, 0))
        self.assertTrue(selected["s10_viewport_renumbering_detected"])

    def test_expected_identity_partial_card_requests_completion_instead_of_not_found(self):
        partial = card(8, title="别克 君越 2021款 652T 豪华型", price=9.19, mileage=6.6)
        partial.update(
            {
                "reference_index": None,
                "canonical_reference_index": None,
                "list_price_text": "",
                "list_price_10k": None,
                "list_price_yuan": None,
                "card_complete": False,
                "card_fully_visible": False,
                "has_price": False,
                "incomplete_reason": ["missing_price", "bottom_partial_card_or_outside_safe_viewport"],
            }
        )
        expected_ref9 = dict(partial)
        expected_ref9.update(
            {
                "reference_index": 9,
                "canonical_reference_index": 9,
                "list_price_text": "9.19万",
                "list_price_10k": 9.19,
                "card_complete": True,
            }
        )

        with mock.patch.object(self.runtime, "_extract_s10_reference_cards", return_value=[partial]):
            selected, point = self.runtime._select_s10_reference_card_by_index(
                self.snapshot,
                9,
                expected_card=expected_ref9,
                reference_history=[],
            )

        self.assertFalse(selected["ok"])
        self.assertEqual(selected["stop_code"], "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED")
        self.assertTrue(selected["s10_partial_card_candidate_seen"])
        self.assertFalse(selected["s10_partial_card_completion_attempted"])
        self.assertEqual(point, (0, 0))


if __name__ == "__main__":
    unittest.main()
