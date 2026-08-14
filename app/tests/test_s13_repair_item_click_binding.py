import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_result_formatter import format_result_reply  # noqa: E402


def node(text: str, bounds: list[int], *, clickable=False, enabled=True) -> dict:
    return {
        "text": text,
        "content_desc": "",
        "labels": [text],
        "bounds": tuple(bounds),
        "clickable": clickable,
        "enabled": enabled,
        "selected": False,
        "resource_id": "",
        "package": "com.ganji.android.haoche_c",
        "class_name": "android.widget.TextView",
    }


def s13_snapshot(*, repair_y=1738) -> dict:
    nodes = [
        node("瓜子官方检测报告", [0, 0, 1220, 90]),
        node("车身外观良好", [48, 1180, 360, 1240]),
        node("驾驶侧深度检测：", [80, 1510, 360, 1570]),
        node("历史修复", [640, 1510, 790, 1570]),
        node("2", [835, 1510, 880, 1570]),
        node("注意事项", [930, 1510, 1080, 1570]),
        node("0", [1110, 1510, 1145, 1570]),
        node("左后翼子板", [134, repair_y, 310, repair_y + 55], clickable=False),
        node("左后翼子板漆面", [602, repair_y, 847, repair_y + 55], clickable=False),
        node("微信", [90, 2470, 170, 2530], clickable=True),
        node("实车讲解", [250, 2470, 390, 2530], clickable=True),
        node("联系卖家", [500, 2470, 670, 2530], clickable=True),
        node("讲价", [930, 2470, 1030, 2530], clickable=True),
    ]
    return {
        "nodes": nodes,
        "visible_texts": [item["text"] for item in nodes],
        "visible_blob": "\n".join(item["text"] for item in nodes),
        "xml_path": "mock_s13.xml",
        "screenshot_path": "mock_s13.png",
    }


def s13_left_sill_snapshot(*, include_history_item=True, include_normal_list=True, history_y=1738) -> dict:
    nodes = [
        node("瓜子官方检测报告", [0, 0, 1080, 90]),
        node("车身外观", [68, 371, 280, 445]),
        node("驾驶侧", [68, 1078, 286, 1168], clickable=True),
        node("车尾", [305, 1078, 484, 1168], clickable=True),
        node("驾驶侧深度检测：", [101, 1628, 442, 1694]),
        node("历史修复", [470, 1628, 638, 1694]),
        node("1", [632, 1628, 660, 1694]),
        node("注意事项", [742, 1628, 907, 1694]),
        node("0", [904, 1628, 937, 1694]),
    ]
    if include_history_item:
        nodes.extend(
            [
                node("左侧底边梁漆面", [134, history_y, 379, history_y + 55], clickable=False),
                node("AI解读驾驶侧异常有什么影响", [156, history_y + 99, 649, history_y + 154]),
            ]
        )
    if include_normal_list:
        nodes.extend(
            [
                node("驾驶侧：检测通过35", [68, 1963, 1012, 2087]),
                node("左后门框漆面", [134, 2103, 346, 2158], clickable=False),
                node("左C柱外侧漆面", [134, 2197, 368, 2255], clickable=False),
                node("左A柱外侧漆面", [602, 2197, 836, 2255], clickable=False),
                node("左侧车顶边梁外侧漆面", [134, 2293, 484, 2348], clickable=False),
            ]
        )
    return {
        "nodes": nodes,
        "visible_texts": [item["text"] for item in nodes],
        "visible_blob": "\n".join(item["text"] for item in nodes),
        "xml_path": "mock_s13_left_sill.xml",
        "screenshot_path": "mock_s13_left_sill.png",
    }


class DummyMachine:
    def assert_action_allowed(self, *_args, **_kwargs):
        return None


class DummyTiming:
    def add(self, **kwargs):
        self.last = kwargs


class DummyIssues:
    def record(self, code, _page, message, context, _severity):
        return {"code": code, "message": message, "context": context}


