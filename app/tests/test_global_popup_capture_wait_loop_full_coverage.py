import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from guazi_app_data_system.exception_handler import GuaziFlowError  # noqa: E402
from guazi_app_data_system.transient_popup_handler import (  # noqa: E402
    GUAZI_PUSH_NOTIFICATION_POPUP,
    GUAZI_PUSH_POPUP_CLOSE_FAILED,
    GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
    PUSH_POPUP_ENABLE_NOW,
    PUSH_POPUP_OPTIONS,
    PUSH_POPUP_SUBTITLE,
    PUSH_POPUP_TITLE,
)
from runtime_s10_to_s16_mainline import (  # noqa: E402
    _capture_with_global_popup_guard,
    _wait_for_page,
    _wait_for_page_with_global_popup_guard,
)


class FakeClient:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int) -> dict[str, object]:
        self.taps.append((x, y))
        return {"ok": True, "x": x, "y": y}


class FakeIssues:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record(self, code, state_id, message=None, context=None, resolution=None):
        record = {
            "code": code,
            "state_id": state_id,
            "message": message or code,
            "context": context or {},
            "resolution": resolution,
        }
        self.records.append(record)
        return record


def _node(text: str, bounds: list[int], *, clickable: bool = False, role: str = "") -> dict:
    return {
        "text": text,
        "labels": [text] if text else [],
        "bounds": bounds,
        "clickable": clickable,
        "enabled": True,
        "role": role,
        "package": "com.ganji.android.haoche_c",
    }


def _push_popup_snapshot(stage: str = "S11", *, include_close: bool = True) -> dict:
    nodes = [
        _node(PUSH_POPUP_TITLE, [160, 560, 650, 625]),
        _node(PUSH_POPUP_SUBTITLE, [160, 640, 760, 700]),
        _node(PUSH_POPUP_OPTIONS[0], [160, 780, 650, 850], clickable=True),
        _node(PUSH_POPUP_OPTIONS[1], [160, 880, 650, 950], clickable=True),
        _node(PUSH_POPUP_OPTIONS[2], [160, 980, 760, 1050], clickable=True),
        _node(PUSH_POPUP_ENABLE_NOW, [250, 1230, 830, 1320], clickable=True),
    ]
    if include_close:
        nodes.append(_node("×", [895, 510, 975, 590], clickable=True))
    labels = [label for node in nodes for label in node["labels"]]
    return {
        "foreground_package": "com.ganji.android.haoche_c",
        "xml_package": "com.ganji.android.haoche_c",
        "recognized_page": stage,
        "visible_texts": labels,
        "visible_blob": "\n".join(labels),
        "nodes": nodes,
        "screenshot_path": f"C:/tmp/{stage}_push.png",
        "xml_path": f"C:/tmp/{stage}_push.xml",
        "screen_width": 1080,
        "screen_height": 2400,
    }


def _normal_snapshot(stage: str) -> dict:
    return {
        "foreground_package": "com.ganji.android.haoche_c",
        "xml_package": "com.ganji.android.haoche_c",
        "recognized_page": stage,
        "visible_texts": [stage, "normal_page"],
        "visible_blob": f"{stage}\nnormal_page",
        "nodes": [_node(stage, [120, 140, 420, 210])],
        "screenshot_path": f"C:/tmp/{stage}_normal.png",
        "xml_path": f"C:/tmp/{stage}_normal.xml",
    }


def _context(stage: str = "S11") -> tuple[dict, FakeClient, FakeIssues]:
    client = FakeClient()
    issues = FakeIssues()
    return (
        {
            "client": client,
            "recognizer": object(),
            "issues": issues,
            "task_id": "FS_TEST",
            "current_reference": {"reference_index": 1},
        },
        client,
        issues,
    )


def _patch_recognizer():
    return patch(
        "runtime_s10_to_s16_mainline._recognize_mainline_page",
        side_effect=lambda _recognizer, snapshot, **_kwargs: snapshot.get("recognized_page"),
    )


