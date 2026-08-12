import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_message_to_target_task import parse_target_task_message  # noqa: E402
from feishu_task_store import FeishuTaskStore  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 26, 1, 23, 45, tzinfo=timezone.utc)


def full_message(model_line: str) -> str:
    return f"""{model_line}
【有无天窗】 有
【指导价】 23.98
【排放标准】 国6
【上牌日期】 21.8
【表显里程】 4.9
【车辆颜色】 黑
【过户次数】 0
【保险到期】 26.8
【车牌归属】 唐山
【具体车况】 原版原漆"""


class FeishuTargetModelStrictTemplateParseTest(unittest.TestCase):
    def parse(self, text: str):
        return parse_target_task_message(
            text,
            task_id="FS20260626_0006",
            raw_message_id="om_model",
            raw_sender_id="ou_sender",
            raw_chat_id="oc_business",
            clock=fixed_clock,
        )

    def test_ora_black_cat_standard_template_parse(self):
        result = self.parse(full_message("【车型】 欧拉 黑猫 2019款 351km 亲子版"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "欧拉")
        self.assertEqual(result.draft["series"], "黑猫")
        self.assertEqual(result.draft["year_model"], "2019款")
        self.assertEqual(result.draft["config_model"], "351km 亲子版")
        self.assertEqual(result.draft["model_parse_source"], "target_model_strict_template")

    def test_buick_regal_standard_template_parse(self):
        result = self.parse(full_message("【车型】 别克 君越 2021款 652T 豪华型"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.status["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertEqual(result.draft["brand"], "别克")
        self.assertEqual(result.draft["series"], "君越")
        self.assertEqual(result.draft["year_model"], "2021款")
        self.assertEqual(result.draft["config_model"], "652T 豪华型")
        self.assertEqual(result.draft["model_parse_source"], "target_model_strict_template")
        self.assertIn("品牌：别克", result.reply_text)
        self.assertIn("车系：君越", result.reply_text)
        self.assertNotIn("车型字段无法确定品牌/车系", result.reply_text)

    def test_nbsp_and_spaced_model_label_parse(self):
        result = self.parse(full_message("【车\u00a0\u00a0\u00a0\u00a0\u00a0\u00a0型 】别克 君越 2021款 652T 豪华型"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "别克")
        self.assertEqual(result.draft["series"], "君越")
        self.assertEqual(result.draft["year_model"], "2021款")
        self.assertEqual(result.draft["config_model"], "652T 豪华型")

    def test_production_store_creates_confirmation_for_buick_regal(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = store.create_task_from_message(
                text=full_message("【车型】 别克 君越 2021款 652T 豪华型"),
                raw_event={"message_id": "om_model"},
                raw_message_id="om_model",
                raw_sender_id="ou_sender",
                raw_chat_id="oc_business",
            )

            self.assertTrue(result.success, result.data)
            self.assertEqual(result.status, "WAITING_TARGET_CONFIRMATION")
            self.assertIn("请确认目标车信息", result.reply_text)
            self.assertNotIn("车型字段无法确定品牌/车系", result.reply_text)
            draft = json.loads((store.task_dir(result.task_id) / "target_task_draft.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["brand"], "别克")
            self.assertEqual(draft["series"], "君越")
            self.assertEqual(draft["year_model"], "2021款")
            self.assertEqual(draft["config_model"], "652T 豪华型")

    def test_missing_brand_prompts_strict_template_format(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = store.create_task_from_message(
                text=full_message("【车型】 君越 2021款 652T 豪华型"),
                raw_event={"message_id": "om_missing_brand"},
                raw_message_id="om_missing_brand",
                raw_sender_id="ou_sender",
                raw_chat_id="oc_business",
            )

            self.assertFalse(result.success)
            self.assertEqual(result.status, "TARGET_INFO_NEEDS_CORRECTION")
            self.assertIn("车型字段格式不完整", result.reply_text)
            self.assertIn("【车型】 品牌 车系 年款 配置", result.reply_text)
            self.assertIn("【车型】 别克 君越 2021款 652T 豪华型", result.reply_text)
            self.assertNotIn("parser", result.reply_text)

    def test_missing_series_prompts_strict_template_format(self):
        result = self.parse(full_message("【车型】 别克 2021款 652T 豪华型"))

        self.assertFalse(result.valid)
        self.assertEqual(result.validation_result["model_resolution_errors"], ["MODEL_STRICT_TEMPLATE_INCOMPLETE"])
        self.assertIn("车型字段格式不完整", result.reply_text)
        self.assertIn("【车型】 品牌 车系 年款 配置", result.reply_text)

    def test_explicit_brand_series_take_priority(self):
        text = """【品牌】别克
【车系】君越
【车型】 2021款 652T 豪华型
【上牌日期】 21.8
【表显里程】 4.9
【车辆颜色】 黑
【过户次数】 0
【具体车况】 原版原漆"""
        result = self.parse(text)

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "别克")
        self.assertEqual(result.draft["series"], "君越")
        self.assertEqual(result.draft["year_model"], "2021款")
        self.assertEqual(result.draft["config_model"], "652T 豪华型")
        self.assertEqual(result.draft["model_parse_source"], "user_input")

    def test_alias_fallback_models_still_parse(self):
        cases = [
            ("【车型】2022款科鲁泽320自动悦享天窗版", "雪佛兰", "科鲁泽"),
            ("【车型】2019款 欧拉黑猫 351km 亲子版", "欧拉", "黑猫"),
            ("【车型】雅阁 2023款 260TURBO 豪华版", "本田", "雅阁"),
            ("【车型】星锐 2020款 1.5L 自动舒适版", "斯柯达", "星锐"),
        ]
        for model_line, brand, series in cases:
            with self.subTest(model_line=model_line):
                result = self.parse(full_message(model_line))
                self.assertTrue(result.valid, result.validation_result)
                self.assertEqual(result.draft["brand"], brand)
                self.assertEqual(result.draft["series"], series)

    def test_no_space_buick_regal_uses_known_brand_prefix_fallback(self):
        result = self.parse(full_message("【车型】别克君越2021款652T豪华型"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "别克")
        self.assertEqual(result.draft["series"], "君越")
        self.assertEqual(result.draft["year_model"], "2021款")
        self.assertEqual(result.draft["config_model"], "652T豪华型")
        self.assertEqual(result.draft["model_parse_source"], "known_brand_prefix_fallback")


if __name__ == "__main__":
    unittest.main()
