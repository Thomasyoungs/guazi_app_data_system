import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from guazi_app_data_system.transient_popup_handler import (  # noqa: E402
    GUAZI_PUSH_NOTIFICATION_POPUP,
    GUAZI_PUSH_POPUP_CLOSE_FAILED,
    GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND,
    close_guazi_push_popup_from_snapshot,
    detect_guazi_push_notification_popup,
    find_guazi_push_popup_close_target,
    is_guazi_push_popup_forbidden_action,
)
from feishu_result_formatter import format_result_reply  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int) -> dict[str, object]:
        self.taps.append((x, y))
        return {"ok": True, "x": x, "y": y}


def _node(text: str, bounds: list[int], *, clickable: bool = False, enabled: bool = True, role: str = "") -> dict:
    return {
        "text": text,
        "labels": [text] if text else [],
        "bounds": bounds,
        "clickable": clickable,
        "enabled": enabled,
        "role": role,
        "package": "com.ganji.android.haoche_c",
    }


def _push_popup_snapshot(*, include_close: bool = True, recognized_page: str = "S11") -> dict:
    nodes = [
        _node("开启消息推送通知", [160, 560, 650, 625]),
        _node("选择想接收的通知类型", [160, 640, 760, 700]),
        _node("车源降价时通知我", [160, 780, 650, 850], clickable=True),
        _node("领取免费检测报告", [160, 880, 650, 950], clickable=True),
        _node("有同款新上车源通知我", [160, 980, 760, 1050], clickable=True),
        _node("立即开启", [250, 1230, 830, 1320], clickable=True),
    ]
    if include_close:
        nodes.append(_node("×", [895, 510, 975, 590], clickable=True))
    return {
        "foreground_package": "com.ganji.android.haoche_c",
        "xml_package": "com.ganji.android.haoche_c",
        "recognized_page": recognized_page,
        "visible_texts": [label for node in nodes for label in node["labels"]],
        "visible_blob": "\n".join(label for node in nodes for label in node["labels"]),
        "nodes": nodes,
        "screenshot_path": f"C:/tmp/{recognized_page}_push.png",
        "xml_path": f"C:/tmp/{recognized_page}_push.xml",
        "screen_width": 1080,
        "screen_height": 2400,
    }


def _non_popup_snapshot(stage: str) -> dict:
    return {
        "foreground_package": "com.ganji.android.haoche_c",
        "xml_package": "com.ganji.android.haoche_c",
        "recognized_page": stage,
        "visible_texts": [stage, "页面已恢复"],
        "visible_blob": f"{stage}\n页面已恢复",
        "nodes": [_node(stage, [100, 100, 300, 160])],
        "screenshot_path": f"C:/tmp/{stage}_resumed.png",
        "xml_path": f"C:/tmp/{stage}_resumed.xml",
    }


def _close_popup(snapshot: dict, *, stage: str = "S11") -> tuple[dict, FakeClient]:
    client = FakeClient()
    context = {"client": client, "task_id": "FS_TEST"}

    def capture_func(stem: str) -> dict:
        return _non_popup_snapshot(stage)

    def recognize_func(fresh_snapshot: dict) -> str | None:
        return fresh_snapshot.get("recognized_page")

    result = close_guazi_push_popup_from_snapshot(
        context,
        snapshot,
        capture_func=capture_func,
        recognize_func=recognize_func,
        current_stage=stage,
        capture_stem="unit",
        click_func=client.tap,
        sleep_func=lambda _: None,
    )
    return result, client


