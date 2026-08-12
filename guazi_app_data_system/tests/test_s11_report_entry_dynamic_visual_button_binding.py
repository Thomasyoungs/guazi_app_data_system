import inspect
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402


def _node(label, bounds, *, clickable=False, enabled=True):
    return {
        "labels": [label],
        "text": label,
        "content_desc": "",
        "bounds": bounds,
        "clickable": clickable,
        "enabled": enabled,
        "selected": False,
    }


class S11ReportEntryXmlOnlyBindingTests(unittest.TestCase):
    def test_xml_bounds_available_clicks_xml_exact_bounds(self):
        report_bounds = [80, 1200, 520, 1320]
        snapshot = {
            "nodes": [
                _node("", [0, 0, 1080, 2712]),
                _node(runtime.S11_REPORT_ENTRY_TEXT, report_bounds, clickable=True),
            ],
            "visible_texts": [runtime.S11_REPORT_ENTRY_TEXT],
            "fresh_xml": "",
        }

        click_target = runtime._s11_report_entry_xml_bounds_click_target(snapshot)

        self.assertTrue(click_target["ok"])
        self.assertEqual(click_target["click_target_source"], "xml_exact_text_bounds")
        self.assertEqual(click_target["click_source"], "xml_exact_text_bounds")
        self.assertEqual(click_target["clicked_point"], runtime._center(tuple(report_bounds)))
        self.assertFalse(click_target.get("screenshot_used_for_click", False))

    def test_screenshot_visible_but_xml_missing_does_not_click(self):
        report_rect = [66, 2050, 530, 2160]
        snapshot = {
            "nodes": [
                _node("price sort", [0, 80, 300, 160]),
                _node("brand area", [0, 180, 300, 260]),
            ],
            "visible_texts": ["price sort", "brand area"],
            "fresh_xml": "",
            "screenshot_path": "mock_s11_report_entry_visible.png",
            "screenshot_dynamic_text_regions": [
                {
                    "text": runtime.S11_REPORT_ENTRY_TEXT,
                    "rect": report_rect,
                    "confidence": 0.94,
                    "source": "debug_fixture_only",
                }
            ],
        }

        click_target = runtime._s11_report_entry_xml_bounds_click_target(
            snapshot,
            fresh_pair={"s11_xml_stale": True},
            recovery=True,
        )

        self.assertFalse(click_target["ok"])
        self.assertEqual(
            click_target["stop_code"],
            "S11_REPORT_ENTRY_XML_MISSING_BUT_SCREENSHOT_VISIBLE_NOT_CLICKED",
        )
        self.assertNotIn("clicked_point", click_target)
        self.assertEqual(click_target["click_source"], "")
        self.assertEqual(click_target["click_target_source"], "no_xml_bindable_target")
        self.assertTrue(click_target["screenshot_visible_xml_missing_debug"])
        self.assertTrue(click_target["s11_visual_debug_not_used_for_click"])
        self.assertFalse(click_target["screenshot_used_for_click"])
        self.assertFalse(click_target["screenshot_detector_attempted"])

    def test_no_xml_bounds_and_no_debug_region_precisely_stops_without_click(self):
        snapshot = {
            "nodes": [_node("price sort", [0, 80, 300, 160])],
            "visible_texts": ["price sort"],
            "fresh_xml": "",
        }

        click_target = runtime._s11_report_entry_xml_bounds_click_target(
            snapshot,
            fresh_pair={"s11_xml_stale": True},
            recovery=True,
        )

        self.assertFalse(click_target["ok"])
        self.assertEqual(click_target["stop_code"], "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET")
        self.assertNotIn("clicked_point", click_target)
        self.assertEqual(click_target["click_target_source"], "no_bindable_target")
        self.assertFalse(click_target["screenshot_used_for_click"])

    def test_advisor_report_button_debug_region_is_not_clicked(self):
        snapshot = {
            "nodes": [],
            "visible_texts": ["advisor report"],
            "fresh_xml": "",
            "screenshot_dynamic_text_regions": [
                {
                    "text": "找顾问解读报告",
                    "rect": [560, 2050, 1030, 2160],
                    "confidence": 0.97,
                }
            ],
        }

        click_target = runtime._s11_report_entry_xml_bounds_click_target(snapshot)

        self.assertFalse(click_target["ok"])
        self.assertEqual(click_target["stop_code"], "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET")
        self.assertNotIn("clicked_point", click_target)
        self.assertEqual(click_target["dynamic_visual_binding_reason"], "no_exact_dynamic_report_entry_region")

    def test_dynamic_visual_helper_has_no_fixed_ratio_or_detector_fallback(self):
        helper_source = inspect.getsource(runtime._s11_dynamic_visual_text_regions)
        click_source = inspect.getsource(runtime._s11_report_entry_dynamic_visual_button_click_target)
        combined = helper_source + "\n" + click_source
        self.assertNotIn("_s11_attach_screenshot_button_layout_regions", helper_source)
        self.assertNotIn("0.28", combined)
        self.assertNotIn("0.78", combined)
        self.assertNotIn("x_ratio", combined)
        self.assertNotIn("y_ratio", combined)
        self.assertNotIn("default_design_viewport", combined)


if __name__ == "__main__":
    unittest.main()
