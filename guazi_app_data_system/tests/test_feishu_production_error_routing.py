import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.admin_intervention_router import (
    ADMIN_RECOVERY_COMMANDS,
    INTERNAL_BUSINESS_FORBIDDEN_TERMS,
    detect_admin_recovery_command,
    format_admin_intervention_reply,
    format_business_system_processing_reply,
)
from scripts.feishu_gateway import handle_event, handle_explicit_confirm
from scripts.feishu_group_bindings import FeishuGroupBindings
from scripts.feishu_pricing_dispatcher import FeishuPricingDispatcher
from scripts.feishu_task_store import FeishuTaskStore
from guazi_app_data_system.app_startup import _is_keyguard_showing_from_window_dump


def fixed_clock():
    return datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)


def gateway_event(text, *, message_id="om_admin", sender_id="ou_admin", chat_id="oc_admin"):
    return {
        "message_id": message_id,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "text": text,
    }


def valid_draft(task_id):
    return {
        "task_id": task_id,
        "source": "feishu",
        "status": "QUEUED",
        "brand": "本田",
        "series": "雅阁",
        "model_config": "2021款 260TURBO 豪华版",
        "license_date": "2021.06",
        "mileage_text": "5.8",
        "color": "白",
        "transfer_count_text": "1",
        "condition_text": "右前门喷漆",
        "created_at": "2026-06-14T09:00:00+00:00",
    }