class GuaziPushNotificationPopupTest(unittest.TestCase):
    def test_detect_guazi_push_notification_popup_by_title_and_subtitle(self) -> None:
        snapshot = _push_popup_snapshot()
        detection = detect_guazi_push_notification_popup(snapshot, current_stage="S11")
        self.assertTrue(detection["popup_detected"])
        self.assertEqual(GUAZI_PUSH_NOTIFICATION_POPUP, detection["popup_type"])
        self.assertEqual("title_and_subtitle", detection["matched_rule"])

    def test_detect_guazi_push_notification_popup_by_options(self) -> None:
        snapshot = _push_popup_snapshot()
        snapshot["visible_texts"] = ["车源降价时通知我", "领取免费检测报告", "有同款新上车源通知我"]
        snapshot["visible_blob"] = "\n".join(snapshot["visible_texts"])
        snapshot["nodes"] = [_node(text, [100, 700 + i * 80, 700, 760 + i * 80]) for i, text in enumerate(snapshot["visible_texts"])]
        detection = detect_guazi_push_notification_popup(snapshot)
        self.assertTrue(detection["popup_detected"])
        self.assertEqual("three_options", detection["matched_rule"])

    def test_detect_guazi_push_notification_popup_by_enable_button(self) -> None:
        snapshot = _push_popup_snapshot()
        snapshot["visible_texts"] = ["开启消息推送通知", "立即开启"]
        snapshot["visible_blob"] = "\n".join(snapshot["visible_texts"])
        snapshot["nodes"] = [
            _node("开启消息推送通知", [160, 560, 650, 625]),
            _node("立即开启", [250, 1230, 830, 1320], clickable=True),
        ]
        detection = detect_guazi_push_notification_popup(snapshot)
        self.assertTrue(detection["popup_detected"])
        self.assertEqual("title_and_enable_now", detection["matched_rule"])

    def test_push_popup_allowed_action_is_close_x(self) -> None:
        target = find_guazi_push_popup_close_target(_push_popup_snapshot())
        self.assertTrue(target["target_found"])
        self.assertEqual("CLICK_POPUP_CLOSE_X", target["allowed_action"])
        self.assertEqual([935, 550], target["click_point"])

    def test_push_popup_forbids_click_enable_now(self) -> None:
        self.assertTrue(is_guazi_push_popup_forbidden_action("立即开启"))
        target = find_guazi_push_popup_close_target(_push_popup_snapshot())
        self.assertNotEqual([540, 1275], target["click_point"])

    def test_push_popup_forbids_click_options(self) -> None:
        for label in ("车源降价时通知我", "领取免费检测报告", "有同款新上车源通知我"):
            self.assertTrue(is_guazi_push_popup_forbidden_action(label))
        target = find_guazi_push_popup_close_target(_push_popup_snapshot())
        self.assertNotEqual([405, 815], target["click_point"])

    def test_push_popup_close_then_resume_s11(self) -> None:
        result, client = _close_popup(_push_popup_snapshot(recognized_page="S11"), stage="S11")
        self.assertTrue(result["popup_closed"])
        self.assertEqual("S11", result["resume_stage"])
        self.assertEqual([(935, 550)], client.taps)

    def test_push_popup_close_then_resume_s12(self) -> None:
        result, _client = _close_popup(_push_popup_snapshot(recognized_page="S12"), stage="S12")
        self.assertTrue(result["resume_success"])
        self.assertEqual("S12", result["resume_stage"])

    def test_push_popup_close_then_resume_s13(self) -> None:
        result, _client = _close_popup(_push_popup_snapshot(recognized_page="S13"), stage="S13")
        self.assertTrue(result["resume_success"])
        self.assertEqual("S13", result["resume_stage"])

    def test_push_popup_close_then_resume_s14(self) -> None:
        result, _client = _close_popup(_push_popup_snapshot(recognized_page="S14"), stage="S14")
        self.assertTrue(result["resume_success"])
        self.assertEqual("S14", result["resume_stage"])

    def test_push_popup_close_failure_after_two_attempts(self) -> None:
        client = FakeClient()
        context = {"client": client, "task_id": "FS_TEST"}
        snapshot = _push_popup_snapshot()

        def capture_func(stem: str) -> dict:
            return _push_popup_snapshot(recognized_page="S11")

        result = close_guazi_push_popup_from_snapshot(
            context,
            snapshot,
            capture_func=capture_func,
            recognize_func=lambda fresh: fresh.get("recognized_page"),
            current_stage="S11",
            capture_stem="unit",
            click_func=client.tap,
            sleep_func=lambda _: None,
        )
        self.assertFalse(result["popup_closed"])
        self.assertEqual(GUAZI_PUSH_POPUP_CLOSE_FAILED, result["stop_code"])
        self.assertEqual(2, result["popup_close_attempt_count"])
        self.assertEqual(2, len(client.taps))

    def test_push_popup_not_classified_as_app_environment_unavailable(self) -> None:
        result = format_result_reply(
            task_id="FS_TEST",
            status="FAILED",
            pricing_result=None,
            errors=[GUAZI_PUSH_POPUP_CLOSE_FAILED, "APP_ENVIRONMENT_UNAVAILABLE"],
        )
        self.assertIn("消息推送通知弹窗", result.text)
        self.assertNotIn("APP_ENVIRONMENT_UNAVAILABLE", result.text)
        self.assertNotIn("手机执行环境", result.text)

    def test_push_popup_not_classified_as_app_not_foreground(self) -> None:
        result = format_result_reply(
            task_id="FS_TEST",
            status="FAILED",
            pricing_result=None,
            errors=[GUAZI_PUSH_POPUP_CLOSE_FAILED, "APP_NOT_FOREGROUND"],
        )
        self.assertIn("消息推送通知弹窗", result.text)
        self.assertNotIn("APP_NOT_FOREGROUND", result.text)

    def test_push_popup_does_not_cancel_task_when_close_success(self) -> None:
        result, _client = _close_popup(_push_popup_snapshot(), stage="S11")
        self.assertTrue(result["popup_closed"])
        self.assertTrue(result["resume_success"])
        self.assertNotIn("cancel_task", result)
        self.assertNotEqual(GUAZI_PUSH_POPUP_CLOSE_FAILED, result.get("stop_code"))

    def test_push_popup_failure_feedback_is_accurate(self) -> None:
        result = format_result_reply(
            task_id="FS20260628_0001",
            status="FAILED",
            pricing_result=None,
            errors=[GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND],
        )
        self.assertIn("【本次定价未完成】FS20260628_0001", result.text)
        self.assertIn("瓜子 APP 弹出消息推送通知弹窗", result.text)
        for forbidden in ("adb", "uiautomator", "runner", "dispatcher", "traceback", "status.json", "APP_NOT_FOREGROUND"):
            self.assertNotIn(forbidden, result.text)

    def test_existing_startup_red_packet_popup_not_regressed(self) -> None:
        snapshot = {
            "foreground_package": "com.ganji.android.haoche_c",
            "visible_texts": ["恭喜获得红包", "立即领取", "×"],
            "visible_blob": "恭喜获得红包\n立即领取\n×",
            "nodes": [_node("恭喜获得红包", [100, 500, 600, 580]), _node("×", [900, 500, 980, 580], clickable=True)],
        }
        self.assertFalse(detect_guazi_push_notification_popup(snapshot)["popup_detected"])

    def test_existing_login_page_not_regressed(self) -> None:
        snapshot = {
            "foreground_package": "com.shuqing.tqaccountcenter",
            "visible_texts": ["登录", "手机号", "验证码"],
            "visible_blob": "登录\n手机号\n验证码",
            "nodes": [_node("登录", [100, 500, 600, 580])],
        }
        self.assertFalse(detect_guazi_push_notification_popup(snapshot)["popup_detected"])


if __name__ == "__main__":
    unittest.main()
