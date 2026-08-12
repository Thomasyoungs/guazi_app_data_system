import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_result_formatter import format_result_reply  # noqa: E402


class S11ContractExecutionAckTest(unittest.TestCase):
    def test_s10_to_s11_xml_stale_maps_to_precise_stop_code(self):
        code, message = runtime._s10_to_s11_failure_stop_code(
            {
                "any_screenshot_changed": True,
                "any_xml_changed": False,
                "xml_stale_during_detail_load": True,
            }
        )

        self.assertEqual(code, "S11_XML_STALE_OR_MISMATCHED_WITH_SCREENSHOT")
        self.assertIn("XML stayed stale", message)

    def test_s10_to_s11_page_changed_but_not_recognized_maps_to_detail_recognition_failed(self):
        code, _message = runtime._s10_to_s11_failure_stop_code(
            {
                "any_screenshot_changed": True,
                "any_xml_changed": True,
                "xml_stale_during_detail_load": False,
            }
        )

        self.assertEqual(code, "S11_DETAIL_PAGE_RECOGNITION_FAILED_AFTER_S10_CLICK")

    def test_s11_handler_ack_requires_allowed_action_and_report_search_init(self):
        context = {
            "current_reference": {
                "s10_to_s11_click_executed": True,
                "page_changed_after_click": True,
            }
        }
        snapshot = {
            "screenshot_path": "artifacts/screenshots/s11.png",
            "xml_path": "artifacts/debug/s11.xml",
            "visible_texts": ["查看完整报告"],
        }

        ack = runtime._mark_s11_contract_execution_ack(
            context,
            snapshot,
            handler_invoked=True,
            allowed_action_started=True,
            report_search_state_initialized=True,
            allowed_action_name="find_view_full_report",
        )

        self.assertTrue(ack["s11_contract_execution_ack"])
        self.assertTrue(ack["s10_to_s11_click_executed"])
        self.assertTrue(ack["page_changed_after_click"])
        self.assertTrue(ack["s11_page_recognized"])
        self.assertTrue(ack["top_one_third_vehicle_image_area"])
        self.assertTrue(ack["s11_handler_invoked"])
        self.assertTrue(ack["s11_allowed_action_started"])
        self.assertEqual(ack["s11_report_search_action_context"], "S11_REPORT_SEARCH")
        self.assertEqual(ack["s11_report_search_strategy"], "xml_exact_text_bounds_search")
        self.assertEqual(ack["s11_contract_execution_ack_stage"], "S11_TRANSFER_COLLECT_OR_REPORT_SEARCH")
        self.assertTrue(ack["s11_report_search_state_initialized"])
        self.assertIn("s11_ack_ts", ack)

    def test_s11_detail_recognized_but_handler_not_invoked_has_precise_stop(self):
        context = {
            "current_reference": {
                "s10_to_s11_click_executed": True,
                "page_changed_after_click": True,
                "transition_context": "S10_TO_S11",
                "s11_page_recognized": True,
                "s11_handler_invoked": False,
                "s11_allowed_action_started": False,
                "s11_report_search_state_initialized": False,
            }
        }
        stop = runtime._s11_contract_ack_stop_context(context, {"visible_texts": ["查看完整报告"]})

        self.assertEqual(stop["code"], "S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED")
        self.assertTrue(stop["context"]["s10_to_s11_click_executed"])
        self.assertTrue(stop["context"]["page_changed_after_click"])
        self.assertFalse(stop["context"]["s11_handler_invoked"])
        self.assertFalse(stop["context"]["s11_allowed_action_started"])

    def test_s11_handler_invoked_but_allowed_action_not_started_has_precise_stop(self):
        context = {
            "current_reference": {
                "s10_to_s11_click_executed": True,
                "page_changed_after_click": True,
                "transition_context": "S10_TO_S11",
                "s11_page_recognized": True,
                "s11_handler_invoked": True,
                "s11_allowed_action_started": False,
                "s11_report_search_state_initialized": False,
            }
        }
        stop = runtime._s11_contract_ack_stop_context(context, {"visible_texts": ["查看完整报告"]})

        self.assertEqual(stop["code"], "S11_ALLOWED_ACTION_NOT_STARTED_AFTER_HANDLER_INVOKED")

    def test_s11_handler_invoked_but_report_search_not_initialized_has_precise_stop(self):
        context = {
            "current_reference": {
                "s10_to_s11_click_executed": True,
                "page_changed_after_click": True,
                "transition_context": "S10_TO_S11",
                "s11_page_recognized": True,
                "s11_handler_invoked": True,
                "s11_allowed_action_started": True,
                "s11_report_search_state_initialized": False,
            }
        }
        stop = runtime._s11_contract_ack_stop_context(context, {"visible_texts": ["查看完整报告"]})

        self.assertEqual(stop["code"], "S11_REPORT_SEARCH_STATE_NOT_INITIALIZED")

    def test_feishu_feedback_s11_contract_handler_not_invoked_accurate(self):
        reply = format_result_reply(
            task_id="FS_TEST",
            pricing_result=None,
            status="FAILED",
            run_meta={},
            errors=["S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED"],
        )

        self.assertIn("参考车详情页", reply.text)
        self.assertIn("详情页采集契约", reply.text)
        for forbidden in ("未开始", "手机执行环境", "未成功打开到前台", "价格已完成"):
            self.assertNotIn(forbidden, reply.text)


if __name__ == "__main__":
    unittest.main()
