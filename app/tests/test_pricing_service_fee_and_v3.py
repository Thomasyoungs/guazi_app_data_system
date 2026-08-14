import unittest
import sys
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from guazi_app_data_system.models import ReferenceCar
from guazi_app_data_system.pricing import (
    ScoreResult,
    _select_v3_boundary_reference,
    calc_guazi_service_fee,
)


def scored_reference(index, score, price_10k=None, *, hard_reject=False, reasons=None):
    reference = ReferenceCar(
        reference_index=index,
        list_price_10k=price_10k if price_10k is not None else float(index),
        list_year=2021,
        list_mileage_10k_km=5.0,
        transfer_count=0,
        accident_count=0,
        max_accident_amount="无",
        repair_counts={},
    )
    return reference, ScoreResult(score=score, components={}, review_reasons=list(reasons or []), hard_reject=hard_reject)


class PricingServiceFeeAndV3Test(unittest.TestCase):
    def test_service_fee_tiers_match_confirmed_boundaries(self):
        cases = [
            (49999, 2500),
            (50000, 3000),
            (83400, 3000),
            (99999, 3000),
            (100000, 3500),
            (149999, 3500),
            (150000, 4000),
            (199999, 4000),
            (200000, 5000),
            (250000, 5000),
        ]

        for price, expected_fee in cases:
            with self.subTest(price=price):
                self.assertEqual(calc_guazi_service_fee(price), expected_fee)

    def test_service_fee_config_is_matched_high_to_low(self):
        unsorted_config = {
            "guazi_service_fee_tiers": [
                {"min_price_yuan": 0, "service_fee_yuan": 2500},
                {"min_price_yuan": 50000, "service_fee_yuan": 3000},
                {"min_price_yuan": 200000, "service_fee_yuan": 5000},
                {"min_price_yuan": 100000, "service_fee_yuan": 3500},
                {"min_price_yuan": 150000, "service_fee_yuan": 4000},
            ]
        }

        self.assertEqual(calc_guazi_service_fee(250000, unsorted_config), 5000)

    def test_fs20260627_0004_service_fee_and_pricing_chain_match_desktop_rule(self):
        target_guazi_listing_price_yuan = 83400
        service_fee_yuan = calc_guazi_service_fee(target_guazi_listing_price_yuan)
        guazi_net_payout_yuan = target_guazi_listing_price_yuan - service_fee_yuan
        cost_yuan = 1000
        profit_yuan = round(guazi_net_payout_yuan * 0.08)
        suggested_purchase_price_yuan = guazi_net_payout_yuan - cost_yuan - profit_yuan

        self.assertEqual(service_fee_yuan, 3000)
        self.assertEqual(guazi_net_payout_yuan, 80400)
        self.assertEqual(profit_yuan, 6432)
        self.assertEqual(suggested_purchase_price_yuan, 72968)

    def test_service_fee_83400_still_3000(self):
        self.assertEqual(calc_guazi_service_fee(83400), 3000)

    def test_profit_rate_config_is_eight_percent(self):
        fields = json.loads((ROOT / "config" / "fields.yaml").read_text(encoding="utf-8-sig"))

        self.assertEqual(fields["pricing"]["profit_rate"], 0.08)
        self.assertNotEqual(fields["pricing"]["profit_rate"], 0.065)

    def test_fs20260612_sample_price_chain_uses_eight_percent_profit(self):
        guazi_net_payout_yuan = 94900
        cost_yuan = 1000
        profit_yuan = round(guazi_net_payout_yuan * 0.08)
        suggested_purchase_price_yuan = guazi_net_payout_yuan - cost_yuan - profit_yuan

        self.assertEqual(profit_yuan, 7592)
        self.assertEqual(suggested_purchase_price_yuan, 86308)

    def test_v33_exact_boundary_selects_previous_reference(self):
        result = _select_v3_boundary_reference(
            [scored_reference(1, 81), scored_reference(2, 83), scored_reference(3, 84)],
            84,
        )

        self.assertTrue(result["boundary_confirmed"])
        self.assertEqual(result["boundary_reference_index"], 3)
        self.assertEqual(result["final_reference_candidate_index"], 2)
        self.assertEqual(result["final_reference_index"], 2)
        self.assertFalse(result["manual_review_required"])

    def test_existing_success_path_not_regressed(self):
        result = _select_v3_boundary_reference(
            [scored_reference(1, 81), scored_reference(2, 84)],
            84,
        )

        self.assertTrue(result["boundary_confirmed"])
        self.assertEqual(result["boundary_reference_index"], 2)
        self.assertEqual(result["final_reference_index"], 1)
        self.assertFalse(result["manual_review_required"])

    def test_v3_above_boundary_selects_previous_low_reference(self):
        result = _select_v3_boundary_reference(
            [scored_reference(1, 81), scored_reference(2, 83), scored_reference(3, 87)],
            84,
        )

        self.assertTrue(result["boundary_confirmed"])
        self.assertEqual(result["boundary_reference_index"], 3)
        self.assertEqual(result["final_reference_index"], 2)
        self.assertFalse(result["manual_review_required"])

    def test_v33_no_boundary_requires_manual_review_without_final_reference(self):
        result = _select_v3_boundary_reference(
            [scored_reference(1, 78), scored_reference(2, 81), scored_reference(3, 83)],
            84,
        )

        self.assertFalse(result["boundary_confirmed"])
        self.assertIsNone(result["final_reference_index"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["manual_review_reason"], "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING")

    def test_existing_needs_review_path_not_regressed(self):
        result = _select_v3_boundary_reference(
            [scored_reference(1, 78), scored_reference(2, 81)],
            84,
        )

        self.assertFalse(result["boundary_confirmed"])
        self.assertIsNone(result["final_reference_index"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["manual_review_reason"], "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING")

    def test_v3_first_reference_above_target_blocks_auto_pricing(self):
        result = _select_v3_boundary_reference([scored_reference(1, 87)], 84)

        self.assertTrue(result["boundary_confirmed"])
        self.assertIsNone(result["final_reference_index"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["manual_review_reason"], "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE")
        self.assertFalse(result["auto_pricing_allowed"])

    def test_v3_runtime_history_skips_untrusted_incomplete_and_rejected_references(self):
        from scripts.runtime_s10_to_s16_mainline import _select_v3_reference_from_history

        references = [
            {"reference_index": 1, "reference_score": 90, "reference_score_trustworthy": False, "list_price_10k": 1.0},
            {"reference_index": 2, "reference_score": 90, "reference_score_trustworthy": True, "hard_reject": True, "list_price_10k": 2.0},
            {
                "reference_index": 3,
                "reference_score": 90,
                "reference_score_trustworthy": True,
                "reference_score_review_reasons": ["REFERENCE_ACCIDENT_COUNT_MISSING_FIELD_INCOMPLETE"],
                "list_price_10k": 3.0,
            },
            {"reference_index": 4, "reference_score": 83, "reference_score_trustworthy": True, "list_price_10k": 4.0},
            {"reference_index": 5, "reference_score": 85, "reference_score_trustworthy": True, "list_price_10k": 5.0},
        ]

        result = _select_v3_reference_from_history(references, {"score": 84})

        self.assertEqual(result["boundary_reference_index"], 5)
        self.assertEqual(result["final_reference_index"], 4)
        self.assertEqual([item["reference_index"] for item in result["candidate_reference_pool"]], [4, 5])


if __name__ == "__main__":
    unittest.main()
