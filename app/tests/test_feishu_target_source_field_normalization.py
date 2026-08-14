import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_message_to_target_task import (  # noqa: E402
    load_target_field_aliases,
    parse_target_task_message,
)
from feishu_task_store import FeishuTaskStore  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 28, 8, 30, tzinfo=timezone.utc)


def full_message(model_line: str = "【车型】蔚来 ES6 2024款 75kWh") -> str:
    return f"""{model_line}
【有无天窗】有
【指导价】33.8
【排放标准】纯电
【上牌日期】25.2
【表显里程】2.053
【车辆颜色】灰
【过户次数】1
【保险到期】27.2.4
【车牌归属】唐山
【具体车况】原版原漆 前杠更换"""


class FeishuTargetSourceFieldNormalizationTest(unittest.TestCase):
    def parse(self, text: str):
        return parse_target_task_message(
            text,
            task_id="FS20260628_0003",
            raw_message_id="om_target",
            raw_sender_id="ou_sales",
            raw_chat_id="oc_business",
            clock=fixed_clock,
        )

    def test_parse_model_line_brand_series_year_config(self):
        result = self.parse(full_message())

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "蔚来")
        self.assertEqual(result.draft["series"], "ES6")
        self.assertEqual(result.draft["year_model"], "2024款")
        self.assertEqual(result.draft["config_model"], "75kWh")
        self.assertEqual(result.draft["raw_model_text"], "蔚来 ES6 2024款 75kWh")

    def test_parse_model_line_with_internal_label_space(self):
        result = self.parse(full_message("【车 型】蔚来 ES6 2024款 75kWh"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "蔚来")
        self.assertEqual(result.draft["series"], "ES6")

    def test_parse_buick_lacrosse_model_line(self):
        result = self.parse(full_message("【车型】别克 君越 2021款 652T 豪华型"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "别克")
        self.assertEqual(result.draft["series"], "君越")
        self.assertEqual(result.draft["year_model"], "2021款")
        self.assertEqual(result.draft["config_model"], "652T 豪华型")

    def test_parse_vw_teramont_model_line(self):
        result = self.parse(full_message("【车型】大众 途昂 2017款 330TSI 两驱豪华版"))

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "大众")
        self.assertEqual(result.draft["series"], "途昂")
        self.assertEqual(result.draft["year_model"], "2017款")
        self.assertEqual(result.draft["config_model"], "330TSI 两驱豪华版")

    def test_parse_bracket_label_without_colon(self):
        result = self.parse(full_message())

        self.assertEqual(result.draft["license_date"], "2025.02")
        self.assertEqual(result.draft["mileage_text"], "2.053")

    def test_parse_label_with_internal_spaces(self):
        text = full_message().replace("【上牌日期】25.2", "【上 牌 日 期】25.2")
        text = text.replace("【车辆颜色】灰", "【车 身 颜 色】灰")
        result = self.parse(text)

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["license_date"], "2025.02")
        self.assertEqual(result.draft["color"], "灰")

    def test_parse_vehicle_color_alias_to_color(self):
        text = full_message().replace("【车辆颜色】灰", "外观颜色 灰")
        result = self.parse(text)

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["color"], "灰")

    def test_parse_specific_condition_alias_to_condition_text(self):
        text = full_message().replace("【具体车况】原版原漆 前杠更换", "车辆车况 原版原漆 前杠更换")
        result = self.parse(text)

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["condition_text"], "原版原漆 前杠更换")

    def test_parse_plate_city_alias_to_city(self):
        text = full_message().replace("【车牌归属】唐山", "车牌归属地 唐山")
        result = self.parse(text)

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["city"], "唐山")

    def test_parse_short_register_date_25_2_to_2025_02(self):
        result = self.parse(full_message())

        self.assertEqual(result.draft["license_date"], "2025.02")
        self.assertEqual(result.draft["register_date"], "2025.02")

    def test_parse_insurance_date_27_2_4_to_2027_02_04(self):
        result = self.parse(full_message())

        self.assertEqual(result.draft["insurance_expire_date"], "2027.02.04")

    def test_parse_mileage_decimal_2_053(self):
        result = self.parse(full_message())

        self.assertEqual(result.draft["mileage_10k_km"], 2.053)

    def test_parse_transfer_count_int(self):
        result = self.parse(full_message())

        self.assertEqual(result.draft["transfer_count"], 1)

    def test_fs20260628_0003_equivalent_not_missing_existing_fields(self):
        result = self.parse(full_message())

        self.assertTrue(result.valid, result.validation_result)
        for field in ["品牌", "车系", "年款", "配置", "上牌日期", "表显里程", "颜色", "过户次数", "车况"]:
            self.assertNotIn(field, result.validation_result["missing_required_fields"])
        self.assertEqual(result.draft["guide_price_wan"], 33.8)
        self.assertEqual(result.draft["sunroof"], "有")
        self.assertEqual(result.draft["energy_type"], "纯电")

    def test_missing_feedback_lists_recognized_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = store.create_task_from_message(
                text=full_message("【车型】蔚来 2024款 75kWh"),
                raw_event={"message_id": "om_bad_model"},
                raw_message_id="om_bad_model",
                raw_sender_id="ou_sales",
                raw_chat_id="oc_business",
            )

            self.assertFalse(result.success)
            self.assertIn("已识别：", result.reply_text)
            self.assertIn("* 车型：蔚来 2024款 75kWh", result.reply_text)
            self.assertIn("* 上牌日期：2025.02", result.reply_text)
            self.assertIn("* 表显里程：2.053", result.reply_text)

    def test_missing_feedback_does_not_claim_present_fields_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FeishuTaskStore(Path(temp) / "feishu_tasks", clock=fixed_clock)
            result = store.create_task_from_message(
                text=full_message("【车型】蔚来 2024款 75kWh"),
                raw_event={"message_id": "om_bad_model"},
                raw_message_id="om_bad_model",
                raw_sender_id="ou_sales",
                raw_chat_id="oc_business",
            )

            self.assertFalse(result.success)
            for label in ["上牌日期", "表显里程", "颜色", "过户次数", "车况"]:
                self.assertNotRegex(result.reply_text, rf"缺少以下信息：[\s\S]*{label}")

    def test_model_line_missing_feedback_requires_first_line_structure(self):
        result = self.parse(full_message("【车型】蔚来 2024款 75kWh"))

        self.assertFalse(result.valid)
        self.assertIn("MODEL_STRICT_TEMPLATE_INCOMPLETE", result.validation_result["model_resolution_errors"])
        self.assertIn("【车型】 品牌 车系 年款 配置", result.reply_text)

    def test_raw_text_preserved_for_debug(self):
        result = self.parse(full_message())

        self.assertEqual(result.draft["raw_model_text"], "蔚来 ES6 2024款 75kWh")
        self.assertEqual(result.draft["vehicle_model"], "蔚来 ES6 2024款 75kWh")
        self.assertEqual(result.draft["full_model"], "蔚来 ES6 2024款 75kWh")

    def test_field_aliases_loaded_from_config(self):
        aliases = load_target_field_aliases()

        self.assertEqual(aliases["目标车型"], "model_config")
        self.assertEqual(aliases["车牌归属地"], "city")
        self.assertEqual(aliases["是否带天窗"], "sunroof_text")

    def test_no_regression_old_standard_format_with_colon(self):
        text = """定价
品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆"""
        result = self.parse(text)

        self.assertTrue(result.valid, result.validation_result)
        self.assertEqual(result.draft["brand"], "本田")
        self.assertEqual(result.draft["series"], "雅阁")
        self.assertEqual(result.draft["license_date"], "2021.06")
        self.assertEqual(result.draft["mileage_10k_km"], 5.8)


if __name__ == "__main__":
    unittest.main()
