import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from s10s16_clean import field_extractors, page_proofs, transition_gates  # noqa: E402


class S12S13CleanModuleBoundariesTest(unittest.TestCase):
    def test_module_responsibility_rules_are_documented(self):
        readme = (SCRIPT_DIR / "s10s16_clean" / "README.md").read_text(encoding="utf-8")

        self.assertIn("page_proofs", readme)
        self.assertIn("only judges page evidence", readme)
        self.assertIn("field_extractors", readme)
        self.assertIn("only extracts or validates fields", readme)
        self.assertIn("transition_gates", readme)
        self.assertIn("orchestration and", readme)
        self.assertIn("Every new stop code", readme)

    def test_baseline_matrix_contains_required_historical_paths(self):
        fixtures = json.loads(
            (SCRIPT_DIR / "s10s16_clean" / "baseline_matrix" / "fixtures.json").read_text(encoding="utf-8")
        )
        ids = {item["id"] for item in fixtures["baselines"]}

        self.assertIn("buick_historical_s13_success", ids)
        self.assertIn("fs20260703_0005_weak_s12_body_proof", ids)
        self.assertIn("fs20260704_0001_s12_claim_malformed_extent", ids)
        self.assertIn("v149_physical_transition", ids)
        self.assertIn("v33_boundary_previous_recollect", ids)
        self.assertIn("cross_task_isolation", ids)
        self.assertIn("global_popup_guard", ids)

    def test_runtime_extent_wrappers_delegate_to_clean_extractors(self):
        with mock.patch.object(runtime.clean_field_extractors, "is_valid_extent", return_value=True) as wrapped:
            self.assertTrue(runtime._is_valid_extent((1, 2, 3, 4)))
            wrapped.assert_called_once_with((1, 2, 3, 4))

        with mock.patch.object(runtime.clean_field_extractors, "coerce_extent", return_value=(1, 2, 3, 4)) as wrapped:
            self.assertEqual((1, 2, 3, 4), runtime._coerce_extent((1, 2, 3, 4)))
            wrapped.assert_called_once_with((1, 2, 3, 4))

        with mock.patch.object(
            runtime.clean_field_extractors,
            "s12_claim_recovery_extent_candidate",
            return_value=((1, 2, 3, 4), {"bounds_valid": True}),
        ) as wrapped:
            self.assertEqual(((1, 2, 3, 4), {"bounds_valid": True}), runtime._s12_claim_recovery_extent_candidate("x"))
            wrapped.assert_called_once_with("x")

    def test_runtime_page_proof_wrappers_delegate_to_clean_modules(self):
        snapshot = {"nodes": [], "visible_texts": [], "visible_blob": "", "screenshot_path": "s.png", "xml_path": "x.xml"}
        expected = {"body_appearance_text_present": False, "s13_region_tabs_present": False}
        with mock.patch.object(runtime.clean_page_proofs, "prove_s12_body_appearance_reached", return_value=expected) as wrapped:
            self.assertIs(expected, runtime._s12_body_appearance_progress_evidence(snapshot))
            wrapped.assert_called_once()

        proof = {"body_appearance_text_present": True, "s12_to_s13_region_tabs_seen": False}
        with mock.patch.object(runtime.clean_transition_gates, "s12_to_s13_proof_stop_code", return_value="STOP") as wrapped:
            self.assertEqual("STOP", runtime._s12_to_s13_proof_stop_code(proof))
            wrapped.assert_called_once_with(proof)

    def test_weak_s12_body_proof_blocks_s13_transition(self):
        proof = page_proofs.prove_s12_to_s13_region_history(
            {"screenshot_path": "s.png", "xml_path": "x.xml"},
            progress={
                "body_appearance_text_present": True,
                "body_appearance_detection_items_present": True,
                "s13_region_tabs_present": False,
                "s13_region_tab_bounds": {},
            },
            history_table_seen=False,
            bindings={},
            recognized_page="S12",
        )
        gate = transition_gates.gate_s12_to_s13(proof)

        self.assertFalse(gate["allowed"])
        self.assertEqual(runtime.S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED, gate["stop_code"])

    def test_malformed_claim_extent_stays_structured(self):
        bounds, trace = field_extractors.s12_claim_recovery_extent_candidate((1, 2))

        self.assertIsNone(bounds)
        self.assertFalse(trace["bounds_valid"])
        self.assertEqual(runtime.S12_CLAIM_RECOVERY_EXTENT_INVALID, trace["stop_code"])


if __name__ == "__main__":
    unittest.main()
