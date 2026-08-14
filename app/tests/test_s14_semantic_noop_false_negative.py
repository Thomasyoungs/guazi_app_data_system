import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402


class DummyTiming:
    def add(self, **kwargs):
        self.last = kwargs


def s14_snapshot(tab_label: str, *labels: str) -> dict:
    nodes = [
        {
            "labels": ["瓜子官方检测报告"],
            "bounds": [0, 0, 1080, 90],
            "selected": False,
            "clickable": False,
            "enabled": True,
        },
        {
            "labels": [tab_label],
            "bounds": [260, 950, 760, 1080],
            "selected": True,
            "clickable": True,
            "enabled": True,
        },
    ]
    visible_texts = ["瓜子官方检测报告", tab_label]
    y = 1800
    for label in labels:
        hidden = label.startswith("HIDDEN:")
        text = label.replace("HIDDEN:", "", 1)
        bounds = [0, 0, 0, 0] if hidden else [90, y, 920, y + 60]
        nodes.append(
            {
                "labels": [text],
                "bounds": bounds,
                "selected": False,
                "clickable": False,
                "enabled": True,
            }
        )
        if not hidden:
            visible_texts.append(text)
        y += 80
    return {
        "nodes": nodes,
        "visible_texts": visible_texts,
        "fresh_xml": "",
        "screenshot_path": "",
        "xml_path": "",
    }


class S14SemanticNoopFalseNegativeTest(unittest.TestCase):
    def test_tab_label_ai_detail_overrides_stale_first_line_for_right_rear_door_paint(self):
        snapshot = s14_snapshot(
            "右侧后门漆面(1/1)",
            "右后翼子板—钣金",
            "HIDDEN:右侧后门漆面—钣金",
            "AI详细解读【右侧后门漆面】异常细节",
        )
        tab = runtime._s14_selected_tab(snapshot)
        state = runtime._s14_semantic_state(snapshot, tab)
        context = {"damage_by_part": {}, "timing": DummyTiming()}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 4)

        self.assertTrue(state["stale_first_line_suspected"])
        self.assertTrue(state["ignore_raw_first_line_for_current_item"])
        self.assertEqual(state["current_item_binding_source"], "tab_label_ai_detail")
        self.assertEqual(record["normalized_part"], "右后门漆面")
        self.assertEqual(record["normalized_damage"], "钣金")
        self.assertTrue(record["saved_to_repair_items"])
        self.assertIn("右后门漆面", context["damage_by_part"])
        self.assertNotIn("右后翼子板", context["damage_by_part"])

    def test_tab_label_detail_overrides_stale_first_line_for_right_d_pillar_outer_paint(self):
        snapshot = s14_snapshot(
            "右D柱外侧漆面(1/1)",
            "右后翼子板—钣金",
            "HIDDEN:右D柱外侧漆面—钣金",
        )
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming()}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 5)

        self.assertTrue(record["stale_first_line_suspected"])
        self.assertTrue(record["ignore_raw_first_line_for_current_item"])
        self.assertEqual(record["current_item_binding_source"], "tab_label_detail")
        self.assertEqual(record["normalized_part"], "右D柱外侧漆面")
        self.assertEqual(record["normalized_damage"], "钣金")
        self.assertTrue(record["saved_to_repair_items"])
        self.assertIn("右D柱外侧漆面", context["damage_by_part"])
        self.assertNotIn("右后翼子板", context["damage_by_part"])

    def test_left_headlight_repair_trace_does_not_bind_to_stale_fender(self):
        snapshot = s14_snapshot(
            "左前大灯(1/1)",
            "右后翼子板—钣金",
            "HIDDEN:左前大灯—维修痕迹",
            "AI详细解读【左前大灯】异常细节",
        )
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming()}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 7)

        self.assertEqual(record["normalized_part"], "左前大灯")
        self.assertEqual(record["normalized_damage"], runtime.S14_NON_SCORING_DAMAGE)
        self.assertEqual(record["skipped_reason"], runtime.S14_NON_SCORING_DAMAGE)
        self.assertFalse(record["saved_to_repair_items"])
        self.assertNotIn("右后翼子板", context["damage_by_part"])
        self.assertTrue(context["unparsed_s14_items"])
        self.assertIn("non_scoring_or_rule_review", context["unparsed_s14_items"][-1]["suspected_reason"])

    def test_changed_page_repeated_key_records_unparsed_trace(self):
        context = {
            "s14_horizontal_swipes": [],
            "visited_s14_keys": ["右侧后门漆面(1/1)|右后翼子板—钣金|右后翼子板|钣金"],
            "current_reference": {},
        }
        trace = {
            "selected_tab": "右侧后门漆面(1/1)",
            "raw_first_line": "右后翼子板—钣金",
            "normalized_part": "右后翼子板",
            "normalized_damage": "钣金",
            "image_hash_changed": True,
            "xml_changed": True,
            "semantic_changed": True,
            "suspected_reason": ["stale_first_line", "repeated_s14_key_after_page_change"],
        }
        context.setdefault("unparsed_s14_items", []).append(trace)
        context.setdefault("current_reference", {}).setdefault("unparsed_s14_items", []).append(trace)

        self.assertEqual(context["unparsed_s14_items"][0]["selected_tab"], "右侧后门漆面(1/1)")
        self.assertIn("repeated_s14_key_after_page_change", context["unparsed_s14_items"][0]["suspected_reason"])

    def test_missing_repair_count_blocks_last_page_even_after_repeated_no_semantic_change(self):
        snapshot = s14_snapshot("右侧后门(1/1)", "右侧后门—钣金")
        tab = runtime._s14_selected_tab(snapshot)
        context = {
            "damage_by_part": {
                "右后翼子板": {"part": "右后翼子板", "normalized_damage": "钣金"},
                "右D柱覆盖面": {"part": "右D柱覆盖面", "normalized_damage": "钣金"},
                "右后门": {"part": "右后门", "normalized_damage": "钣金"},
            },
            "current_reference": {"repair_counts": {"副驾驶": 6}},
            "timing": DummyTiming(),
            "s14_no_semantic_change_count": 2,
        }
        record = runtime._s14_collect_current_image(context, snapshot, tab, 6)
        state = runtime._s14_semantic_state(snapshot, tab)

        gate = runtime.is_s14_last_page_reached(
            context,
            selected_tab=tab,
            semantic_state=state,
            horizontal_swipe_effective=False,
            next_signal={"s14_has_uncollected_next_condition_signal": False},
        )
        completion = runtime._repair_item_completion_state(context)

        self.assertTrue(record["saved_to_repair_items"])
        self.assertEqual(completion["missing_repair_count"], 3)
        self.assertFalse(gate["last_page_reached"])
        self.assertTrue(gate["last_page_blocked_by_missing_repair_count"])
        self.assertFalse(completion["reference_score_trustworthy"])
        self.assertFalse(completion["reference_score_usable_for_boundary"])
        self.assertTrue(completion["excluded_from_boundary"])


if __name__ == "__main__":
    unittest.main()
