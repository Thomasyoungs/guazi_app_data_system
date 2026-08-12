import tempfile
import unittest
from pathlib import Path

from guazi_app_data_system.output_writer import read_json, write_feedback_report, write_json


class OutputFormatTest(unittest.TestCase):
    def test_json_and_report_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = {
                "pricing": {"status": "priced"},
                "manual_review_reasons": ["样本不足，结论参考性下降。"],
                "phone_test": {"adb_available": False},
            }
            write_json(tmp_path / "result.json", result)
            self.assertEqual(read_json(tmp_path / "result.json")["pricing"]["status"], "priced")

            write_feedback_report(tmp_path / "report.md", tmp_path, result, result["phone_test"], {"status": "通过"}, [])
            text = (tmp_path / "report.md").read_text(encoding="utf-8")
            self.assertIn("执行反馈报告", text)
            self.assertIn("样本不足，结论参考性下降。", text)


if __name__ == "__main__":
    unittest.main()
