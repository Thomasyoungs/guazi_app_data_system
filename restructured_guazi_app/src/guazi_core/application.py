"""Refactored main application logic for the Guazi APP data system."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .data_collector import DataCollector
from .pricing_calculator import PricingCalculator
from .simulator import StateActionSimulator
from .exceptions import GuaziFlowError


def build_runtime() -> dict[str, Any]:
    """Build the runtime context for the application."""
    # 在重构版本中，我们简化配置加载
    # 并在适当位置使用默认值
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)  # 确保输出目录存在
    
    configs = {
        "fields": {
            "same_source_policy": {
                "sample_too_small_message": "三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。"
            }
        },
        "system": {
            "paths": {
                "result_json": str(output_dir / "result.json")
            }
        }
    }
    
    runtime_context = {
        "configs": configs,
        "collector": DataCollector(configs["fields"]),
        "calculator": PricingCalculator()
    }
    
    return runtime_context


def run_simulation(runtime: dict[str, Any]) -> dict[str, Any]:
    """Run the simulation with simplified logic."""
    collector = runtime["collector"]
    calculator = runtime["calculator"]
    configs = runtime["configs"]

    # 使用模拟器运行状态-动作序列
    simulator = StateActionSimulator(collector)
    simulator.execute_sequence()

    # 获取收集的数据
    target = collector.simulated_target()
    references = collector.simulated_reference_cars()

    # 计算分数和定价
    target_score = calculator.score_target(target)
    selected_reference = calculator.select_reference(target_score, references)
    pricing = calculator.calculate_pricing(selected_reference)

    result = {
        "metadata": {
            "project": "refactored_guazi_app",
            "mode": "simulate",
        },
        "target_car": target.to_dict() if target else None,
        "target_score": target_score.to_dict() if target_score else None,
        "same_source_count": len(references),
        "reference_cars": [ref.to_dict() for ref in references],
        "selected_reference": selected_reference.to_dict() if selected_reference else None,
        "pricing": pricing,
    }

    # 将结果写入输出
    result_path = Path(configs["system"]["paths"]["result_json"])
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Refactored 瓜子二手车 APP 数据获取系统")
    parser.add_argument("--mode", choices=["simulate"], default="simulate", 
                       help="运行模式 (仅支持simulate)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the refactored application."""
    args = parse_args(argv or sys.argv[1:])
    
    try:
        runtime = build_runtime()
        
        if args.mode == "simulate":
            result = run_simulation(runtime)
            print(json.dumps({
                "result": "output/result.json",
                "status": "success"
            }, ensure_ascii=False, indent=2))
            return 0
            
    except GuaziFlowError as e:
        print(f"Guazi flow error occurred: {e.code} - {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())