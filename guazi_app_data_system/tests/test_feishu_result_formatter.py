import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_result_formatter import (  # noqa: E402
    format_manual_review_business_notice,
    format_result_reply,
    format_supervisor_review_card,
    write_feishu_result_preview,
)
from pricing_result_collector import FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED  # noqa: E402


class FeishuResultFormatterTest(unittest.TestCase):
    def test_s07_color_mismatch_failure_reply_is_business_safe(self):
        result = format_result_reply(
            task_id="FS20260626_0011",
            status="FAILED",
            pricing_result=None,
            errors=["S10_COLOR_FILTER_MISMATCH"],
        )

        self.assertIn("颜色筛选结果与目标车颜色不一致", result.text)
        self.assertIn("S10_COLOR_FILTER_MISMATCH", result.warnings)
        for forbidden in [
            "adb",
            "uiautomator",
            "runner",
            "dispatcher",
            "traceback",
            "status.json",
            "APP_NOT_FOREGROUND",
            "未成功打开到前台",
        ]:
            self.assertNotIn(forbidden, result.text)

    def test_success_reply_contains_pricing_fields(self):
        result = format_result_reply(
            task_id="FS20260609_0001",
            status="SUCCEEDED",
            pricing_result={
                "target_vehicle": "本田 雅阁 2021款 260TURBO 豪华版",
                "boundary_confirmed": True,
                "boundary_reference_index": 3,
                "boundary_reference_score": 87,
                "final_reference_index": 2,
                "final_reference_score": 83,
                "final_reference_price": 108900,
                "target_score": 86,
                "target_guazi_listing_price_yuan": 120000,
                "guazi_service_fee_yuan": 1800,
                "guazi_net_payout_yuan": 118200,
                "guazi_return_price_yuan": 118200,
                "cost_yuan": 1400,
                "profit_yuan": 7800,
                "profit_rate": 0.08,
                "suggested_purchase_price_yuan": 106000,
                "final_purchase_price_yuan": 106000,
                "manual_review_required": False,
            },
        )

        self.assertIn("【定价完成】FS20260609_0001", result.text)
        self.assertIn("V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT", result.text)
        self.assertIn("target_score = 86", result.text)
        self.assertIn("final_reference_score = 83", result.text)
        self.assertIn("final_reference_price_yuan = 108900", result.text)
        self.assertIn("target_guazi_listing_price_yuan = 120000 元", result.text)
        self.assertIn("guazi_service_fee_yuan = 1800 元", result.text)
        self.assertIn("guazi_net_payout_yuan = 118200 元", result.text)
        self.assertIn("guazi_return_price_yuan = 118200 元", result.text)
        self.assertIn("cost_yuan = 1400 元", result.text)
        self.assertIn("profit_yuan = 7800 元", result.text)
        self.assertIn("profit_rate = 8%", result.text)
        self.assertIn("suggested_purchase_price_yuan = 106000 元", result.text)
        self.assertIn("final_purchase_price_yuan = 106000 元", result.text)

    def test_full_chain_priced_done_formats_as_success_not_failure(self):
        result = format_result_reply(
            task_id="FS20260701_0006",
            status="SUCCEEDED",
            pricing_result={
                "status": "FULL_CHAIN_PRICED_DONE",
                "final_status": "FULL_CHAIN_PRICED_DONE",
                "current_state": "FULL_CHAIN_PRICED_DONE",
                "s16_status": "S16_READY",
                "pricing_decision_source": "AUTOMATIC_PRICING",
                "manual_review_required": False,
                "target_score": {"score": 92},
                "boundary_confirmed": True,
                "boundary_reference_index": 4,
                "boundary_reference_score": 93,
                "selected_reference": {
                    "reference_index": 3,
                    "list_price_10k": 16.58,
                    "score": 89,
                },
                "selected_reference_score": {"score": 89},
                "pricing": {
                    "status": "priced",
                    "base_reference_price_yuan": 165800,
                    "target_guazi_listing_price_yuan": 158300,
                    "guazi_service_fee_yuan": 4000,
                    "guazi_net_payout_yuan": 154300,
                    "guazi_return_price_yuan": 154300,
                    "cost_yuan": 800,
                    "profit_rate": 0.08,
                    "profit_yuan": 13344,
                    "suggested_purchase_price_yuan": 140156,
                    "final_purchase_price_yuan": 140156,
                    "manual_review_required": False,
                },
                "s17_payload": {
                    "task_status": "priced",
                    "suggested_acquisition_price_yuan": 140156,
                    "final_reference_index": 3,
                    "reference_score": 89,
                    "target_score": 92,
                    "manual_review_required": False,
                },
                "trace": [{"status": "CONTINUE_NEXT_REFERENCE"}],
            },
        )

        self.assertIn("【定价完成】FS20260701_0006", result.text)
        self.assertIn("final_reference_index = 3", result.text)
        self.assertIn("final_purchase_price_yuan = 140156", result.text)
        self.assertNotIn("【本次定价未完成】", result.text)
        self.assertNotIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result.text)

    def test_missing_success_fields_do_not_format_as_success(self):
        result = format_result_reply(
            task_id="FS20260609_0001",
            status="SUCCEEDED",
            pricing_result={"manual_review_required": False},
        )

        self.assertIn("【本次定价未完成】FS20260609_0001", result.text)
        self.assertNotIn("【定价完成】", result.text)
        self.assertNotIn("未输出", result.text)
        self.assertTrue(result.warnings)

    def test_continue_next_reference_does_not_format_as_success(self):
        result = format_result_reply(
            task_id="FS20260623_0007",
            status="CONTINUE_NEXT_REFERENCE",
            pricing_result={
                "status": "CONTINUE_NEXT_REFERENCE",
                "final_status": "CONTINUE_NEXT_REFERENCE",
                "current_reference_index": 1,
                "next_reference_index": 2,
                "target_score": 82,
                "reference_score": 68,
                "manual_review_required": False,
            },
        )

        self.assertIn("【本次定价未完成】FS20260623_0007", result.text)
        self.assertIn("参考车边界还没有闭合", result.text)
        self.assertNotIn("【定价完成】", result.text)
        self.assertNotIn("final_purchase_price_yuan", result.text)

    def test_blocked_result_is_formatted_as_failure_not_success(self):
        result = format_result_reply(
            task_id="FS20260609_0001",
            status="SUCCEEDED",
            pricing_result={
                "status": "SECOND_STAGE_BLOCKED_NOT_AT_S10_READY",
                "issue_code": "PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE",
            },
        )

        self.assertIn("【定价失败】FS20260609_0001", result.text)
        self.assertIn("MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY", result.text)
        self.assertIn("当前主流程未到达 S10_READY", result.text)
        self.assertNotIn("【定价完成】", result.text)

    def test_schema_invalid_result_is_formatted_as_failure_not_success(self):
        result = format_result_reply(
            task_id="FS20260609_0001",
            status="SUCCEEDED",
            pricing_result={"status": "CONTRACT_ONLY"},
        )

        self.assertIn("【定价失败】FS20260609_0001", result.text)
        self.assertIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result.text)
        self.assertNotIn("【定价完成】", result.text)

    def test_second_stage_handoff_failure_uses_specific_description(self):
        result = format_result_reply(
            task_id="FS20260622_0009",
            status="FAILED",
            errors=["SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED"],
            pricing_result={
                "status": "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
                "issue_code": "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED",
            },
        )

        self.assertIn("【定价失败】FS20260622_0009", result.text)
        self.assertIn("SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED", result.text)
        self.assertIn("已进入瓜子结果页", result.text)
        self.assertIn("未能稳定绑定参考车", result.text)
        self.assertNotIn("手机执行环境暂不可用", result.text)

    def test_reference_card_binding_failure_hides_internal_code_from_business_text(self):
        result = format_result_reply(
            task_id="FS20260624_0001",
            status="FAILED",
            errors=["REFERENCE_CARD_BINDING_NOT_UNIQUE"],
            pricing_result={"status": "REFERENCE_CARD_BINDING_NOT_UNIQUE"},
        )

        self.assertIn("【本次定价未完成】FS20260624_0001", result.text)
        self.assertIn("参考车卡片", result.text)
        self.assertIn("无法唯一确认", result.text)
        self.assertNotIn("REFERENCE_CARD_BINDING_NOT_UNIQUE", result.text)
        self.assertNotIn("APP_NOT_FOREGROUND", result.text)
        self.assertNotIn("S10", result.text)
        self.assertNotIn("S14", result.text)
        self.assertNotIn("S15", result.text)

    def test_s10_next_reference_duplicate_failure_is_business_safe(self):
        result = format_result_reply(
            task_id="FS20260630_0001",
            status="FAILED",
            errors=["DUPLICATE_REFERENCE_CLICK_BLOCKED"],
            pricing_result={"status": "DUPLICATE_REFERENCE_CLICK_BLOCKED"},
        )

        self.assertIn("系统已开始自动定价", result.text)
        self.assertIn("参考车回采阶段未能继续执行", result.text)
        self.assertIn("DUPLICATE_REFERENCE_CLICK_BLOCKED", result.warnings)
        for forbidden in [
            "\u672c\u6b21\u5b9a\u4ef7\u672a\u5f00\u59cb",
            "\u624b\u673a\u6267\u884c\u73af\u5883\u6682\u4e0d\u53ef\u7528",
            "APP_NOT_FOREGROUND",
            "RESULT_SCHEMA_INVALID_FOR_PRICING",
            "S10",
            "\u5b9a\u4ef7\u5b8c\u6210",
        ]:
            self.assertNotIn(forbidden, result.text)

    def test_schema_wrapper_uses_duplicate_reference_pricing_result_issue(self):
        result = format_result_reply(
            task_id="FS20260701_0004",
            status="FAILED",
            errors=["RESULT_SCHEMA_INVALID_FOR_PRICING"],
            pricing_result={
                "status": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                "issue_code": "DUPLICATE_REFERENCE_CLICK_BLOCKED",
                "issue_context": {
                    "binding_result": {"stop_code": "DUPLICATE_REFERENCE_CLICK_BLOCKED"}
                },
            },
        )

        self.assertIn("系统已开始自动定价", result.text)
        self.assertIn("参考车回采阶段未能继续执行", result.text)
        self.assertIn("DUPLICATE_REFERENCE_CLICK_BLOCKED", result.warnings)
        self.assertNotIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result.text)
        self.assertNotIn("\u672c\u6b21\u5b9a\u4ef7\u672a\u5f00\u59cb", result.text)
        self.assertNotIn("\u624b\u673a\u6267\u884c\u73af\u5883\u6682\u4e0d\u53ef\u7528", result.text)

    def test_v33_recollected_previous_reference_needs_review_is_business_safe(self):
        code = "V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW"
        result = format_result_reply(
            task_id="FS20260701_0005",
            status="NEEDS_REVIEW",
            errors=[code],
            pricing_result={
                "status": "NEEDS_REVIEW",
                "business_status": "NEEDS_REVIEW",
                "manual_review_required": True,
                "manual_review_reasons": [code],
                "issue_code": code,
                "selected_reference_index": 3,
                "boundary_reference_index": 4,
                "boundary_reference_score": 93,
                "target_score": 92,
            },
        )

        self.assertIn("【需要人工复核定价】FS20260701_0005", result.text)
        self.assertIn("边界前参考车回采后仍不完整", result.text)
        self.assertIn(code, result.warnings)
        self.assertNotIn("RESULT_SCHEMA_INVALID_FOR_PRICING", result.text)
        self.assertNotIn("【本次定价未开始】", result.text)
        self.assertNotIn("手机执行环境暂不可用", result.text)

    def test_s13_repair_click_failure_uses_specific_description(self):
        result = format_result_reply(
            task_id="FS20260622_0012",
            status="FAILED",
            errors=["S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED"],
            pricing_result={
                "status": "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
                "issue_code": "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED",
            },
        )

        self.assertIn("【定价失败】FS20260622_0012", result.text)
        self.assertIn("S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED", result.text)
        self.assertIn("已进入检测报告", result.text)
        self.assertIn("未能安全打开历史修复详情", result.text)
        self.assertNotIn("手机执行环境暂不可用", result.text)

    def test_config_tier_mismatch_does_not_output_purchase_price(self):
        result = format_result_reply(
            task_id="FS20260619_0004",
            status="SUCCEEDED",
            pricing_result={
                "target_vehicle": "大众 迈腾 2018款 330TSI DSG 豪华型",
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
                "config_semantic_decision_code": "CONFIG_TIER_MISMATCH",
            },
        )

        self.assertIn("【目标车信息需修改】FS20260619_0004", result.text)
        self.assertIn("本次定价已停止", result.text)
        self.assertIn("等级差异", result.text)
        self.assertIn("重新发送完整车型配置", result.text)
        self.assertNotIn("【定价完成】", result.text)
        self.assertNotIn("suggested_purchase_price_yuan", result.text)
        self.assertNotIn("最终收车价", result.text)

    def test_powertrain_mismatch_does_not_output_purchase_price(self):
        result = format_result_reply(
            task_id="FS20260619_0005",
            status="SUCCEEDED",
            pricing_result={
                "target_vehicle": "大众 迈腾 2018款 330TSI DSG 豪华型",
                "manual_review_required": False,
                "suggested_purchase_price_yuan": 100000,
                "config_semantic_result": {"decision_code": "POWERTRAIN_TOKEN_MISMATCH"},
            },
        )

        self.assertIn("【目标车信息需修改】FS20260619_0005", result.text)
        self.assertIn("动力差异", result.text)
        self.assertNotIn("【定价完成】", result.text)
        self.assertNotIn("suggested_purchase_price_yuan", result.text)

    def test_manual_review_reason_aliases_are_supported(self):
        single = format_result_reply(
            task_id="FS20260609_0001",
            status="NEEDS_REVIEW",
            pricing_result={"manual_review_required": True, "manual_review_reason": "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING"},
        )
        multiple = format_result_reply(
            task_id="FS20260609_0002",
            status="NEEDS_REVIEW",
            pricing_result={"manual_review_required": True, "manual_review_reasons": ["A", "B"]},
        )

        self.assertIn("NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING", single.text)
        self.assertIn("A\nB", multiple.text)

    def test_full_chain_manual_review_reads_nested_fields_without_missing_warnings(self):
        result = format_result_reply(
            task_id="FS20260612_0002",
            status="SUCCEEDED",
            pricing_result=full_chain_manual_review_payload(),
        )

        self.assertEqual(result.warnings, [])
        self.assertIn("\u3010\u5f85\u4eba\u5de5\u590d\u6838\u3011FS20260612_0002", result.text)
        self.assertNotIn("\u3010\u5b9a\u4ef7\u5b8c\u6210\u3011", result.text)
        self.assertIn("NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING", result.text)
        self.assertIn("SAMPLE_SHORTAGE_MANUAL_REVIEW", result.text)
        self.assertIn("reference_score >= target_score", result.text)
        self.assertIn("target_score = 94.5", result.text)
        self.assertIn("final_reference_index = 1", result.text)
        self.assertIn("final_reference_score = 94.0", result.text)
        self.assertIn("final_reference_price_yuan = 98400", result.text)
        self.assertIn("target_guazi_listing_price_yuan = 96400", result.text)
        self.assertIn("guazi_service_fee_yuan = 1500", result.text)
        self.assertIn("guazi_return_price_yuan = 94900", result.text)
        self.assertIn("cost_yuan = 1000", result.text)
        self.assertIn("profit_yuan = 7592", result.text)
        self.assertIn("system_suggested_purchase_price_yuan = 86308", result.text)
        self.assertIn("请直接回复人工确认收车价", result.text)

    def test_manual_review_business_notice_uses_business_safe_text(self):
        result = format_manual_review_business_notice()

        self.assertIn("【本次定价进入人工复核】", result.text)
        self.assertIn("已通知主管处理", result.text)
        for forbidden in (
            "WAITING_MANUAL_PRICE",
            "NEEDS_REVIEW",
            "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "REFERENCE_CARD_BINDING_NOT_UNIQUE",
            "APP_NOT_FOREGROUND",
            "stale",
            "attempt",
            "gate_passed",
            "S10",
            "S14",
            "S15",
            "pricing_result.json",
            "status.json",
            "run_id",
            "generation_id",
            "dispatcher",
            "runner",
            "adb",
            "uiautomator",
            "traceback",
            "schema",
            "current_target_task",
            "pricing.lock",
        ):
            self.assertNotIn(forbidden, result.text)

    def test_supervisor_review_card_uses_fs0011_business_format(self):
        payload = {
            "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
            "target_score": {"score": 92.0},
            "s17_payload": {
                "manual_review_required": True,
                "manual_review_reasons": ["FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE"],
            },
        }
        target_task = {
            "brand": "欧拉",
            "series": "黑猫",
            "year_model": "2019款",
            "config_model": "351km 亲子版",
            "registration_date": "2020.08",
            "color": "白",
            "mileage_text": "4.5",
        }

        result = format_supervisor_review_card(
            task_id="FS20260623_0011",
            pricing_result=payload,
            target_task=target_task,
        )

        self.assertEqual(result.warnings, [])
        self.assertIn("【人工复核定价】FS20260623_0011", result.text)
        self.assertIn("欧拉 黑猫 2019款 351km 亲子版", result.text)
        self.assertIn("上牌：2020.08", result.text)
        self.assertIn("颜色：白", result.text)
        self.assertIn("里程：4.5", result.text)
        self.assertIn("目标车分数：92.0", result.text)
        self.assertIn("请直接回复最终收车价", result.text)

    def test_fs0004_manual_review_supervisor_card_shows_transparent_reasons(self):
        result = format_supervisor_review_card(
            task_id="FS20260625_0004",
            pricing_result=fs0004_manual_review_payload(),
            target_task=fs0004_target_task(),
        )

        self.assertEqual(result.warnings, [])
        self.assertIn("【人工复核定价】FS20260625_0004", result.text)
        self.assertIn("欧拉 黑猫 2019款 351km 亲子版", result.text)
        self.assertIn("价格分布离散较大", result.text)
        self.assertIn("三同价格：26300 / 31400 / 32700 / 34200", result.text)
        self.assertIn("价格离散率：30.04%", result.text)
        self.assertIn("可信参考车：3 辆 / 已尝试 4 辆", result.text)
        self.assertIn("可用于边界：第 1 / 第 2 / 第 4", result.text)
        self.assertIn("排除参考车：第 3 辆，车况证据未完整，未参与边界", result.text)
        self.assertIn("目标车分数：92.0", result.text)
        self.assertIn("边界参考车：第 4 辆，95.5 分", result.text)
        self.assertIn("最终参考车：第 2 辆，88.5 分", result.text)
        self.assertIn("系统测算价：25700 元", result.text)
        self.assertIn("目标车底边梁/门槛/边梁类车况是否按规则处理", result.text)
        self.assertIn("目标车大灯更换规则确认", result.text)
        self.assertIn("目标车出险次数采用默认分，需要确认", result.text)
        self.assertIn("目标车最大金额采用默认分，需要确认", result.text)
        for forbidden in ("traceback", "XML", "adb", "runner", "dispatcher", "status.json"):
            self.assertNotIn(forbidden, result.text)

    def test_fs0004_manual_review_business_notice_is_specific_and_business_safe(self):
        result = format_manual_review_business_notice(fs0004_manual_review_payload())

        self.assertIn("【本次定价进入人工复核】", result.text)
        self.assertIn("系统已完成可用参考车采集", result.text)
        self.assertIn("参考车价格分布差异较大", result.text)
        self.assertIn("目标车部分车况规则需要主管确认", result.text)
        self.assertIn("暂不能自动给出最终收车价", result.text)
        self.assertIn("请等待主管确认价格后再收车", result.text)
        for forbidden in (
            "PRICE_DISTRIBUTION_MANUAL_REVIEW",
            "TARGET_CONDITION_SILL_SCORING_REVIEW",
            "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW",
            "XML",
            "traceback",
            "adb",
            "runner",
            "dispatcher",
            "status.json",
            "S13",
            "S14",
            "bounds",
            "candidate",
        ):
            self.assertNotIn(forbidden, result.text)

    def test_manual_review_business_notice_outputs_clean_pricing_chain(self):
        payload = full_chain_manual_review_payload()
        payload.update(
            {
                "task_id": "FS20260627_0003",
                "target_guazi_listing_price_yuan": 83400,
                "guazi_service_fee_yuan": 1500,
                "guazi_net_payout_yuan": 81900,
                "cost_yuan": 1000,
                "profit_yuan": 6552,
                "profit_rate": 0.08,
                "suggested_purchase_price_yuan": 74348,
                "reference_history_current_task_valid": True,
            }
        )

        result = format_manual_review_business_notice(payload)

        self.assertIn("【本次定价进入人工复核】FS20260627_0003", result.text)
        self.assertIn("target_guazi_listing_price_yuan = 83400", result.text)
        self.assertIn("guazi_service_fee_yuan = 1500", result.text)
        self.assertIn("guazi_net_payout_yuan = 81900", result.text)
        self.assertIn("cost_yuan = 1000", result.text)
        self.assertIn("profit_yuan = 6552", result.text)
        self.assertIn("profit_rate = 8%", result.text)
        self.assertIn("system_suggested_purchase_price_yuan = 74348", result.text)

    def test_manual_review_business_notice_hides_price_when_reference_history_stale(self):
        payload = {
            "task_id": "FS20260627_0003",
            "target_guazi_listing_price_yuan": 83400,
            "guazi_service_fee_yuan": 1500,
            "guazi_net_payout_yuan": 81900,
            "cost_yuan": 1000,
            "profit_yuan": 6552,
            "profit_rate": 0.08,
            "suggested_purchase_price_yuan": 74348,
            "reference_history_current_task_valid": False,
        }

        result = format_manual_review_business_notice(payload)

        self.assertIn("参考车历史记录与本次三同车源顺序不一致", result.text)
        self.assertNotIn("system_suggested_purchase_price_yuan", result.text)
        self.assertNotIn("74348", result.text)

    def test_manual_review_confirmed_reply_shows_system_and_final_prices(self):
        payload = full_chain_manual_review_payload()
        payload.update(
            {
                "status": "MANUAL_REVIEW_CONFIRMED",
                "manual_review_confirmed": True,
                "system_suggested_purchase_price_yuan": 86308,
                "manual_confirmed_purchase_price_yuan": 86000,
                "manual_adjustment_yuan": -308,
                "manual_review_note": "未找到边界参考车，样本偏少，按系统测算价 86308 元下调取整，人工确认收车价 86000 元。",
                "final_purchase_price_yuan": 86000,
            }
        )

        result = format_result_reply(
            task_id="FS20260612_0002",
            status="MANUAL_REVIEW_CONFIRMED",
            pricing_result=payload,
        )

        self.assertEqual(result.warnings, [])
        self.assertIn("\u3010\u4eba\u5de5\u590d\u6838\u5df2\u786e\u8ba4\u3011FS20260612_0002", result.text)
        self.assertIn("system_suggested_purchase_price_yuan = 86308", result.text)
        self.assertIn("manual_confirmed_purchase_price_yuan = 86000", result.text)
        self.assertIn("manual_adjustment_yuan = -308", result.text)
        self.assertIn("final_purchase_price_yuan = 86000", result.text)
        self.assertNotIn("\u3010\u5f85\u4eba\u5de5\u590d\u6838\u3011", result.text)

    def test_manual_review_confirmed_reply_without_system_price_uses_supervisor_price_text(self):
        payload = full_chain_manual_review_payload()
        payload["pricing"].pop("suggested_purchase_price_yuan", None)
        payload.update(
            {
                "status": "MANUAL_REVIEW_CONFIRMED",
                "manual_review_confirmed": True,
                "manual_confirmed_purchase_price_yuan": 86000,
                "manual_price_yuan": 86000,
                "manual_adjustment_yuan": None,
                "system_suggested_price_missing": True,
                "system_suggested_price_required": False,
                "manual_review_note": "系统未输出自动建议价，主管人工确认收车价 86000 元。",
                "final_purchase_price_yuan": 86000,
                "final_price_source": "SUPERVISOR_MANUAL_CONFIRM",
                "pricing_decision_source": "MANUAL_SUPERVISOR_PRICE",
            }
        )

        result = format_result_reply(
            task_id="FS20260623_0011",
            status="MANUAL_REVIEW_CONFIRMED",
            pricing_result=payload,
        )

        self.assertEqual(result.warnings, [])
        self.assertIn("【人工复核已确认】FS20260623_0011", result.text)
        self.assertIn("最终收车价：86000 元", result.text)
        self.assertIn("确认来源：主管人工报价", result.text)
        self.assertIn("final_purchase_price_yuan = 86000", result.text)
        self.assertNotIn("system_suggested_purchase_price_yuan", result.text)
        self.assertNotIn("suggested_purchase_price_yuan", result.text)
        self.assertNotIn("SYSTEM_SUGGESTED_PRICE_MISSING", result.text)

    def test_failure_reply_contains_error_code(self):
        result = format_result_reply(
            task_id="FS20260609_0001",
            status="FAILED",
            pricing_result=None,
            errors=["RESULT_FILE_NOT_FOUND"],
        )

        self.assertIn("【定价失败】FS20260609_0001", result.text)
        self.assertIn("RESULT_FILE_NOT_FOUND", result.text)

    def test_first_stage_failure_reply_contains_guidance(self):
        result = format_result_reply(
            task_id="FS20260611_0001",
            status="FAILED",
            pricing_result=None,
            errors=["FIRST_STAGE_NOT_S10_READY"],
        )

        self.assertIn("【定价失败】FS20260611_0001", result.text)
        self.assertIn("FIRST_STAGE_NOT_S10_READY", result.text)
        self.assertIn("第一段 S01-S10 未到达 S10_READY", result.text)

    def test_desktop_upgrade_modal_failure_reply_forbids_immediate_upgrade(self):
        result = format_result_reply(
            task_id="FS20260611_0002",
            status="FAILED",
            pricing_result=None,
            errors=["DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS"],
        )

        self.assertIn("DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS", result.text)
        self.assertIn("拒绝点击“立即升级”", result.text)

    def test_login_required_failure_reply_forbids_auto_login(self):
        result = format_result_reply(
            task_id="FS20260612_0002",
            status="FAILED",
            pricing_result=None,
            errors=["LOGIN_REQUIRED_MANUAL"],
        )

        self.assertIn("LOGIN_REQUIRED_MANUAL", result.text)
        self.assertIn("不会输入手机号", result.text)

    def test_write_preview_file(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir = Path(temp)
            (task_dir / "pricing_result.json").write_text(
                json.dumps({"manual_review_required": False}, ensure_ascii=False),
                encoding="utf-8",
            )

            write_feishu_result_preview(task_dir=task_dir, task_id="FS20260609_0001", status="SUCCEEDED")

            self.assertTrue((task_dir / "feishu_result_reply.preview.txt").exists())

    def test_write_preview_blocks_cross_task_success_before_feishu_send(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir = Path(temp) / "data" / "feishu_tasks" / "FS20260702_0010"
            task_dir.mkdir(parents=True)
            (task_dir / "first_stage_result.json").write_text(
                json.dumps({"task_id": "FS20260702_0010", "target_fingerprint": "buick-lacrosse"}, ensure_ascii=False),
                encoding="utf-8",
            )
            pricing_result = {
                "task_id": "FS20260702_0003",
                "produced_by_task_id": "FS20260702_0003",
                "target_fingerprint": "nio-es6",
                "task_target_fingerprint": "nio-es6",
                "status": "FULL_CHAIN_PRICED_DONE",
                "final_status": "FULL_CHAIN_PRICED_DONE",
                "current_state": "FULL_CHAIN_PRICED_DONE",
                "pricing_decision_source": "AUTOMATIC_PRICING",
                "manual_review_required": False,
                "target_score": 86,
                "boundary_confirmed": True,
                "boundary_reference_index": 3,
                "boundary_reference_score": 87,
                "final_reference_index": 2,
                "final_reference_score": 83,
                "final_reference_price": 108900,
                "target_guazi_listing_price_yuan": 120000,
                "guazi_service_fee_yuan": 3000,
                "guazi_net_payout_yuan": 117000,
                "guazi_return_price_yuan": 117000,
                "cost_yuan": 1400,
                "profit_yuan": 7800,
                "profit_rate": 0.08,
                "suggested_purchase_price_yuan": 106000,
                "final_purchase_price_yuan": 106000,
            }
            (task_dir / "pricing_result.json").write_text(
                json.dumps(pricing_result, ensure_ascii=False),
                encoding="utf-8",
            )

            result = write_feishu_result_preview(task_dir=task_dir, task_id="FS20260702_0010", status="SUCCEEDED")

            preview = (task_dir / "feishu_result_reply.preview.txt").read_text(encoding="utf-8")
            self.assertIn(FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED, result.warnings)
            self.assertNotIn("target_score =", preview)
            guard = json.loads((task_dir / "final_feedback_target_scope_guard.json").read_text(encoding="utf-8"))
            self.assertTrue(guard["cross_task_success_result_blocked_before_feishu_send"])
            self.assertEqual(guard["primary_error_code"], "CROSS_TASK_PRICING_RESULT_REJECTED")


def full_chain_manual_review_payload():
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "final_status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "current_state": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_vehicle": "Honda Accord 2021 260TURBO",
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
            "profit_yuan": 7592,
            "suggested_purchase_price_yuan": 86308,
            "manual_review_required": True,
        },
    }


