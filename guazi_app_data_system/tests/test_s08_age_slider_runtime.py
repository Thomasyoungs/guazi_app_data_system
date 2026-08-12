import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_s08_set_age_slider import (  # noqa: E402
    detect_dual_handle_snapshot,
    find_handle_candidates,
    right_handle_overlap_target_coordinate,
    target_coordinate_from_slider,
)


class S08AgeSliderRuntimeTest(unittest.TestCase):
    def test_visual_handle_filter_ignores_background_list_nodes(self):
        track_bounds = [279, 1235, 1192, 1274]
        nodes = [
            {"bounds": [650, 1235, 773, 1387], "labels": []},
            {"bounds": [1053, 1235, 1176, 1387], "labels": []},
            {"bounds": [429, 1261, 536, 1319], "labels": ["10.15"]},
            {"bounds": [1014, 1261, 1196, 1316], "labels": ["首付1.02万"]},
            {"bounds": [429, 1371, 1166, 1498], "labels": ["大众 帕萨特 2020款"]},
            {"bounds": [1027, 1235, 1192, 1274], "labels": ["不限"]},
            {"bounds": [279, 1235, 1192, 1274], "labels": []},
        ]

        candidates = find_handle_candidates(nodes, track_bounds)

        self.assertEqual(candidates, [[650, 1235, 773, 1387], [1053, 1235, 1176, 1387]])

    def test_dual_snapshot_records_actual_handle_centers(self):
        slider_snapshot = {
            "found": True,
            "slider_bounds": [279, 1235, 1192, 1387],
            "track_bounds": [279, 1235, 1192, 1274],
        }
        nodes = [
            {"bounds": [624, 1235, 789, 1274], "labels": ["6"]},
            {"bounds": [1027, 1235, 1192, 1274], "labels": ["不限"]},
            {"bounds": [650, 1235, 773, 1387], "labels": []},
            {"bounds": [1053, 1235, 1176, 1387], "labels": []},
            {"bounds": [429, 1261, 536, 1319], "labels": ["10.15"]},
        ]

        snapshot = detect_dual_handle_snapshot(nodes, slider_snapshot)

        self.assertEqual(snapshot["left_handle_actual_center"], [711, 1311])
        self.assertEqual(snapshot["right_handle_actual_center"], [1114, 1311])
        self.assertFalse(snapshot["handle_physical_overlap"])

    def test_right_drag_target_is_overlap_center_not_tick_text_center(self):
        slider_snapshot = {
            "left_handle_actual_bounds": [650, 1235, 773, 1387],
            "right_handle_actual_bounds": [1053, 1235, 1176, 1387],
            "track_bounds": [279, 1235, 1192, 1274],
            "tick_nodes": [
                {"value": 6, "bounds": [624, 1235, 789, 1274], "center": [706, 1254]},
            ],
        }

        tick = target_coordinate_from_slider(slider_snapshot, 6)
        overlap = right_handle_overlap_target_coordinate(slider_snapshot)

        self.assertEqual(tick["target_coordinate"], [706, 1254])
        self.assertEqual(overlap["right_handle_overlap_target_coordinate"], [711, 1311])
        self.assertNotEqual(tick["target_coordinate"], overlap["right_handle_overlap_target_coordinate"])
        self.assertEqual(overlap["right_handle_target_coordinate_source"], "left_handle_physical_center")


if __name__ == "__main__":
    unittest.main()
