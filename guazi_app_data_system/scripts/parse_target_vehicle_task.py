"""Parse a user-provided target vehicle text block into current task JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "current_target_task.json"

KNOWN_BRANDS = [
    "阿尔法罗密欧",
    "阿斯顿马丁",
    "保时捷",
    "比亚迪",
    "别克",
    "奔驰",
    "宝马",
    "本田",
    "大众",
    "丰田",
    "福特",
    "广汽传祺",
    "哈弗",
    "红旗",
    "吉利",
    "凯迪拉克",
    "雷克萨斯",
    "马自达",
    "日产",
    "特斯拉",
    "沃尔沃",
    "现代",
    "雪佛兰",
    "长安",
]

ALLOWED_ABNORMALITY_PATTERNS = [
    ("更换", re.compile(r"更换|换件")),
    ("钣金", re.compile(r"钣金")),
    ("喷漆", re.compile(r"补漆|喷漆|漆面修复|漆面损伤|漆面")),
]


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", "", label or "")


def _parse_labeled_text(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"【([^】]+)】\s*(.*)", line)
        if not match:
            continue
        fields[_normalize_label(match.group(1))] = match.group(2).strip()
    return fields


def _split_vehicle_model(model_text: str) -> dict[str, str]:
    text = re.sub(r"\s+", " ", model_text.strip())
    year_match = re.search(r"(19\d{2}款|20\d{2}款)", text)
    if not year_match:
        raise ValueError("vehicle model must contain a year model like 2018款")

    before_year = text[: year_match.start()].strip()
    year_model = year_match.group(1)
    config_model = text[year_match.end() :].strip()

    brand = next((item for item in KNOWN_BRANDS if before_year.startswith(item)), "")
    if not brand:
        brand = before_year[:2]
    series = before_year[len(brand) :].strip()
    if not brand or not series or not config_model:
        raise ValueError("vehicle model must include brand, series, year model, and config model")
    return {
        "brand": brand,
        "series": series,
        "year_model": year_model,
        "config_model": config_model,
    }


def _parse_year_month(value: str) -> str:
    match = re.search(r"(\d{2,4})[./-](\d{1,2})", value.strip())
    if not match:
        raise ValueError(f"invalid year-month value: {value}")
    raw_year = match.group(1)
    month = int(match.group(2))
    year = int(raw_year)
    if len(raw_year) == 2:
        year = 2000 + year if year <= 50 else 1900 + year
    return f"{year}.{month:02d}"


def _parse_float(value: str) -> float:
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        raise ValueError(f"invalid numeric value: {value}")
    return float(match.group(0))


def _parse_int(value: str) -> int:
    match = re.search(r"\d+", value)
    if not match:
        raise ValueError(f"invalid integer value: {value}")
    return int(match.group(0))


def _parse_condition(condition_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw_item in re.split(r"[，,、]\s*", condition_text.strip()):
        item = raw_item.strip()
        if not item:
            continue
        cleaned = item.replace("局部", "")
        abnormalities = [
            normalized
            for normalized, pattern in ALLOWED_ABNORMALITY_PATTERNS
            if pattern.search(cleaned)
        ]
        if not abnormalities:
            continue
        first_match = min(
            (pattern.search(cleaned) for _normalized, pattern in ALLOWED_ABNORMALITY_PATTERNS),
            key=lambda match: match.start() if match else 10**9,
        )
        if not first_match:
            continue
        part = cleaned[: first_match.start()].strip()
        if not part:
            continue
        items.append({"part": part, "abnormalities": abnormalities})
    return items


def parse_target_vehicle_text(text: str) -> dict[str, Any]:
    fields = _parse_labeled_text(text)
    model_parts = _split_vehicle_model(fields.get("车型", ""))
    return {
        **model_parts,
        "sunroof": fields.get("有无天窗", ""),
        "guide_price_wan": _parse_float(fields.get("指导价", "")),
        "emission_standard": fields.get("排放标准", ""),
        "registration_date": _parse_year_month(fields.get("上牌日期", "")),
        "display_mileage_wan_km": _parse_float(fields.get("表显里程", "")),
        "color": fields.get("车辆颜色", ""),
        "transfer_count": _parse_int(fields.get("过户次数", "")),
        "insurance_expire": _parse_year_month(fields.get("保险到期", "")),
        "plate_location": fields.get("车牌归属", ""),
        "target_condition": _parse_condition(fields.get("具体车况", "")),
    }


def write_current_target_task(task: dict[str, Any], output_path: Path = DEFAULT_OUTPUT) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_and_write(text: str, output_path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    task = parse_target_vehicle_text(text)
    write_current_target_task(task, output_path)
    return task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse target vehicle task text into JSON.")
    parser.add_argument("input", nargs="?", help="Input text file. If omitted, stdin is used.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    args = parser.parse_args(argv)

    if args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    task = parse_and_write(text, Path(args.output))
    print(json.dumps(task, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