class GlobalPopupCaptureWaitLoopFullCoverageTest(unittest.TestCase):
    def test_wait_for_page_closes_popup_recaptures_and_returns_real_page(self) -> None:
        context, client, _issues = _context("S11")
        captures = [_push_popup_snapshot("S11"), _normal_snapshot("S11")]

        with _patch_recognizer(), patch("runtime_s10_to_s16_mainline._capture", side_effect=lambda *_args: captures.pop(0)), patch(
            "runtime_s10_to_s16_mainline.time.sleep",
            side_effect=lambda *_args: None,
        ):
            snapshot, _elapsed = _wait_for_page_with_global_popup_guard(
                context,
                "S11",
                "s11_wait",
                current_stage="S11",
                timeout_s=1.0,
                interval_s=0.01,
            )

        self.assertEqual("S11", snapshot["recognized_page"])
        self.assertEqual(GUAZI_PUSH_NOTIFICATION_POPUP, snapshot["popup_type"])
        self.assertTrue(snapshot["popup_closed"])
        self.assertTrue(snapshot["popup_close_verified"])
        self.assertTrue(snapshot["popup_guard_recaptured"])
        self.assertTrue(snapshot["popup_guard_blocked_underlying_click"])
        self.assertFalse(snapshot["popup_detected_after_close"])
        self.assertEqual("wait_for_page", snapshot["popup_guard_call_site"])
        self.assertEqual([(935, 550)], client.taps)

    def test_wait_for_page_requires_context_so_capture_is_not_naked(self) -> None:
        with patch("runtime_s10_to_s16_mainline._capture", side_effect=AssertionError("raw capture forbidden")):
            with self.assertRaises(ValueError):
                _wait_for_page(FakeClient(), object(), "S11", "raw_wait")

    def test_s11_wait_loop_popup_closes_and_continues_s11(self) -> None:
        context, client, _issues = _context("S11")
        captures = [_push_popup_snapshot("S11"), _normal_snapshot("S11")]

        with _patch_recognizer(), patch("runtime_s10_to_s16_mainline._capture", side_effect=lambda *_args: captures.pop(0)), patch(
            "runtime_s10_to_s16_mainline.time.sleep",
            side_effect=lambda *_args: None,
        ):
            snapshot = _capture_with_global_popup_guard(
                context,
                "s11_report_entry_wait",
                current_stage="S11_REPORT_SEARCH",
                call_site="s11_report_entry_wait_loop",
            )

        self.assertEqual("S11", snapshot["recognized_page"])
        self.assertEqual("s11_report_entry_wait_loop", snapshot["popup_guard_call_site"])
        self.assertEqual([(935, 550)], client.taps)

    def test_s12_s13_s14_s15_s16_capture_popups_use_guard(self) -> None:
        for stage in ("S12", "S13", "S14", "S15", "S16"):
            with self.subTest(stage=stage):
                context, client, _issues = _context(stage)
                captures = [_push_popup_snapshot(stage), _normal_snapshot(stage)]
                with _patch_recognizer(), patch(
                    "runtime_s10_to_s16_mainline._capture",
                    side_effect=lambda *_args, _captures=captures: _captures.pop(0),
                ), patch("runtime_s10_to_s16_mainline.time.sleep", side_effect=lambda *_args: None):
                    snapshot = _capture_with_global_popup_guard(
                        context,
                        f"{stage.lower()}_fresh",
                        current_stage=stage,
                        call_site=f"{stage.lower()}_capture_loop",
                    )
                self.assertEqual(stage, snapshot["recognized_page"])
                self.assertTrue(snapshot["popup_closed"])
                self.assertEqual([(935, 550)], client.taps)

    def test_missing_close_x_stops_and_never_clicks_enable_now(self) -> None:
        context, client, issues = _context("S13")
        with _patch_recognizer(), patch(
            "runtime_s10_to_s16_mainline._capture",
            return_value=_push_popup_snapshot("S13", include_close=False),
        ):
            with self.assertRaises(GuaziFlowError) as caught:
                _capture_with_global_popup_guard(
                    context,
                    "s13_mid_capture",
                    current_stage="S13",
                    call_site="s13_region_capture",
                )

        self.assertEqual(GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND, caught.exception.code)
        self.assertEqual([], client.taps)
        self.assertEqual(1, len(issues.records))
        evidence = issues.records[0]["context"]["guazi_push_popup_close_evidence"]
        self.assertTrue(evidence["popup_guard_blocked_underlying_click"])
        self.assertFalse(evidence["popup_close_attempted"])

    def test_click_x_then_popup_still_present_stops_without_continuing(self) -> None:
        context, client, issues = _context("S14")
        captures = [_push_popup_snapshot("S14"), _push_popup_snapshot("S14"), _push_popup_snapshot("S14")]
        with _patch_recognizer(), patch("runtime_s10_to_s16_mainline._capture", side_effect=lambda *_args: captures.pop(0)), patch(
            "runtime_s10_to_s16_mainline.time.sleep",
            side_effect=lambda *_args: None,
        ):
            with self.assertRaises(GuaziFlowError) as caught:
                _capture_with_global_popup_guard(
                    context,
                    "s14_return_capture",
                    current_stage="S14",
                    call_site="s14_return_capture",
                )

        self.assertEqual(GUAZI_PUSH_POPUP_CLOSE_FAILED, caught.exception.code)
        self.assertEqual([(935, 550), (935, 550)], client.taps)
        evidence = issues.records[0]["context"]["guazi_push_popup_close_evidence"]
        self.assertTrue(evidence["popup_close_attempted"])
        self.assertFalse(evidence["popup_close_verified"])
        self.assertTrue(evidence["popup_detected_after_close"])

    def test_underlying_click_is_blocked_while_popup_exists(self) -> None:
        context, _client, _issues = _context("S10")
        captures = [_push_popup_snapshot("S10"), _normal_snapshot("S10")]
        with _patch_recognizer(), patch("runtime_s10_to_s16_mainline._capture", side_effect=lambda *_args: captures.pop(0)), patch(
            "runtime_s10_to_s16_mainline.time.sleep",
            side_effect=lambda *_args: None,
        ):
            snapshot = _capture_with_global_popup_guard(
                context,
                "s10_reference_card_wait",
                current_stage="S10",
                call_site="s10_reference_click_wait",
            )

        self.assertTrue(snapshot["popup_guard_blocked_underlying_click"])
        self.assertEqual("S10", snapshot["popup_guard_resume_stage"])

    def test_no_popup_normal_page_unaffected(self) -> None:
        context, client, _issues = _context("S15")
        with _patch_recognizer(), patch("runtime_s10_to_s16_mainline._capture", return_value=_normal_snapshot("S15")):
            snapshot = _capture_with_global_popup_guard(
                context,
                "s15_pricing",
                current_stage="S15",
                call_site="s15_pricing_capture",
            )

        self.assertFalse(snapshot["popup_detected"])
        self.assertFalse(snapshot["popup_guard_blocked_underlying_click"])
        self.assertEqual([], client.taps)


if __name__ == "__main__":
    unittest.main()
