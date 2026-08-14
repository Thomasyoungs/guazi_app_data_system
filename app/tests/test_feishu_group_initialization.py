import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_gateway import handle_event, load_feishu_roles, sync_manual_review_to_supervisor  # noqa: E402
from feishu_group_bindings import FeishuGroupBindings  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402


VALID_TEMPLATE = """定价
品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
"""

FIELD_TARGET_WITH_NUMBERS = """【品牌】雪佛兰
【车系】科鲁泽
【车型】2022款科鲁泽320自动悦享天窗版（1.5L四缸）
【有无天窗】有
【指导价】11.49
【排放标准】国六
【上牌日期】22.8
【表显里程】1.05
【车辆颜色】红
【过户次数】0
【保险到期】26.8
【车牌归属】唐山
【具体车况】原漆，右后叶小坑掉漆"""

FIELD_TARGET_MISSING_BRAND_SERIES = """【车型】2022款科鲁泽320自动悦享天窗版（1.5L四缸）
【有无天窗】有
【指导价】11.49
【排放标准】国六
【上牌日期】22.8
【表显里程】1.05
【车辆颜色】红
【过户次数】0
【保险到期】26.8
【车牌归属】唐山
【具体车况】原漆，右后叶小坑掉漆"""

FORBIDDEN_USER_TEXT = [
    "PowerShell",
    "--run-first-stage",
    "--run-second-stage",
    "--requeue-second-stage",
    "--revalidate-result",
    "--manual-confirm-price",
    "--manual-review-note",
    "--send-result",
    "adb",
    "uiautomator",
    "run_id",
    "generation_id",
    "status.json",
    "runner_result",
    "pricing_result.json",
    "STALE_RUN_RESULT_IGNORED",
    "oc_business_secret",
    "oc_supervisor_secret",
    "ou_admin_mock",
    "ou_supervisor_mock",
]


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 6, 14, 8, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value = self.value + timedelta(**kwargs)


def event(text, *, chat_id, sender_id="ou_admin_mock", message_id="om_x", chat_name=None, reply_to_message_id=None):
    return {
        "message_id": message_id,
        "sender_id": sender_id,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "reply_to_message_id": reply_to_message_id,
        "text": text,
    }


def roles():
    return {
        "admin_open_ids": ["ou_admin_mock"],
        "business_chat_ids": [],
        "supervisor_chat_ids": [],
        "supervisor_open_ids": ["ou_supervisor_mock"],
        "admin_chat_ids": [],
    }


class FeishuGroupInitializationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = MutableClock()
        self.store = FeishuTaskStore(self.root / "data" / "feishu_tasks", clock=self.clock)
        self.bindings = FeishuGroupBindings(
            self.root / "data" / "feishu_group_bindings.json",
            clock=self.clock,
            code_generator=self.next_code,
        )
        self.codes = iter(["BD-4826", "BD-1111", "BD-2222", "BD-3333"])

    def tearDown(self):
        self.temp.cleanup()

    def next_code(self):
        return next(self.codes)

    def gateway(self, text, *, chat_id="oc_business_secret_A", sender_id="ou_admin_mock", message_id="om_x", chat_name=None, reply_to_message_id=None):
        return handle_event(
            event(text, chat_id=chat_id, sender_id=sender_id, message_id=message_id, chat_name=chat_name, reply_to_message_id=reply_to_message_id),
            store=self.store,
            roles=roles(),
            group_bindings=self.bindings,
        )

    def test_admin_sets_business_and_supervisor_chats(self):
        business = self.gateway("设置本群为一线群", chat_name="定价一线群")
        supervisor = self.gateway("设置本群为主管群", chat_id="oc_supervisor_secret_B", chat_name="主管复核群", message_id="om_supervisor")

        self.assertTrue(business["ok"])
        self.assertTrue(supervisor["ok"])
        payload = self.bindings.load()
        self.assertIn("oc_business_secret_A", payload["business_chats"])
        self.assertIn("oc_supervisor_secret_B", payload["supervisor_chats"])
        self.assertIn("已设置本群为一线群", business["reply_text"])
        self.assertIn("已设置本群为主管复核群", supervisor["reply_text"])

    def test_non_admin_cannot_set_group_identity(self):
        business = self.gateway("设置本群为一线群", sender_id="ou_sales_mock")
        supervisor = self.gateway("设置本群为主管群", sender_id="ou_sales_mock", chat_id="oc_supervisor_secret_B", message_id="om_nope")

        self.assertFalse(business["ok"])
        self.assertFalse(supervisor["ok"])
        self.assertIn("没有权限", business["reply_text"])
        self.assertEqual(self.bindings.load()["business_chats"], {})

    def test_admin_open_ids_empty_safely_rejects_group_commands(self):
        result = handle_event(
            event("设置本群为一线群", chat_id="oc_business_secret_A", sender_id="ou_admin_mock"),
            store=self.store,
            roles={"admin_open_ids": [], "supervisor_open_ids": ["ou_supervisor_mock"]},
            group_bindings=self.bindings,
        )

        self.assertFalse(result["ok"])
        self.assertIn("管理员权限未配置", result["reply_text"])

    def test_binding_code_generation_and_binding_success(self):
        self.setup_business_and_supervisor()

        code_result = self.gateway("生成主管群绑定码", message_id="om_code")
        bind_result = self.gateway("绑定一线群 BD-4826", chat_id="oc_supervisor_secret_B", message_id="om_bind")

        self.assertTrue(code_result["ok"])
        self.assertIn("绑定码：BD-4826", code_result["reply_text"])
        self.assertTrue(bind_result["ok"])
        self.assertIn("绑定成功", bind_result["reply_text"])
        business_chat = self.bindings.business_chat("oc_business_secret_A")
        self.assertEqual(business_chat["bound_supervisor_chat_id"], "oc_supervisor_secret_B")

    def test_expired_and_used_binding_codes_are_rejected(self):
        self.setup_business_and_supervisor()
        self.gateway("生成主管群绑定码", message_id="om_code_expired")
        self.clock.advance(minutes=11)

        expired = self.gateway("绑定一线群 BD-4826", chat_id="oc_supervisor_secret_B", message_id="om_bind_expired")
        self.clock.value = datetime(2026, 6, 14, 8, 30, tzinfo=timezone.utc)
        self.gateway("生成主管群绑定码", message_id="om_code_used")
        first = self.gateway("绑定一线群 BD-1111", chat_id="oc_supervisor_secret_B", message_id="om_bind_used_once")
        second = self.gateway("绑定一线群 BD-1111", chat_id="oc_supervisor_secret_B", message_id="om_bind_used_twice")

        self.assertFalse(expired["ok"])
        self.assertIn("已过期", expired["reply_text"])
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("已使用", second["reply_text"])

    def test_uninitialized_group_rejects_target_template(self):
        result = self.gateway(VALID_TEMPLATE, chat_id="oc_unset_secret", sender_id="ou_sales_mock", message_id="om_template")

        self.assertFalse(result["ok"])
        self.assertIn("尚未设置为一线群", result["reply_text"])

    def test_business_chat_target_fields_with_numbers_create_waiting_task_without_supervisor_binding(self):
        self.gateway("设置本群为一线群", message_id="om_business")

        result = self.gateway(FIELD_TARGET_WITH_NUMBERS, sender_id="ou_sales_mock", message_id="om_field_target")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "create_task")
        self.assertEqual(result["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertIn("请确认目标车信息", result["reply_text"])
        self.assertNotIn("人工确认收车价格式无法识别", result["reply_text"])
        draft = self.read_json(self.store.task_dir(result["task_id"]) / "target_task_draft.json")
        self.assertEqual(draft["brand"], "雪佛兰")
        self.assertEqual(draft["series"], "科鲁泽")
        self.assertEqual(draft["model_config"], "2022款科鲁泽320自动悦享天窗版（1.5L四缸）")
        self.assertEqual(draft["license_date"], "2022.08")
        self.assertEqual(draft["register_date"], "2022.08")
        self.assertEqual(draft["registration_date"], "2022.08")
        self.assertEqual(draft["register_year"], 2022)
        self.assertEqual(draft["registration_date_year"], 2022)
        self.assertEqual(draft["mileage_text"], "1.05")
        self.assertEqual(draft["color"], "红")
        self.assertEqual(draft["transfer_count_text"], "0")
        self.assertEqual(draft["condition_text"], "原漆，右后叶小坑掉漆")
        self.assertEqual(draft["city"], "唐山")

    def test_target_fields_missing_brand_series_infers_from_model_not_price(self):
        self.gateway("设置本群为一线群", message_id="om_business")

        result = self.gateway(FIELD_TARGET_MISSING_BRAND_SERIES, sender_id="ou_sales_mock", message_id="om_missing_brand_series")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "create_task")
        self.assertEqual(result["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertIn("品牌：雪佛兰（系统识别）", result["reply_text"])
        self.assertIn("车系：科鲁泽（系统识别）", result["reply_text"])
        self.assertNotIn("缺少以下必填字段", result["reply_text"])
        self.assertNotIn("人工确认收车价格式无法识别", result["reply_text"])
        draft = self.read_json(self.store.task_dir(result["task_id"]) / "target_task_draft.json")
        self.assertEqual(draft["brand"], "雪佛兰")
        self.assertEqual(draft["series"], "科鲁泽")
        self.assertEqual(draft["brand_source"], "inferred_from_model_text")
        self.assertEqual(draft["series_source"], "inferred_from_model_text")
        status = self.read_json(self.store.task_dir(result["task_id"]) / "status.json")
        confirm = self.gateway(
            "确认",
            sender_id="ou_sales_mock",
            message_id="om_missing_brand_series_confirm",
            reply_to_message_id=status["confirm_card_message_id"],
        )
        self.assertTrue(confirm["ok"])
        self.assertEqual(confirm["status"], "QUEUED")
        preview = self.read_json(self.store.task_dir(result["task_id"]) / "current_target_task.preview.json")
        self.assertEqual(preview["brand"], "雪佛兰")
        self.assertEqual(preview["series"], "科鲁泽")
        self.assert_user_text_safe(result["reply_text"])

    def test_unrecognized_model_prompts_model_resolution_not_price(self):
        self.gateway("设置本群为一线群", message_id="om_business")
        text = """【车型】2022款未知车款自动豪华版
【上牌日期】22.8
【表显里程】1.05
【车辆颜色】红
【过户次数】0
【具体车况】原漆
"""

        result = self.gateway(text, sender_id="ou_sales_mock", message_id="om_unknown_model")

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "create_task")
        self.assertEqual(result["status"], "TARGET_INFO_NEEDS_CORRECTION")
        self.assertIn("车型字段无法确定品牌/车系", result["reply_text"])
        self.assertNotIn("人工确认收车价格式无法识别", result["reply_text"])
        self.assert_user_text_safe(result["reply_text"])

    def test_business_chat_sales_price_text_does_not_trigger_manual_price(self):
        self.gateway("设置本群为一线群", message_id="om_business")

        result = self.gateway("8.6万", sender_id="ou_sales_mock", message_id="om_sales_price_business")

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "help")
        self.assertNotIn("人工复核已确认", result["reply_text"])
        self.assertNotIn("人工确认收车价格式无法识别", result["reply_text"])
        self.assert_user_text_safe(result["reply_text"])

    def test_business_chat_without_supervisor_binding_keeps_needs_review(self):
        self.gateway("设置本群为一线群", message_id="om_business")
        task_id = self.create_needs_review_task("FS20260614_0001", business_chat_id="oc_business_secret_A")

        result = sync_manual_review_to_supervisor(task_id, store=self.store, roles=roles(), group_bindings=self.bindings)

        self.assertFalse(result["ok"])
        self.assertEqual(result["recommended_next_action"], "bind-supervisor-chat")
        self.assertIn("尚未绑定主管复核群", result["business_reply_text"])
        status = self.read_json(self.store.task_dir(task_id) / "status.json")
        self.assertEqual(status["status"], "NEEDS_REVIEW")
        self.assertEqual(status["recommended_next_action"], "bind-supervisor-chat")

    def test_bound_business_chat_syncs_needs_review_to_supervisor_chat(self):
        self.setup_business_and_supervisor(bound=True)
        task_id = self.create_needs_review_task("FS20260614_0001", business_chat_id="oc_business_secret_A")

        result = sync_manual_review_to_supervisor(task_id, store=self.store, roles=roles(), group_bindings=self.bindings)

        self.assertTrue(result["ok"])
        self.assertEqual(result["supervisor_chat_id"], "oc_supervisor_secret_B")
        self.assertIn("【人工复核定价】FS20260614_0001", result["supervisor_reply_text"])
        status = self.read_json(self.store.task_dir(task_id) / "status.json")
        self.assertEqual(status["status"], "WAITING_MANUAL_PRICE")
        self.assertIn("supervisor_review_card:", status["supervisor_review_card_message_id"])
        self.assert_user_text_safe(result["business_reply_text"], result["supervisor_reply_text"])

    def test_supervisor_reply_card_confirms_price_and_sales_in_supervisor_group_rejected(self):
        task_id = self.create_waiting_manual_price_task("FS20260614_0001")
        card_id = self.read_json(self.store.task_dir(task_id) / "status.json")["supervisor_review_card_message_id"]

        sales = self.gateway("8.6万", chat_id="oc_supervisor_secret_B", sender_id="ou_sales_mock", message_id="om_sales_price", reply_to_message_id=card_id)
        supervisor = self.gateway("8.6万", chat_id="oc_supervisor_secret_B", sender_id="ou_supervisor_mock", message_id="om_supervisor_price", reply_to_message_id=card_id)

        self.assertFalse(sales["ok"])
        self.assertIn("请主管回复人工确认价", sales["reply_text"])
        self.assertTrue(supervisor["ok"])
        pricing = self.read_json(self.store.task_dir(task_id) / "pricing_result.json")
        self.assertEqual(pricing["manual_confirmed_purchase_price_yuan"], 86000)

    def test_supervisor_price_in_business_chat_is_rejected(self):
        self.create_waiting_manual_price_task("FS20260614_0001")

        result = self.gateway("8.6万", chat_id="oc_business_secret_A", sender_id="ou_supervisor_mock", message_id="om_wrong_chat")

        self.assertFalse(result["ok"])
        self.assertIn("请到主管复核群", result["reply_text"])

    def test_multiple_waiting_manual_price_requires_card_or_task_id(self):
        first = self.create_waiting_manual_price_task("FS20260614_0001")
        second = self.create_waiting_manual_price_task("FS20260614_0002")

        ambiguous = self.gateway("8.6万", chat_id="oc_supervisor_secret_B", sender_id="ou_supervisor_mock", message_id="om_ambiguous")
        selected = self.gateway(f"{second} 8.6万", chat_id="oc_supervisor_secret_B", sender_id="ou_supervisor_mock", message_id="om_selected")

        self.assertFalse(ambiguous["ok"])
        self.assertIn("多个待复核任务", ambiguous["reply_text"])
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["task_id"], second)
        self.assertEqual(self.read_json(self.store.task_dir(first) / "status.json")["status"], "WAITING_MANUAL_PRICE")

    def test_view_group_settings_masks_identifiers(self):
        self.setup_business_and_supervisor(bound=True)

        business = self.gateway("查看本群设置", chat_id="oc_business_secret_A", message_id="om_view_a")
        supervisor = self.gateway("查看本群设置", chat_id="oc_supervisor_secret_B", message_id="om_view_b")
        unset = self.gateway("查看本群设置", chat_id="oc_unset_secret", message_id="om_view_unset")

        self.assertIn("当前群身份：一线群", business["reply_text"])
        self.assertIn("当前群身份：主管群", supervisor["reply_text"])
        self.assertIn("当前群身份：未设置", unset["reply_text"])
        self.assert_user_text_safe(business["reply_text"], supervisor["reply_text"], unset["reply_text"])

    def test_self_identity_commands_return_sender_open_id_without_admin_required(self):
        for index, text in enumerate(("查看我的ID", "我是谁", "我的ID")):
            result = handle_event(
                event(
                    text,
                    chat_id="oc_group_secret",
                    sender_id="ou_admin_candidate",
                    message_id=f"om_self_{index}",
                    chat_name="配置测试群",
                ),
                store=self.store,
                roles={"admin_open_ids": [], "supervisor_open_ids": []},
                group_bindings=self.bindings,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "self_identity")
            self.assertFalse(result["changed"])
            self.assertIn("你的飞书身份信息：", result["reply_text"])
            self.assertIn("open_id：ou_admin_candidate", result["reply_text"])
            self.assertIn("当前群：配置测试群", result["reply_text"])
            self.assertIn("是否管理员：否", result["reply_text"])
            self.assertIn("是否主管：否", result["reply_text"])
            self.assertNotIn("oc_group_secret", result["reply_text"])
            self.assertNotIn("ou_admin_mock", result["reply_text"])
            self.assertNotIn("ou_supervisor_mock", result["reply_text"])

        task_dirs = [path for path in self.store.base_dir.iterdir() if path.is_dir()] if self.store.base_dir.exists() else []
        self.assertEqual(task_dirs, [])
        self.assertFalse(self.store.task_index_path.exists())
        self.assertFalse(self.store.current_target_task_path.exists())

    def test_self_identity_reports_admin_and_supervisor_roles(self):
        admin = self.gateway("查看我的ID", chat_id="oc_group_secret", sender_id="ou_admin_mock", message_id="om_self_admin")
        supervisor = self.gateway(
            "我的ID",
            chat_id="oc_group_secret",
            sender_id="ou_supervisor_mock",
            message_id="om_self_supervisor",
        )

        self.assertIn("open_id：ou_admin_mock", admin["reply_text"])
        self.assertIn("是否管理员：是", admin["reply_text"])
        self.assertIn("是否主管：否", admin["reply_text"])
        self.assertIn("open_id：ou_supervisor_mock", supervisor["reply_text"])
        self.assertIn("是否管理员：否", supervisor["reply_text"])
        self.assertIn("是否主管：是", supervisor["reply_text"])
        self.assertNotIn("oc_group_secret", admin["reply_text"])
        self.assertNotIn("oc_group_secret", supervisor["reply_text"])

    def test_self_identity_reports_same_open_id_as_admin_and_supervisor(self):
        result = handle_event(
            event(
                "查看我的ID",
                chat_id="oc_group_secret",
                sender_id="ou_dual_role",
                message_id="om_self_dual_role",
            ),
            store=self.store,
            roles={
                "admin_open_ids": ["ou_dual_role"],
                "supervisor_open_ids": ["ou_dual_role"],
            },
            group_bindings=self.bindings,
        )

        self.assertTrue(result["ok"])
        self.assertIn("open_id：ou_dual_role", result["reply_text"])
        self.assertIn("是否管理员：是", result["reply_text"])
        self.assertIn("是否主管：是", result["reply_text"])

    def test_bom_prefixed_roles_yaml_keeps_admin_open_ids(self):
        roles_path = self.root / "config" / "feishu_roles.yaml"
        roles_path.parent.mkdir(parents=True, exist_ok=True)
        roles_path.write_text(
            """admin_open_ids:
  - "ou_dual_role"

supervisor_open_ids:
  - "ou_dual_role"

business_chat_ids: []
supervisor_chat_ids: []
admin_chat_ids: []
""",
            encoding="utf-8-sig",
        )

        loaded = load_feishu_roles(roles_path)
        result = handle_event(
            event(
                "查看我的ID",
                chat_id="oc_group_secret",
                sender_id="ou_dual_role",
                message_id="om_self_bom",
            ),
            store=self.store,
            roles=loaded,
            group_bindings=self.bindings,
        )

        self.assertEqual(loaded["admin_open_ids"], ["ou_dual_role"])
        self.assertEqual(loaded["supervisor_open_ids"], ["ou_dual_role"])
        self.assertIn("是否管理员：是", result["reply_text"])
        self.assertIn("是否主管：是", result["reply_text"])

    def test_group_identity_commands_use_same_admin_role_helper(self):
        denied = handle_event(
            event("设置本群为一线群", chat_id="oc_group_secret", sender_id="ou_sales", message_id="om_set_denied"),
            store=self.store,
            roles={"admin_open_ids": ["ou_admin"], "supervisor_open_ids": []},
            group_bindings=self.bindings,
        )
        allowed = handle_event(
            event("设置本群为一线群", chat_id="oc_group_secret", sender_id="ou_admin", message_id="om_set_allowed"),
            store=self.store,
            roles={"admin_open_ids": ["ou_admin"], "supervisor_open_ids": []},
            group_bindings=self.bindings,
        )

        self.assertFalse(denied["ok"])
        self.assertIn("没有权限", denied["reply_text"])
        self.assertTrue(allowed["ok"])
        self.assertIn("已设置本群为一线群", allowed["reply_text"])

    def setup_business_and_supervisor(self, *, bound=False):
        self.gateway("设置本群为一线群", chat_name="定价一线群", message_id="om_business")
        self.gateway("设置本群为主管群", chat_id="oc_supervisor_secret_B", chat_name="主管复核群", message_id="om_supervisor")
        if bound:
            self.gateway("生成主管群绑定码", message_id="om_code")
            self.gateway("绑定一线群 BD-4826", chat_id="oc_supervisor_secret_B", message_id="om_bind")

    def create_waiting_manual_price_task(self, task_id):
        self.setup_business_and_supervisor(bound=True)
        self.create_needs_review_task(task_id, business_chat_id="oc_business_secret_A")
        result = sync_manual_review_to_supervisor(task_id, store=self.store, roles=roles(), group_bindings=self.bindings)
        self.assertTrue(result["ok"])
        return task_id

    def create_needs_review_task(self, task_id, *, business_chat_id):
        task_dir = self.store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        self.write_json(
            task_dir / "status.json",
            {
                "task_id": task_id,
                "status": "NEEDS_REVIEW",
                "technical_status": "SUCCEEDED",
                "business_status": "NEEDS_REVIEW",
                "business_chat_id": business_chat_id,
                "raw_chat_id": business_chat_id,
                "sender_open_id": "ou_sales_mock",
                "created_at": "2026-06-14T08:30:00+00:00",
                "updated_at": "2026-06-14T08:30:00+00:00",
            },
        )
        self.write_json(task_dir / "pricing_result.json", full_chain_manual_review_payload())
        return task_id

    def assert_user_text_safe(self, *texts):
        joined = "\n".join(texts)
        for forbidden in FORBIDDEN_USER_TEXT:
            self.assertNotIn(forbidden, joined)

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


def full_chain_manual_review_payload():
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_vehicle": "本田 雅阁 2021款 260TURBO 豪华版",
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": False,
        "boundary_reference_index": None,
        "boundary_reference_score": None,
        "s17_payload": {
            "final_reference_index": 1,
            "reference_price_10k": 9.84,
            "reference_score": 94.0,
            "target_score": 94.5,
            "manual_review_required": True,
            "manual_review_reasons": [
                "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING",
                "SAMPLE_SHORTAGE_MANUAL_REVIEW",
            ],
        },
        "pricing": {
            "base_reference_price_yuan": 98400,
            "target_guazi_listing_price_yuan": 96400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 94900,
            "guazi_return_price_yuan": 94900,
            "cost_yuan": 1000,
            "profit_rate": 0.08,
            "profit_yuan": 7592,
            "suggested_purchase_price_yuan": 86308,
            "manual_review_required": True,
        },
    }


if __name__ == "__main__":
    unittest.main()
