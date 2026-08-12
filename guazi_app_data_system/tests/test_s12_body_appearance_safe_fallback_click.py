import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from guazi_app_data_system.exception_handler import GuaziFlowError  # noqa: E402


def node(text: str, bounds: tuple[int, int, int, int], *, selected: bool = False) -> dict:
    return {
        "text": text,
        "content_desc": "",
        "labels": [text],
        "bounds": bounds,
        "clickable": False,
        "enabled": True,
        "selected": selected,
        "resource_id": "",
        "package": runtime.GUAZI_PACKAGE,
        "class_name": "android.widget.TextView",
    }


def snapshot(nodes: list[dict], *, name: str = "s12") -> dict:
    visible_texts = [str(item.get("text") or "") for item in nodes]
    return {
        "nodes": nodes,
        "visible_texts": visible_texts,
        "visible_blob": "\n".join(visible_texts),
        "foreground_package": runtime.GUAZI_PACKAGE,
        "xml_package": runtime.GUAZI_PACKAGE,
        "screenshot_path": f"{name}.png",
        "xml_path": f"{name}.xml",
    }


def s12_top_tab_snapshot() -> dict:
    return snapshot(
        [
            node("瓜子官方检测报告", (0, 0, 1080, 90)),
            node("保险理赔记录", (70, 120, 360, 180)),
            node("理赔次数", (70, 210, 220, 270)),
            node("0次", (230, 210, 300, 270)),
            node("最大金额", (70, 300, 220, 360)),
            node("0元", (230, 300, 300, 360)),
            node("重大问题排查", (80, 420, 310, 510)),
            node("车身外观", (380, 420, 560, 510)),
            node("内饰及配置", (640, 420, 850, 510)),
        ],
        name="s12_top_tab",
    )


def s12_body_section_snapshot() -> dict:
    nodes = [
        node("瓜子官方检测报告", (0, 0, 1080, 90)),
        node("车身外观", (22, 1180, 1058, 1260)),
        node(runtime.S13_REGION_ORDER[0], (68, 1330, 250, 1410)),
        node(runtime.S13_REGION_ORDER[1], (300, 1330, 450, 1410)),
        node(runtime.S13_REGION_ORDER[2], (500, 1330, 700, 1410)),
        node(runtime.S13_REGION_ORDER[3], (760, 1330, 920, 1410)),
        node(f"{runtime.S13_REGION_ORDER[0]}深度检测：", (88, 1510, 420, 1580)),
        node("历史修复", (480, 1510, 650, 1580)),
        node("0", (660, 1510, 690, 1580)),
    ]
    return snapshot(nodes, name="s12_body_section")


def s12_body_detection_items_without_region_proof_snapshot() -> dict:
    nodes = [
        node("瓜子官方检测报告", (0, 0, 1080, 90)),
        node("车身外观", (22, 1180, 1058, 1260)),
        node("车身外观良好", (88, 1370, 420, 1440)),
        node(f"{runtime.S13_REGION_ORDER[0]}深度检测：", (88, 1510, 420, 1580)),
    ]
    return snapshot(nodes, name="s12_body_no_s13_proof")


def s12_after_fallback_success_snapshot() -> dict:
    nodes = [
        node("瓜子官方检测报告", (0, 0, 1080, 90)),
        node("车身外观", (380, 120, 560, 210), selected=True),
        node(runtime.S13_REGION_ORDER[0], (68, 720, 250, 810)),
        node(runtime.S13_REGION_ORDER[1], (300, 720, 450, 810)),
        node(runtime.S13_REGION_ORDER[2], (500, 720, 700, 810)),
        node(runtime.S13_REGION_ORDER[3], (760, 720, 920, 810)),
        node(f"{runtime.S13_REGION_ORDER[0]}深度检测：", (88, 900, 420, 970)),
    ]
    return snapshot(nodes, name="s12_after_fallback_success")


class DummyMachine:
    def assert_action_allowed(self, *_args, **_kwargs):
        return None


class DummyTiming:
    def __init__(self):
        self.records = []

    def add(self, **kwargs):
        self.records.append(kwargs)


class DummyIssues:
    def record(self, code, _page, message, context, _severity):
        return {"code": code, "message": message, "context": context}


class DummyRecognizer:
    def recognize(self, *_args, **_kwargs):
        return None


class DummyClient:
    def __init__(self):
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int):
        self.taps.append((x, y))


