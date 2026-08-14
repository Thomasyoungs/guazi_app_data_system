import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_message_to_target_task import parse_target_task_message  # noqa: E402
import feishu_message_to_target_task  # noqa: E402


def fixed_clock():
    return datetime(2026, 6, 9, 8, 30, tzinfo=timezone.utc)


SAMPLE_TEMPLATE = """定价
品牌：本田
车系：雅阁
车型配置：2021款 260TURBO 豪华版
上牌日期：2021-06
表显里程：5.8万公里
颜色：白色
过户次数：1
车况：右前门喷漆，前杠喷漆
出险次数：1
最大金额：3200
城市：唐山
备注：客户着急卖
"""


class FeishuMessageToTargetTaskTest(unittest.TestCase):
    def parse(self, text=SAMPLE_TEMPLATE):
        return parse_target_task_message(
            text,
            task_id="FS20260609_0001",
            raw_message_id="om_xxx",
            raw_sender_id="ou_xxx",
            raw_chat_id="oc_xxx",
            clock=fixed_clock,
        )

    def test_complete_template_parse_success(self):
        result = self.parse()

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["brand"], "本田")
        self.assertEqual(result.draft["series"], "雅阁")
        self.assertEqual(result.draft["model_config"], "2021款 260TURBO 豪华版")
        self.assertEqual(result.draft["license_date"], "2021.06")
        self.assertEqual(result.draft["register_date"], "2021.06")
        self.assertEqual(result.draft["registration_date"], "2021.06")
        self.assertEqual(result.draft["register_year"], 2021)
        self.assertEqual(result.draft["registration_date_year"], 2021)
        self.assertEqual(result.validation_result["missing_required_fields"], [])

    def test_complete_template_preserves_optional_and_raw_fields(self):
        result = self.parse()

        self.assertEqual(result.draft["accident_count_text"], "1")
        self.assertEqual(result.draft["max_claim_amount_text"], "3200")
        self.assertEqual(result.draft["city"], "唐山")
        self.assertEqual(result.draft["remark"], "客户着急卖")
        self.assertEqual(result.draft["raw_message_id"], "om_xxx")
        self.assertEqual(result.draft["raw_sender_id"], "ou_xxx")
        self.assertEqual(result.draft["raw_chat_id"], "oc_xxx")
        self.assertEqual(result.draft["created_at"], "2026-06-09T08:30:00+00:00")

    def test_chinese_colon_parse_success(self):
        result = self.parse(SAMPLE_TEMPLATE)

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["mileage_text"], "5.8万公里")

    def test_english_colon_parse_success(self):
        result = self.parse(SAMPLE_TEMPLATE.replace("：", ":"))

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["transfer_count_text"], "1")

    def test_field_aliases_parse_success(self):
        text = """定价
品牌：本田
车系：雅阁
配置：2021款 260TURBO 豪华版
上牌日期：2021-06
里程：5.8万公里
颜色：白色
过户：1
车况描述：右前门喷漆
出险：1
金额：3200
"""
        result = self.parse(text)

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["model_config"], "2021款 260TURBO 豪华版")
        self.assertEqual(result.draft["mileage_text"], "5.8万公里")
        self.assertEqual(result.draft["max_claim_amount_text"], "3200")

    def test_missing_brand_is_inferred_from_series(self):
        result = self.parse(self.remove_line("品牌"))

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["brand"], "本田")
        self.assertEqual(result.draft["brand_source"], "inferred_from_model_text")
        self.assertEqual(result.status["status"], "WAITING_TARGET_CONFIRMATION")

    def test_keluze_without_brand_series_is_inferred_from_model_text(self):
        text = """【车型】2022款科鲁泽320自动悦享天窗版（1.5L四缸）
【有无天窗】有
【指导价】11.49
【排放标准】国六
【上牌日期】22.8
【表显里程】1.05
【车辆颜色】红
【过户次数】0
【保险到期】26.8
【车牌归属】唐山
【具体车况】原漆，右后叶小坑掉漆
"""
        result = self.parse(text)

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["brand"], "雪佛兰")
        self.assertEqual(result.draft["series"], "科鲁泽")
        self.assertEqual(result.draft["year_model"], "2022款")
        self.assertEqual(result.draft["config_model"], "320自动悦享天窗版（1.5L四缸）")
        self.assertEqual(result.draft["raw_config_model"], "科鲁泽320自动悦享天窗版（1.5L四缸）")
        self.assertEqual(result.draft["brand_source"], "inferred_from_model_text")
        self.assertEqual(result.draft["series_source"], "inferred_from_model_text")
        self.assertEqual(result.draft["model_parse_source"], "universal_vehicle_parser_v1")
        self.assertEqual(result.draft["vehicle_parser_version"], "UNIVERSAL_VEHICLE_PARSER_V1")
        self.assertEqual(result.draft["license_date"], "2022.08")
        self.assertEqual(result.draft["register_date"], "2022.08")
        self.assertEqual(result.draft["registration_date"], "2022.08")
        self.assertEqual(result.draft["register_year"], 2022)
        self.assertEqual(result.draft["registration_date_year"], 2022)
        self.assertIn("品牌：雪佛兰（系统识别）", result.reply_text)
        self.assertIn("车系：科鲁泽（系统识别）", result.reply_text)
        self.assertNotIn("品牌", result.validation_result["missing_required_fields"])
        self.assertNotIn("车系", result.validation_result["missing_required_fields"])
        self.assertNotIn("人工确认收车价格式无法识别", result.reply_text)

    def test_ora_black_cat_without_brand_series_is_inferred_by_universal_parser(self):
        text = """【车    型】2019款 欧拉黑猫 351km 亲子版
【有无天窗】无
【指导价】7.38
【排放标准】电动
【上牌日期】20.8
【表显里程】4.5
【车辆颜色】白
【过户次数】2
【保险到期】26.8
【车牌归属】唐山
【具体车况】右后叶板金 右后门板喷 左后叶剐蹭变形
"""
        result = self.parse(text)

        self.assertTrue(result.valid)
        self.assertEqual(result.draft["brand"], "欧拉")
        self.assertEqual(result.draft["series"], "黑猫")
        self.assertEqual(result.draft["year_model"], "2019款")
        self.assertEqual(result.draft["config_model"], "351km 亲子版")
        self.assertEqual(result.draft["raw_config_model"], "欧拉黑猫 351km 亲子版")
        self.assertEqual(result.draft["vehicle_parser_matched_alias"], "欧拉黑猫")
        self.assertEqual(result.draft["vehicle_model_decision_code"], "VEHICLE_MODEL_PARSE_OK")
        self.assertEqual(result.draft["vehicle_model_identity_key"], "欧拉|黑猫|2019款|351km亲子版")
        self.assertEqual(result.draft["vehicle_model_identity_key_v2"], "欧拉|黑猫|2019款")
        self.assertEqual(result.draft["vehicle_model_identity_key_v2_scope"], "brand_series_year")
        self.assertEqual(result.draft["config_semantic_key"], "351KM亲子版")
        self.assertEqual(
            result.draft["config_semantic_version"],
            "FEISHU_CONFIG_SEMANTIC_NORMALIZATION_V1",
        )
        self.assertEqual(result.draft["canonical_brand"], "欧拉")
        self.assertEqual(result.draft["canonical_series"], "黑猫")
        self.assertEqual(result.draft["canonical_year_model"], "2019款")
        self.assertEqual(result.draft["canonical_config_model"], "351km亲子版")
        self.assertEqual(result.draft["license_date"], "2020.08")
        self.assertIn("品牌：欧拉（系统识别）", result.reply_text)
        self.assertIn("车系：黑猫（系统识别）", result.reply_text)
        self.assertNotIn("车型字段无法确定品牌/车系", result.reply_text)

    def test_parse_target_task_uses_single_vehicle_parser_entry(self):
        real_parser = feishu_message_to_target_task.parse_vehicle_model
        with patch.object(feishu_message_to_target_task, "parse_vehicle_model", side_effect=real_parser) as parser:
            result = self.parse("""【车型】2019款 欧拉黑猫 351km 亲子版
【上牌日期】20.8
【表显里程】4.5
【车辆颜色】白
【过户次数】2
【具体车况】右后叶板金
""")

        self.assertTrue(result.valid)
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(parser.call_args.args[0], "2019款 欧拉黑猫 351km 亲子版")

    def test_unknown_model_prompts_model_resolution_not_price(self):
        text = """【车型】2022款未知车款自动豪华版
【上牌日期】22.8
【表显里程】1.05
【车辆颜色】红
【过户次数】0
【具体车况】原漆
"""
        result = self.parse(text)

        self.assertFalse(result.valid)
        self.assertEqual(result.status["status"], "DRAFT_NEEDS_MODEL_RESOLUTION")
        self.assertIn("MODEL_BRAND_SERIES_UNRESOLVED", result.validation_result["model_resolution_errors"])
        self.assertIn("车型字段无法确定品牌/车系", result.reply_text)
        self.assertNotIn("人工确认收车价格式无法识别", result.reply_text)

    def test_unrecognized_registration_date_prompts_target_info_not_price(self):
        result = self.parse(SAMPLE_TEMPLATE.replace("2021-06", "ABC"))

        self.assertFalse(result.valid)
        self.assertEqual(result.status["status"], "DRAFT_NEEDS_TARGET_INFO")
        self.assertIn("REGISTRATION_DATE_UNRECOGNIZED", result.validation_result["date_errors"])
        self.assertIn("2022.08", result.reply_text)
        self.assertIn("22.8", result.reply_text)
        self.assertNotIn("人工确认收车价格式无法识别", result.reply_text)

    def test_explicit_brand_series_conflict_is_not_silently_overwritten(self):
        text = """品牌：本田
车系：雅阁
车型：2022款科鲁泽320自动悦享天窗版
上牌日期：22.8
表显里程：1.05
车辆颜色：红
过户次数：0
具体车况：原漆
"""
        result = self.parse(text)

        self.assertFalse(result.valid)
        self.assertEqual(result.status["status"], "DRAFT_NEEDS_MODEL_RESOLUTION")
        self.assertIn("MODEL_BRAND_SERIES_CONFLICT", result.validation_result["model_resolution_errors"])
        self.assertIn("不一致", result.reply_text)

    def test_missing_model_config_is_invalid(self):
        result = self.parse(self.remove_line("车型配置"))

        self.assertFalse(result.valid)
        self.assertIn("车型配置", result.validation_result["missing_required_fields"])

    def test_missing_mileage_is_invalid(self):
        result = self.parse(self.remove_line("表显里程"))

        self.assertFalse(result.valid)
        self.assertIn("表显里程", result.validation_result["missing_required_fields"])

    def test_missing_transfer_count_is_invalid(self):
        result = self.parse(self.remove_line("过户次数"))

        self.assertFalse(result.valid)
        self.assertIn("过户次数", result.validation_result["missing_required_fields"])

    def test_missing_optional_fields_is_valid(self):
        text = self.remove_line("出险次数")
        text = "\n".join(line for line in text.splitlines() if not line.startswith("最大金额"))
        text = "\n".join(line for line in text.splitlines() if not line.startswith("城市"))
        text = "\n".join(line for line in text.splitlines() if not line.startswith("备注"))

        result = self.parse(text)

        self.assertTrue(result.valid)
        self.assertNotIn("出险次数", result.validation_result["missing_required_fields"])

    def test_missing_accident_count_does_not_default_to_zero(self):
        result = self.parse(self.remove_line("出险次数"))

        self.assertTrue(result.valid)
        self.assertNotIn("accident_count_text", result.draft)

    def test_missing_max_claim_amount_does_not_default_to_zero(self):
        result = self.parse(self.remove_line("最大金额"))

        self.assertTrue(result.valid)
        self.assertNotIn("max_claim_amount_text", result.draft)

    def test_complete_template_generates_draft_status(self):
        result = self.parse()

        self.assertEqual(result.status["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertEqual(result.draft["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertIn("请确认目标车信息：", result.reply_text)
        self.assertIn("确认无误请回复：确认", result.reply_text)
        self.assertNotIn("确认 FS20260609_0001", result.reply_text)

    def test_missing_brand_generates_waiting_confirmation_status(self):
        result = self.parse(self.remove_line("品牌"))

        self.assertEqual(result.status["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertEqual(result.draft["status"], "WAITING_TARGET_CONFIRMATION")
        self.assertIn("品牌：本田（系统识别）", result.reply_text)

    def remove_line(self, prefix):
        return "\n".join(
            line for line in SAMPLE_TEMPLATE.splitlines() if not line.startswith(prefix)
        )


if __name__ == "__main__":
    unittest.main()