class S13RepairItemClickBindingTest(unittest.TestCase):
    def test_left_sill_history_entry_binds_before_normal_check_list(self):
        result = runtime._s13_find_repair_item_click_target(s13_left_sill_snapshot(), "驾驶侧", 1)

        self.assertTrue(result["ok"], result)
        audit = result["audit"]
        self.assertEqual(audit["selected_repair_item_text"], "左侧底边梁漆面")
        self.assertEqual(audit["selected_history_repair_entry"], "左侧底边梁漆面")
        self.assertEqual(audit["selected_entry_source"], "history_repair_region_text_node")
        self.assertEqual(audit["s13_repair_item_candidate_source"], "history_repair_region_text_node")
        self.assertTrue(audit["requires_safe_row_binding"])
        self.assertTrue(audit["bound_to_safe_row"])
        self.assertTrue(audit["normal_check_list_boundary_detected"])
        self.assertEqual(audit["s13_history_entry_zone"]["normal_check_list_top_y"], 1963)
        self.assertNotEqual(audit["selected_repair_item_text"], "左后门框漆面")

    def test_left_sill_is_s13_navigation_alias_without_s14_scoring_alias(self):
        self.assertNotIn("底边梁", runtime.S14_ALLOWED_PARTS)
        self.assertTrue(runtime._s13_repair_item_label_matches("左侧底边梁漆面"))

    def test_normal_check_list_candidates_are_still_rejected(self):
        result = runtime._s13_find_repair_item_click_target(
            s13_left_sill_snapshot(include_history_item=False, include_normal_list=True),
            "驾驶侧",
            1,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED")
        audit = result["audit"]
        self.assertEqual(audit["repair_item_candidates"], [])
        rejected_texts = [item["candidate_text"] for item in audit["s13_repair_item_rejected_candidates"]]
        self.assertIn("左后门框漆面", rejected_texts)
        self.assertTrue(all(item["candidate_is_normal_check_item"] for item in audit["s13_repair_item_rejected_candidates"]))

    def test_count_positive_without_visible_history_entry_gets_specific_stop_code(self):
        result = runtime._s13_find_repair_item_click_target(
            s13_left_sill_snapshot(include_history_item=False, include_normal_list=False),
            "驾驶侧",
            1,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND")

    def test_left_sill_in_bottom_unsafe_area_requests_reposition(self):
        result = runtime._s13_find_repair_item_click_target(
            s13_left_sill_snapshot(include_history_item=True, include_normal_list=False, history_y=2100),
            "驾驶侧",
            1,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE")
        self.assertTrue(runtime._s13_repair_item_needs_reposition(result))

    def test_s13_history_entry_business_reply_hides_internal_terms(self):
        result = format_result_reply(
            task_id="FS20260625_0003",
            pricing_result=None,
            status="FAILED",
            run_meta={"error_code": "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND"},
            errors=["S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND"],
        )

        self.assertIn("【本次定价未完成】FS20260625_0003", result.text)
        self.assertIn("历史修复车况入口", result.text)
        for forbidden in [
            "S13",
            "S14",
            "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
            "XML",
            "bounds",
            "candidate",
            "normal_check_list",
            "runner",
            "dispatcher",
            "traceback",
            "status.json",
            "adb",
            "uiautomator",
        ]:
            self.assertNotIn(forbidden, result.text)

    def test_non_clickable_repair_text_binds_to_safe_row_area(self):
        result = runtime._s13_find_repair_item_click_target(s13_snapshot(), "驾驶侧", 2)

        self.assertTrue(result["ok"], result)
        audit = result["audit"]
        self.assertEqual(audit["selected_repair_item_text"], "左后翼子板")
        self.assertTrue(audit["bound_to_safe_row"])
        self.assertFalse(audit["clicked_node_clickable"])
        self.assertTrue(audit["safe_click_region"])
        self.assertIsNotNone(audit["selected_repair_item_bounds"])
        self.assertIsNotNone(audit["selected_repair_item_click_bounds"])
        self.assertEqual(audit["selected_repair_item_click_strategy"], "non_clickable_text_safe_row_bounds")
        self.assertNotIn("联系卖家", audit["forbidden_nearby_texts"])
        self.assertNotIn("讲价", audit["forbidden_nearby_texts"])

    def test_bottom_bar_repair_text_is_rejected_as_unsafe(self):
        result = runtime._s13_find_repair_item_click_target(s13_snapshot(repair_y=2440), "驾驶侧", 2)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE")
        audit = result["audit"]
        self.assertFalse(audit["safe_click_region"])

    def test_click_without_page_change_returns_specific_error(self):
        snapshot = s13_snapshot()
        context = {
            "recognizer": object(),
            "issues": DummyIssues(),
            "machine": DummyMachine(),
            "timing": DummyTiming(),
            "client": object(),
            "current_reference": {},
        }
        click_target = {
            "ok": True,
            "click_bounds": (120, 1700, 460, 1780),
            "audit": {
                "selected_repair_item_text": "左后翼子板",
                "selected_repair_item_bounds": [134, 1738, 310, 1793],
            },
        }

        with patch.object(runtime, "_ensure_page", return_value=None), patch.object(
            runtime, "_scroll_s13_to_history_repair_table", return_value=(snapshot, 0)
        ), patch.object(runtime, "_tap_s13_region_tab", return_value=(snapshot, 0, 0)), patch.object(
            runtime, "_extract_all_history_repair_counts", return_value=({"驾驶侧": 2}, {"regions": {}})
        ), patch.object(runtime, "_store_repair_item_completion_state", return_value={}), patch.object(
            runtime, "_s13_find_repair_item_click_target", return_value=click_target
        ), patch.object(runtime, "contract_execute_click", return_value=0), patch.object(
            runtime, "_capture", return_value=snapshot
        ), patch.object(runtime, "_s13_live_room_signals", return_value=[]), patch.object(
            runtime, "_s14_candidate_signals", return_value=[]
        ), patch.object(runtime, "_recognize_mainline_page", return_value="S13"), patch.object(
            runtime, "_sha256_text", return_value="same"
        ), patch.object(runtime, "_sha256_file", return_value="same"), patch.object(runtime.time, "sleep", return_value=None):
            with self.assertRaises(runtime.GuaziFlowError) as ctx:
                runtime.handle_s13(context, snapshot)

        self.assertEqual(ctx.exception.code, "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL")


if __name__ == "__main__":
    unittest.main()