def fs0004_target_task():
    return {
        "brand": "欧拉",
        "series": "黑猫",
        "year_model": "2019款",
        "config_model": "351km 亲子版",
        "registration_date": "2020.08",
        "color": "白",
        "mileage_text": "4.5",
    }


def fs0004_manual_review_payload():
    return {
        "status": "FULL_CHAIN_MANUAL_REVIEW_DONE",
        "target_score": {
            "score": 92.0,
            "review_reasons": [
                "目标车缺少出险次数，已采用默认分。",
                "目标车缺少最大金额，已采用默认分。",
                "TARGET_CONDITION_SILL_SCORING_REVIEW",
                "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW",
            ],
        },
        "reference_selection_rule": "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        "boundary_confirmed": True,
        "boundary_reference_index": 4,
        "boundary_reference_score": 95.5,
        "pre_boundary_reference_index": 2,
        "selected_reference": {
            "reference_index": 2,
            "score": 88.5,
            "list_price_10k": 3.14,
        },
        "reference_history": [
            {
                "reference_index": 1,
                "list_price_10k": 2.63,
                "reference_score": 68.0,
                "reference_score_trustworthy": True,
                "reference_score_usable_for_boundary": True,
                "excluded_from_boundary": False,
            },
            {
                "reference_index": 2,
                "list_price_10k": 3.14,
                "reference_score": 88.5,
                "reference_score_trustworthy": True,
                "reference_score_usable_for_boundary": True,
                "excluded_from_boundary": False,
            },
            {
                "reference_index": 3,
                "list_price_10k": 3.27,
                "reference_score": 96.5,
                "reference_score_trustworthy": False,
                "reference_score_usable_for_boundary": False,
                "excluded_from_boundary": True,
                "excluded_from_boundary_reason": "S14_COLLECTION_INCOMPLETE_UNRECOVERABLE",
            },
            {
                "reference_index": 4,
                "list_price_10k": 3.42,
                "reference_score": 95.5,
                "reference_score_trustworthy": True,
                "reference_score_usable_for_boundary": True,
                "excluded_from_boundary": False,
            },
        ],
        "s17_payload": {
            "manual_review_required": True,
            "manual_review_reasons": [
                "目标车缺少出险次数，已采用默认分。",
                "目标车缺少最大金额，已采用默认分。",
                "TARGET_CONDITION_SILL_SCORING_REVIEW",
                "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW",
                "PRICE_DISTRIBUTION_MANUAL_REVIEW",
            ],
            "target_score": 92.0,
            "boundary_confirmed": True,
            "boundary_reference_index": 4,
            "boundary_reference_score": 95.5,
            "pre_boundary_reference_index": 2,
            "final_reference_index": 2,
            "reference_score": 88.5,
            "suggested_acquisition_price_yuan": 25700,
            "competition_coefficient_reasons": [
                {
                    "factor": "price_distribution_adjustment",
                    "reason": "price_distribution_highly_discrete_manual_review",
                    "data_source": {
                        "price_list_yuan": [26300, 31400, 32700, 34200],
                        "price_spread_rate": 0.3004,
                    },
                }
            ],
        },
    }


if __name__ == "__main__":
    unittest.main()
