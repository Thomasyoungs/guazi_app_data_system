"""Structured result and feedback report output."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def directory_tree(root: Path, max_depth: int = 3) -> list[str]:
    lines: list[str] = []
    root = root.resolve()
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        if depth > max_depth:
            dirs[:] = []
            continue
        indent = "  " * depth
        lines.append(f"{indent}{current_path.name}/")
        for file_name in sorted(files):
            lines.append(f"{indent}  {file_name}")
    return lines


def write_feedback_report(
    path: Path,
    project_root: Path,
    result: dict[str, Any],
    phone_test: dict[str, Any] | None,
    local_tests: dict[str, Any] | None,
    issues: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    phone_test = phone_test or {}
    local_tests = local_tests or {}
    result_status = result.get("pricing", {}).get("status", "unknown")
    warnings = result.get("manual_review_reasons", [])
    unique_issues = _dedupe_issues(issues)
    next_steps = _next_steps_for_phone(phone_test)
    lines = [
        "# 执行反馈报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 工程目录：{project_root}",
        f"- 定价状态：{result_status}",
        "",
        "## 已创建的目录结构",
        "",
        "```text",
        *directory_tree(project_root),
        "```",
        "",
        "## 本地测试",
        "",
        f"- 状态：{local_tests.get('status', '未记录')}",
        f"- 说明：{local_tests.get('summary', '未记录')}",
        "",
        "## 手机探测结果",
        "",
        f"- adb 可用：{phone_test.get('adb_available')}",
        f"- adb 路径：{phone_test.get('adb_path')}",
        f"- 设备列表：{phone_test.get('devices', [])}",
        f"- 设备信息：{phone_test.get('device_info', {})}",
        f"- 截图路径：{phone_test.get('screenshot_path')}",
        f"- 页面识别：{phone_test.get('recognized_start_state')}",
        "",
        "## 问题记录",
        "",
    ]
    if unique_issues:
        for issue in unique_issues:
            lines.append(f"- {issue.get('code')}：{issue.get('message')}（处理：{issue.get('resolution')}）")
    else:
        lines.append("- 暂无问题记录。")
    lines.extend([
        "",
        "## 人工确认事项",
        "",
    ])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- 暂无。")
    lines.extend([
        "",
        "## 下一步建议",
        "",
        *next_steps,
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    output: list[dict[str, Any]] = []
    for issue in issues:
        key = (issue.get("code"), issue.get("message"), issue.get("resolution"))
        if key in seen:
            continue
        seen.add(key)
        output.append(issue)
    return output


def _next_steps_for_phone(phone_test: dict[str, Any]) -> list[str]:
    devices = phone_test.get("devices") or []
    has_unauthorized = any(item.get("status") == "unauthorized" for item in devices if isinstance(item, dict))
    if has_unauthorized:
        return [
            "- 在手机上确认 USB 调试 RSA 授权弹窗，勾选始终允许后点击允许。",
            "- 授权后重新执行设备检查和 APP 启动。",
        ]
    if not phone_test.get("adb_available"):
        return [
            "- 安装或配置 Android platform-tools，把 adb.exe 加入 PATH 或设置 ADB_PATH。",
            "- 然后重新执行设备检查。",
        ]
    return [
        "- 重新执行设备检查与页面识别，确认主流程页是否可稳定命中。",
    ]