def context() -> dict:
    return {
        "recognizer": DummyRecognizer(),
        "issues": DummyIssues(),
        "machine": DummyMachine(),
        "timing": DummyTiming(),
        "client": DummyClient(),
        "current_reference": {},
    }


class S12BodyAppearanceSafeFallbackClickTest(unittest.TestCase):
    def test_section_already_reached_skips_forced_tab_click_and_continues(self):
        ctx = context()
        current = s12_body_section_snapshot()

        with patch.object(runtime, "_maybe_close_guazi_push_popup_and_resume", side_effect=lambda _ctx, snap, **_kw: snap), \
            patch.object(runtime, "_ensure_page", return_value=None), \
            patch.object(runtime, "_extract_claim_count_with_candidates", return_value=(0, [])), \
            patch.object(runtime, "_extract_max_amount_with_candidates", return_value=(0.0, [])), \
            patch.object(runtime, "_find_body_appearance_tab_node", return_value=None), \
            patch.object(runtime, "_find_body_appearance_tab_after_controlled_scroll", side_effect=AssertionError("must not scroll after section proof")), \
            patch.object(runtime, "contract_execute_click", side_effect=AssertionError("must not click after section proof")):
            next_state, next_snapshot = runtime.handle_s12(ctx, current)

        self.assertEqual("S13", next_state)
        self.assertIs(next_snapshot, current)
        click_trace = ctx["current_reference"]["s12_body_appearance_click"]
        self.assertFalse(click_trace["click_attempted"])
        self.assertEqual("SECTION_ALREADY_REACHED", click_trace["body_appearance_tab_click_skipped_reason"])
        self.assertTrue(click_trace["s12_to_s13_region_proof"]["s12_to_s13_proof_confirmed"])

    def test_body_detection_items_alone_do_not_allow_s13_transition(self):
        ctx = context()
        current = s12_body_detection_items_without_region_proof_snapshot()

        with patch.object(runtime, "_maybe_close_guazi_push_popup_and_resume", side_effect=lambda _ctx, snap, **_kw: snap), \
            patch.object(runtime, "_ensure_page", return_value=None), \
            patch.object(runtime, "_extract_claim_count_with_candidates", return_value=(0, [])), \
            patch.object(runtime, "_extract_max_amount_with_candidates", return_value=(0.0, [])), \
            patch.object(runtime, "_find_body_appearance_tab_node", return_value=None), \
            patch.object(runtime, "_capture_with_global_popup_guard", return_value=current), \
            patch.object(runtime, "_controlled_scroll_towards_history_repair", return_value=(current, 0, False)), \
            patch.object(runtime, "contract_execute_click", side_effect=AssertionError("must not click body tab from body section")):
            with self.assertRaises(GuaziFlowError) as raised:
                runtime.handle_s12(ctx, current)

        self.assertEqual(runtime.S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED, raised.exception.code)
        click_trace = ctx["current_reference"]["s12_body_appearance_click"]
        self.assertFalse(click_trace["click_attempted"])
        self.assertIn(
            click_trace["body_appearance_tab_click_skipped_reason"],
            {"SECTION_ALREADY_REACHED", "SECTION_REACHED_WITHOUT_S13_REGION_PROOF"},
        )
        proof = ctx["current_reference"]["s12_to_s13_region_proof"]
        self.assertFalse(proof["s12_to_s13_proof_confirmed"])
        self.assertFalse(proof["s12_to_s13_transition_allowed"])

    def test_top_text_bounds_safe_fallback_clicks_once_and_requires_fresh_proof(self):
        ctx = context()
        current = s12_top_tab_snapshot()
        fresh = s12_after_fallback_success_snapshot()

        with patch.object(runtime, "_maybe_close_guazi_push_popup_and_resume", side_effect=lambda _ctx, snap, **_kw: snap), \
            patch.object(runtime, "_ensure_page", return_value=None), \
            patch.object(runtime, "_extract_claim_count_with_candidates", return_value=(0, [])), \
            patch.object(runtime, "_extract_max_amount_with_candidates", return_value=(0.0, [])), \
            patch.object(runtime, "_find_body_appearance_tab_node", return_value=None), \
            patch.object(runtime, "_find_body_appearance_tab_after_controlled_scroll", side_effect=AssertionError("fallback should run before scroll")), \
            patch.object(runtime, "_capture_with_global_popup_guard", return_value=fresh):
            next_state, next_snapshot = runtime.handle_s12(ctx, current)

        self.assertEqual("S13", next_state)
        self.assertIs(next_snapshot, fresh)
        self.assertEqual([(470, 465)], ctx["client"].taps)
        click_trace = ctx["current_reference"]["s12_body_appearance_click"]
        self.assertEqual(runtime.S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK, click_trace["fallback_action"])
        self.assertEqual("body_appearance_text_node_bounds", click_trace["click_source"])
        self.assertTrue(click_trace["post_click_verify"]["safe_fallback_verify_success"])
        self.assertTrue(click_trace["s12_to_s13_region_proof"]["s12_to_s13_proof_confirmed"])

    def test_safe_fallback_verify_failure_gets_specific_stop_code(self):
        ctx = context()
        current = s12_top_tab_snapshot()
        fresh_without_proof = snapshot([node("保险理赔记录", (80, 120, 360, 180))], name="s12_no_body_proof")

        with patch.object(runtime, "_maybe_close_guazi_push_popup_and_resume", side_effect=lambda _ctx, snap, **_kw: snap), \
            patch.object(runtime, "_ensure_page", return_value=None), \
            patch.object(runtime, "_extract_claim_count_with_candidates", return_value=(0, [])), \
            patch.object(runtime, "_extract_max_amount_with_candidates", return_value=(0.0, [])), \
            patch.object(runtime, "_find_body_appearance_tab_node", return_value=None), \
            patch.object(runtime, "_find_body_appearance_tab_after_controlled_scroll", side_effect=AssertionError("fallback should run before scroll")), \
            patch.object(runtime, "_capture_with_global_popup_guard", return_value=fresh_without_proof):
            with self.assertRaises(GuaziFlowError) as raised:
                runtime.handle_s12(ctx, current)

        self.assertEqual(runtime.S12_BODY_APPEARANCE_SAFE_FALLBACK_CLICK_VERIFY_FAILED, raised.exception.code)
        self.assertEqual([(470, 465)], ctx["client"].taps)

    def test_zero_bounds_body_text_is_not_clicked(self):
        current = snapshot(
            [
                node("重大问题排查", (80, 420, 310, 510)),
                node("车身外观", (0, 0, 0, 0)),
                node("内饰及配置", (640, 420, 850, 510)),
            ],
            name="s12_zero_bounds",
        )

        self.assertIsNone(runtime._find_s12_body_appearance_safe_fallback_click_target(current))

    def test_interior_tab_is_not_used_as_body_appearance(self):
        current = snapshot(
            [
                node("重大问题排查", (80, 420, 310, 510)),
                node("内饰及配置", (640, 420, 850, 510)),
            ],
            name="s12_interior_only",
        )

        self.assertIsNone(runtime._find_s12_body_appearance_safe_fallback_click_target(current))

    def test_tab_row_estimate_requires_body_text_and_safe_row(self):
        current = snapshot(
            [
                node("瓜子官方检测报告", (0, 0, 1080, 90)),
                node("重大问题排查", (80, 420, 310, 510)),
                node("内饰及配置", (640, 420, 850, 510)),
                node("检测报告页底部占位", (0, 2300, 100, 2360)),
            ],
            name="s12_estimate",
        )
        current["visible_texts"].append("车身外观")
        current["visible_blob"] = "\n".join(current["visible_texts"])

        target = runtime._find_s12_body_appearance_safe_fallback_click_target(current)

        self.assertIsNotNone(target)
        self.assertEqual("body_appearance_tab_row_estimated_safe_center", target["click_source"])
        self.assertEqual((470, 465), target["click_point"])

    def test_s13_count_failure_code_distinguishes_missing_headers_from_binding_failure(self):
        counts = {region: None for region in runtime.S13_REGION_ORDER}
        debug = {
            "regions": {
                region: {"not_confirmed_reason": "region_header_not_found"}
                for region in runtime.S13_REGION_ORDER
            }
        }
        self.assertEqual(runtime.S13_REGION_HEADERS_NOT_FOUND, runtime._s13_region_count_failure_code(counts, debug, runtime.S13_REGION_ORDER[0]))

        partial_counts = {region: 0 for region in runtime.S13_REGION_ORDER}
        partial_counts[runtime.S13_REGION_ORDER[1]] = None
        partial_debug = {"regions": {runtime.S13_REGION_ORDER[1]: {"not_confirmed_reason": "history_repair_count_node_not_bound"}}}
        self.assertEqual(
            runtime.S13_REGION_HISTORY_COUNT_BINDING_FAILED,
            runtime._s13_region_count_failure_code(partial_counts, partial_debug, runtime.S13_REGION_ORDER[1]),
        )


if __name__ == "__main__":
    unittest.main()