class FeishuProductionErrorRoutingTest(unittest.TestCase):
    def setUp(self):
        self.adb_env_patch = patch.dict("os.environ", {"GUAZI_ADB_SERIAL": "UNITTEST_TARGET_SERIAL"}, clear=False)
        self.adb_env_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data"
        self.task_root = self.data_dir / "feishu_tasks"
        self.runtime_lock = self.root / "runtime" / "pricing.lock"
        self.first_stage_script = self.root / "scripts" / "runtime_s01_to_s10_mainline.py"
        self.second_stage_script = self.root / "scripts" / "runtime_s10_to_s16_mainline.py"
        self.first_stage_result = self.root / "output" / "result_s01_to_s10.json"
        self.second_stage_result = self.root / "output" / "result_s10_to_s16.json"
        self.first_stage_script.parent.mkdir(parents=True, exist_ok=True)
        self.first_stage_script.write_text("# fake first stage\n", encoding="utf-8")
        self.second_stage_script.write_text("# fake second stage\n", encoding="utf-8")
        self.store = FeishuTaskStore(self.task_root, clock=fixed_clock)
        self.bindings = FeishuGroupBindings(self.data_dir / "feishu_group_bindings.json", clock=fixed_clock)
        self.roles = {
            "admin_open_ids": ["ou_admin"],
            "supervisor_open_ids": ["ou_supervisor"],
            "business_chat_ids": [],
            "supervisor_chat_ids": [],
            "admin_chat_ids": ["oc_admin"],
        }

    def tearDown(self):
        self.temp.cleanup()
        self.adb_env_patch.stop()

    def create_task(self, task_id, status, *, errors=None, queued_at="2026-06-14T09:00:00+00:00"):
        task_dir = self.task_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        status_payload = {
            "task_id": task_id,
            "status": status,
            "business_status": status,
            "technical_status": "FAILED" if status != "QUEUED" else "QUEUED",
            "recommended_next_action": "wait-admin-resolution" if status == "SYSTEM_BLOCKED" else "wait-dispatcher",
            "errors": errors or [],
            "queued_at": queued_at,
            "confirmed_at": queued_at,
            "created_at": queued_at,
            "updated_at": queued_at,
            "raw_chat_id": "oc_business",
            "business_chat_id": "oc_business",
            "sender_open_id": "ou_sales",
        }
        self.write_json(task_dir / "status.json", status_payload)
        self.write_json(task_dir / "target_task_draft.json", valid_draft(task_id))
        return task_id

    def make_dispatcher(self, *, health=None):
        return FeishuPricingDispatcher(
            task_root=self.task_root,
            data_dir=self.data_dir,
            runtime_lock_path=self.runtime_lock,
            clock=fixed_clock,
            first_stage_script=self.first_stage_script,
            first_stage_result_path=self.first_stage_result,
            second_stage_script=self.second_stage_script,
            second_stage_result_path=self.second_stage_result,
            system_health_checker=health or (lambda **kwargs: {"ok": True, "errors": []}),
            supervisor_sync=lambda *args, **kwargs: {"ok": True, "status": "WAITING_MANUAL_PRICE"},
        )

    def test_system_health_preflight_failure_auto_cancels_not_started_task_without_subprocess(self):
        task_id = self.create_task("FS20260614_0001", "QUEUED")

        def failed_health(**kwargs):
            return {"ok": False, "status": "ADB_UNAUTHORIZED", "errors": ["ADB_UNAUTHORIZED"]}

        with patch("scripts.pricing_runner.subprocess.run") as run:
            result = self.make_dispatcher(health=failed_health).dispatch_once(dry_run=False, allow_app_run=True)

        run.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["cancel_reason"], "SYSTEM_PRECHECK_FAILED_NOT_STARTED")
        self.assertFalse(result["started"])
        self.assertFalse(result["blocks_queue"])
        self.assertEqual(result["recommended_next_action"], "resend-target-info")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["business_status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "SYSTEM_PRECHECK_FAILED_NOT_STARTED")
        self.assertEqual(status["canonical_error_code"], "ADB_UNAUTHORIZED")
        self.assertFalse(status["started"])
        self.assertFalse(status["blocks_queue"])
        self.assertFalse(status["recoverable_by_health_check"])
        self.assertTrue((self.task_root / task_id / "not_started_auto_cancel_business_reply.preview.txt").exists())
        self.assertTrue((self.task_root / task_id / "not_started_auto_cancel_admin_reply.preview.txt").exists())

    def test_first_stage_login_required_cancels_with_concrete_feedback(self):
        task_id = self.create_task("FS20260614_0001", "QUEUED")

        def fake_run(*args, **kwargs):
            self.write_json(self.first_stage_result, {"final_status": "HUMAN_LOGIN_REQUIRED", "errors": ["HUMAN_LOGIN_REQUIRED"]})
            return SimpleNamespace(returncode=0, stdout="login", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["cancel_reason"], "SYSTEM_PRECHECK_FAILED_NOT_STARTED")
        self.assertEqual(result["canonical_error_code"], "HUMAN_LOGIN_REQUIRED")
        self.assertIn("瓜子 APP 未登录或登录状态异常", result["business_reply_text"])
        self.assertNotIn("系统暂时不能开始定价", result["business_reply_text"])
        self.assertNotIn("已通知管理员处理", result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["recommended_next_action"], "resend-target-info")
        self.assertTrue(status["final_feedback_generated"])
        self.assertTrue(status["final_feedback_delivery_dry_run"])
        self.assertFalse(status["final_feedback_sent"])
        self.assertFalse(status["blocks_queue"])
        self.assertIn("HUMAN_LOGIN_REQUIRED", status["canonical_error_codes"])
        self.assertTrue((self.task_root / task_id / "final_failure_business_reply.preview.txt").exists())

    def test_first_stage_precheck_failures_return_concrete_business_reasons(self):
        cases = [
            ("FS20260614_0001", "ADB_DEVICE_NOT_FOUND", "执行手机未连接"),
            ("FS20260614_0002", "ADB_UNAUTHORIZED", "手机授权未通过"),
            ("FS20260614_0003", "PHONE_NOT_AWAKE", "手机未亮屏解锁"),
            ("FS20260614_0004", "APP_NOT_READY", "瓜子 APP 未处于可操作状态"),
        ]

        for task_id, error_code, expected_reason in cases:
            with self.subTest(error_code=error_code):
                self.create_task(task_id, "QUEUED", queued_at=f"2026-06-14T09:0{task_id[-1]}:00+00:00")

                def fake_run(*args, **kwargs):
                    self.write_json(self.first_stage_result, {"final_status": error_code, "errors": [error_code]})
                    return SimpleNamespace(returncode=0, stdout=error_code, stderr="")

                with patch("pricing_runner.subprocess.run", side_effect=fake_run):
                    result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "CANCELLED")
                self.assertEqual(result["canonical_error_code"], error_code)
                self.assertIn(expected_reason, result["business_reply_text"])
                self.assertNotIn("系统暂时不能开始定价", result["business_reply_text"])
                status = self.read_json(self.task_root / task_id / "status.json")
                self.assertEqual(status["status"], "CANCELLED")
                self.assertEqual(status["cancel_reason"], "SYSTEM_PRECHECK_FAILED_NOT_STARTED")
                self.assertTrue(status["final_feedback_generated"])
                self.assertTrue(status["final_feedback_delivery_dry_run"])
                self.assertFalse(status["final_feedback_sent"])

    def test_first_stage_not_s10_ready_with_keyguard_failure_maps_to_phone_not_awake(self):
        task_id = self.create_task("FS20260618_0004", "QUEUED")
        keyguard_failure = "NON_SECURE_" + "KEYGUARD_" + "SWIPE_" + "FAILED"

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "final_status": keyguard_failure,
                    "current_state": keyguard_failure,
                    "errors": ["FIRST_STAGE_NOT_S10_READY"],
                    "keyguard_showing": True,
                    "focused_window": "NotificationShade",
                },
            )
            return SimpleNamespace(returncode=0, stdout="keyguard", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["canonical_error_code"], "PHONE_NOT_AWAKE")
        self.assertIn("手机未亮屏解锁", result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "PHONE_NOT_AWAKE")
        self.assertTrue(status["final_feedback_generated"])
        self.assertTrue(status["final_feedback_delivery_dry_run"])
        self.assertFalse(status["final_feedback_sent"])

    def test_miui_launcher_overlay_failure_has_concrete_business_feedback_without_internal_terms(self):
        task_id = self.create_task("FS20260623_0006", "QUEUED")
        overlay_code = "DEVICE_READY_" + "LAUNCHER_" + "OVERLAY_" + "KEYGUARD_" + "STALE"

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "status": overlay_code,
                    "final_status": overlay_code,
                    "current_state": overlay_code,
                    "errors": ["FIRST_STAGE_NOT_S10_READY", overlay_code],
                    "context": {
                        "focused_window": "LauncherOverlayWindow:com.miui.newhome",
                        "xml_package": "com.miui.newhome",
                        "visible_text_digest": ["看点", "穿山甲AD", "广告", "立即下载", "首页", "视频", "热榜", "我的"],
                        "miui_launcher_overlay_detected": True,
                        "miui_newhome_ad_close_detected": True,
                    },
                },
            )
            return SimpleNamespace(returncode=0, stdout=overlay_code, stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["canonical_error_code"], overlay_code)
        self.assertIn("手机桌面被系统弹窗或广告页遮挡", result["business_reply_text"])
        self.assertNotIn("手机未亮屏解锁", result["business_reply_text"])
        forbidden = set(INTERNAL_BUSINESS_FORBIDDEN_TERMS) | {
            "ADB_VENDOR_KEYS",
            "output/adb_home",
            "adb_path",
            "device_snapshot",
            "keyguard",
            "MIUI",
            "launcher",
            "XML",
            "screenshot",
            "com.miui.newhome",
            "ad_close",
        }
        for term in forbidden:
            self.assertNotIn(term, result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], overlay_code)
        self.assertEqual(status["human_reason"], "手机桌面被系统弹窗或广告页遮挡，未能进入瓜子")

    def test_empty_window_dump_is_not_keyguard_evidence(self):
        self.assertIsNone(_is_keyguard_showing_from_window_dump(""))
        self.assertFalse(_is_keyguard_showing_from_window_dump("mKeyguardShowing=false"))
        self.assertTrue(_is_keyguard_showing_from_window_dump("mCurrentFocus=Window{abc u0 NotificationShade}"))
        self.assertTrue(_is_keyguard_showing_from_window_dump("mKeyguardShowing=true"))

    def test_target_adb_device_not_found_overrides_keyguard_failure(self):
        task_id = self.create_task("FS20260622_0013", "QUEUED")
        keyguard_failure = "NON_SECURE_" + "KEYGUARD_" + "SWIPE_" + "FAILED"
        device_not_found = "adb.e: device '6TGYHPZCETCSK6L' not found"

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "status": "FAILED",
                    "final_status": keyguard_failure,
                    "current_state": keyguard_failure,
                    "errors": ["FIRST_STAGE_NOT_S10_READY"],
                    "focused_window": "",
                    "foreground_package": "",
                    "screenshot_error": device_not_found,
                    "xml_dump_error": device_not_found,
                    "swipe_stderr": device_not_found,
                },
            )
            return SimpleNamespace(returncode=0, stdout="", stderr=device_not_found)

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["canonical_error_code"], "TARGET_ADB_DEVICE_NOT_CONNECTED")
        self.assertIn("指定执行手机未连接或当前不可见", result["business_reply_text"])
        self.assertNotIn("手机未亮屏解锁", result["business_reply_text"])
        self.assertNotIn("手机执行环境暂不可用", result["business_reply_text"])
        for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
            self.assertNotIn(term, result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "TARGET_ADB_DEVICE_NOT_CONNECTED")
        self.assertEqual(status["original_error_code"], "TARGET_ADB_DEVICE_NOT_CONNECTED")
        self.assertIn("device '6TGYHPZCETCSK6L' not found", status["raw_error_summary"])
        self.assertTrue(status["final_feedback_generated"])
        self.assertTrue(status["final_feedback_delivery_dry_run"])
        self.assertFalse(status["final_feedback_sent"])

    def test_first_stage_app_icon_missing_maps_to_app_launch_failed(self):
        task_id = self.create_task("FS20260619_0001", "QUEUED")

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "status": "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
                    "final_status": "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
                    "error": "Launcher is visible after device ready gate, but exact Guazi app icon text was not found.",
                    "errors": ["FIRST_STAGE_NOT_S10_READY"],
                    "context": {
                        "focused_window": "com.shuqing.launcher/com.shuqing.launcher.Launcher",
                        "foreground_package": "com.shuqing.launcher",
                        "visible_text_digest": ["应用列表", "设置", "微信"],
                    },
                },
            )
            return SimpleNamespace(returncode=0, stdout="app icon missing", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["canonical_error_code"], "APP_LAUNCH_FAILED")
        self.assertIn("瓜子 APP 启动失败", result["business_reply_text"])
        self.assertNotIn("手机执行环境暂不可用", result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "APP_LAUNCH_FAILED")
        self.assertEqual(status["original_error_code"], "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY")
        self.assertIn("APP_ICON_NOT_FOUND_AFTER_DEVICE_READY", status["raw_error_summary"])

    def test_old_guazi_page_reopen_failure_has_specific_business_feedback(self):
        task_id = self.create_task("FS20260623_0009", "QUEUED")

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "status": "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE",
                    "final_status": "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE",
                    "error": "Old Guazi page was visible and operable, but the forced Guazi reopen did not find the launcher icon.",
                    "errors": ["FIRST_STAGE_NOT_S10_READY", "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE"],
                    "context": {
                        "focused_window": "com.ganji.android.haoche_c/com.guazi.h5.Html5NewContainerActivity",
                        "foreground_package": "com.ganji.android.haoche_c",
                        "xml_package": "com.ganji.android.haoche_c",
                        "guazi_foreground_visible_despite_keyguard": True,
                        "stale_keyguard_ignored_for_reopen": True,
                        "old_guazi_page_detected": True,
                        "old_guazi_page_type": "S14_DETAIL_POPUP",
                        "force_reopen_required": True,
                        "visible_text_digest": ["瓜子官方检测报告", "后保险杠—拆卸痕迹", "AI详细解读"],
                    },
                },
            )
            return SimpleNamespace(returncode=0, stdout="reopen failed", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["canonical_error_code"], "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE")
        self.assertIn("瓜子 APP 重新打开失败", result["business_reply_text"])
        self.assertNotIn("手机未亮屏解锁", result["business_reply_text"])
        forbidden = set(INTERNAL_BUSINESS_FORBIDDEN_TERMS) | {
            "keyguard",
            "XML",
            "screenshot",
            "APP_FORCE_RESTART",
            "old_guazi_page",
            "force_reopen",
        }
        for term in forbidden:
            self.assertNotIn(term, result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE")
        self.assertEqual(status["original_error_code"], "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE")

    def test_s02_select_page_contract_failure_maps_to_brand_filter_step_not_entered(self):
        task_id = self.create_task("FS20260619_0002", "QUEUED")
        visible_text_digest = [
            "全部",
            "官方自营",
            "品牌选车",
            "AI选车",
            "理想汽车理想L7",
            "综合排序",
            "品牌",
            "价格",
            "车龄/里程",
            "筛选",
            "2020年 | 6.92万公里",
            "门店实车",
            "3.49",
            "万",
            "首页",
            "选车",
            "新能源",
            "我的",
        ]

        def fake_run(*args, **kwargs):
            self.write_json(
                self.first_stage_result,
                {
                    "status": "FAILED",
                    "final_status": "PAGE_CONTRACT_MISMATCH",
                    "current_state": "PAGE_CONTRACT_MISMATCH",
                    "errors": ["FIRST_STAGE_NOT_S10_READY", "PAGE_CONTRACT_MISMATCH"],
                    "context": {
                        "visible_text_digest": visible_text_digest,
                        "foreground_package": "com.ganji.android.haoche_c",
                    },
                },
            )
            return SimpleNamespace(returncode=0, stdout="s02 contract mismatch", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["canonical_error_code"], "BRAND_FILTER_STEP_NOT_ENTERED")
        self.assertIn("未能进入品牌筛选页", result["business_reply_text"])
        self.assertNotIn("手机执行环境暂不可用", result["business_reply_text"])
        for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
            self.assertNotIn(term, result["business_reply_text"])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "BRAND_FILTER_STEP_NOT_ENTERED")
        self.assertFalse(status["blocks_queue"])

    def test_brand_filter_specific_errors_have_concrete_business_feedback(self):
        cases = [
            ("BRAND_FILTER_NOT_FOUND", "未找到品牌筛选入口"),
            ("BRAND_FILTER_CLICK_FAILED", "品牌筛选入口点击失败"),
            ("BRAND_FILTER_PANEL_NOT_OPENED", "品牌筛选页未打开"),
        ]
        for index, (code, expected_reason) in enumerate(cases, start=10):
            with self.subTest(code=code):
                task_id = self.create_task(f"FS20260619_{index:04d}", "CANCELLED", errors=[code])
                details = self.store.concrete_failure_details(task_id, errors=[code])

                self.assertEqual(details["canonical_error_code"], code)
                self.assertIn(expected_reason, details["business_reply_text"])
                self.assertNotIn("手机执行环境暂不可用", details["business_reply_text"])

    def test_s03_brand_selection_errors_have_concrete_business_feedback(self):
        cases = [
            ("S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE", "目标品牌【本田】的品牌分组"),
            ("S03_TARGET_INITIAL_LETTER_NOT_FOUND", "目标品牌【本田】所在的字母分组"),
            ("S03_TARGET_BRAND_NOT_FOUND", "目标品牌【本田】"),
            ("S03_TARGET_BRAND_CLICK_FAILED", "找到目标品牌，但点击失败"),
            ("S03_TARGET_BRAND_PANEL_NOT_READY", "品牌选择页没有准备好"),
        ]
        for index, (code, expected_reason) in enumerate(cases, start=30):
            with self.subTest(code=code):
                task_id = self.create_task(f"FS20260619_{index:04d}", "CANCELLED", errors=[code])
                details = self.store.concrete_failure_details(task_id, errors=[code])

                self.assertEqual(details["canonical_error_code"], code)
                self.assertIn(expected_reason, details["business_reply_text"])
                self.assertNotIn("手机执行环境暂不可用", details["business_reply_text"])
                for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
                    self.assertNotIn(term, details["business_reply_text"])

    def test_s05_target_config_errors_have_concrete_business_feedback(self):
        cases = [
            ("S05_TARGET_CONFIG_NOT_FOUND", "已进入车款配置页，但未能确认目标配置【2021款 260TURBO 豪华版】"),
            ("S05_TARGET_CONFIG_VISIBLE_BUT_NOT_MATCHED", "页面中已出现目标车款配置，但系统未能完成配置匹配"),
            ("S05_TARGET_CONFIG_CLICK_FAILED", "车款配置已找到，但点击目标配置失败"),
            ("S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED", "车款配置【2021款 260TURBO 豪华版】已点击，但未能确认已选中"),
        ]
        for index, (code, expected_reason) in enumerate(cases, start=50):
            with self.subTest(code=code):
                task_id = self.create_task(f"FS20260622_{index:04d}", "CANCELLED", errors=["FIRST_STAGE_NOT_S10_READY"])
                self.write_json(
                    self.task_root / task_id / "first_stage_result.json",
                    {
                        "status": "FAILED",
                        "final_status": code,
                        "current_state": code,
                        "errors": ["FIRST_STAGE_NOT_S10_READY", code],
                        "target_trim": "2021款 260TURBO 豪华版",
                    },
                )

                details = self.store.concrete_failure_details(task_id)

                self.assertEqual(details["canonical_error_code"], code)
                self.assertIn(expected_reason, details["business_reply_text"])
                self.assertNotIn("手机执行环境暂不可用", details["business_reply_text"])
                for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
                    self.assertNotIn(term, details["business_reply_text"])

    def test_s13_repair_item_errors_have_concrete_business_feedback(self):
        cases = [
            ("S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED", "未能安全打开历史修复详情"),
            ("S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE", "历史修复项点击区域不安全"),
            ("S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL", "点击历史修复项后未打开详情页"),
        ]
        for index, (code, expected_reason) in enumerate(cases, start=70):
            with self.subTest(code=code):
                task_id = self.create_task(f"FS20260622_{index:04d}", "CANCELLED", errors=[code])
                details = self.store.concrete_failure_details(task_id, errors=[code])

                self.assertEqual(details["canonical_error_code"], code)
                self.assertIn("【本次定价已停止】", details["business_reply_text"])
                self.assertIn("已进入检测报告", details["business_reply_text"])
                self.assertIn(expected_reason, details["business_reply_text"])
                self.assertNotIn("手机执行环境暂不可用", details["business_reply_text"])
                self.assertNotIn("RESULT_SCHEMA_INVALID_FOR_PRICING", details["business_reply_text"])
                for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
                    self.assertNotIn(term, details["business_reply_text"])

    def test_app_force_restart_non_contract_page_has_concrete_business_feedback(self):
        task_id = self.create_task("FS20260622_0005", "CANCELLED", errors=["FIRST_STAGE_NOT_S10_READY"])
        task_dir = self.task_root / task_id
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "FAILED",
                "final_status": "APP_FORCE_RESTART_NON_CONTRACT_PAGE",
                "current_state": "APP_FORCE_RESTART_NON_CONTRACT_PAGE",
                "errors": ["FIRST_STAGE_NOT_S10_READY", "APP_FORCE_RESTART_NON_CONTRACT_PAGE"],
                "error": "APP_FORCE_RESTART did not land on a verified S00-S10 or S_LOGIN page contract.",
                "context": {
                    "foreground_package": "",
                    "focused_window": "",
                    "xml_package": "com.ganji.android.haoche_c",
                    "visible_text_digest": ["唐山", "搜索", "卖车", "新能源", "首页", "选车", "我的", "唐山瓜子二手车直卖场"],
                },
            },
        )

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "GUAZI_PAGE_UNRECOGNIZED_AFTER_FORCE_RESTART")
        self.assertIn("瓜子 APP 已打开，但系统未能识别当前页面", details["business_reply_text"])
        self.assertNotIn("手机执行环境暂不可用", details["business_reply_text"])
        for term in [
            "PowerShell",
            "dispatcher",
            "runner",
            "adb",
            "uiautomator",
            "status.json",
            "current_target_task.json",
            "run_id",
            "generation_id",
            "pricing.lock",
            "first_stage_result",
            "first_stage_run_meta",
            "ADMIN_INTERVENTION_TASK_EXISTS",
            "SYSTEM_BLOCKED",
            "UNKNOWN_PRECHECK_FAILED",
        ]:
            self.assertNotIn(term, details["business_reply_text"])

    def test_security_exception_inject_events_maps_to_input_permission_denied(self):
        task_id = self.create_task("FS20260619_0001", "CANCELLED", errors=[])
        self.write_json(
            self.task_root / task_id / "first_stage_result.json",
            {
                "status": "FAILED",
                "error": "java.lang.SecurityException: Permission Denial: Injecting to another application requires INJECT_EVENTS permission",
            },
        )

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "ADB_INPUT_PERMISSION_DENIED")
        self.assertIn("手机未允许电脑自动操作", details["business_reply_text"])
        self.assertIn("SecurityException", details["raw_error_summary"])

    def test_existing_generic_final_feedback_refreshes_when_specific_code_is_found(self):
        task_id = self.create_task("FS20260619_0001", "CANCELLED", errors=["FIRST_STAGE_NOT_S10_READY"])
        task_dir = self.task_root / task_id
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "CANCELLED",
                "business_status": "CANCELLED",
                "technical_status": "CANCELLED",
                "errors": ["FIRST_STAGE_NOT_S10_READY"],
                "final_feedback_sent": True,
                "business_chat_id": "oc_business",
            },
        )
        self.write_json(
            task_dir / "final_failure_feedback_delivery.json",
            {
                "task_id": task_id,
                "status": "CANCELLED",
                "canonical_error_code": "FIRST_STAGE_NOT_S10_READY",
                "business_reply_text": "本次定价没有开始执行，原因：手机执行环境暂不可用。",
            },
        )
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "APP_ICON_NOT_FOUND_AFTER_DEVICE_READY",
                "error": "Launcher is visible after device ready gate, but exact Guazi app icon text was not found.",
            },
        )

        result = self.store.ensure_cancelled_task_final_feedback(task_id)

        self.assertTrue(result.success)
        self.assertTrue(result.changed)
        self.assertIn("瓜子 APP 启动失败", result.reply_text)
        updated = self.read_json(task_dir / "status.json")
        self.assertEqual(updated["canonical_error_code"], "APP_LAUNCH_FAILED")
        self.assertIn("APP_ICON_NOT_FOUND_AFTER_DEVICE_READY", updated["canonical_error_codes"])

    def test_second_stage_handoff_failure_feedback_is_not_phone_environment_generic(self):
        task_id = self.create_task(
            "FS20260622_0009",
            "CANCELLED",
            errors=[
                "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
                "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            ],
        )

        result = self.store.ensure_cancelled_task_final_feedback(task_id)

        self.assertTrue(result.success)
        self.assertIn("参考车卡片", result.reply_text)
        self.assertIn("无法唯一确认", result.reply_text)
        self.assertNotIn("手机执行环境暂不可用", result.reply_text)
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")

    def test_s10_ready_release_blocker_preserves_reference_card_binding_reason(self):
        task_id = self.create_task("FS20260624_0001", "ADMIN_INTERVENTION_REQUIRED", errors=["APP_NOT_FOREGROUND"])
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "canonical_error_code": "APP_NOT_FOREGROUND",
                "canonical_blocking_error_code": "APP_NOT_FOREGROUND",
                "admin_intervention_error_code": "APP_NOT_FOREGROUND",
                "last_blocking_error_code": "APP_NOT_FOREGROUND",
                "original_error_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                "raw_error_summary": "code=REFERENCE_CARD_BINDING_NOT_UNIQUE; focused_window=com.ganji.android.haoche_c/.Page; foreground_package=com.ganji.android.haoche_c",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "S10_READY",
                "flow_state": {"S10_READY": True},
                "trisame_cards_count": 4,
            },
        )
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
                "current_reference": {
                    "s14_collect_done": True,
                    "s15_entry_allowed": True,
                    "target_score_source": "score_target_runtime_s15",
                    "s10_reliable_list_evidence": {"selected_reference_card_stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE"},
                },
            },
        )

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("参考车卡片", result.reply_text)
        self.assertIn("无法唯一确认", result.reply_text)
        self.assertNotIn("瓜子 APP 未成功打开到前台", result.reply_text)
        updated = self.read_json(status_path)
        self.assertEqual(updated["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertEqual(updated["canonical_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")
        self.assertEqual(updated["user_facing_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")
        self.assertEqual(updated["original_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")
        self.assertTrue(updated["reached_s10_before_failure"])
        self.assertTrue(updated["app_foreground_confirmed_before_failure"])
        self.assertFalse(updated["blocks_queue"])
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertEqual(delivery["canonical_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")
        self.assertEqual(delivery["user_facing_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")
        forbidden = {
            "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            "APP_NOT_FOREGROUND",
            "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER",
            "S10",
            "S14",
            "S15",
            "second stage",
            "first_stage_result",
            "pricing_result.json",
            "status.json",
            "run_id",
            "generation_id",
            "runner",
            "dispatcher",
            "adb",
            "uiautomator",
            "foreground_package",
            "focused_window",
            "traceback",
            "schema",
        }
        business_reply = delivery["business_reply_text"]
        for term in forbidden:
            self.assertNotIn(term, business_reply)

    def test_started_duplicate_reference_blocked_beats_schema_wrapper_and_app_foreground(self):
        task_id = self.create_task(
            "FS20260701_0004",
            "ADMIN_INTERVENTION_REQUIRED",
            errors=["APP_NOT_FOREGROUND", "RESULT_SCHEMA_INVALID_FOR_PRICING"],
        )
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "canonical_error_code": "RESULT_SCHEMA_INVALID_FOR_PRICING",
                "original_error_code": "RESULT_SCHEMA_INVALID_FOR_PRICING",
                "raw_error_summary": "RESULT_SCHEMA_INVALID_FOR_PRICING; foreground_package=com.ganji.android.haoche_c",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "S10_READY",
                "flow_state": {"S10_READY": True},
                "trisame_cards_count": 4,
            },
        )
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                "final_status": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                "current_state": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                "issue_code": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                "errors": ["RESULT_SCHEMA_INVALID_FOR_PRICING"],
                "current_reference_index": 3,
                "reference_history": [{"reference_index": 1}, {"reference_index": 2}, {"reference_index": 3}, {"reference_index": 4}],
                "issue_context": {
                    "binding_result": {
                        "stop_code": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                        "target_reference_index": 3,
                        "processed_reference_indices": [3, 4],
                        "duplicate_detected_by_index": True,
                        "duplicate_detected_by_identity": True,
                        "duplicate_reference_allowed_for_recollect": False,
                    }
                },
            },
        )

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("【本次定价未完成】FS20260701_0004", result.reply_text)
        self.assertIn("系统已开始自动定价", result.reply_text)
        self.assertIn("参考车回采阶段未能继续执行", result.reply_text)
        for forbidden in ("【本次定价未开始】", "本次定价没有开始执行", "手机执行环境暂不可用", "APP_NOT_FOREGROUND", "RESULT_SCHEMA_INVALID_FOR_PRICING", "PHONE_EXECUTION_ENVIRONMENT_UNAVAILABLE"):
            self.assertNotIn(forbidden, result.reply_text)
        updated = self.read_json(status_path)
        self.assertEqual(updated["canonical_error_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(updated["user_facing_error_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(updated["primary_error_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(updated["wrapper_error_code"], "RESULT_SCHEMA_INVALID_FOR_PRICING")
        self.assertEqual(updated["pricing_result_issue_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(updated["binding_stop_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertTrue(updated["post_start_failure"])
        self.assertEqual(updated["post_start_failure_stage"], "S10")
        self.assertTrue(updated["second_stage_entered"])
        self.assertTrue(updated["reached_s10_before_failure"])
        self.assertTrue(updated["post_start_not_started_template_blocked"])
        self.assertEqual(updated["post_start_failure_business_template"], "POST_START_DUPLICATE_REFERENCE_RECOLLECT")
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertEqual(delivery["canonical_error_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(delivery["primary_error_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertEqual(delivery["wrapper_error_code"], "RESULT_SCHEMA_INVALID_FOR_PRICING")
        self.assertEqual(delivery["binding_stop_code"], "DUPLICATE_REFERENCE_CLICK_BLOCKED")
        self.assertTrue(delivery["post_start_failure"])
        self.assertEqual(delivery["post_start_failure_stage"], "S10")
        self.assertEqual(delivery["post_start_failure_business_template"], "POST_START_DUPLICATE_REFERENCE_RECOLLECT")
        self.assertIn("primary_error_code=DUPLICATE_REFERENCE_CLICK_BLOCKED", delivery["admin_reply_text"])
        self.assertIn("post_start_failure=True", delivery["admin_reply_text"])
        self.assertIn("post_start_failure_stage=S10", delivery["admin_reply_text"])
        self.assertIn("processed_reference_indices=[3, 4]", delivery["admin_reply_text"])
        business_reply = delivery["business_reply_text"]
        for forbidden in {
            "DUPLICATE_REFERENCE_CLICK_BLOCKED",
            "RESULT_SCHEMA_INVALID_FOR_PRICING",
            "APP_NOT_FOREGROUND",
            "PHONE_EXECUTION_ENVIRONMENT_UNAVAILABLE",
            "S10",
            "runner",
            "dispatcher",
            "adb",
            "uiautomator",
            "status.json",
            "pricing_result",
        }:
            self.assertNotIn(forbidden, business_reply)

    def test_second_stage_entered_failure_uses_post_start_generic_template(self):
        task_id = self.create_task(
            "FS20260701_0005",
            "ADMIN_INTERVENTION_REQUIRED",
            errors=["APP_NOT_FOREGROUND_AFTER_3_RETRIES"],
        )
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "canonical_error_code": "APP_NOT_FOREGROUND_AFTER_3_RETRIES",
                "original_error_code": "APP_NOT_FOREGROUND_AFTER_3_RETRIES",
                "raw_error_summary": "APP_NOT_FOREGROUND_AFTER_3_RETRIES",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "APP_NOT_FOREGROUND_AFTER_3_RETRIES",
                "second_stage_entered": True,
                "failed_state": "S10",
                "reference_history": [{"reference_index": 1}],
            },
        )

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("【本次定价未完成】FS20260701_0005", result.reply_text)
        self.assertIn("参考车采集阶段未能形成完整结果", result.reply_text)
        for forbidden in ("【本次定价未开始】", "本次定价没有开始执行", "手机执行环境暂不可用", "PHONE_EXECUTION_ENVIRONMENT_UNAVAILABLE"):
            self.assertNotIn(forbidden, result.reply_text)
        updated = self.read_json(status_path)
        self.assertTrue(updated["post_start_failure"])
        self.assertEqual(updated["post_start_failure_business_template"], "POST_START_REFERENCE_COLLECTION_INCOMPLETE")
        self.assertTrue(updated["second_stage_entered"])
        self.assertTrue(updated["post_start_not_started_template_blocked"])

    def test_started_priced_result_missing_fields_overrides_stale_reference_binding_code(self):
        task_id = self.create_task(
            "FS20260625_0008",
            "ADMIN_INTERVENTION_REQUIRED",
            errors=["REFERENCE_CARD_BINDING_NOT_UNIQUE"],
        )
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "canonical_error_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                "original_error_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                "raw_error_summary": "code=REFERENCE_CARD_BINDING_NOT_UNIQUE; foreground_package=com.ganji.android.haoche_c",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "S10_READY",
                "flow_state": {"S10_READY": True},
                "trisame_cards_count": 3,
            },
        )
        self.write_json(
            task_dir / "runner_error.json",
            {
                "status": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                "canonical_error_code": "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                "errors": [
                    "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
                    "MISSING_REQUIRED_FIELD:profit_rate",
                    "MISSING_REQUIRED_FIELD:final_purchase_price_yuan",
                ],
            },
        )
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "FULL_CHAIN_PRICED_DONE",
                "current_reference_index": 3,
                "reference_history": [{"reference_index": 1}, {"reference_index": 2}, {"reference_index": 3}],
                "ignored_stale_error_codes": ["REFERENCE_CARD_BINDING_NOT_UNIQUE"],
                "current_reference": {
                    "returned_s10_reliable_evidence": {
                        "selected_reference_card_stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE"
                    }
                },
            },
        )

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("已完成参考车采集并形成价格测算", result.reply_text)
        self.assertNotIn("瓜子 APP 未成功打开到前台", result.reply_text)
        updated = self.read_json(status_path)
        self.assertEqual(updated["canonical_error_code"], "RESULT_MISSING_REQUIRED_PRICING_FIELDS")
        self.assertEqual(updated["user_facing_error_code"], "RESULT_MISSING_REQUIRED_PRICING_FIELDS")
        self.assertTrue(updated["reached_s10_before_failure"])
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        self.assertEqual(delivery["canonical_error_code"], "RESULT_MISSING_REQUIRED_PRICING_FIELDS")
        business_reply = delivery["business_reply_text"]
        for term in {
            "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            "RESULT_MISSING_REQUIRED_PRICING_FIELDS",
            "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER",
            "S10",
            "S13",
            "S14",
            "S15",
            "XML",
            "candidate",
            "runner",
            "dispatcher",
            "status.json",
            "pricing_result",
            "adb",
            "uiautomator",
        }:
            self.assertNotIn(term, business_reply)

    def test_second_stage_s14_s15_evidence_does_not_downgrade_to_app_not_foreground(self):
        task_id = self.create_task("FS20260624_0002", "CANCELLED", errors=["APP_NOT_FOREGROUND"])
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "original_error_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                "raw_error_summary": "code=REFERENCE_CARD_BINDING_NOT_UNIQUE; foreground_package=com.ganji.android.haoche_c",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "S14_FULL_IMAGE_SEQUENCE_COLLECTED",
                "current_reference": {
                    "s15_entry_allowed": True,
                    "target_score_source": "score_target_runtime_s15",
                    "stop_code": "REFERENCE_CARD_BINDING_NOT_UNIQUE",
                },
            },
        )

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "REFERENCE_CARD_BINDING_NOT_UNIQUE")
        self.assertTrue(details["reached_s10_before_failure"])
        self.assertTrue(details["app_foreground_confirmed_before_failure"])
        self.assertNotIn("瓜子 APP 未成功打开到前台", details["business_reply_text"])

    def test_real_app_not_foreground_still_uses_app_foreground_feedback(self):
        task_id = self.create_task("FS20260624_0003", "ADMIN_INTERVENTION_REQUIRED", errors=["APP_NOT_FOREGROUND"])

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("瓜子 APP 未成功打开到前台", result.reply_text)
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_error_code"], "APP_NOT_FOREGROUND")
        self.assertFalse(status["reached_s10_before_failure"])

    def test_guazi_foreground_xml_failure_beats_app_not_foreground_feedback(self):
        task_id = self.create_task(
            "FS20260626_0010",
            "CANCELLED",
            errors=["APP_NOT_FOREGROUND", "RUNTIME_FRESH_EVIDENCE_MISSING"],
        )
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "raw_error_summary": "APP_NOT_FOREGROUND; RUNTIME_FRESH_EVIDENCE_MISSING; foreground_package=com.ganji.android.haoche_c; xml_dump_failed rc=137",
                "original_error_code": "APP_NOT_FOREGROUND",
                "canonical_error_code": "APP_NOT_FOREGROUND",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "RUNTIME_FRESH_EVIDENCE_MISSING",
                "foreground_package": "com.ganji.android.haoche_c",
                "focused_window": "com.ganji.android.haoche_c/.MainActivity",
                "xml_missing": True,
                "xml_dump_error": "xml dump failed rc=137",
            },
        )

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES")
        self.assertTrue(details["app_foreground_confirmed_before_failure"])
        self.assertIn("页面证据", details["business_reply_text"])
        self.assertNotIn("APP_NOT_FOREGROUND", details["business_reply_text"])
        for term in {"adb", "uiautomator", "runner", "dispatcher", "traceback", "status.json", "XML", "未成功打开到前台"}:
            self.assertNotIn(term, details["business_reply_text"])

    def test_guazi_frontend_three_retry_error_has_specific_feedback(self):
        task_id = self.create_task(
            "FS20260626_0011",
            "CANCELLED",
            errors=["APP_NOT_FOREGROUND", "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES"],
        )
        task_dir = self.task_root / task_id
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES",
                "foreground_package": "com.ganji.android.haoche_c",
                "focused_window": "com.ganji.android.haoche_c/.MainActivity",
                "guazi_frontend_retry_attempts": [
                    {"attempt": 1, "guazi_foreground": True, "xml_missing": True},
                    {"attempt": 2, "guazi_foreground": True, "xml_missing": True},
                    {"attempt": 3, "guazi_foreground": True, "xml_missing": True},
                ],
            },
        )

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES")
        self.assertIn("连续 3 次", details["business_reply_text"])
        self.assertNotIn("未成功打开到前台", details["business_reply_text"])

    def test_guazi_splash_three_retry_error_does_not_use_not_foreground_feedback(self):
        task_id = self.create_task("FS20260626_0012", "CANCELLED", errors=["GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES"])

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "GUAZI_SPLASH_PAGE_STUCK_AFTER_3_RETRIES")
        self.assertIn("启动广告", details["business_reply_text"])
        self.assertNotIn("未成功打开到前台", details["business_reply_text"])

    def test_three_retry_real_not_foreground_keeps_not_foreground_classification(self):
        task_id = self.create_task("FS20260626_0013", "CANCELLED", errors=["APP_NOT_FOREGROUND_AFTER_3_RETRIES"])

        details = self.store.concrete_failure_details(task_id)

        self.assertEqual(details["canonical_error_code"], "APP_NOT_FOREGROUND_AFTER_3_RETRIES")
        self.assertIn("仍未能进入瓜子 APP 前台", details["business_reply_text"])

    def test_started_reference_collection_incomplete_feedback_beats_app_not_foreground(self):
        task_id = self.create_task(
            "FS20260624_0004",
            "ADMIN_INTERVENTION_REQUIRED",
            errors=["APP_NOT_FOREGROUND"],
        )
        task_dir = self.task_root / task_id
        status_path = task_dir / "status.json"
        status = self.read_json(status_path)
        status.update(
            {
                "canonical_error_code": "APP_NOT_FOREGROUND",
                "canonical_blocking_error_code": "APP_NOT_FOREGROUND",
                "admin_intervention_error_code": "APP_NOT_FOREGROUND",
                "last_blocking_error_code": "APP_NOT_FOREGROUND",
                "original_error_code": "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
                "raw_error_summary": "code=S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED; foreground_package=com.ganji.android.haoche_c",
            }
        )
        self.write_json(status_path, status)
        self.write_json(
            task_dir / "first_stage_result.json",
            {
                "status": "S10_READY",
                "flow_state": {"S10_READY": True},
                "trisame_cards_count": 4,
            },
        )
        self.write_json(
            task_dir / "pricing_result.json",
            {
                "status": "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
                "issue_code": "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED",
                "current_reference": {
                    "s14_collect_done": False,
                    "reference_score_trustworthy": False,
                    "reference_score_invalid_reason": "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE",
                },
            },
        )

        result = self.store.release_blocker_without_active_runner(task_id)

        self.assertTrue(result.success)
        self.assertIn("【本次定价未完成】", result.reply_text)
        self.assertIn("系统已开始采集参考车", result.reply_text)
        self.assertIn("参考车车况证据未能完整采集", result.reply_text)
        self.assertNotIn("本次定价未开始", result.reply_text)
        self.assertNotIn("瓜子 APP 未成功打开到前台", result.reply_text)
        updated = self.read_json(status_path)
        self.assertEqual(updated["canonical_error_code"], "S13_REPAIR_ITEM_CANDIDATE_IN_NORMAL_CHECK_LIST_REJECTED")
        self.assertFalse(updated["blocks_queue"])
        delivery = self.read_json(task_dir / "released_blocker_delivery.json")
        forbidden = {
            "S14",
            "S13",
            "S15",
            "s14_whole_vehicle_collection_complete",
            "s14_has_uncollected_next_condition_signal",
            "CONTINUE_CURRENT_REFERENCE_S14",
            "CONTINUE_NEXT_REFERENCE",
            "APP_NOT_FOREGROUND",
            "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER",
            "runtime",
            "dispatcher",
            "collector",
            "pricing_result",
            "status.json",
            "run_id",
            "generation_id",
            "adb",
            "uiautomator",
            "traceback",
            "schema",
        }
        for term in forbidden:
            with self.subTest(term=term):
                self.assertNotIn(term, delivery["business_reply_text"])

    def test_page_contract_error_routes_to_admin_intervention_not_sender_correction(self):
        task_id = self.create_task("FS20260614_0001", "QUEUED")

        def fake_run(*args, **kwargs):
            self.write_json(self.first_stage_result, {"final_status": "PAGE_CONTRACT_MISMATCH", "errors": ["PAGE_CONTRACT_MISMATCH"]})
            return SimpleNamespace(returncode=0, stdout="page", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher().dispatch_once(dry_run=False, allow_app_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "ADMIN_INTERVENTION_REQUIRED")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["business_status"], "ADMIN_INTERVENTION_REQUIRED")
        self.assertNotEqual(status["status"], "TARGET_INFO_NEEDS_CORRECTION")

    def test_business_reply_hides_engineering_terms(self):
        reply = format_business_system_processing_reply(
            "FS20260614_0001",
            {"category": "system_environment", "status": "SYSTEM_BLOCKED"},
        )

        for term in INTERNAL_BUSINESS_FORBIDDEN_TERMS:
            self.assertNotIn(term, reply)
        self.assertIn("FS20260614_0001", reply)

    def test_admin_intervention_reply_promotes_confirm_not_old_commands(self):
        reply = format_admin_intervention_reply(
            "FS20260614_0001",
            {"category": "system_environment", "error_codes": ["HUMAN_LOGIN_REQUIRED"]},
        )

        self.assertIn("问题：瓜子 APP 需要人工登录。", reply)
        self.assertIn("请在执行手机上登录瓜子 APP，确认进入首页。", reply)
        self.assertIn("处理完成后回复：确认。", reply)
        self.assertNotIn("已处理", reply)
        self.assertNotIn("PowerShell", reply)
        self.assertNotIn("dispatcher", reply)

    def test_admin_recovery_command_detection_supports_task_id(self):
        self.assertEqual(detect_admin_recovery_command("已处理"), (True, None))
        self.assertEqual(detect_admin_recovery_command("FS20260614_0001 已处理"), (True, "FS20260614_0001"))
        self.assertEqual(detect_admin_recovery_command("恢复运行 FS20260614_0001"), (True, "FS20260614_0001"))

    def test_admin_can_resolve_single_recoverable_blocked_task(self):
        task_id = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])

        result = handle_event(
            gateway_event("已处理"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
            system_health_checker=lambda **kwargs: {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertFalse(status["blocks_queue"])
        self.assertFalse(status["recoverable_by_health_check"])
        self.assertIn("历史未完成任务", result["reply_text"])
        self.assertIn("释放队列", result["reply_text"])

    def test_admin_confirm_recovers_blocked_task_and_triggers_dispatch_kick(self):
        task_id = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        kick_calls = []

        def fake_kick(**kwargs):
            kick_calls.append(kwargs)
            return {"ok": True, "dispatch_once_called": True, "dispatcher_loop_running": False}

        result = handle_event(
            gateway_event("确认"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
            system_health_checker=lambda **kwargs: {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
            dispatch_kicker=fake_kick,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertIn("历史未完成任务", result["reply_text"])
        self.assertEqual(kick_calls, [])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")

    def test_admin_confirm_bypasses_health_check_cooldown(self):
        task_id = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        status_path = self.task_root / task_id / "status.json"
        status = self.read_json(status_path)
        status["last_health_check_at"] = "2026-06-14T09:00:00+00:00"
        status["health_check_count"] = 1
        self.write_json(status_path, status)
        calls = []

        result = handle_event(
            gateway_event("确认"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
            system_health_checker=lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
            dispatch_kicker=lambda **kwargs: {"ok": True, "dispatch_once_called": True},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(calls, [])
        updated = self.read_json(status_path)
        self.assertEqual(updated["status"], "CANCELLED")
        self.assertEqual(updated["health_check_count"], 1)
        self.assertEqual(updated["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")

    def test_multiple_blocked_tasks_require_task_id(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        self.create_task("FS20260614_0002", "SYSTEM_BLOCKED", errors=["ADB_UNAUTHORIZED"], queued_at="2026-06-14T09:01:00+00:00")

        result = handle_event(
            gateway_event("已处理"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
        )

        self.assertFalse(result["ok"])
        self.assertIn("多个", result["reply_text"])
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "SYSTEM_BLOCKED")

    def test_admin_confirm_multiple_blocked_tasks_does_not_guess_or_preflight(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        self.create_task("FS20260614_0002", "SYSTEM_BLOCKED", errors=["ADB_UNAUTHORIZED"], queued_at="2026-06-14T09:01:00+00:00")
        calls = []

        result = handle_event(
            gateway_event("确认"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
            system_health_checker=lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reply_text"], "当前有多个待处理任务，请回复对应任务卡片“确认”，或输入任务号确认。")
        self.assertEqual(calls, [])
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "SYSTEM_BLOCKED")

    def test_non_admin_cannot_resolve_admin_intervention(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])

        result = handle_event(
            gateway_event("已处理", sender_id="ou_sales"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
        )

        self.assertFalse(result["ok"])
        self.assertIn("没有权限", result["reply_text"])
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "SYSTEM_BLOCKED")

    def test_non_admin_confirm_cannot_recover_system_blocked(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])

        result = handle_event(
            gateway_event("确认", sender_id="ou_sales"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
        )

        self.assertFalse(result["ok"])
        self.assertIn("系统定价暂未完成", result["reply_text"])
        self.assertNotIn("已通知管理员处理", result["reply_text"])
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "SYSTEM_BLOCKED")

    def test_target_info_correction_cannot_be_admin_recovered(self):
        self.create_task("FS20260614_0001", "TARGET_INFO_NEEDS_CORRECTION", errors=["TARGET_DATE_UNRECOGNIZED"])

        result = handle_event(
            gateway_event("FS20260614_0001 已处理"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
        )

        self.assertFalse(result["ok"])
        self.assertIn("目标车信息需要修改", result["reply_text"])
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "TARGET_INFO_NEEDS_CORRECTION")

    def test_dispatcher_pauses_queue_when_blocked_task_exists(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        queued = self.create_task("FS20260614_0002", "QUEUED", queued_at="2026-06-14T09:01:00+00:00")

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "DRY_RUN_READY")
        self.assertEqual(result["selected_task_id"], queued)
        self.assertEqual(result["queued_task_ids"], ["FS20260614_0002"])
        self.assertEqual(result["auto_recovery_attempts"][0]["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "CANCELLED")
        self.assertFalse((self.data_dir / "current_target_task.json").exists())

    def test_historical_blocker_with_old_run_files_releases_and_clears_current_target(self):
        blocked = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        self.write_json(self.task_root / blocked / "dispatcher_result.json", {"started": True})
        self.write_json(self.task_root / blocked / "first_stage_run_meta.json", {"run_started_at": "2026-06-14T09:00:00+00:00"})
        self.write_json(self.task_root / blocked / "first_stage_result.json", {"errors": ["HUMAN_LOGIN_REQUIRED"]})
        self.write_json(self.data_dir / "current_target_task.json", {"task_id": blocked, "brand": "本田"})
        queued = self.create_task("FS20260614_0002", "QUEUED", queued_at="2026-06-14T09:01:00+00:00")

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_task_id"], queued)
        released = self.read_json(self.task_root / blocked / "status.json")
        self.assertEqual(released["status"], "CANCELLED")
        self.assertEqual(released["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertFalse(released["blocks_queue"])
        self.assertFalse((self.data_dir / "current_target_task.json").exists())
        self.assertTrue((self.task_root / blocked / "released_blocker_business_reply.preview.txt").exists())

    def test_runtime_lock_keeps_queue_blocked_and_does_not_release_old_blocker(self):
        blocked = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        self.create_task("FS20260614_0002", "QUEUED", queued_at="2026-06-14T09:01:00+00:00")
        self.runtime_lock.parent.mkdir(parents=True, exist_ok=True)
        self.write_json(self.runtime_lock, {"task_id": "FS_RUNNING", "created_at": "2026-06-14T09:00:00+00:00"})

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["errors"], ["ACTIVE_PRICING_LOCK_EXISTS"])
        self.assertEqual(self.read_json(self.task_root / blocked / "status.json")["status"], "SYSTEM_BLOCKED")

    def test_not_started_human_login_system_blocked_auto_cancels_and_unblocks_queue(self):
        blocked = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        blocked_path = self.task_root / blocked / "status.json"
        blocked_status = self.read_json(blocked_path)
        blocked_status["started"] = False
        self.write_json(blocked_path, blocked_status)
        queued = self.create_task("FS20260614_0002", "QUEUED", queued_at="2026-06-14T09:01:00+00:00")

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "DRY_RUN_READY")
        self.assertEqual(result["selected_task_id"], queued)
        status = self.read_json(blocked_path)
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertEqual(status["canonical_error_code"], "HUMAN_LOGIN_REQUIRED")
        self.assertFalse(status["blocks_queue"])
        business_reply = (self.task_root / blocked / "released_blocker_business_reply.preview.txt").read_text(encoding="utf-8")
        self.assertIn("历史未完成任务", business_reply)
        for forbidden in ["PowerShell", "dispatcher", "runner", "adb", "uiautomator", "status.json", "current_target_task.json", "SYSTEM_BLOCKED"]:
            self.assertNotIn(forbidden, business_reply)

    def test_not_started_adb_unauthorized_system_blocked_auto_cancels(self):
        blocked = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["ADB_UNAUTHORIZED"])
        status_path = self.task_root / blocked / "status.json"
        status = self.read_json(status_path)
        status["started"] = False
        self.write_json(status_path, status)

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        updated = self.read_json(status_path)
        self.assertEqual(updated["status"], "CANCELLED")
        self.assertEqual(updated["canonical_error_code"], "ADB_UNAUTHORIZED")
        self.assertFalse(updated["blocks_queue"])

    def test_admin_confirm_cancelled_task_does_not_revive_it(self):
        task_id = self.create_task("FS20260614_0001", "CANCELLED", errors=["HUMAN_LOGIN_REQUIRED"])
        calls = []

        result = handle_explicit_confirm(
            task_id,
            sender_open_id="ou_admin",
            roles=self.roles,
            store=self.store,
            system_health_checker=lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "CANCELLED")
        self.assertIn("自动取消", result.reply_text)
        self.assertIn("重新发送目标车源", result.reply_text)
        self.assertEqual(calls, [])
        self.assertEqual(self.read_json(self.task_root / task_id / "status.json")["status"], "CANCELLED")

    def test_cancelled_old_task_does_not_block_new_queued_task(self):
        self.create_task("FS20260614_0001", "CANCELLED", errors=["HUMAN_LOGIN_REQUIRED"])
        queued = self.create_task("FS20260614_0002", "QUEUED", queued_at="2026-06-14T09:01:00+00:00")

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "DRY_RUN_READY")
        self.assertEqual(result["selected_task_id"], queued)

    def test_admin_handled_runs_health_check_and_keeps_blocked_when_unhealthy(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["ADB_UNAUTHORIZED"])
        command = next(iter(ADMIN_RECOVERY_COMMANDS))

        result = handle_event(
            gateway_event(command),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
            system_health_checker=lambda **kwargs: {"ok": False, "status": "ADB_UNAUTHORIZED", "errors": ["ADB_UNAUTHORIZED"]},
        )

        self.assertTrue(result["ok"])
        status = self.read_json(self.task_root / "FS20260614_0001" / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertNotIn("last_health_check_ok", status)

    def test_admin_confirm_keeps_blocked_when_preflight_fails(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])

        result = handle_event(
            gateway_event("确认"),
            store=self.store,
            roles=self.roles,
            group_bindings=self.bindings,
            system_health_checker=lambda **kwargs: {"ok": False, "status": "HUMAN_LOGIN_REQUIRED", "errors": ["HUMAN_LOGIN_REQUIRED"]},
        )

        self.assertTrue(result["ok"])
        self.assertIn("历史未完成任务", result["reply_text"])
        status = self.read_json(self.task_root / "FS20260614_0001" / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertTrue((self.task_root / "FS20260614_0001" / "released_blocker_business_reply.preview.txt").exists())

    def test_system_blocked_human_login_auto_recovers_and_continues_queue(self):
        task_id = self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["HUMAN_LOGIN_REQUIRED"])
        calls = []

        def fake_run(*args, **kwargs):
            script = str(args[0][1])
            calls.append(script)
            if "runtime_s01_to_s10" in script:
                self.write_json(self.first_stage_result, {"flow_state": {"S10_READY": True}, "same_source_cards": [{"id": 1}]})
                return SimpleNamespace(returncode=0, stdout="first", stderr="")
            self.write_json(
                self.second_stage_result,
                {
                    "manual_review_required": False,
                    "target_guazi_listing_price_yuan": 96400,
                    "guazi_service_fee_yuan": 1500,
                    "guazi_net_payout_yuan": 94900,
                    "cost_yuan": 1000,
                    "profit_yuan": 7592,
                    "suggested_purchase_price_yuan": 86308,
                },
            )
            return SimpleNamespace(returncode=0, stdout="second", stderr="")

        with patch("pricing_runner.subprocess.run", side_effect=fake_run):
            result = self.make_dispatcher(health=lambda **kwargs: {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []}).dispatch_once(
                dry_run=False,
                allow_app_run=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        self.assertEqual(result["auto_recovery_attempts"][0]["status_after"], "CANCELLED")
        self.assertEqual(calls, [])
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")

    def test_system_blocked_adb_unauthorized_auto_recovers_when_healthy(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["ADB_UNAUTHORIZED"])

        attempts = self.make_dispatcher(health=lambda **kwargs: {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []})._attempt_auto_recover_blocked_tasks(
            ["FS20260614_0001"]
        )

        self.assertEqual(attempts[0]["status_after"], "CANCELLED")
        self.assertEqual(attempts[0]["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        status = self.read_json(self.task_root / "FS20260614_0001" / "status.json")
        self.assertEqual(status["status"], "CANCELLED")

    def test_system_blocked_health_check_failed_keeps_blocked_and_records_cooldown(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["DEVICE_OFFLINE"])

        with patch("pricing_runner.subprocess.run") as run:
            result = self.make_dispatcher(health=lambda **kwargs: {"ok": False, "status": "DEVICE_OFFLINE", "errors": ["DEVICE_OFFLINE"]}).dispatch_once(
                dry_run=False,
                allow_app_run=True,
            )

        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        status = self.read_json(self.task_root / "FS20260614_0001" / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")
        self.assertNotIn("next_health_check_at", status)

    def test_system_blocked_health_check_cooldown_prevents_repeated_attempts(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["APP_NO_RESPONSE"])
        status_path = self.task_root / "FS20260614_0001" / "status.json"
        status = self.read_json(status_path)
        status["last_health_check_at"] = "2026-06-14T09:00:00+00:00"
        status["health_check_count"] = 1
        self.write_json(status_path, status)
        calls = []

        result = self.make_dispatcher(
            health=lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []}
        ).dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        self.assertEqual(calls, [])
        status = self.read_json(status_path)
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["health_check_count"], 1)

    def test_forced_dispatch_kick_bypasses_blocked_task_cooldown(self):
        self.create_task("FS20260614_0001", "SYSTEM_BLOCKED", errors=["APP_NO_RESPONSE"])
        status_path = self.task_root / "FS20260614_0001" / "status.json"
        status = self.read_json(status_path)
        status["last_health_check_at"] = "2026-06-14T09:00:00+00:00"
        status["health_check_count"] = 1
        self.write_json(status_path, status)
        calls = []

        result = self.make_dispatcher(
            health=lambda **kwargs: calls.append(kwargs) or {"ok": False, "status": "APP_NO_RESPONSE", "errors": ["APP_NO_RESPONSE"]}
        ).dispatch_once(dry_run=False, allow_app_run=True, force_health_check=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        self.assertEqual(calls, [])
        self.assertEqual(result["auto_recovery_attempts"][0]["status_after"], "CANCELLED")
        updated = self.read_json(status_path)
        self.assertEqual(updated["health_check_count"], 1)
        self.assertTrue((self.task_root / "FS20260614_0001" / "released_blocker_business_reply.preview.txt").exists())

    def test_page_contract_mismatch_does_not_auto_recover(self):
        self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=["PAGE_CONTRACT_MISMATCH"])
        calls = []

        result = self.make_dispatcher(
            health=lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []}
        ).dispatch_once(dry_run=False, allow_app_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        self.assertEqual(calls, [])
        status = self.read_json(self.task_root / "FS20260614_0001" / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["cancel_reason"], "NO_ACTIVE_RUNNER_RELEASED_FROM_BLOCKER")

    def test_target_info_needs_correction_is_not_auto_recovered(self):
        self.create_task("FS20260614_0001", "TARGET_INFO_NEEDS_CORRECTION", errors=["TARGET_DATE_UNRECOGNIZED"])

        result = self.make_dispatcher(health=lambda **kwargs: {"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []}).dispatch_once(
            dry_run=False,
            allow_app_run=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        self.assertEqual(self.read_json(self.task_root / "FS20260614_0001" / "status.json")["status"], "TARGET_INFO_NEEDS_CORRECTION")

    def test_old_admin_intervention_empty_reason_recovers_from_first_stage_result(self):
        task_id = self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=[])
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"errors": ["HUMAN_LOGIN_REQUIRED"]})

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["canonical_error_code"], "HUMAN_LOGIN_REQUIRED")

    def test_maintenance_error_does_not_override_human_login_required(self):
        task_id = self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=[])
        status_path = self.task_root / task_id / "status.json"
        status = self.read_json(status_path)
        status["last_error_code"] = "TASK_NOT_FAILED"
        self.write_json(status_path, status)
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"errors": ["HUMAN_LOGIN_REQUIRED"]})

        codes = self.store.canonical_blocking_error_codes(task_id)

        self.assertEqual(codes, ["HUMAN_LOGIN_REQUIRED"])

    def test_old_admin_intervention_health_ok_restores_queued(self):
        task_id = self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=[])
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"errors": ["HUMAN_LOGIN_REQUIRED"]})

        result = self.store.resolve_admin_intervention(
            task_id=task_id,
            health_result={"ok": True, "status": "SYSTEM_HEALTH_OK", "errors": []},
            resolved_by_open_id="ou_admin",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "QUEUED")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["canonical_blocking_error_code"], "HUMAN_LOGIN_REQUIRED")
        self.assertTrue(status["recoverable_by_health_check"])
        self.assertEqual(status["recommended_next_action"], "wait-dispatcher")

    def test_old_admin_intervention_health_failed_keeps_system_blocked_with_reason(self):
        task_id = self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=[])
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"errors": ["HUMAN_LOGIN_REQUIRED"]})

        result = self.store.resolve_admin_intervention(
            task_id=task_id,
            health_result={"ok": False, "status": "HUMAN_LOGIN_REQUIRED", "errors": ["HUMAN_LOGIN_REQUIRED"]},
            resolved_by_open_id="ou_admin",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "SYSTEM_BLOCKED")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "SYSTEM_BLOCKED")
        self.assertEqual(status["canonical_blocking_error_code"], "HUMAN_LOGIN_REQUIRED")
        self.assertEqual(status["admin_intervention_error_codes"], ["HUMAN_LOGIN_REQUIRED"])
        self.assertTrue(status["recoverable_by_health_check"])
        self.assertFalse(status["last_health_check_ok"])

    def test_page_contract_mismatch_empty_reason_is_not_health_recoverable(self):
        task_id = self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=[])
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"errors": ["PAGE_CONTRACT_MISMATCH"]})

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["canonical_error_code"], "PAGE_CONTRACT_MISMATCH")

    def test_target_info_correction_empty_reason_is_not_health_recoverable(self):
        task_id = self.create_task("FS20260614_0001", "ADMIN_INTERVENTION_REQUIRED", errors=[])
        self.write_json(self.task_root / task_id / "first_stage_result.json", {"errors": ["TARGET_INFO_NEEDS_CORRECTION"]})

        result = self.make_dispatcher().dispatch_once(dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "NO_QUEUED_TASK")
        status = self.read_json(self.task_root / task_id / "status.json")
        self.assertEqual(status["status"], "CANCELLED")
        self.assertEqual(status["canonical_error_code"], "TARGET_INFO_NEEDS_CORRECTION")

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
