import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_result_formatter import _failure_description  # noqa: E402


def _node(label, bounds, *, clickable=False, enabled=True):
    return {
        "labels": [label],
        "bounds": bounds,
        "clickable": clickable,
        "enabled": enabled,
        "selected": False,
    }


class S11ReportEntryNoFixedCoordinateClickTests(unittest.TestCase):
    def test_xml_view_full_report_exact_node_clicks_node_bounds(self):
        report_bounds = [66, 2050, 530, 2160]
        snapshot = {
            "nodes": [
                _node(runtime.S11_REPORT_ENTRY_TEXT, report_bounds, clickable=True),
                _node("找顾问解读报告", [560, 2050, 1030, 2160], clickable=True),
            ],
            "visible_texts": [runtime.S11_REPORT_ENTRY_TEXT, "找顾问解读报告"],
            "fresh_xml": "",
        }
        report_node, report_text = runtime._find_s11_official_report_entry_node(snapshot)
        self.assertEqual(report_text, runtime.S11_REPORT_ENTRY_TEXT)

        click_target = runtime._find_s11_report_click_target(
            snapshot,
            report_node,
            {
                "viewport_bounds": [0, 0, 1080, 2712],
                "safe_bottom_y": 2400,
                "overlapped_bottom_bar": False,
                "below_safe_bottom": False,
                "too_close_to_bottom": False,
            },
        )

        self.assertTrue(click_target["ok"])
        self.assertEqual(click_target["click_target_source"], "xml_exact_text_bounds")
        self.assertEqual(click_target["clicked_point"], runtime._center(report_bounds))
        self.assertNotIn("ratio", click_target)

    def test_stale_xml_recovery_uses_new_xml_bounds_after_redump(self):
        report_bounds = [72, 2018, 528, 2136]
        snapshot_after_redump = {
            "nodes": [_node(runtime.S11_REPORT_ENTRY_TEXT, report_bounds, clickable=True)],
            "visible_texts": [runtime.S11_REPORT_ENTRY_TEXT],
            "fresh_xml": "",
        }
        click_target = runtime._s11_report_entry_xml_bounds_click_target(
            snapshot_after_redump,
            visibility={
                "viewport_bounds": [0, 0, 1080, 2712],
                "safe_bottom_y": 2400,
                "overlapped_bottom_bar": False,
                "below_safe_bottom": False,
                "too_close_to_bottom": False,
            },
            click_source="xml_after_stale_recovery",
            fresh_pair={"s11_xml_stale": True},
            recovery=True,
        )

        self.assertTrue(click_target["ok"])
        self.assertEqual(click_target["click_target_source"], "xml_after_stale_recovery")
        self.assertEqual(click_target["s11_report_entry_click_mode"], "xml_after_stale_recovery")
        self.assertEqual(click_target["clicked_point"], runtime._center(report_bounds))
        self.assertTrue(click_target["s11_xml_stale_warning"])

    def test_stale_xml_without_bindable_node_does_not_return_blind_click(self):
        stale_snapshot = {
            "nodes": [
                _node("价格从低到高", [0, 0, 1080, 2712]),
                _node("品牌专区", [0, 180, 1080, 260]),
                _node("车型配置", [0, 260, 1080, 340]),
            ],
            "visible_texts": ["价格从低到高", "品牌专区", "车型配置"],
            "fresh_xml": "",
        }
        click_target = runtime._s11_report_entry_xml_bounds_click_target(
            stale_snapshot,
            fresh_pair={"s11_xml_stale": True},
            recovery=True,
        )

        self.assertFalse(click_target["ok"])
        self.assertEqual(click_target["stop_code"], "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET")
        self.assertNotIn("clicked_point", click_target)
        self.assertEqual(click_target["click_target_source"], "no_bindable_target")

    def test_no_fixed_coordinate_fallback_symbols_remain(self):
        source = Path(runtime.__file__).read_text(encoding="utf-8")
        self.assertNotIn("S11_REPORT_ENTRY_DIRECT_CLICK_X_RATIO", source)
        self.assertNotIn("S11_REPORT_ENTRY_DIRECT_CLICK_Y_RATIO", source)
        self.assertNotIn("S11_REPORT_ENTRY_DIRECT_CLICK_BOUNDS_RATIOS", source)
        self.assertNotIn("_s11_report_entry_visible_direct_click_target", source)
        self.assertNotIn("visible_direct_click_known_report_entry_region", source)
        self.assertNotIn("default_design_viewport", source)

    def test_does_not_bind_advisor_or_contact_buttons_without_report_node(self):
        snapshot = {
            "nodes": [
                _node("找顾问解读报告", [560, 2050, 1030, 2160], clickable=True),
                _node("联系顾问", [760, 2350, 1030, 2480], clickable=True),
            ],
            "visible_texts": ["找顾问解读报告", "联系顾问"],
            "fresh_xml": "",
        }
        click_target = runtime._s11_report_entry_xml_bounds_click_target(snapshot)

        self.assertFalse(click_target["ok"])
        self.assertEqual(click_target["stop_code"], "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET")
        self.assertNotEqual(click_target.get("clicked_text"), "找顾问解读报告")
        self.assertNotEqual(click_target.get("clicked_text"), "联系顾问")

    def test_recovered_bounds_click_failure_uses_precise_stop_code(self):
        self.assertEqual(
            runtime._s11_report_entry_failure_code_after_click(
                True,
                "S11_REPORT_ENTRY_CLICK_NO_EFFECT",
            ),
            "S11_REPORT_ENTRY_DIRECT_CLICK_DID_NOT_ENTER_REPORT",
        )
        self.assertEqual(
            runtime._s11_report_entry_failure_code_after_click(
                False,
                "S11_REPORT_ENTRY_CLICK_NO_EFFECT",
            ),
            "S11_REPORT_ENTRY_CLICK_NO_EFFECT",
        )

    def test_business_failure_copy_has_no_internal_or_coordinate_terms(self):
        description = _failure_description("S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET")
        self.assertIn("系统已进入参考车详情页", description)
        self.assertIn("未能可靠绑定", description)
        for forbidden in (
            "S11",
            "XML",
            "fresh pair",
            "WebView",
            "bounds",
            "candidate",
            "coordinate",
            "坐标",
            "adb",
            "uiautomator",
            "runner",
            "dispatcher",
            "traceback",
            "status.json",
        ):
            self.assertNotIn(forbidden, description)


if __name__ == "__main__":
    unittest.main()
