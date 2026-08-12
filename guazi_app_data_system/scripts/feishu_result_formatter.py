"""Format local pricing results into Feishu reply preview text."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

try:
    from pricing_result_collector import (
        CONFIG_MISMATCH_HARD_STOP,
        CONTINUE_NEXT_REFERENCE,
        CROSS_TASK_PRICING_RESULT_REJECTED,
        CROSS_TASK_SUCCESS_RESULT_BLOCKED_BEFORE_FEISHU_SEND,
        FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED,
        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        is_automatic_pricing_terminal_success,
        is_pricing_result_manual_review,
        pricing_result_config_mismatch_reason,
        pricing_result_non_terminal_status,
        pricing_result_manual_review_reasons,
        pricing_success_missing_required_fields,
        resolve_pricing_result_field,
        target_fingerprints_from_artifacts,
        validate_result_task_scope,
        validate_pricing_result_payload,
    )
except ImportError:  # pragma: no cover - supports package-style imports in tests.
    from scripts.pricing_result_collector import (
        CONFIG_MISMATCH_HARD_STOP,
        CONTINUE_NEXT_REFERENCE,
        CROSS_TASK_PRICING_RESULT_REJECTED,
        CROSS_TASK_SUCCESS_RESULT_BLOCKED_BEFORE_FEISHU_SEND,
        FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED,
        RESULT_MISSING_REQUIRED_PRICING_FIELDS,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        is_automatic_pricing_terminal_success,
        is_pricing_result_manual_review,
        pricing_result_config_mismatch_reason,
        pricing_result_non_terminal_status,
        pricing_result_manual_review_reasons,
        pricing_success_missing_required_fields,
        resolve_pricing_result_field,
        target_fingerprints_from_artifacts,
        validate_result_task_scope,
        validate_pricing_result_payload,
    )


MISSING = "未输出"
V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW = (
    "V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW"
)
S14_BUSINESS_SAFE_FAILURE_CODES = {
    "S14_CONTRACT_DEGRADED_NEEDS_REVIEW",
    "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED",
    "S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED",
}
GUAZI_PUSH_POPUP_BUSINESS_SAFE_FAILURE_CODES = {
    "GUAZI_TRANSIENT_POPUP_BLOCKED_FLOW",
    "GUAZI_PUSH_POPUP_CLOSE_TARGET_NOT_FOUND",
    "GUAZI_PUSH_POPUP_CLOSE_FAILED",
}
S13_BUSINESS_SAFE_FAILURE_CODES = {
    "S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND",
    "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND",
    "S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED",
    "S12_TO_S13_REGION_PROOF_NOT_CONFIRMED",
    "S13_REGION_HEADERS_NOT_FOUND_AFTER_S12_BODY_REACHED",
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED_AFTER_S12_BODY_REACHED",
    "S13_REGION_HEADERS_NOT_FOUND",
    "S13_HISTORY_REPAIR_TABLE_NOT_CONFIRMED",
    "S13_REGION_HISTORY_COUNT_BINDING_FAILED",
    "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED",
}
REFERENCE_PHYSICAL_UI_BUSINESS_SAFE_FAILURE_CODES = {
    "S13_RETURN_TO_S10_ACTION_NOT_EXECUTED",
    "S13_RETURN_ACTION_EXECUTED_BUT_STILL_ON_S13",
    "S13_RETURN_ACTION_LANDED_ON_NON_S10_PAGE",
    "S13_RETURN_TO_S10_PROOF_STALE_OR_MISSING",
    "REFERENCE_DESTINATION_IDENTITY_NOT_MATCHED",
    "REFERENCE_HISTORY_ENTRY_BLOCKED_BY_MISSING_PHYSICAL_UI_PROOF",
    "REFERENCE_HISTORY_ENTRY_BLOCKED_BY_REUSED_PHYSICAL_PAGE_SIGNATURE",
    "ALL_REFERENCES_EXHAUSTED_BLOCKED_BY_MISSING_PHYSICAL_EVIDENCE",
}
S07_COLOR_BUSINESS_SAFE_FAILURE_CODES = {
    "S07_COLOR_CANDIDATE_TARGET_MISMATCH",
    "S07_COLOR_CLICK_POINT_OUTSIDE_CANDIDATE_BOUNDS",
    "S07_COLOR_CANDIDATE_PARENT_BOUNDS_AMBIGUOUS",
    "S07_COLOR_SELECTION_RETRY_TARGET_NOT_BINDABLE",
    "S07_COLOR_SELECTION_TARGET_MISMATCH_AFTER_RETRY",
    "S08_COLOR_FILTER_MISMATCH",
    "S10_COLOR_FILTER_MISMATCH",
}
S07_AGE_BUSINESS_SAFE_FAILURE_CODES = {
    "S07_AGE_SLIDER_FASTPATH_FAILED",
    "S07_AGE_SLIDER_FINAL_VALUE_MISMATCH",
    "S07_AGE_SLIDER_FALLBACK_BUDGET_EXCEEDED",
    "S07_AGE_SLIDER_DIRECT_FASTPATH_NO_EFFECT",
    "S07_AGE_SLIDER_REAL_HANDLE_BINDING_FAILED",
    "S07_AGE_SLIDER_REAL_TOUCH_NO_EFFECT",
    "S07_AGE_LEFT_SLIDER_REAL_TOUCH_NO_EFFECT",
    "S07_AGE_SLIDER_HANDLE_BINDING_FAILED",
    "S07_AGE_SLIDER_DRAG_START_OUTSIDE_HANDLE_BOUNDS",
    "S07_AGE_EXACT_RANGE_VERIFY_FAILED",
    "S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE",
    "S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED",
    "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED",
    "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED",
    "S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED",
    "S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH",
    "S07_POST_ACTION_FRESH_EVIDENCE_MISSING",
    "S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH",
    "S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER",
}
S10_NEXT_REFERENCE_BUSINESS_SAFE_FAILURE_CODES = {
    "DUPLICATE_REFERENCE_CLICK_BLOCKED",
    "NEXT_REFERENCE_CARD_NOT_FOUND_IN_S10",
    "NEXT_REFERENCE_CARD_NOT_FULLY_VISIBLE_AFTER_SCROLL",
    "S10_NEXT_REFERENCE_ABSOLUTE_CARD_NOT_FOUND",
    "S10_NEXT_REFERENCE_CARD_NOT_UNIQUE",
    "S10_NEXT_REFERENCE_PARTIAL_CARD_NOT_COMPLETED",
    "REFERENCE_LOOP_STATE_RESET_DETECTED",
}


@dataclass(frozen=True)
class FormatResult:
    text: str
    warnings: list[str]


def value_from(payload: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        value = resolve_pricing_result_field(payload, alias, default=MISSING)
        if value != MISSING:
            return value
    return MISSING


def is_manual_review_required(payload: dict[str, Any]) -> bool:
    return is_pricing_result_manual_review(payload)


def manual_review_reasons(payload: dict[str, Any]) -> list[str]:
    return pricing_result_manual_review_reasons(payload)


def format_manual_review_business_notice(pricing_result: dict[str, Any] | None = None) -> FormatResult:
    pricing_payload = pricing_result or {}
    pricing_chain = _manual_review_business_pricing_chain_context(pricing_payload)
    if pricing_chain.get("service_fee_contract_mismatch"):
        text = "\n".join(
            [
                "【本次定价进入人工复核】",
                "系统价格规则校验失败，已通知管理员处理。",
                "请等待主管确认价格后再收车。",
            ]
        )
        return FormatResult(text=text, warnings=["SERVICE_FEE_CONTRACT_MISMATCH"])
    if pricing_chain["stale_reference_history"]:
        text = "\n".join(
            [
                "\u3010\u672c\u6b21\u5b9a\u4ef7\u8fdb\u5165\u4eba\u5de5\u590d\u6838\u3011",
                "\u7cfb\u7edf\u5df2\u5b8c\u6210\u53c2\u8003\u8f66\u91c7\u96c6\uff0c\u4f46\u53c2\u8003\u8f66\u5386\u53f2\u8bb0\u5f55\u4e0e\u672c\u6b21\u4e09\u540c\u8f66\u6e90\u987a\u5e8f\u4e0d\u4e00\u81f4\uff0c\u6682\u4e0d\u8f93\u51fa\u7cfb\u7edf\u6d4b\u7b97\u4ef7\u3002",
                "\u5df2\u901a\u77e5\u4e3b\u7ba1\u5904\u7406\uff0c\u8bf7\u7b49\u5f85\u4e3b\u7ba1\u786e\u8ba4\u4ef7\u683c\u540e\u518d\u6536\u8f66\u3002",
            ]
        )
        return FormatResult(text=text, warnings=[])
    if pricing_chain["available"]:
        task_id = str(pricing_payload.get("task_id") or "").strip()
        title = "\u3010\u672c\u6b21\u5b9a\u4ef7\u8fdb\u5165\u4eba\u5de5\u590d\u6838\u3011"
        if task_id:
            title = f"{title}{task_id}"
        text = "\n".join(
            [
                title,
                "\u7cfb\u7edf\u5df2\u5b8c\u6210\u53c2\u8003\u8f66\u91c7\u96c6\u5e76\u5f62\u6210\u6d4b\u7b97\u4ef7\u683c\u94fe\uff0c\u4f46\u5f53\u524d\u7ed3\u679c\u9700\u8981\u4e3b\u7ba1\u786e\u8ba4\u3002",
                "",
                "\u7cfb\u7edf\u6d4b\u7b97\u4ef7\u683c\uff08\u5f85\u4eba\u5de5\u786e\u8ba4\uff09\uff1a",
                f"target_guazi_listing_price_yuan = {pricing_chain['target_guazi_listing_price_yuan']} \u5143",
                f"guazi_service_fee_yuan = {pricing_chain['guazi_service_fee_yuan']} \u5143",
                f"guazi_net_payout_yuan = {pricing_chain['guazi_net_payout_yuan']} \u5143",
                f"cost_yuan = {pricing_chain['cost_yuan']} \u5143",
                f"profit_yuan = {pricing_chain['profit_yuan']} \u5143",
                f"profit_rate = {pricing_chain['profit_rate']}",
                f"system_suggested_purchase_price_yuan = {pricing_chain['suggested_purchase_price_yuan']} \u5143",
                "",
                "\u5df2\u901a\u77e5\u4e3b\u7ba1\u5904\u7406\uff0c\u8bf7\u7b49\u5f85\u4e3b\u7ba1\u786e\u8ba4\u4ef7\u683c\u540e\u518d\u6536\u8f66\u3002",
            ]
        )
        return FormatResult(text=text, warnings=[])
    context = _manual_review_transparency_context(pricing_payload)
    if context["has_price_distribution"] and context["has_target_condition_review"]:
        reason_line = "\u7cfb\u7edf\u5df2\u5b8c\u6210\u53ef\u7528\u53c2\u8003\u8f66\u91c7\u96c6\uff0c\u4f46\u5f53\u524d\u53c2\u8003\u8f66\u4ef7\u683c\u5206\u5e03\u5dee\u5f02\u8f83\u5927\uff0c\u4e14\u76ee\u6807\u8f66\u90e8\u5206\u8f66\u51b5\u89c4\u5219\u9700\u8981\u4e3b\u7ba1\u786e\u8ba4\uff0c\u6682\u4e0d\u80fd\u81ea\u52a8\u7ed9\u51fa\u6700\u7ec8\u6536\u8f66\u4ef7\u3002"
    elif context["has_price_distribution"]:
        reason_line = "\u7cfb\u7edf\u5df2\u5b8c\u6210\u53ef\u7528\u53c2\u8003\u8f66\u91c7\u96c6\uff0c\u4f46\u5f53\u524d\u53c2\u8003\u8f66\u4ef7\u683c\u5206\u5e03\u5dee\u5f02\u8f83\u5927\uff0c\u9700\u8981\u4e3b\u7ba1\u786e\u8ba4\uff0c\u6682\u4e0d\u80fd\u81ea\u52a8\u7ed9\u51fa\u6700\u7ec8\u6536\u8f66\u4ef7\u3002"
    elif context["has_target_condition_review"]:
        reason_line = "\u7cfb\u7edf\u5df2\u5b8c\u6210\u53ef\u7528\u53c2\u8003\u8f66\u91c7\u96c6\uff0c\u4f46\u76ee\u6807\u8f66\u90e8\u5206\u8f66\u51b5\u89c4\u5219\u9700\u8981\u4e3b\u7ba1\u786e\u8ba4\uff0c\u6682\u4e0d\u80fd\u81ea\u52a8\u7ed9\u51fa\u6700\u7ec8\u6536\u8f66\u4ef7\u3002"
    else:
        reason_line = "\u7cfb\u7edf\u5df2\u5b8c\u6210\u53ef\u7528\u53c2\u8003\u8f66\u91c7\u96c6\uff0c\u4f46\u5f53\u524d\u7ed3\u679c\u9700\u8981\u4eba\u5de5\u786e\u8ba4\uff0c\u6682\u4e0d\u80fd\u81ea\u52a8\u7ed9\u51fa\u6700\u7ec8\u6536\u8f66\u4ef7\u3002"
    text = "\n".join(
        [
            "\u3010\u672c\u6b21\u5b9a\u4ef7\u8fdb\u5165\u4eba\u5de5\u590d\u6838\u3011",
            reason_line,
            "\u5df2\u901a\u77e5\u4e3b\u7ba1\u5904\u7406\uff0c\u8bf7\u7b49\u5f85\u4e3b\u7ba1\u786e\u8ba4\u4ef7\u683c\u540e\u518d\u6536\u8f66\u3002",
        ]
    )
    return FormatResult(text=text, warnings=[])

def format_result_reply(
    *,
    task_id: str,
    pricing_result: dict[str, Any] | None,
    status: str,
    run_meta: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> FormatResult:
    errors = errors or []
    run_meta = run_meta or {}
    config_mismatch_reason = pricing_result_config_mismatch_reason(pricing_result)
    if config_mismatch_reason:
        return _format_config_mismatch_hard_stop_reply(task_id, config_mismatch_reason, pricing_result or {})
    if pricing_result is not None and status != "FAILED":
        if not is_automatic_pricing_terminal_success(pricing_result):
            non_terminal_status = pricing_result_non_terminal_status(pricing_result)
            if non_terminal_status:
                return _format_second_stage_incomplete_reply(task_id, non_terminal_status)
        schema_errors = validate_pricing_result_payload(pricing_result)
        if schema_errors:
            if CONFIG_MISMATCH_HARD_STOP in schema_errors:
                return _format_config_mismatch_hard_stop_reply(task_id, CONFIG_MISMATCH_HARD_STOP, pricing_result)
            if RESULT_MISSING_REQUIRED_PRICING_FIELDS in schema_errors:
                return _format_pricing_success_guard_reply(task_id, [
                    error.split(":", 1)[1] for error in schema_errors if error.startswith("MISSING_REQUIRED_FIELD:")
                ])
            return _format_failure_reply(task_id, schema_errors, run_meta, pricing_result=pricing_result)
        missing_required_fields = pricing_success_missing_required_fields(pricing_result)
        if missing_required_fields:
            return _format_pricing_success_guard_reply(task_id, missing_required_fields)
    if status == "FAILED" or pricing_result is None:
        return _format_failure_reply(task_id, errors, run_meta, pricing_result=pricing_result)

    if status == "MANUAL_REVIEW_CONFIRMED" or value_from(pricing_result, "manual_review_confirmed") is True:
        return _format_manual_review_confirmed_reply(task_id, pricing_result)

    if status == "NEEDS_REVIEW" or is_manual_review_required(pricing_result):
        return _format_manual_review_reply(task_id, pricing_result)

    return _format_success_reply(task_id, pricing_result)


def _format_second_stage_incomplete_reply(task_id: str, status: str) -> FormatResult:
    text = "\n".join(
        [
            f"【本次定价未完成】{task_id}",
            "",
            "系统已经采集到部分参考车信息，但参考车边界还没有闭合。",
            "本次不会输出自动收车价，也不会按定价完成发送结果。",
            "",
            "请等待系统继续采集下一台参考车；如长时间未继续，请联系管理员检查采集流程。",
        ]
    )
    return FormatResult(text=text, warnings=[status])


def _format_pricing_success_guard_reply(task_id: str, missing_fields: list[str] | None) -> FormatResult:
    missing_fields = missing_fields or []
    text = "\n".join(
        [
            f"【本次定价未完成】{task_id}",
            "",
            "系统未拿到完整的参考车边界和价格链。",
            "本次不会输出自动收车价，也不会按定价完成发送结果。",
            "",
            "请联系管理员检查参考车采集结果后再继续。",
        ]
    )
    warnings = [RESULT_MISSING_REQUIRED_PRICING_FIELDS]
    warnings.extend(f"MISSING_REQUIRED_FIELD:{field_name}" for field_name in missing_fields)
    return FormatResult(text=text, warnings=warnings)


def _format_failure_reply(
    task_id: str,
    errors: list[str] | None,
    run_meta: dict[str, Any],
    *,
    pricing_result: dict[str, Any] | None = None,
) -> FormatResult:
    errors = errors or []
    code = errors[0] if errors else value_from(run_meta, "error_code")
    if code == MISSING:
        code = "MAIN_SCRIPT_FAILED"
    if str(code) == "RESULT_SCHEMA_INVALID_FOR_PRICING" and isinstance(pricing_result, dict):
        issue_context = pricing_result.get("issue_context") if isinstance(pricing_result.get("issue_context"), dict) else {}
        binding_result = issue_context.get("binding_result") if isinstance(issue_context.get("binding_result"), dict) else {}
        specific_code = next(
            (
                str(value)
                for value in (
                    pricing_result.get("issue_code"),
                    pricing_result.get("current_state"),
                    pricing_result.get("final_status"),
                    pricing_result.get("status"),
                    binding_result.get("stop_code"),
                )
                if str(value or "") == "DUPLICATE_REFERENCE_CLICK_BLOCKED"
            ),
            "",
        )
        if specific_code:
            code = specific_code
    second_stage_runtime_exception = bool(
        str(code) in {
            "SECOND_STAGE_RUNTIME_EXCEPTION",
            "V149_REFERENCE_IDENTITY_SUMMARY_TYPE_ERROR",
            "RESULT_SCHEMA_INVALID_FOR_PRICING_WITH_RUNTIME_EXCEPTION",
        }
        or (
            str(code) == "RESULT_SCHEMA_INVALID_FOR_PRICING"
            and isinstance(pricing_result, dict)
            and (
                pricing_result.get("exception_type")
                or pricing_result.get("root_exception_type")
                or pricing_result.get("issue_code") == "SECOND_STAGE_RUNTIME_EXCEPTION"
            )
        )
    )
    if second_stage_runtime_exception:
        text = "\n".join(
            [
                f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                "",
                "\u7cfb\u7edf\u5df2\u5f00\u59cb\u81ea\u52a8\u5b9a\u4ef7\uff0c\u4f46\u5728\u53c2\u8003\u8f66\u8be6\u60c5\u91c7\u96c6\u9636\u6bb5\u51fa\u73b0\u7cfb\u7edf\u5f02\u5e38\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                "",
                "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
            ]
        )
        warnings = ["SECOND_STAGE_RUNTIME_EXCEPTION"]
        if str(code) not in warnings:
            warnings.append(str(code))
        return FormatResult(text=text, warnings=warnings)
    if str(code) in {
        CROSS_TASK_PRICING_RESULT_REJECTED,
        TARGET_FINGERPRINT_MISMATCH_RESULT_REJECTED,
        FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED,
        CROSS_TASK_SUCCESS_RESULT_BLOCKED_BEFORE_FEISHU_SEND,
    }:
        text = "\n".join(
            [
                f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                "",
                "\u7cfb\u7edf\u68c0\u6d4b\u5230\u5b9a\u4ef7\u7ed3\u679c\u4e0e\u5f53\u524d\u4efb\u52a1\u76ee\u6807\u8f66\u4e0d\u4e00\u81f4\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                "",
                "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u6838\u67e5\u7ed3\u679c\u6765\u6e90\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
            ]
        )
        warnings = [FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED]
        for item in errors:
            if item and str(item) not in warnings:
                warnings.append(str(item))
        return FormatResult(text=text, warnings=warnings)
    if str(code) in GUAZI_PUSH_POPUP_BUSINESS_SAFE_FAILURE_CODES:
        text = "\n".join(
            [
                f"【本次定价未完成】{task_id}",
                "",
                "原因：瓜子 APP 弹出消息推送通知弹窗，系统未能安全关闭该弹窗。",
                "需要处理：请管理员检查手机页面后重新发起任务。",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) == "REFERENCE_CARD_BINDING_NOT_UNIQUE":
        text = "\n".join(
            [
                f"【本次定价未完成】{task_id}",
                "",
                "系统已进入三同参考车采集，但在识别下一辆参考车卡片时无法唯一确认目标卡片。",
                "为避免采错参考车，本次已安全停止，不会输出自动收车价。",
                "",
                "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW:
        text = _format_v33_recollected_previous_reference_needs_review_text(task_id)
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) == "DUPLICATE_REFERENCE_CLICK_BLOCKED":
        text = "\n".join(
            [
                f"【本次定价未完成】{task_id}",
                "",
                "系统已开始自动定价，但参考车回采阶段未能继续执行，已安全停止，已通知管理员处理。",
                "",
                "请等待管理员处理后再重新发起任务。",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) in S10_NEXT_REFERENCE_BUSINESS_SAFE_FAILURE_CODES:
        text = "\n".join(
            [
                f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                "",
                "\u7cfb\u7edf\u5df2\u5f00\u59cb\u91c7\u96c6\u4e09\u540c\u53c2\u8003\u8f66\uff0c\u4f46\u8fd4\u56de\u4e09\u540c\u5217\u8868\u540e\u672a\u80fd\u53ef\u9760\u5b9a\u4f4d\u4e0b\u4e00\u8f86\u5f85\u91c7\u96c6\u53c2\u8003\u8f66\u3002",
                "\u4e3a\u907f\u514d\u91cd\u590d\u91c7\u96c6\u5df2\u5904\u7406\u8f66\u6e90\u6216\u70b9\u9519\u53c2\u8003\u8f66\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                "",
                "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) in REFERENCE_PHYSICAL_UI_BUSINESS_SAFE_FAILURE_CODES:
        text = "\n".join(
            [
                f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                "",
                "\u7cfb\u7edf\u5df2\u5f00\u59cb\u91c7\u96c6\u53c2\u8003\u8f66\uff0c\u4f46\u8fd4\u56de\u4e09\u540c\u5217\u8868\u6216\u8fdb\u5165\u4e0b\u4e00\u8f86\u53c2\u8003\u8f66\u7684\u9875\u9762\u8bc1\u636e\u672a\u80fd\u53ef\u9760\u786e\u8ba4\u3002",
                "\u4e3a\u907f\u514d\u91cd\u590d\u91c7\u96c6\u6216\u91c7\u9519\u53c2\u8003\u8f66\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                "",
                "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) in {"S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED", "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED"}:
        text = "\n".join(
            [
                f"\u3010\u672c\u6b21\u5b9a\u4ef7\u672a\u5b8c\u6210\u3011{task_id}",
                "",
                "\u7cfb\u7edf\u5df2\u5f00\u59cb\u91c7\u96c6\u53c2\u8003\u8f66\uff0c\u4f46\u68c0\u6d4b\u62a5\u544a\u4e2d\u7684\u5386\u53f2\u4fee\u590d\u6b21\u6570\u672a\u80fd\u53ef\u9760\u786e\u8ba4\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002",
                "",
                "\u8bf7\u6682\u7b49\u7ba1\u7406\u5458\u5904\u7406\u540e\u518d\u91cd\u65b0\u53d1\u8d77\u4efb\u52a1\u3002",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) in S13_BUSINESS_SAFE_FAILURE_CODES:
        text = "\n".join(
            [
                f"【本次定价未完成】{task_id}",
                "",
                "系统已开始采集参考车，但未能可靠定位检测报告中的历史修复车况入口，为避免误采正常检测项，本次已安全停止。",
                "",
                "请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) in S07_COLOR_BUSINESS_SAFE_FAILURE_CODES:
        text = "\n".join(
            [
                f"【本次定价未完成】{task_id}",
                "",
                "系统已进入筛选流程，但车辆颜色筛选结果与目标车颜色不一致。",
                "为避免采错参考车，本次已安全停止，已通知管理员处理。",
                "",
                "请稍后重新发送目标车源并回复“确认”；如连续出现，请联系管理员检查筛选页状态。",
            ]
        )
        return FormatResult(text=text, warnings=[str(code)])
    if str(code) in S07_AGE_BUSINESS_SAFE_FAILURE_CODES:
        return _format_s07_age_business_safe_failure_reply(
            str(task_id),
            str(code),
            pricing_result=pricing_result,
            run_meta=run_meta,
        )
    if str(code) in S14_BUSINESS_SAFE_FAILURE_CODES:
        return _format_s14_business_safe_failure_reply(task_id, str(code))
    text = "\n".join(
        [
            f"【定价失败】{task_id}",
            "",
            "错误码：",
            str(code),
            "",
            "说明：",
            _failure_description(str(code)),
        ]
    )
    return FormatResult(text=text, warnings=[])


def _format_s07_age_business_safe_failure_reply(
    task_id: str,
    code: str,
    *,
    pricing_result: dict[str, Any] | None = None,
    run_meta: dict[str, Any] | None = None,
) -> FormatResult:
    context = _s07_age_feedback_context(pricing_result=pricing_result, run_meta=run_meta)
    target_age = context.get("target_age")
    if code in {"S07_AGE_ONE_POST_ACTION_VERIFY_FAILED", "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED"}:
        registration_date = str(context.get("registration_date") or "2025.02")
        text = "\n".join(
            [
                "【本次定价未完成】" + str(task_id),
                "",
                f"系统已识别上牌日期为 {registration_date}，按年份差应筛选 1 年车龄。",
                "本次已进入车龄筛选步骤，但动作后的新鲜页面证据未能证明最终筛选为 1-1年。",
                "",
                "请勿重复确认，等待管理员处理后再重新发起任务。",
            ]
        )
        return FormatResult(text=text, warnings=[code])
    if code in {
        "S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED",
        "S07_AGE_FILTER_ACTUAL_RANGE_MISMATCH",
        "S07_POST_ACTION_FRESH_EVIDENCE_MISSING",
        "S07_AGE_FILTER_PLANNED_ACTUAL_MISMATCH",
        "S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER",
    }:
        text = "\n".join(
            [
                "【本次定价未完成】" + str(task_id),
                "",
                "系统已进入车龄筛选步骤，但车龄筛选动作后的实际结果验证未通过。",
                "为避免带着未确认的车龄条件进入车源列表，本次已安全停止，已通知管理员处理。",
                "",
                "请勿重复确认，等待管理员处理后再重新发起任务。",
            ]
        )
        return FormatResult(text=text, warnings=[code])
    if target_age == 1 or code in {"S07_AGE_ONE_HIDDEN_TICK_NOT_BINDABLE", "S07_AGE_ONE_HIDDEN_TICK_VERIFY_FAILED"}:
        registration_date = str(context.get("registration_date") or "2025.02")
        text = "\n".join(
            [
                "【本次定价未完成】" + str(task_id),
                "",
                f"系统已识别上牌日期为 {registration_date}，按年份差应筛选 1 年车龄。",
                "本次未能稳定完成 1 年隐藏刻度选择或 1-1年结果验证，已通知管理员处理。",
                "",
                "请勿重复确认，等待管理员处理后再重新发起任务。",
            ]
        )
        return FormatResult(text=text, warnings=[code])
    text = "\n".join(
        [
            "【本次定价未完成】" + str(task_id),
            "",
            "系统已开始执行，并已进入车龄筛选步骤。",
            "本次在瓜子车龄滑块筛选时安全停止，已通知管理员处理。",
            "",
            "请勿重复确认，等待管理员处理后再重新发起任务。",
        ]
    )
    return FormatResult(text=text, warnings=[code])


def _s07_age_feedback_context(
    *,
    pricing_result: dict[str, Any] | None = None,
    run_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = [payload for payload in (pricing_result, run_meta) if isinstance(payload, dict)]

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    def first_text(keys: tuple[str, ...]) -> str | None:
        for payload in payloads:
            for item in walk(payload):
                for key in keys:
                    text = str(item.get(key) or "").strip()
                    if text:
                        return text
        return None

    def first_int(keys: tuple[str, ...]) -> int | None:
        for payload in payloads:
            for item in walk(payload):
                for key in keys:
                    value = item.get(key)
                    if value in (None, ""):
                        continue
                    try:
                        return int(float(value))
                    except (TypeError, ValueError):
                        continue
        return None

    return {
        "registration_date": first_text(("registration_date", "register_date", "target_registration_date", "registration_date_raw")),
        "target_age": first_int(("target_age_years", "target_age")),
    }


def _format_s14_business_safe_failure_reply(task_id: str, code: str) -> FormatResult:
    if code in {"S14_CONTRACT_DEGRADED_NEEDS_REVIEW", "S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED"}:
        text = "\n".join(
            [
                f"【本次定价需人工复核】{task_id}",
                "",
                "本次定价已采集到参考车信息，但部分车况详情需要人工确认。",
                "为避免错误定价，系统不会按自动定价完成发送结果。",
                "",
                "请等待人工复核后再确认最终收车价。",
            ]
        )
    else:
        text = "\n".join(
            [
                f"【本次定价未完成】{task_id}",
                "",
                "系统已进入参考车检测报告，但当前车况详情无法安全确认。",
                "本次已停止并通知管理员处理。",
                "",
                "请稍后重新发送目标车源，或联系管理员检查车况详情页识别。",
            ]
        )
    return FormatResult(text=text, warnings=[code])


def _format_config_mismatch_hard_stop_reply(
    task_id: str,
    mismatch_reason: str,
    pricing_result: dict[str, Any],
) -> FormatResult:
    target_vehicle = value_from(pricing_result, "target_vehicle", "target_vehicle_text")
    if target_vehicle == MISSING:
        target_vehicle = " ".join(
            str(value)
            for value in [
                pricing_result.get("brand"),
                pricing_result.get("series"),
                pricing_result.get("model_config") or pricing_result.get("trim"),
            ]
            if value
        ) or MISSING
    if mismatch_reason == "POWERTRAIN_TOKEN_MISMATCH":
        reason_line = "车型动力配置无法确认一致。系统识别到目标车与参考车存在动力差异，例如 330TSI 与 380TSI 不能视为同一配置。"
    else:
        reason_line = "车型配置无法确认一致。系统识别到目标车配置与参考车配置存在等级差异，例如“豪华”与“尊贵”不能视为同一配置。"
    text = "\n".join(
        [
            f"【目标车信息需修改】{task_id}",
            "",
            "本次定价已停止。",
            "",
            "目标车：",
            str(target_vehicle),
            "",
            "原因：",
            reason_line,
            "",
            "为避免错误定价，请重新发送完整车型配置，例如：",
            "2018款 改款 330TSI DSG 豪华型",
        ]
    )
    return FormatResult(text=text, warnings=[])


def _format_success_reply(task_id: str, pricing_result: dict[str, Any]) -> FormatResult:
    fields = {
        "target_score": value_from(pricing_result, "target_score", "目标车分数"),
        "boundary_confirmed": value_from(pricing_result, "boundary_confirmed"),
        "boundary_reference_index": value_from(pricing_result, "boundary_reference_index"),
        "boundary_reference_score": value_from(pricing_result, "boundary_reference_score"),
        "final_reference_index": value_from(pricing_result, "final_reference_index"),
        "final_reference_score": value_from(pricing_result, "final_reference_score"),
        "final_reference_price": value_from(pricing_result, "final_reference_price_yuan", "final_reference_price", "base_reference_price_yuan"),
        "target_guazi_listing_price_yuan": value_from(
            pricing_result,
            "target_guazi_listing_price_yuan",
            "guazi_listing_price_yuan",
        ),
        "guazi_service_fee_yuan": value_from(pricing_result, "guazi_service_fee_yuan"),
        "guazi_net_payout_yuan": value_from(pricing_result, "guazi_net_payout_yuan"),
        "guazi_return_price_yuan": value_from(pricing_result, "guazi_return_price_yuan", "guazi_net_payout_yuan"),
        "cost_yuan": value_from(pricing_result, "cost_yuan"),
        "profit_yuan": value_from(pricing_result, "profit_yuan"),
        "profit_rate": _format_profit_rate(value_from(pricing_result, "profit_rate")),
        "suggested_purchase_price_yuan": value_from(pricing_result, "suggested_purchase_price_yuan"),
        "final_purchase_price_yuan": value_from(pricing_result, "final_purchase_price_yuan", "suggested_purchase_price_yuan"),
        "manual_review_required": value_from(pricing_result, "manual_review_required"),
    }
    warnings = [f"MISSING_FIELD:{key}" for key, value in fields.items() if value == MISSING]
    target_vehicle = value_from(pricing_result, "target_vehicle", "target_vehicle_text")
    if target_vehicle == MISSING:
        target_vehicle = " ".join(
            str(value)
            for value in [
                pricing_result.get("brand"),
                pricing_result.get("series"),
                pricing_result.get("model_config") or pricing_result.get("trim"),
            ]
            if value
        ) or MISSING
        if target_vehicle == MISSING:
            warnings.append("MISSING_FIELD:target_vehicle")

    text = "\n".join(
        [
            f"【定价完成】{task_id}",
            "",
            "目标车信息：",
            str(target_vehicle),
            _target_vehicle_detail_line(pricing_result),
            "",
            "参考车选择：",
            "规则：V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            f"boundary_confirmed = {fields['boundary_confirmed']}",
            f"boundary_reference_index = {fields['boundary_reference_index']}",
            f"boundary_reference_score = {fields['boundary_reference_score']}",
            f"final_reference_index = {fields['final_reference_index']}",
            f"final_reference_score = {fields['final_reference_score']}",
            f"final_reference_price_yuan = {fields['final_reference_price']}",
            f"target_score = {fields['target_score']}",
            "",
            "瓜子价格链：",
            f"target_guazi_listing_price_yuan = {fields['target_guazi_listing_price_yuan']} 元",
            f"guazi_service_fee_yuan = {fields['guazi_service_fee_yuan']} 元",
            f"guazi_net_payout_yuan = {fields['guazi_net_payout_yuan']} 元",
            f"guazi_return_price_yuan = {fields['guazi_return_price_yuan']} 元",
            "",
            "收车价计算：",
            f"cost_yuan = {fields['cost_yuan']} 元",
            f"profit_yuan = {fields['profit_yuan']} 元",
            f"profit_rate = {fields['profit_rate']}",
            f"suggested_purchase_price_yuan = {fields['suggested_purchase_price_yuan']} 元",
            f"final_purchase_price_yuan = {fields['final_purchase_price_yuan']} 元",
            "",
            "说明：",
            "该结果为系统自动定价结果，参考车已满足 V3 边界确认规则。",
        ]
    )
    return FormatResult(text=text, warnings=warnings)


def _first_present(*payloads_and_aliases: Any) -> str:
    *payloads, aliases = payloads_and_aliases
    if not isinstance(aliases, tuple):
        aliases = tuple(payloads_and_aliases[2:])
        payloads = list(payloads_and_aliases[:2])
    for alias in aliases:
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            value = payload.get(alias)
            if value not in (None, ""):
                return str(value)
    return MISSING


def _manual_review_business_pricing_chain_context(pricing_result: dict[str, Any]) -> dict[str, Any]:
    pricing_result = pricing_result if isinstance(pricing_result, dict) else {}
    pricing_section = pricing_result.get("pricing") if isinstance(pricing_result.get("pricing"), dict) else {}
    service_fee_contract_mismatch = (
        pricing_result.get("service_fee_contract_match") is False
        or pricing_section.get("service_fee_contract_match") is False
        or ((pricing_result.get("pricing_contract_guard") or {}).get("contract_actual") or {}).get("service_fee_contract_match") is False
    )
    stale_reference_history = (
        pricing_result.get("reference_history_current_task_valid") is False
        or pricing_result.get("pricing_chain_available") is False
    )
    payloads = [pricing_result, pricing_section]
    fields = {
        "target_guazi_listing_price_yuan": _first_non_missing(
            *(payload.get("target_guazi_listing_price_yuan") for payload in payloads),
            value_from(pricing_result, "target_guazi_listing_price_yuan", "guazi_listing_price_yuan"),
        ),
        "guazi_service_fee_yuan": _first_non_missing(
            *(payload.get("guazi_service_fee_yuan") for payload in payloads),
            value_from(pricing_result, "guazi_service_fee_yuan"),
        ),
        "guazi_net_payout_yuan": _first_non_missing(
            *(payload.get("guazi_net_payout_yuan") for payload in payloads),
            value_from(pricing_result, "guazi_net_payout_yuan"),
        ),
        "cost_yuan": _first_non_missing(
            *(payload.get("cost_yuan") for payload in payloads),
            value_from(pricing_result, "cost_yuan"),
        ),
        "profit_yuan": _first_non_missing(
            *(payload.get("profit_yuan") for payload in payloads),
            value_from(pricing_result, "profit_yuan"),
        ),
        "profit_rate": _format_profit_rate(
            _first_non_missing(
                *(payload.get("profit_rate") for payload in payloads),
                value_from(pricing_result, "profit_rate"),
            )
        ),
        "suggested_purchase_price_yuan": _first_non_missing(
            *(payload.get("suggested_purchase_price_yuan") for payload in payloads),
            value_from(pricing_result, "suggested_purchase_price_yuan", "suggested_acquisition_price_yuan"),
        ),
    }
    required = (
        "target_guazi_listing_price_yuan",
        "guazi_service_fee_yuan",
        "guazi_net_payout_yuan",
        "cost_yuan",
        "profit_yuan",
        "profit_rate",
        "suggested_purchase_price_yuan",
    )
    missing = [key for key in required if fields.get(key) in (None, "", MISSING)]
    return {
        **fields,
        "available": not stale_reference_history and not service_fee_contract_mismatch and not missing,
        "missing_fields": missing,
        "stale_reference_history": stale_reference_history,
        "service_fee_contract_mismatch": service_fee_contract_mismatch,
    }


def _format_score_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("score")
    if value == MISSING or value is None:
        return MISSING
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _format_supervisor_target_vehicle(pricing_result: dict[str, Any], target_task: dict[str, Any]) -> str:
    pieces = [
        _first_present(target_task, pricing_result, ("brand",)),
        _first_present(target_task, pricing_result, ("series",)),
        _first_present(target_task, pricing_result, ("year_model",)),
        _first_present(target_task, pricing_result, ("config_model", "model_config", "trim")),
    ]
    text = " ".join(piece for piece in pieces if piece != MISSING).strip()
    if text:
        return text
    target_vehicle = value_from(pricing_result, "target_vehicle", "target_vehicle_text")
    if target_vehicle != MISSING:
        return str(target_vehicle)
    return MISSING


def _manual_review_transparency_context(pricing_result: dict[str, Any]) -> dict[str, Any]:
    pricing_result = pricing_result if isinstance(pricing_result, dict) else {}
    reasons = manual_review_reasons(pricing_result)
    s17_payload = pricing_result.get("s17_payload") if isinstance(pricing_result.get("s17_payload"), dict) else {}
    reference_history = pricing_result.get("reference_history")
    if not isinstance(reference_history, list):
        reference_history = []
    candidate_pool = pricing_result.get("candidate_reference_pool")
    if not isinstance(candidate_pool, list):
        candidate_pool = []

    price_distribution = _price_distribution_context(pricing_result)
    attempted_count = _first_non_missing(
        pricing_result.get("attempted_reference_count"),
        s17_payload.get("attempted_reference_count"),
        len(reference_history) if reference_history else MISSING,
    )
    trusted_references = [
        item
        for item in reference_history
        if isinstance(item, dict)
        and item.get("reference_score_trustworthy") is True
        and item.get("reference_score_usable_for_boundary") is True
        and item.get("excluded_from_boundary") is not True
    ]
    if trusted_references:
        trusted_count = len(trusted_references)
    else:
        trusted_count = _first_non_missing(
            pricing_result.get("trusted_reference_count"),
            s17_payload.get("trusted_reference_count"),
            len(candidate_pool) if candidate_pool else MISSING,
        )

    usable_indices = _manual_review_indices(pricing_result.get("usable_boundary_reference_indices"))
    if not usable_indices:
        usable_indices = _manual_review_indices(s17_payload.get("usable_boundary_reference_indices"))
    if not usable_indices:
        usable_indices = _manual_review_indices(
            item.get("reference_index")
            for item in trusted_references
            if isinstance(item, dict)
        )
    if not usable_indices and candidate_pool:
        usable_indices = _manual_review_indices(
            item.get("reference_index")
            for item in candidate_pool
            if isinstance(item, dict)
        )

    excluded_summary = _excluded_reference_summary(pricing_result, s17_payload, reference_history)
    target_condition_items = _target_condition_review_items(reasons)
    target_condition_items.extend(_coerce_string_list(pricing_result.get("target_condition_review_items")))
    target_condition_items.extend(_coerce_string_list(s17_payload.get("target_condition_review_items")))
    target_condition_items = list(dict.fromkeys(item for item in target_condition_items if item))

    system_price = _first_non_missing(
        value_from(pricing_result, "suggested_purchase_price_yuan", "suggested_acquisition_price_yuan"),
        s17_payload.get("system_calculated_price_yuan"),
        pricing_result.get("system_calculated_price_yuan"),
    )
    final_reference_score = value_from(pricing_result, "final_reference_score")
    boundary_reference_score = value_from(pricing_result, "boundary_reference_score")

    return {
        "reasons": reasons,
        "has_price_distribution": "PRICE_DISTRIBUTION_MANUAL_REVIEW" in reasons or bool(price_distribution.get("price_list")),
        "has_target_condition_review": bool(target_condition_items),
        "price_list": price_distribution.get("price_list") or [],
        "price_dispersion_rate": price_distribution.get("price_dispersion_rate", MISSING),
        "attempted_reference_count": attempted_count,
        "trusted_reference_count": trusted_count,
        "usable_boundary_reference_indices": usable_indices,
        "excluded_reference_summary": excluded_summary,
        "boundary_confirmed": value_from(pricing_result, "boundary_confirmed"),
        "boundary_reference_index": value_from(pricing_result, "boundary_reference_index"),
        "boundary_reference_score": boundary_reference_score,
        "final_reference_index": value_from(pricing_result, "final_reference_index"),
        "final_reference_score": final_reference_score,
        "target_score": value_from(pricing_result, "target_score"),
        "system_calculated_price_yuan": system_price,
        "target_condition_review_items": target_condition_items,
    }


def _price_distribution_context(pricing_result: dict[str, Any]) -> dict[str, Any]:
    s17_payload = pricing_result.get("s17_payload") if isinstance(pricing_result.get("s17_payload"), dict) else {}
    price_list = _coerce_number_list(
        _first_non_missing(
            pricing_result.get("price_distribution_values"),
            pricing_result.get("price_list_yuan"),
            s17_payload.get("price_distribution_values"),
            s17_payload.get("price_list_yuan"),
        )
    )
    dispersion = _first_non_missing(
        pricing_result.get("price_dispersion_rate"),
        s17_payload.get("price_dispersion_rate"),
    )
    for item in s17_payload.get("competition_coefficient_reasons") or pricing_result.get("competition_coefficient_reasons") or []:
        if not isinstance(item, dict):
            continue
        if item.get("factor") != "price_distribution_adjustment":
            continue
        data_source = item.get("data_source") if isinstance(item.get("data_source"), dict) else {}
        price_list = price_list or _coerce_number_list(data_source.get("price_list_yuan"))
        dispersion = _first_non_missing(dispersion, data_source.get("price_spread_rate"))
        break
    return {"price_list": price_list, "price_dispersion_rate": dispersion}


def _excluded_reference_summary(
    pricing_result: dict[str, Any],
    s17_payload: dict[str, Any],
    reference_history: list[Any],
) -> list[str]:
    explicit = _coerce_string_list(pricing_result.get("excluded_reference_summary"))
    explicit.extend(_coerce_string_list(s17_payload.get("excluded_reference_summary")))
    if explicit:
        return list(dict.fromkeys(explicit))
    summaries: list[str] = []
    for item in reference_history:
        if not isinstance(item, dict) or item.get("excluded_from_boundary") is not True:
            continue
        index = item.get("reference_index")
        reason = str(item.get("excluded_from_boundary_reason") or item.get("reference_exclusion_reason") or "").strip()
        readable = _excluded_reference_reason_label(reason)
        if index not in (None, ""):
            summaries.append(f"第 {_format_reference_index(index)} 辆，{readable}")
        else:
            summaries.append(readable)
    return summaries


def _excluded_reference_reason_label(reason: str) -> str:
    if "S14_COLLECTION_INCOMPLETE" in reason or "INCOMPLETE" in reason:
        return "车况证据未完整，未参与边界"
    if reason:
        return reason
    return "未参与边界"


def _target_condition_review_items(reasons: list[str]) -> list[str]:
    items: list[str] = []
    if "TARGET_CONDITION_SILL_SCORING_REVIEW" in reasons:
        items.append("目标车底边梁/门槛/边梁类车况是否按规则处理")
    if "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW" in reasons:
        items.append("目标车大灯更换规则确认")
    if any("缺少出险次数" in str(reason) for reason in reasons):
        items.append("目标车出险次数采用默认分，需要确认")
    if any("缺少最大金额" in str(reason) for reason in reasons):
        items.append("目标车最大金额采用默认分，需要确认")
    return items


def _manual_review_reason_label(reason: str) -> str:
    if reason == "PRICE_DISTRIBUTION_MANUAL_REVIEW":
        return "价格分布离散较大，需要主管确认。"
    if reason == "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING":
        return "未找到满足 V3 的边界参考车，需要主管确认。"
    if reason == "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE":
        return "第一辆参考车分数已高于目标车，需要主管确认。"
    if reason == "SAMPLE_SHORTAGE_MANUAL_REVIEW":
        return "可用参考车样本偏少，需要主管确认。"
    if reason in {"TARGET_CONDITION_SILL_SCORING_REVIEW", "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW"}:
        return "目标车车况存在规则确认项，需要主管判断。"
    return reason


def _manual_review_primary_reason(context: dict[str, Any]) -> str:
    reasons = context.get("reasons") or []
    if "PRICE_DISTRIBUTION_MANUAL_REVIEW" in reasons:
        return _manual_review_reason_label("PRICE_DISTRIBUTION_MANUAL_REVIEW")
    if context.get("has_target_condition_review"):
        return "目标车车况存在规则确认项，需要主管判断。"
    if reasons:
        return _manual_review_reason_label(str(reasons[0]))
    return "当前结果需要主管确认。"


def _manual_review_indices(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value] if str(value).strip() else []
    try:
        iterator = iter(value)
    except TypeError:
        return [value]
    result = []
    for item in iterator:
        if item in (None, ""):
            continue
        result.append(item)
    return result


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, "", MISSING):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _coerce_number_list(value: Any) -> list[Any]:
    if value in (None, "", MISSING):
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value]


def _first_non_missing(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", MISSING):
            return value
    return MISSING


def _format_reference_index(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _format_reference_indices(indices: list[Any]) -> str:
    if not indices:
        return MISSING
    return " / ".join(f"第 {_format_reference_index(index)}" for index in indices)


def _format_price_list(values: list[Any]) -> str:
    if not values:
        return MISSING
    return " / ".join(_format_yuan_value(value, suffix=False) for value in values)


def _format_yuan_value(value: Any, *, suffix: bool = True) -> str:
    if value in (None, "", MISSING):
        return MISSING
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        text = str(value)
        return f"{text} 元" if suffix and "元" not in text else text
    if numeric.is_integer():
        text = str(int(numeric))
    else:
        text = f"{numeric:g}"
    return f"{text} 元" if suffix else text


def _format_dispersion_rate(value: Any) -> str:
    if value in (None, "", MISSING):
        return MISSING
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:.2f}%"


def _format_score_line(index: Any, score: Any) -> str:
    if index in (None, "", MISSING) and score in (None, "", MISSING):
        return MISSING
    index_text = f"第 {_format_reference_index(index)} 辆" if index not in (None, "", MISSING) else MISSING
    score_text = _format_score_value(score)
    if score_text == MISSING:
        return index_text
    if index_text == MISSING:
        return f"{score_text} 分"
    return f"{index_text}，{score_text} 分"


def _format_v33_recollected_previous_reference_needs_review_text(task_id: str) -> str:
    return "\n".join(
        [
            f"【需要人工复核定价】{task_id}",
            "",
            "系统已完成三同车源边界判断，但边界前参考车回采后仍不完整，暂不能自动给出收车价，已提交管理员人工复核。",
            "",
            "请等待管理员确认价格后再收车。",
        ]
    )


def _format_manual_review_reply(task_id: str, pricing_result: dict[str, Any]) -> FormatResult:
    reasons = manual_review_reasons(pricing_result)
    issue_code = str(pricing_result.get("issue_code") or pricing_result.get("stop_code") or "")
    if (
        issue_code == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW
        or V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW in {str(item) for item in reasons}
    ):
        return FormatResult(
            text=_format_v33_recollected_previous_reference_needs_review_text(task_id),
            warnings=[V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW],
        )
    warnings = [] if reasons else ["MISSING_FIELD:manual_review_reason"]
    if not reasons:
        reasons = [MISSING]
    fields = {
        "target_score": value_from(pricing_result, "target_score"),
        "boundary_confirmed": value_from(pricing_result, "boundary_confirmed"),
        "boundary_reference_index": value_from(pricing_result, "boundary_reference_index"),
        "boundary_reference_score": value_from(pricing_result, "boundary_reference_score"),
        "final_reference_index": value_from(pricing_result, "final_reference_index"),
        "final_reference_score": value_from(pricing_result, "final_reference_score"),
        "final_reference_price": value_from(pricing_result, "final_reference_price_yuan", "final_reference_price", "base_reference_price_yuan"),
        "target_guazi_listing_price_yuan": value_from(
            pricing_result,
            "target_guazi_listing_price_yuan",
            "guazi_listing_price_yuan",
        ),
        "guazi_service_fee_yuan": value_from(pricing_result, "guazi_service_fee_yuan"),
        "guazi_net_payout_yuan": value_from(pricing_result, "guazi_net_payout_yuan"),
        "guazi_return_price_yuan": value_from(pricing_result, "guazi_return_price_yuan", "guazi_net_payout_yuan"),
        "cost_yuan": value_from(pricing_result, "cost_yuan"),
        "profit_yuan": value_from(pricing_result, "profit_yuan"),
        "profit_rate": _format_profit_rate(value_from(pricing_result, "profit_rate")),
        "suggested_purchase_price_yuan": value_from(pricing_result, "suggested_purchase_price_yuan"),
    }
    target_vehicle = value_from(pricing_result, "target_vehicle", "target_vehicle_text")
    if target_vehicle == MISSING:
        target_vehicle = " ".join(
            str(value)
            for value in [
                pricing_result.get("brand"),
                pricing_result.get("series"),
                pricing_result.get("model_config") or pricing_result.get("trim"),
            ]
            if value
        ) or MISSING
    text = "\n".join(
        [
            f"【待人工复核】{task_id}",
            "",
            "目标车：",
            str(target_vehicle),
            "",
            "复核原因：",
            "\n".join(str(item) for item in reasons),
            "",
            "原因说明：",
            _manual_review_reason_description(reasons),
            "",
            "参考车选择规则：",
            "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            "",
            "边界确认：",
            f"boundary_confirmed = {fields['boundary_confirmed']}",
            f"boundary_reference_index = {fields['boundary_reference_index']}",
            f"boundary_reference_score = {fields['boundary_reference_score']}",
            "",
            "分数与参考车：",
            f"target_score = {fields['target_score']}",
            f"final_reference_index = {fields['final_reference_index']}",
            f"final_reference_score = {fields['final_reference_score']}",
            f"final_reference_price_yuan = {fields['final_reference_price']}",
            "",
            "系统测算价格（待人工确认）：",
            f"target_guazi_listing_price_yuan = {fields['target_guazi_listing_price_yuan']} 元",
            f"guazi_service_fee_yuan = {fields['guazi_service_fee_yuan']} 元",
            f"guazi_net_payout_yuan = {fields['guazi_net_payout_yuan']} 元",
            f"guazi_return_price_yuan = {fields['guazi_return_price_yuan']} 元",
            f"cost_yuan = {fields['cost_yuan']} 元",
            f"profit_yuan = {fields['profit_yuan']} 元",
            f"profit_rate = {fields['profit_rate']}",
            f"system_suggested_purchase_price_yuan = {fields['suggested_purchase_price_yuan']} 元",
            "",
            "请直接回复人工确认收车价，例如：",
            "86000",
            "或：",
            "8.6万",
        ]
    )
    return FormatResult(text=text, warnings=warnings)


def format_supervisor_review_card(
    *,
    task_id: str,
    pricing_result: dict[str, Any],
    target_task: dict[str, Any] | None = None,
) -> FormatResult:
    reasons = manual_review_reasons(pricing_result)
    warnings = [] if reasons else ["MISSING_FIELD:manual_review_reason"]
    if not reasons:
        reasons = [MISSING]
    target_task = target_task or {}
    target_vehicle = _format_supervisor_target_vehicle(pricing_result, target_task)
    registration_date = _first_present(
        target_task,
        pricing_result,
        "registration_date",
        "register_date",
        "license_date",
    )
    color = _first_present(target_task, pricing_result, "color", "vehicle_color")
    mileage = _first_present(
        target_task,
        pricing_result,
        "mileage",
        "mileage_text",
        "mileage_10k_km",
        "display_mileage",
    )
    target_score = _format_score_value(value_from(pricing_result, "target_score"))
    context = _manual_review_transparency_context(pricing_result)
    primary_reason = _manual_review_primary_reason(context)
    price_lines: list[str] = []
    if context["price_list"]:
        price_lines.append(f"三同价格：{_format_price_list(context['price_list'])}")
    if context["price_dispersion_rate"] != MISSING:
        price_lines.append(f"价格离散率：{_format_dispersion_rate(context['price_dispersion_rate'])}")
    boundary_lines = [
        f"可信参考车：{context['trusted_reference_count']} 辆 / 已尝试 {context['attempted_reference_count']} 辆",
        f"可用于边界：{_format_reference_indices(context['usable_boundary_reference_indices'])}",
    ]
    if context["excluded_reference_summary"]:
        boundary_lines.append("排除参考车：" + "；".join(context["excluded_reference_summary"]))
    boundary_lines.extend(
        [
            f"目标车分数：{_format_score_value(context['target_score'])}",
            f"边界参考车：{_format_score_line(context['boundary_reference_index'], context['boundary_reference_score'])}",
            f"最终参考车：{_format_score_line(context['final_reference_index'], context['final_reference_score'])}",
            f"系统测算价：{_format_yuan_value(context['system_calculated_price_yuan'])}",
        ]
    )
    review_item_lines = [f"- {item}" for item in context["target_condition_review_items"]]
    if not review_item_lines:
        review_item_lines = ["- 当前结果需要主管确认"]
    text = "\n".join(
        [
            f"【人工复核定价】{task_id}",
            "",
            "目标车：",
            str(target_vehicle),
            f"上牌：{registration_date}",
            f"颜色：{color}",
            f"里程：{mileage}",
            f"目标车分数：{target_score}",
            "",
            "系统已完成可用边界链，但触发人工复核门禁，暂不自动输出最终收车价。",
            "",
            "复核原因：",
            primary_reason,
            *price_lines,
            "",
            "边界链：",
            *boundary_lines,
            "",
            "需主管确认项：",
            *review_item_lines,
            "",
            "请直接回复最终收车价，例如：",
            "86000",
            "8.6万",
        ]
    )
    return FormatResult(text=text, warnings=warnings)


def _format_manual_review_confirmed_reply(task_id: str, pricing_result: dict[str, Any]) -> FormatResult:
    reasons = manual_review_reasons(pricing_result)
    warnings = [] if reasons else ["MISSING_FIELD:manual_review_reason"]
    if not reasons:
        reasons = [MISSING]
    fields = {
        "target_score": value_from(pricing_result, "target_score"),
        "final_reference_index": value_from(pricing_result, "final_reference_index"),
        "final_reference_score": value_from(pricing_result, "final_reference_score"),
        "final_reference_price": value_from(pricing_result, "final_reference_price_yuan", "final_reference_price", "base_reference_price_yuan"),
        "target_guazi_listing_price_yuan": value_from(
            pricing_result,
            "target_guazi_listing_price_yuan",
            "guazi_listing_price_yuan",
        ),
        "guazi_service_fee_yuan": value_from(pricing_result, "guazi_service_fee_yuan"),
        "guazi_net_payout_yuan": value_from(pricing_result, "guazi_net_payout_yuan"),
        "cost_yuan": value_from(pricing_result, "cost_yuan"),
        "profit_yuan": value_from(pricing_result, "profit_yuan"),
        "system_suggested_purchase_price_yuan": value_from(
            pricing_result,
            "system_suggested_purchase_price_yuan",
            "suggested_purchase_price_yuan",
        ),
        "manual_confirmed_purchase_price_yuan": value_from(pricing_result, "manual_confirmed_purchase_price_yuan"),
        "manual_adjustment_yuan": value_from(pricing_result, "manual_adjustment_yuan"),
        "manual_review_note": value_from(pricing_result, "manual_review_note"),
        "final_purchase_price_yuan": value_from(pricing_result, "final_purchase_price_yuan"),
    }
    system_price_missing = (
        pricing_result.get("system_suggested_price_missing") is True
        or fields["system_suggested_purchase_price_yuan"] in (MISSING, None, "")
    )
    required_keys = ["manual_confirmed_purchase_price_yuan", "final_purchase_price_yuan"]
    if not system_price_missing:
        required_keys.extend(["system_suggested_purchase_price_yuan", "manual_adjustment_yuan"])
    for key in required_keys:
        if fields[key] == MISSING:
            warnings.append(f"MISSING_FIELD:{key}")
    target_vehicle = value_from(pricing_result, "target_vehicle", "target_vehicle_text")
    if target_vehicle == MISSING:
        target_vehicle = " ".join(
            str(value)
            for value in [
                pricing_result.get("brand"),
                pricing_result.get("series"),
                pricing_result.get("model_config") or pricing_result.get("trim"),
            ]
            if value
        ) or MISSING
    lines = [
        f"【人工复核已确认】{task_id}",
        "",
        "目标车：",
        str(target_vehicle),
        "",
        "复核原因：",
        "\n".join(str(item) for item in reasons),
        "",
        "规则说明：",
        _manual_review_reason_description(reasons),
        "",
        "分数与参考车：",
        f"target_score = {fields['target_score']}",
        f"final_reference_index = {fields['final_reference_index']}",
        f"final_reference_score = {fields['final_reference_score']}",
        f"final_reference_price_yuan = {fields['final_reference_price']}",
        "",
    ]
    if system_price_missing:
        lines.extend(
            [
                "人工确认：",
                f"最终收车价：{fields['final_purchase_price_yuan']} 元",
                "确认来源：主管人工报价",
                f"manual_review_note = {fields['manual_review_note']}",
                "",
                "最终结果：",
                f"final_purchase_price_yuan = {fields['final_purchase_price_yuan']} 元",
                "状态：人工复核已确认，待发送/回写飞书",
            ]
        )
    else:
        lines.extend(
            [
                "系统测算价格：",
                f"target_guazi_listing_price_yuan = {fields['target_guazi_listing_price_yuan']} 元",
                f"guazi_service_fee_yuan = {fields['guazi_service_fee_yuan']} 元",
                f"guazi_net_payout_yuan = {fields['guazi_net_payout_yuan']} 元",
                f"cost_yuan = {fields['cost_yuan']} 元",
                f"profit_yuan = {fields['profit_yuan']} 元",
                f"system_suggested_purchase_price_yuan = {fields['system_suggested_purchase_price_yuan']} 元",
                "",
                "人工确认：",
                f"manual_confirmed_purchase_price_yuan = {fields['manual_confirmed_purchase_price_yuan']} 元",
                f"manual_adjustment_yuan = {fields['manual_adjustment_yuan']} 元",
                f"manual_review_note = {fields['manual_review_note']}",
                "",
                "最终结果：",
                f"final_purchase_price_yuan = {fields['final_purchase_price_yuan']} 元",
                "状态：人工复核已确认，待发送/回写飞书",
            ]
        )
    text = "\n".join(lines)
    return FormatResult(text=text, warnings=warnings)


def _manual_review_reason_description(reasons: list[str]) -> str:
    descriptions: list[str] = []
    if "PRICE_DISTRIBUTION_MANUAL_REVIEW" in reasons:
        descriptions.append("参考车价格分布离散较大，系统已测算价格但需要主管确认后再下发。")
    if "TARGET_CONDITION_SILL_SCORING_REVIEW" in reasons or "TARGET_CONDITION_HEADLIGHT_REPLACE_RULE_REVIEW" in reasons:
        descriptions.append("目标车部分车况项目存在规则确认项，需要主管判断。")
    if "NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING" in reasons:
        descriptions.append("未找到 reference_score >= target_score 的边界参考车，按 V3 规则需要人工复核。")
    if "S14_WHOLE_VEHICLE_COLLECTION_INCOMPLETE" in reasons or "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION" in reasons:
        descriptions.append("参考车检测报告存在多项历史修复记录，但本次只采集到部分车况证据，参考车分数不能直接用于自动定价。")
    if "SAMPLE_SHORTAGE_MANUAL_REVIEW" in reasons:
        descriptions.append("样本偏少，建议人工复核。")
    return "\n".join(descriptions) if descriptions else "当前结果需要人工复核确认。"


def _format_profit_rate(value: Any) -> str:
    if value == MISSING:
        return "8%"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:g}%"


def _target_vehicle_detail_line(pricing_result: dict[str, Any]) -> str:
    parts = [
        value_from(pricing_result, "color"),
        value_from(pricing_result, "license_date", "register_date", "registration_date"),
        value_from(pricing_result, "mileage_text", "mileage_10k_km"),
        value_from(pricing_result, "transfer_count_text", "transfer_count"),
    ]
    visible = [str(item) for item in parts if item != MISSING and str(item).strip()]
    return "｜".join(visible) if visible else ""


def write_feishu_result_preview(
    *,
    task_dir: str | Path,
    task_id: str,
    status: str,
    errors: list[str] | None = None,
) -> FormatResult:
    task_dir = Path(task_dir)
    pricing_result_path = task_dir / "pricing_result.json"
    run_meta_path = task_dir / "run_meta.json"
    pricing_result = json.loads(pricing_result_path.read_text(encoding="utf-8")) if pricing_result_path.exists() else None
    run_meta = json.loads(run_meta_path.read_text(encoding="utf-8")) if run_meta_path.exists() else {}
    errors = list(errors or [])
    if isinstance(pricing_result, dict) and status != "FAILED" and is_automatic_pricing_terminal_success(pricing_result):
        project_root = _project_root_from_task_dir(task_dir)
        current_fingerprints = target_fingerprints_from_artifacts(project_root, task_dir, task_id=task_id)
        should_enforce_scope = bool(
            current_fingerprints
            or pricing_result.get("task_id")
            or pricing_result.get("produced_by_task_id")
            or pricing_result.get("target_fingerprint")
            or pricing_result.get("task_target_fingerprint")
        )
        if should_enforce_scope:
            scope_check = validate_result_task_scope(
                pricing_result,
                current_task_id=task_id,
                current_target_fingerprints=current_fingerprints,
                source_path=pricing_result_path,
                require_task_id=True,
                require_target_fingerprint=bool(current_fingerprints or pricing_result.get("target_fingerprint") or pricing_result.get("task_target_fingerprint")),
            )
            if not scope_check.ok:
                trace = {
                    **scope_check.as_trace(),
                    "final_feedback_target_mismatch_blocked": True,
                    "cross_task_success_result_blocked_before_feishu_send": True,
                    "canonical_error_code": FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED,
                    "primary_error_code": scope_check.code,
                }
                (task_dir / "final_feedback_target_scope_guard.json").write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                pricing_result = {**pricing_result, **trace}
                status = "FAILED"
                errors = [
                    FINAL_FEEDBACK_TARGET_MISMATCH_BLOCKED,
                    CROSS_TASK_SUCCESS_RESULT_BLOCKED_BEFORE_FEISHU_SEND,
                    scope_check.code or CROSS_TASK_PRICING_RESULT_REJECTED,
                ]
    formatted = format_result_reply(
        task_id=task_id,
        pricing_result=pricing_result,
        status=status,
        run_meta=run_meta,
        errors=errors,
    )
    (task_dir / "feishu_result_reply.preview.txt").write_text(formatted.text + "\n", encoding="utf-8")
    return formatted


def _project_root_from_task_dir(task_dir: Path) -> Path:
    for parent in task_dir.parents:
        if parent.name == "data":
            return parent.parent
    if len(task_dir.parents) >= 3:
        return task_dir.parents[2]
    return task_dir


def _failure_description(code: str) -> str:
    if code == CONFIG_MISMATCH_HARD_STOP:
        return "配置一致性硬门禁未通过，系统未输出自动收车价。请业务重新发送完整车型配置。"
    if code == "FIRST_STAGE_NOT_S10_READY":
        return "第一段 S01-S10 未到达 S10_READY，不能继续执行 S10-S16。请检查车型配置、筛选流程、颜色/车龄筛选或页面状态。"
    if code == "FIRST_STAGE_TARGET_NOT_FOUND":
        return "第一段 S01-S10 未找到目标车型入口，不能继续执行 S10-S16。请检查品牌、车系、车型配置和页面状态。"
    if code == "FIRST_STAGE_RESULT_NOT_FOUND":
        return "第一段 S01-S10 已结束，但未找到 result_s01_to_s10.json，请检查第一段输出路径。"
    if code == "FIRST_STAGE_RESULT_JSON_INVALID":
        return "第一段 S01-S10 输出的 result_s01_to_s10.json 不是有效 JSON，请检查结果文件。"
    if code == "FIRST_STAGE_SCHEMA_INVALID":
        return "第一段 S01-S10 输出缺少可判断 S10_READY 的字段，请检查第一段结果结构。"
    if code == "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS":
        return "检测到桌面升级弹窗，但没有可安全点击的“稍后升级”，已拒绝点击“立即升级”。"
    if code == "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED":
        return "桌面升级弹窗点击“稍后升级”后仍未消失，已按边界重试一次并停止，未点击“立即升级”。"
    if code in {"HUMAN_LOGIN_REQUIRED", "LOGIN_REQUIRED_MANUAL"}:
        return "检测到瓜子正式登录页且没有安全跳过入口，系统不会输入手机号、验证码或点击登录，请人工登录后再继续。"
    if code == "MAIN_SCRIPT_BLOCKED_NOT_AT_S10_READY":
        return "当前主流程未到达 S10_READY，S10-S16 二段不能执行。请先运行完整 S01-S10 入口或切换到真正全链 APP 自动化入口。"
    if code == "RESULT_SCHEMA_INVALID_FOR_PRICING":
        return "主流程输出只有合约状态或阻塞状态，没有定价核心字段，不能视为定价成功。"
    if code in {
        CONTINUE_NEXT_REFERENCE,
        "SECOND_STAGE_COLLECTION_INCOMPLETE",
        "SECOND_STAGE_CONTINUE_NEXT_REFERENCE_NOT_COMPLETED",
        "V33_LOW_SCORE_SKIPPED_CONTINUE_NEXT_REFERENCE",
    }:
        return "第二段只完成了部分参考车采集，参考车边界还没有闭合，系统应继续采集下一辆参考车，不能视为定价成功。"
    if code == V33_RECOLLECTED_PREVIOUS_REFERENCE_STILL_INCOMPLETE_NEEDS_REVIEW:
        return "V3.3 边界前参考车回采后仍不完整，不能继续点击边界车，也不能自动输出收车价，需要人工复核。"
    if code == "SECOND_STAGE_CONTINUATION_STATE_MISSING":
        return "参考车仍需继续采集，但系统未正确读取续采状态或三同车源数量，不能视为定价成功。"
    if code == RESULT_MISSING_REQUIRED_PRICING_FIELDS or str(code).startswith("MISSING_REQUIRED_FIELD:"):
        return "定价结果缺少完整参考车边界或价格链字段，系统未输出自动收车价。"
    if code == "SECOND_STAGE_FAST_HANDOFF_S10_GATE_FAILED":
        return "已进入瓜子结果页，但系统未能稳定绑定参考车，已停止定价。请稍后重试或联系管理员检查结果页识别。"
    if code == "REFERENCE_CARD_BINDING_NOT_UNIQUE":
        return "已进入瓜子结果页，但参考车卡片无法唯一绑定，已停止定价。请稍后重试或联系管理员检查结果页识别。"
    if code in {
        "S11_REPORT_ENTRY_DIRECT_CLICK_DID_NOT_ENTER_REPORT",
        "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET",
        "S11_REPORT_ENTRY_XML_STALE_RECOVERY_FAILED",
    }:
        return "系统已进入参考车详情页，但未能可靠绑定“查看完整报告”按钮，本次已安全停止，已通知管理员处理。"
    if code == "S11_CONTRACT_HANDLER_NOT_INVOKED_AFTER_DETAIL_PAGE_RECOGNIZED":
        return "系统已进入参考车详情页，但未能执行详情页采集契约，本次已安全停止，已通知管理员处理。"
    if code == "S11_DETAIL_PAGE_RECOGNITION_FAILED_AFTER_S10_CLICK":
        return "系统已点击参考车卡片，但未能可靠确认进入参考车详情页，本次已安全停止，已通知管理员处理。"
    if code == "S11_XML_STALE_OR_MISMATCHED_WITH_SCREENSHOT":
        return "系统已点击参考车卡片，但详情页截图与页面结构证据不一致，无法安全继续采集，本次已安全停止，已通知管理员处理。"
    if code == "S11_ALLOWED_ACTION_NOT_STARTED_AFTER_HANDLER_INVOKED":
        return "系统已进入参考车详情页，但详情页采集动作未能启动，本次已安全停止，已通知管理员处理。"
    if code == "S11_REPORT_SEARCH_STATE_NOT_INITIALIZED":
        return "系统已进入参考车详情页，但完整报告搜索状态未能初始化，本次已安全停止，已通知管理员处理。"
    if code in GUAZI_PUSH_POPUP_BUSINESS_SAFE_FAILURE_CODES:
        return "瓜子 APP 弹出消息推送通知弹窗，系统未能安全关闭该弹窗。"
    if code == "S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED":
        return "已进入检测报告，但系统未能安全打开历史修复详情，已停止定价。请稍后重试或联系管理员检查车况详情页识别。"
    if code == "S13_REPAIR_ITEM_CLICK_TARGET_UNSAFE":
        return "已进入检测报告，但历史修复项点击区域不安全，系统未继续自动点击，已停止定价。"
    if code == "S13_REPAIR_ITEM_CLICK_DID_NOT_OPEN_DETAIL":
        return "已进入检测报告，但点击历史修复项后未打开详情页，已停止定价。请稍后重试或联系管理员检查车况详情页识别。"
    if code in {"S13_HISTORY_REPAIR_COUNT_NOT_CONFIRMED", "S13_FOUR_REGION_LOOP_GUARD_TRIGGERED"}:
        return "\u7cfb\u7edf\u5df2\u5f00\u59cb\u91c7\u96c6\u53c2\u8003\u8f66\uff0c\u4f46\u68c0\u6d4b\u62a5\u544a\u4e2d\u7684\u5386\u53f2\u4fee\u590d\u6b21\u6570\u672a\u80fd\u53ef\u9760\u786e\u8ba4\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002"
    if code in REFERENCE_PHYSICAL_UI_BUSINESS_SAFE_FAILURE_CODES:
        return "\u7cfb\u7edf\u5df2\u5f00\u59cb\u91c7\u96c6\u53c2\u8003\u8f66\uff0c\u4f46\u8fd4\u56de\u4e09\u540c\u5217\u8868\u6216\u8fdb\u5165\u4e0b\u4e00\u8f86\u53c2\u8003\u8f66\u7684\u9875\u9762\u8bc1\u636e\u672a\u80fd\u53ef\u9760\u786e\u8ba4\uff0c\u672c\u6b21\u5df2\u5b89\u5168\u505c\u6b62\uff0c\u5df2\u901a\u77e5\u7ba1\u7406\u5458\u5904\u7406\u3002"
    if code in {"S13_HISTORY_REPAIR_ENTRY_NOT_VISIBLE_OR_UNBOUND", "S13_HISTORY_REPAIR_ENTRY_CLICK_TARGET_NOT_FOUND"}:
        return "系统已开始采集参考车，但未能可靠定位检测报告中的历史修复车况入口，为避免误采正常检测项，本次已安全停止。请重新发送目标车源并回复“确认”重新开始；如连续出现，请联系管理员检查页面状态。"
    if code == "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED":
        return "第二段 S14 车身外观详情采集未完成，不能进入打分或定价成功态。请检查 S14 最后页横滑无新内容返回契约。"
    if code == "S14_REPAIR_DETAIL_NOT_FULLY_COLLECTED":
        return "第二段 S14 车身外观详情缺少完整采集证据，已阻断 S15/S16，不能视为定价成功。"
    if code == "S14_STALE_FIRST_LINE_BINDING_UNRESOLVED":
        return "第二段 S14 当前 tab 与第一行损伤文本无法安全绑定，已拒绝把旧损伤文本写入当前部位。"
    if code == "STALE_RESULT_FILE":
        return "找到的结果文件早于本次运行开始时间，疑似旧结果，已拒绝收集。"
    if code == "MAIN_SCRIPT_NOOP_OR_STALE_RESULT":
        return "主流程可能未实际产生新结果，请检查入口脚本和结果文件时间。"
    if code == "RESULT_FILE_NOT_FOUND":
        return "主流程已结束，但未找到 pricing_result.json，请检查主流程输出路径或运行日志。"
    if code == "RESULT_JSON_INVALID":
        return "主流程输出的 pricing_result.json 不是有效 JSON，请检查结果文件。"
    if code == "MAIN_SCRIPT_FAILED":
        return "主流程返回非零退出码，请检查 run_stdout.log 和 run_stderr.log。"
    return "运行未完成，请检查任务目录中的 runner_error.json 和运行日志。"
