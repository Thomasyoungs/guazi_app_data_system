"""Handle Feishu messages for the Guazi app data system."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from ..models import CarData


class FeishuMessageHandler:
    """Handles incoming Feishu messages and converts them to car data."""
    
    def __init__(self, config_dir: str | Path = "./config"):
        self.config_dir = Path(config_dir)
        
    def parse_message_to_task(self, message: dict[str, Any]) -> CarData | None:
        """
        Parse a Feishu message into a target task (CarData object).
        
        Args:
            message: Dictionary containing Feishu message data
            
        Returns:
            CarData object with parsed information or None if parsing fails
        """
        try:
            # Extract text content from the message
            text = self._extract_text_from_message(message)
            
            if not text:
                return None
                
            # Parse the text to extract car information
            car_data = self._parse_text_to_car_data(text)
            
            return car_data
            
        except Exception as e:
            print(f"Error parsing Feishu message: {e}")
            return None
    
    def _extract_text_from_message(self, message: dict[str, Any]) -> str:
        """Extract text content from Feishu message."""
        # Try different possible locations for text in the message
        text = message.get('text')
        if text:
            return text.strip()
            
        message_body = message.get('message', {})
        text = message_body.get('text')
        if text:
            return text.strip()
            
        content = message_body.get('content')
        if content:
            if isinstance(content, str):
                try:
                    # Content might be JSON-encoded
                    decoded = json.loads(content)
                    return decoded.get('text', '').strip()
                except json.JSONDecodeError:
                    return content.strip()
            elif isinstance(content, dict):
                return content.get('text', '').strip()
                
        return ''
    
    def _parse_text_to_car_data(self, text: str) -> CarData:
        """
        Parse text content to extract car information.
        
        This is a simplified parser. In a full implementation, this would 
        use more sophisticated NLP techniques to extract structured data.
        """
        # Simple pattern matching for demonstration
        # In a real implementation, this would use NLP models or regex patterns
        data = {
            "brand": "",
            "series": "",
            "year": None,
            "mileage": None,
            "price": None,
            "features": [],
            "task_id": ""
        }
        
        # Look for basic patterns in the text
        lines = text.split('\n')
        for line in lines:
            line_lower = line.lower()
            if '品牌' in line or 'brand' in line_lower:
                # Extract brand info
                parts = line.split(':') if ':' in line else line.split('：')
                if len(parts) > 1:
                    data['brand'] = parts[1].strip()
            elif '车系' in line or 'series' in line_lower:
                # Extract series info
                parts = line.split(':') if ':' in line else line.split('：')
                if len(parts) > 1:
                    data['series'] = parts[1].strip()
            elif '年份' in line or 'year' in line_lower:
                # Extract year info
                import re
                year_match = re.search(r'\d{4}', line)
                if year_match:
                    data['year'] = int(year_match.group())
            elif '里程' in line or 'mileage' in line_lower:
                # Extract mileage info
                import re
                mileage_match = re.search(r'(\d+\.?\d*)\s*(万公里|km|万)', line)
                if mileage_match:
                    data['mileage'] = float(mileage_match.group(1))
                    
        # Generate a simple task ID based on timestamp
        import time
        data['task_id'] = f"FS{int(time.time())}"
        
        return CarData(**data)
    
    def send_result_back(self, result: dict[str, Any], chat_id: str | None = None) -> dict[str, Any]:
        """
        Send the pricing result back to Feishu.
        
        Args:
            result: The pricing result to send
            chat_id: The chat ID to send the message to
            
        Returns:
            Response from the send operation
        """
        # In a real implementation, this would use the Feishu API to send messages
        # For now, we'll simulate the sending
        chat_id = chat_id or os.getenv("FEISHU_TEST_CHAT_ID", "test_chat_id")
        
        # Format the result into a message
        message_text = self._format_result_as_message(result)
        
        # Simulate sending the message (in real implementation, this would call Feishu API)
        print(f"Would send message to chat {chat_id}: {message_text}")
        
        return {
            "ok": True,
            "chat_id": chat_id,
            "message_sent": message_text,
            "dry_run": True  # In a real implementation, this would be determined by configuration
        }
    
    def _format_result_as_message(self, result: dict[str, Any]) -> str:
        """Format the pricing result as a Feishu message."""
        metadata = result.get('metadata', {})
        target_car = result.get('target_car', {})
        pricing = result.get('pricing', 'N/A')
        
        message_parts = [
            "【瓜子二手车定价结果】",
            f"项目: {metadata.get('project', 'N/A')}",
            f"模式: {metadata.get('mode', 'N/A')}",
        ]
        
        if target_car:
            message_parts.append("\n--- 目标车辆信息 ---")
            message_parts.append(f"品牌: {target_car.get('brand', 'N/A')}")
            message_parts.append(f"车系: {target_car.get('series', 'N/A')}")
            message_parts.append(f"年份: {target_car.get('year', 'N/A')}")
            message_parts.append(f"里程: {target_car.get('mileage', 'N/A')}万公里")
        
        message_parts.append(f"\n--- 定价结果 ---")
        message_parts.append(f"建议收车价: {pricing} 元")
        
        if 'manual_review_reasons' in result and result['manual_review_reasons']:
            message_parts.append(f"\n--- 人工审核原因 ---")
            for reason in result['manual_review_reasons']:
                message_parts.append(f"- {reason}")
        
        return "\n".join(message_parts)