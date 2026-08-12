import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


BLACK = "\u9ed1"
BLACK_LABEL = "\u9ed1\u8272"
BLUE_LABEL = "\u84dd\u8272"
WHITE_LABEL = "\u767d\u8272"


class S07ColorClickTraceStrictConfirmTest(unittest.TestCase):
    def setUp(self):
        self.module = load_script_module()

    def test_color_click_trace_uses_candidate_bounds_and_point_inside(self):
        node = {
            "text": BLACK_LABEL,
            "labels": [BLACK_LABEL],
            "matched_color_text": BLACK_LABEL,
            "bounds": (264, 984, 508, 1097),
            "clickable": True,
            "enabled": True,
            "color_click_strategy": "direct_clickable_color_text_node",
        }
        trace = self.module._s07_build_color_click_action_trace(
            target_color=BLACK,
            color_node=node,
            click_point=self.module._center(node["bounds"]),
            attempt_index=1,
            snapshot={"nodes": [node]},
        )

        self.assertEqual(trace["candidate_bounds"], (264, 984, 508, 1097))
        self.assertEqual(trace["click_point"], [386, 1040])
        self.assertTrue(trace["candidate_matches_target"])
        self.assertTrue(trace["click_point_inside_candidate_bounds"])
        self.assertEqual(trace["candidate_bounds_role"], "color_grid_candidate")
        self.assertEqual(trace["click_source"], "direct_clickable_color_text_node")

    def test_grid_candidate_is_not_misread_as_selected_chip(self):
        snapshot = {
            "nodes": [
                {"labels": [BLACK_LABEL], "bounds": (264, 984, 508, 1097), "selected": False},
            ],
        }

        self.assertFalse(self.module._s07_is_top_selected_filter_chip((264, 984, 508, 1097)))
        self.assertFalse(self.module._target_color_selected(snapshot, BLACK_LABEL))

    def test_selected_blue_chip_blocks_black_target(self):
        snapshot = {
            "nodes": [
                {"labels": [BLUE_LABEL], "bounds": (57, 783, 129, 830), "selected": False},
                {"labels": [BLACK_LABEL], "bounds": (264, 984, 508, 1097), "selected": False},
            ],
        }

        evidence = self.module._s07_snapshot_color_filter_evidence(
            snapshot,
            BLACK_LABEL,
            source="s10_ready_source_gate",
        )

        self.assertEqual(self.module._s07_selected_color(snapshot), BLUE_LABEL)
        self.assertFalse(self.module._target_color_selected(snapshot, BLACK_LABEL))
        self.assertTrue(evidence["color_filter_mismatch"])
        self.assertEqual(evidence["color_filter_stop_code"], "S10_COLOR_FILTER_MISMATCH")
        self.assertEqual(evidence["color_filter_mismatch_reason"], "selected_color_chip_mismatch")

    def test_black_selected_chip_confirms_black_target(self):
        snapshot = {
            "nodes": [
                {"labels": [BLACK_LABEL], "bounds": (57, 783, 129, 830), "selected": False},
                {"labels": [BLACK_LABEL], "bounds": (264, 984, 508, 1097), "selected": False},
            ],
        }

        evidence = self.module._s07_snapshot_color_filter_evidence(
            snapshot,
            BLACK_LABEL,
            source="s07_after_color_select",
        )

        self.assertTrue(self.module._target_color_selected(snapshot, BLACK_LABEL))
        self.assertFalse(evidence["color_filter_mismatch"])
        self.assertTrue(evidence["target_color_confirmed"])

    def test_s07_color_path_has_no_fixed_ratio_click_fallback(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        s07_start = source.index("def handle_s07")
        s07_end = source.index("def handle_s08", s07_start)
        s07_source = source[s07_start:s07_end]

        self.assertNotIn("0.28", s07_source)
        self.assertNotIn("0.78", s07_source)
        self.assertNotIn("ratio coordinate", s07_source.lower())
        self.assertNotIn("fixed coordinate", s07_source.lower())
        self.assertIn("_execute_s07_target_color_click", s07_source)
        self.assertIn("S07_COLOR_CLICK_POINT_OUTSIDE_CANDIDATE_BOUNDS", source)

    def test_0012_non_clickable_black_text_uses_grid_text_bounds_not_fullscreen_parent(self):
        xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node text="" class="android.widget.LinearLayout" clickable="true" enabled="true" bounds="[0,0][1080,2400]">
    <node text="{BLACK_LABEL}" class="android.widget.TextView" clickable="false" enabled="true" bounds="[264,984][508,1097]" />
    <node text="{WHITE_LABEL}" class="android.widget.TextView" clickable="false" enabled="true" bounds="[528,984][772,1097]" />
    <node text="{BLUE_LABEL}" class="android.widget.TextView" clickable="false" enabled="true" bounds="[528,1122][772,1237]" />
  </node>
</hierarchy>"""
        snapshot = {
            "fresh_xml": xml,
            "nodes": [
                {"labels": [BLACK_LABEL], "bounds": (264, 984, 508, 1097), "selected": False},
                {"labels": [WHITE_LABEL], "bounds": (528, 984, 772, 1097), "selected": False},
                {"labels": [BLUE_LABEL], "bounds": (528, 1122, 772, 1237), "selected": False},
            ],
        }

        node = self.module._find_target_color_node(snapshot, BLACK)
        self.assertIsNotNone(node)
        self.assertEqual(node["bounds"], (264, 984, 508, 1097))
        self.assertFalse(node["clickable"])
        self.assertEqual(node["matched_color_text"], BLACK_LABEL)
        self.assertEqual(node["color_click_strategy"], "color_grid_text_node_bounds")
        self.assertEqual(node["click_source"], "color_grid_text_node_bounds")
        self.assertEqual(node["parent_bounds"], (0, 0, 1080, 2400))
        self.assertTrue(node["parent_bounds_ambiguous"])
        self.assertTrue(node["candidate_grid_bounds_safe"])

        trace = self.module._s07_build_color_click_action_trace(
            target_color=BLACK,
            color_node=node,
            click_point=self.module._center(node["bounds"]),
            attempt_index=1,
            snapshot=snapshot,
        )
        self.assertEqual(trace["candidate_bounds"], (264, 984, 508, 1097))
        self.assertEqual(trace["click_point"], [386, 1040])
        self.assertEqual(trace["click_source"], "color_grid_text_node_bounds")
        self.assertTrue(trace["click_point_inside_candidate_bounds"])
        self.assertTrue(trace["candidate_grid_bounds_safe"])
        self.assertTrue(trace["parent_bounds_ambiguous"])
        self.assertIn(BLUE_LABEL, trace["parent_contained_color_labels"])
        self.assertTrue(trace["allowed_click"])

    def test_fullscreen_parent_is_not_used_when_text_node_bounds_are_unsafe(self):
        xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<hierarchy>
  <node text="" class="android.widget.LinearLayout" clickable="true" enabled="true" bounds="[0,0][1080,2400]">
    <node text="{BLACK_LABEL}" class="android.widget.TextView" clickable="false" enabled="true" bounds="[0,0][1080,2400]" />
    <node text="{BLUE_LABEL}" class="android.widget.TextView" clickable="false" enabled="true" bounds="[528,1122][772,1237]" />
  </node>
</hierarchy>"""
        snapshot = {
            "fresh_xml": xml,
            "nodes": [
                {"labels": [BLACK_LABEL], "bounds": (0, 0, 1080, 2400), "selected": False},
                {"labels": [BLUE_LABEL], "bounds": (528, 1122, 772, 1237), "selected": False},
            ],
        }

        self.assertIsNone(self.module._find_target_color_node(snapshot, BLACK))

    def test_non_clickable_snapshot_node_with_safe_grid_bounds_is_bindable(self):
        snapshot = {
            "nodes": [
                {"labels": [BLACK_LABEL], "bounds": (264, 984, 508, 1097), "selected": False, "clickable": False},
            ],
        }

        node = self.module._find_target_color_node(snapshot, BLACK)
        self.assertIsNotNone(node)
        self.assertEqual(node["bounds"], (264, 984, 508, 1097))
        self.assertEqual(node["color_click_strategy"], "color_grid_text_node_bounds")
        self.assertEqual(self.module._center(node["bounds"]), (386, 1040))


if __name__ == "__main__":
    unittest.main()
