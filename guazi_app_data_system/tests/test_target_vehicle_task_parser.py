import importlib.util
import json
import shutil
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "parse_target_vehicle_task.py"

SAMPLE_TEXT = """【车      型 】本田缤智2018款1.5L CVT两驱科技精英
【有无天窗】无
【指 导  价】14.58
【排放标准】国五
【上牌日期】17.10
【表显里程】8.54
【车辆颜色】白
【过户次数】0
【保险到期】26.10
【车牌归属】唐山
【具体车况】左后门局部钣金喷漆，右前门局部钣金喷漆"""

MOJIBAKE_MARKERS = ("缂ゆ櫤", "瀹氫环", "鏈", "æ", "ä")


def load_parser_module():
    spec = importlib.util.spec_from_file_location("parse_target_vehicle_task", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class TargetVehicleTaskParserTest(unittest.TestCase):
    def test_parse_honda_vezel_sample_text(self):
        module = load_parser_module()
        task = module.parse_target_vehicle_text(SAMPLE_TEXT)
        self.assertEqual(task["brand"], "本田")
        self.assertEqual(task["series"], "缤智")
        self.assertEqual(task["year_model"], "2018款")
        self.assertEqual(task["config_model"], "1.5L CVT两驱科技精英")
        self.assertEqual(task["sunroof"], "无")
        self.assertEqual(task["guide_price_wan"], 14.58)
        self.assertEqual(task["emission_standard"], "国五")
        self.assertEqual(task["registration_date"], "2017.10")
        self.assertEqual(task["display_mileage_wan_km"], 8.54)
        self.assertEqual(task["color"], "白")
        self.assertEqual(task["transfer_count"], 0)
        self.assertEqual(task["insurance_expire"], "2026.10")
        self.assertEqual(task["plate_location"], "唐山")
        self.assertEqual(
            task["target_condition"],
            [
                {"part": "左后门", "abnormalities": ["钣金", "喷漆"]},
                {"part": "右前门", "abnormalities": ["钣金", "喷漆"]},
            ],
        )

    def test_parse_vehicle_model_splits_brand_series_year_config(self):
        module = load_parser_module()
        parsed = module._split_vehicle_model("本田缤智2018款1.5L CVT两驱科技精英")
        self.assertEqual(parsed["brand"], "本田")
        self.assertEqual(parsed["series"], "缤智")
        self.assertEqual(parsed["year_model"], "2018款")
        self.assertEqual(parsed["config_model"], "1.5L CVT两驱科技精英")

    def test_parse_short_year_month_to_full_year_month(self):
        module = load_parser_module()
        self.assertEqual(module._parse_year_month("17.10"), "2017.10")
        self.assertEqual(module._parse_year_month("26.10"), "2026.10")

    def test_parse_condition_removes_local_modifier(self):
        module = load_parser_module()
        condition = module._parse_condition("左后门局部钣金喷漆")
        self.assertEqual(condition, [{"part": "左后门", "abnormalities": ["钣金", "喷漆"]}])
        self.assertNotIn("局部", condition[0]["abnormalities"])

    def test_parse_condition_keeps_only_allowed_abnormalities(self):
        module = load_parser_module()
        condition = module._parse_condition("左后门局部划痕破损喷漆，右前门换件凹陷，车顶漆面损伤")
        self.assertEqual(
            condition,
            [
                {"part": "左后门划痕破损", "abnormalities": ["喷漆"]},
                {"part": "右前门", "abnormalities": ["更换"]},
                {"part": "车顶", "abnormalities": ["喷漆"]},
            ],
        )
        flattened = [item for entry in condition for item in entry["abnormalities"]]
        self.assertNotIn("划痕", flattened)
        self.assertNotIn("破损", flattened)
        self.assertNotIn("凹陷", flattened)

    def test_parser_outputs_current_target_task_json(self):
        module = load_parser_module()
        temp_dir = ROOT / "output" / "tmp_test" / "target_vehicle_task_parser"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        output_path = temp_dir / "data" / "current_target_task.json"

        task = module.parse_and_write(SAMPLE_TEXT, output_path)

        self.assertTrue(output_path.exists())
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, task)
        self.assertEqual(payload["brand"], "本田")
        self.assertEqual(payload["target_condition"][0]["part"], "左后门")

    def test_current_target_task_written_utf8_chinese(self):
        module = load_parser_module()
        temp_dir = ROOT / "output" / "tmp_test" / "target_vehicle_task_utf8"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        output_path = temp_dir / "current_target_task.json"

        module.parse_and_write(SAMPLE_TEXT, output_path)
        payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["series"], "缤智")

    def test_current_target_task_no_mojibake(self):
        module = load_parser_module()
        temp_dir = ROOT / "output" / "tmp_test" / "target_vehicle_task_no_mojibake"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        output_path = temp_dir / "current_target_task.json"

        module.parse_and_write(SAMPLE_TEXT, output_path)
        raw = output_path.read_text(encoding="utf-8")

        for marker in MOJIBAKE_MARKERS:
            self.assertNotIn(marker, raw)

    def test_parser_writes_json_with_ensure_ascii_false(self):
        module = load_parser_module()
        temp_dir = ROOT / "output" / "tmp_test" / "target_vehicle_task_ascii_false"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        output_path = temp_dir / "current_target_task.json"

        module.parse_and_write(SAMPLE_TEXT, output_path)
        raw = output_path.read_text(encoding="utf-8")

        self.assertIn('"series": "缤智"', raw)
        self.assertNotIn("\\u7f24\\u667a", raw)

    def test_parser_reads_utf8_input_text(self):
        module = load_parser_module()
        task = module.parse_target_vehicle_text(SAMPLE_TEXT)
        self.assertEqual(task["brand"], "本田")
        self.assertEqual(task["series"], "缤智")
        self.assertEqual(task["config_model"], "1.5L CVT两驱科技精英")

    def test_honda_vezel_target_task_exact_values(self):
        module = load_parser_module()
        task = module.parse_target_vehicle_text(SAMPLE_TEXT)
        self.assertEqual(task["brand"], "本田")
        self.assertEqual(task["series"], "缤智")
        self.assertEqual(task["year_model"], "2018款")
        self.assertEqual(task["config_model"], "1.5L CVT两驱科技精英")
        self.assertEqual(task["color"], "白")


if __name__ == "__main__":
    unittest.main()
