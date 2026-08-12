import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_result_formatter import format_result_reply  # noqa: E402


class S13FourRegionAllZeroExitTest(unittest.TestCase):
    def _context(self):
        return {"current_reference_index": 3, "current_reference": {}}

    def _record(self, context, region, count):
        return runtime._record_s13_region_count(
            context,
            region,
            count,
            counts_summary={region: count},
            counts_debug={"regions": {region: {"bind_reason": "unit_fixture"}}},
            snapshot={"screenshot_path": f"{region}.png", "xml_path": f"{region}.xml"},
        )

    def test_four_regions_zero_persist_all_zero_exit_state(self):
        context = self._context()

        for region in runtime.S13_REGION_ORDER:
            state = self._record(context, region, 0)

        self.assertTrue(state["all_regions_checked"])
        self.assertTrue(state["s13_all_zero"])
        self.assertEqual(state["s13_total_repair_count"], 0)
        self.assertEqual(state["completed_regions"], runtime.S13_REGION_ORDER)
        self.assertEqual(state["visited_regions"], runtime.S13_REGION_ORDER)
        self.assertEqual(state["s13_region_scan_exit_reason"], "ALL_REGIONS_ZERO")
        self.assertTrue(context["current_reference"]["s13_region_scan_state_persisted"])
        self.assertEqual(context["current_reference"]["s13_region_history_count_bindings"], {region: 0 for region in runtime.S13_REGION_ORDER})

        completion = runtime._store_repair_item_completion_state(context)
        self.assertTrue(completion["all_regions_checked"])
        self.assertTrue(completion["s13_all_zero"])
        self.assertEqual(completion["s13_total_repair_count"], 0)
        self.assertEqual(completion["s15_entry_reason"], "NO_HISTORY_REPAIR_COUNT_S14_NOT_REQUIRED")

    def test_only_last_region_zero_does_not_pass_all_zero(self):
        context = self._context()
        last_region = runtime.S13_REGION_ORDER[-1]
        context["current_reference"]["s13_region_history_count_bindings"] = {last_region: 0}

        state = runtime._s13_region_scan_state(context)

        self.assertFalse(state["all_regions_checked"])
        self.assertFalse(state["s13_all_zero"])
        self.assertEqual(state["completed_regions"], [last_region])
        self.assertIsNone(state["s13_region_history_count_bindings"][runtime.S13_REGION_ORDER[0]])

    def test_completed_region_zero_is_not_cleared_by_later_null_snapshot(self):
        context = self._context()
        first_region = runtime.S13_REGION_ORDER[0]
        second_region = runtime.S13_REGION_ORDER[1]
        self._record(context, first_region, 0)

        merged = runtime._merge_s13_region_history_count_bindings(
            context["current_reference"]["s13_region_history_count_bindings"],
            {first_region: None, second_region: 0},
        )

        self.assertEqual(merged[first_region], 0)
        self.assertEqual(merged[second_region], 0)

    def test_four_region_loop_guard_blocks_completed_nonzero_restart(self):
        context = self._context()
        for index, region in enumerate(runtime.S13_REGION_ORDER):
            self._record(context, region, 1 if index == 0 else 0)

        guard = runtime._s13_four_region_loop_guard(context, runtime.S13_REGION_ORDER[0])

        self.assertTrue(guard["blocked"])
        self.assertEqual(guard["stop_code"], "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED")
        self.assertTrue(guard["all_regions_checked"])
        self.assertFalse(guard["s13_all_zero"])

    def test_positive_region_marks_first_positive_exit_reason(self):
        context = self._context()
        state = self._record(context, runtime.S13_REGION_ORDER[0], 2)

        self.assertEqual(state["s13_region_scan_exit_reason"], "FIRST_POSITIVE_REGION_FOUND")
        self.assertEqual(context["current_reference"]["repair_counts"][runtime.S13_REGION_ORDER[0]], 2)

    def test_s13_count_not_confirmed_feedback_is_not_phone_environment(self):
        reply = format_result_reply(
            task_id="FS_TEST",
            status="FAILED",
            pricing_result=None,
            run_meta={"error_code": "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"},
            errors=["S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED"],
        )

        self.assertIn("S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED", reply.warnings)
        for forbidden in ("未开始", "手机执行环境", "未成功打开到前台", "APP_NOT_FOREGROUND", "adb", "uiautomator"):
            self.assertNotIn(forbidden, reply.text)


if __name__ == "__main__":
    unittest.main()
