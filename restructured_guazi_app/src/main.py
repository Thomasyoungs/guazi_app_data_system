"""Simplified CLI entry point for the Guazi APP data system."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Add src to path to import modules
sys.path.insert(0, str(Path(__file__).parent))

from guazi_core.app import GuaziApp
from guazi_core.exceptions import GuaziFlowError


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="瓜子二手车 APP 数据获取系统")
    parser.add_argument("--mode", choices=["simulate", "feishu"], default="simulate", 
                       help="运行模式：模拟或飞书消息处理")
    parser.add_argument("--config-dir", default="./config", 
                       help="配置文件目录路径")
    parser.add_argument("--output-dir", default="./output", 
                       help="输出目录路径")
    parser.add_argument("--feishu-message", type=str, 
                       help="飞书消息JSON字符串（用于feishu模式）")
    parser.add_argument("--chat-id", type=str, 
                       help="飞书聊天ID")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    
    try:
        # 初始化应用
        app = GuaziApp(config_dir=args.config_dir, output_dir=args.output_dir)
        
        if args.mode == "simulate":
            # 运行模拟模式
            result = app.run_simulation()
            print(json.dumps({
                "status": "success", 
                "mode": "simulation", 
                "result": result,
                "output_file": str(app.result_path)
            }, ensure_ascii=False, indent=2))
            
        elif args.mode == "feishu":
            # 处理飞书消息
            if not args.feishu_message:
                print(json.dumps({
                    "status": "error",
                    "message": "--feishu-message 参数必需用于飞书模式"
                }, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
                
            # 解析飞书消息
            try:
                message_data = json.loads(args.feishu_message)
            except json.JSONDecodeError as e:
                print(json.dumps({
                    "status": "error",
                    "message": f"无效的JSON消息: {e}"
                }, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
            
            # 处理飞书消息
            result = app.handle_feishu_message(message_data, args.chat_id)
            print(json.dumps({
                "status": "success" if result.get("ok") else "error", 
                "mode": "feishu",
                "result": result
            }, ensure_ascii=False, indent=2))
            
        return 0
        
    except GuaziFlowError as e:
        print(json.dumps({
            "status": "error",
            "error_type": "GuaziFlowError",
            "code": e.code,
            "message": str(e),
            "context": e.context
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error_type": type(e).__name__,
            "message": str(e)
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())