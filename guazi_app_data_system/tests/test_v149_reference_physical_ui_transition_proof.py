import inspect
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_result_formatter import format_result_reply  # noqa: E402


class DummyMachine:
    def __init__(self):
        self.allowed = []

    def assert_action_allowed(self, page, action):
        self.allowed.append((page, action))


class DummyIssues:
    def __init__(self):
        self.records = []

    def record(self, code, page, message, context, severity):
        payload = {
            "code": code,
            "page": page,
            "message": message,
            "context": context,
            "severity": severity,
        }
        self.records.append(payload)
        return payload


def proof(index=1, signature="sig-1"):
    return {
        "proof_version": runtime.REFERENCE_PHYSICAL_UI_TRANSITION_PROOF_VERSION,
        "transition_context": "S10_TO_S11",
        "reference_index": index,
        "next_card_click_verified": True,
        "page_changed_after_click": True,
        "destination_identity_matched": True,
        "same_page_signature_reused": False,
        "actual_page_signature": signature,
        "physical_evidence_ok": True,
    }


class V149ReferencePhysicalUiTransitionProofTest(unittest.TestCase):
    def test_reference_identity_summary_single_signature_supports_v149_proof(self):
        source = (ROOT / "scripts" / "runtime_s10_to_s16_mainline.py").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("def _reference_identity_summary("))
        signature = inspect.signature(runtime._reference_identity_summary)

        self.assertIn("reference_index", signature.parameters)
        self.assertEqual(signature.parameters["reference_index"].default, 0)
        summary = runtime._reference_identity_summary(
            {
                "selected_card_title": "Ref 1",
                "selected_card_price": "9.8万",
                "selected_card_metadata": "2021 | 4.8万公里",
            },
            1,
        )

        self.assertEqual(summary["reference_index"], 1)
        self.assertAlmostEqual(summary["price_yuan"], 98000.0)
        self.assertTrue(summary["reference_identity_summary_function_signature_checked"])
        self.assertTrue(summary["v149_reference_identity_summary_duplicate_removed"])

    def test_build_reference_physical_ui_transition_proof_two_arg_identity_path_no_type_error(self):
        proof_payload = runtime._build_reference_physical_ui_transition_proof(
            {},
            transition_context="S10_TO_S11",
            from_page="S10",
            to_page="S11",
            reference_index=1,
            expected_card={
                "selected_card_title": "Ref 1",
                "selected_card_price": "9.8万",
                "selected_card_metadata": "2021 | 4.8万公里",
            },
            before_snapshot={"fresh_xml": "<s10 />"},
            after_snapshot={
                "visible_texts": ["Ref 1", "9.8万", "2021 | 4.8万公里"],
                "fresh_xml": "<s11 />",
            },
            click_evidence={"clicked_text": "Ref 1"},
            page_changed_after_click=True,
            next_card_click_verified=True,
        )

        self.assertTrue(proof_payload["reference_identity_summary_function_signature_checked"])
        self.assertTrue(proof_payload["v149_reference_identity_summary_duplicate_removed"])
        self.assertTrue(proof_payload["physical_evidence_ok"])

    def test_reference_history_write_requires_physical_ui_transition_proof(self):
        context = {
            "current_reference": {
                "reference_index": 2,
                "selected_card_title": "Ref 2",
                "selected_card_price": "10.2万",
                "selected_card_metadata": "2021 | 5.1w | Tangshan",
                "reference_score": 80.0,
            },
            "reference_history": [],
        }

        history, gate = runtime._safe_reference_history_with_current_reference(
            context,
            purpose="unit_v149_no_proof",
            require_identity=True,
        )

        self.assertEqual([], history)
        self.assertTrue(gate["reference_history_write_blocked"])
        self.assertEqual(runtime.REFERENCE_HISTORY_PHYSICAL_PROOF_MISSING, gate["reference_history_write_block_code"])

    def test_reference_history_write_rejects_same_physical_page_signature_reuse(self):
        context = {
            "current_reference": {
                "reference_index": 2,
                "selected_card_title": "Ref 2",
                "selected_card_price": "10.2万",
                "selected_card_metadata": "2021 | 5.1w | Tangshan",
                "reference_score": 80.0,
                "physical_ui_transition_proof": proof(2, "same-page"),
                "actual_page_signature": "same-page",
            },
            "reference_history": [
                {
                    "reference_index": 1,
                    "selected_card_title": "Ref 1",
                    "selected_card_price": "9.8万",
                    "selected_card_metadata": "2021 | 4.8w | Tangshan",
                    "reference_score": 78.0,
                    "physical_ui_transition_proof": proof(1, "same-page"),
                    "actual_page_signature": "same-page",
                }
            ],
        }

        history, gate = runtime._safe_reference_history_with_current_reference(
            context,
            purpose="unit_v149_signature_reuse",
            require_identity=True,
        )

        self.assertEqual(1, len(history))
        self.assertTrue(gate["reference_history_write_blocked"])
        self.assertEqual(runtime.REFERENCE_HISTORY_PHYSICAL_SIGNATURE_REUSED, gate["reference_history_write_block_code"])

    def test_s13_all_zero_exit_uses_return_to_reliable_s10_transaction(self):
        returned_snapshot = {
            "recognized_page": "S10",
            "visible_texts": ["价格从低到高", "Ref 2"],
            "fresh_xml": "<hierarchy />",
        }

        def hook(_context, _snapshot, _current_reference, _expected_next_reference):
            return {
                "ok": True,
                "return_to_reliable_s10_verified": True,
                "recognized_page": "S10",
                "snapshot": returned_snapshot,
                "s10_reliable_list_evidence": {"reliable": True, "target_card_visible": True},
                "next_reference_index": 2,
            }

        context = {
            "machine": DummyMachine(),
            "issues": DummyIssues(),
            "current_reference_index": 1,
            "current_reference": {
                "reference_index": 1,
                "selected_card_title": "Ref 1",
                "selected_card_price": "9.8万",
                "selected_card_metadata": "2021 | 4.8w | Tangshan",
                "physical_ui_transition_proof": proof(1, "ref-1"),
            },
            "first_stage_evidence": {"same_source_cards": [{"title": "Ref 1"}, {"title": "Ref 2"}]},
            "s13_return_to_s10_transaction_hook": hook,
        }

        state, snapshot = runtime._finish_s13_all_zero_with_reliable_s10_return(
            context,
            {"recognized_page": "S13", "fresh_xml": "<s13 />"},
            scan_state={"all_regions_checked": True, "s13_all_zero": True},
        )

        self.assertEqual("S15", state)
        self.assertIs(snapshot, returned_snapshot)
        self.assertTrue(context["current_reference"]["return_to_reliable_s10_verified"])
        self.assertTrue(context["current_reference"]["s13_return_to_s10_physical_transaction"]["ok"])
        self.assertIn(("S13", "return_to_s10_if_all_zero"), context["machine"].allowed)

    def test_all_references_exhausted_blocks_logical_count_without_physical_proof(self):
        gate = runtime._all_references_exhausted_physical_gate(
            [
                {"reference_index": 1, "physical_ui_transition_proof": proof(1, "ref-1")},
                {"reference_index": 2},
            ],
            trisame_count=2,
            next_reference_index=3,
        )

        self.assertFalse(gate["physical_evidence_ok"])
        self.assertEqual(runtime.ALL_REFERENCES_EXHAUSTED_BLOCKED_BY_MISSING_PHYSICAL_EVIDENCE, gate["stop_code"])
        self.assertEqual([2], gate["missing_physical_evidence_reference_indices"])

    def test_reference_physical_ui_failure_business_reply_is_not_generic_environment_error(self):
        result = format_result_reply(
            task_id="FS_TEST",
            pricing_result={"status": runtime.S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING},
            status=runtime.S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING,
        )

        self.assertIn("参考车", result.text)
        for forbidden in ("APP_NOT_FOREGROUND", "UNKNOWN_ERROR", "手机执行环境", "未开始"):
            self.assertNotIn(forbidden, result.text)


if __name__ == "__main__":
    unittest.main()
