import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "src" / "guazi_app_data_system" / "main.py"
SCRIPT_PATH = ROOT / "scripts" / "runtime_s10_to_s16_mainline.py"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def kw(*parts: str) -> str:
    return "".join(parts)


class RealMainlineExecutorTest(unittest.TestCase):
    def test_device_mode_does_not_call_run_simulation(self):
        source = read_text(MAIN_PATH)
        self.assertIn('if args.mode == "device":', source)
        device_branch = source.split('if args.mode == "device":', 1)[1]
        device_branch = device_branch.split("\n\n    result = run_simulation", 1)[0]
        self.assertNotIn("run_simulation(runtime", device_branch)

    def test_missing_real_executor_reports_executor_missing(self):
        source = read_text(MAIN_PATH)
        self.assertIn("PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE", source)
        self.assertIn("_load_real_device_mainline_runner", source)

    def test_runtime_s10_to_s16_mainline_script_exists(self):
        self.assertTrue(SCRIPT_PATH.exists())

    def test_s10_to_s16_mainline_has_required_states(self):
        source = read_text(SCRIPT_PATH)
        for func_name in [
            "handle_s10",
            "handle_s11",
            "handle_s12",
            "handle_s13",
            "handle_s14",
            "handle_s15",
            "handle_s16",
            "run_s10_to_s16_mainline",
        ]:
            self.assertIn(f"def {func_name}(", source)

    def test_s14_fast_loop_contract_preserved(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn("_parse_first_line_damage", source)
        self.assertIn("s14_image_horizontal_swipe", source)
        self.assertIn("S14_ONLY_ALLOWED_ACTION_IMAGE_HORIZONTAL_SWIPE", source)
        self.assertNotIn("tap_ai_detail", source)
        self.assertNotIn("collect_image_description", source)
        self.assertNotIn("tap photo", source.lower())
        self.assertNotIn("second line", source.lower())

    def test_s15_is_internal_state_not_app_page(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn('if state == "S15":', source)
        self.assertIn("handle_s15(context)", source)
        self.assertNotIn("tap_s15", source)

    def test_old_keywords_still_zero(self):
        targets = [
            ROOT / "src",
            ROOT / "config",
            ROOT / "knowledge_base" / "solutions.jsonl",
            ROOT / "tests",
        ]
        keywords = [
            kw("wake", "_device_with_", "menu", "_key"),
            kw("menu", "_key_", "once"),
            kw("SCREEN_", "WAKE_", "FAILED"),
            kw("NON_SECURE_", "KEYGUARD_", "SWIPE_", "REQUIRED"),
            kw("SCREEN_", "OFF_", "WAKE_", "REQUIRED"),
            kw("NON_SECURE_", "KEYGUARD_", "SWIPE_", "FAILED"),
            kw("APP_NOT_", "FOREGROUND_", "RECOVERY_", "REQUIRED"),
            kw("discover_", "target_", "app"),
            kw("launch_", "activity_", "component"),
            kw("tap_", "bottom_", "buy_", "car"),
            kw("click_", "bottom_", "home_", "tab"),
            kw("collect_", "panel_", "and_", "damage_", "type"),
            kw("return_", "to_", "s10_", "after_", "collect"),
            kw("SOL-", "APP-", "LABEL-", "UNREADABLE"),
            kw("SOL-", "TARGET-", "APP-", "VERIFIED"),
            kw("SOL-", "STARTUP-", "LANDS-", "ON-", "MY-", "TAB"),
            kw("SOL-", "SYSTEM-", "OVERLAY-", "OR-", "KEYGUARD-", "BLOCKING-", "APP"),
            kw("SOL-", "RUNTIME-", "SCREEN-", "WAKE-", "MECHANISM"),
            kw("SOL-", "RUNTIME-", "WAKE-", "SWIPE-", "OPEN-", "GUAZI-", "AND-", "REFRESH"),
            kw("APP_", "LABEL_", "UNREADABLE"),
            kw("APP_", "IDENTITY_", "NOT_", "FOUND"),
            kw("APP_", "IDENTITY_", "AMBIGUOUS"),
        ]
        corpus: list[str] = []
        for target in targets:
            if target.is_file():
                corpus.append(read_text(target))
                continue
            for path in target.rglob("*"):
                if path.is_file() and path.suffix in {".py", ".yaml", ".jsonl"}:
                    corpus.append(read_text(path))
        blob = "\n".join(corpus)
        for keyword in keywords:
            self.assertNotRegex(blob, re.escape(keyword))


if __name__ == "__main__":
    unittest.main()
