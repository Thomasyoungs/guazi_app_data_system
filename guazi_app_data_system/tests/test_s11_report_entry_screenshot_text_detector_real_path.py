import inspect
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def _draw_outline(pixels: list[list[tuple[int, int, int, int]]], rect: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = rect
    color = (106, 112, 124, 255)
    for x in range(x1, x2):
        pixels[y1][x] = color
        pixels[y2 - 1][x] = color
    for y in range(y1, y2):
        pixels[y][x1] = color
        pixels[y][x2 - 1] = color


def _write_s11_report_entry_png(path: Path) -> None:
    width, height = 1080, 2400
    left_rect = (76, 1500, 520, 1602)
    right_rect = (560, 1500, 1004, 1602)
    pixels = [[(248, 249, 250, 255) for _x in range(width)] for _y in range(height)]
    _draw_outline(pixels, left_rect)
    _draw_outline(pixels, right_rect)
    raw_rows = bytearray()
    for row in pixels:
        raw_rows.append(0)
        for r, g, b, a in row:
            raw_rows.extend((r, g, b, a))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw_rows)))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _stale_s10_snapshot(screenshot_path: Path) -> dict:
    return {
        "nodes": [
            {"text": "price sort", "labels": ["price sort"], "bounds": [0, 80, 300, 160]},
            {"text": "brand area", "labels": ["brand area"], "bounds": [0, 180, 300, 260]},
            {"text": "trim config", "labels": ["trim config"], "bounds": [0, 260, 300, 340]},
        ],
        "visible_texts": ["price sort", "brand area", "trim config"],
        "fresh_xml": "",
        "screenshot_path": str(screenshot_path),
    }


class S11ReportEntryScreenshotDetectorRealPathTests(unittest.TestCase):
    def test_stale_xml_with_screenshot_png_does_not_run_layout_detector_or_click(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot_path = Path(temp_dir) / "s11_report_entry.png"
            _write_s11_report_entry_png(screenshot_path)
            snapshot = _stale_s10_snapshot(screenshot_path)

            click_target = runtime._s11_report_entry_xml_bounds_click_target(
                snapshot,
                fresh_pair={"s11_xml_stale": True},
                recovery=True,
            )

        self.assertFalse(click_target["ok"])
        self.assertEqual(click_target["stop_code"], "S11_REPORT_ENTRY_VISIBLE_BUT_NO_BINDABLE_TARGET")
        self.assertFalse(click_target["click_attempted"])
        self.assertFalse(click_target["screenshot_used_for_click"])
        self.assertFalse(click_target["screenshot_detector_attempted"])
        self.assertEqual(click_target["detector_source"], "")
        self.assertNotIn("s11_debug_only_report_entry_layout_probe", snapshot)
        self.assertNotIn("clicked_point", click_target)

    def test_s11_runtime_real_path_has_no_screenshot_detector_auto_attach(self):
        source = inspect.getsource(runtime._s11_dynamic_visual_text_regions)
        self.assertNotIn("_s11_attach_screenshot_button_layout_regions", source)
        self.assertNotIn("_s11_detect_report_entry_button_layout_from_screenshot", source)

    def test_explicit_detector_function_is_not_a_runtime_click_source(self):
        plan_sources = runtime._s11_report_entry_contract_plan_evidence()["contract_action_algorithm"][
            "allowed_binding_sources"
        ]
        self.assertEqual(
            set(plan_sources),
            {"xml_exact_text_bounds", "xml_clickable_parent_bounds", "xml_safe_container_bounds", "xml_after_stale_recovery"},
        )


if __name__ == "__main__":
    unittest.main()
