import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from feishu_result_formatter import format_result_reply  # noqa: E402


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeAdbResult:
    success = True
    stdout = ""
    stderr = ""
    returncode = 0


class FakeClient:
    def __init__(self):
        self.commands = []

    def run(self, args, timeout=20):
        self.commands.append(list(args))
        return FakeAdbResult()


class DummyIssues:
    def record(self, code, state_id, message, context, resolution, recognized_text=None, attempts=0):
        return {"code": code, "state_id": state_id, "message": message, "context": context}


def _tick(label: str, x: int) -> dict:
    return {
        "labels": [label],
        "bounds": (x - 20, 600, x + 20, 640),
        "clickable": False,
        "enabled": True,
        "selected": False,
    }


def make_age_snapshot(*, left_age=None, right_age=None, visible_blob="age panel", xml_stale=False) -> dict:
    nodes = [
        _tick("0", 100),
        _tick("2", 300),
        _tick("4", 500),
        _tick("6", 700),
        _tick("8", 900),
        _tick("10", 1100),
        {"labels": [], "bounds": (100, 660, 1100, 700), "clickable": True, "enabled": True},
        {"labels": [], "bounds": (60, 620, 140, 760), "clickable": True, "enabled": True},
        {"labels": [], "bounds": (1060, 620, 1140, 760), "clickable": True, "enabled": True},
    ]
    snapshot = {
        "nodes": nodes,
        "visible_blob": visible_blob,
        "screenshot_path": "artifacts/screenshots/s07_age.png",
        "xml_path": "artifacts/debug/s07_age.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }
    if left_age is not None:
        snapshot["left_age"] = left_age
    if right_age is not None:
        snapshot["right_age"] = right_age
    if xml_stale:
        snapshot["xml_stale"] = True
    return snapshot


def view_result_node(text="查看12辆"):
    return {"labels": [text], "bounds": (300, 1800, 780, 1920), "clickable": True, "enabled": True}


class S07PostActionProofGateTest(unittest.TestCase):
    def setUp(self):
        self.module = load_runtime_module()

    def test_target_age_one_planned_only_does_not_pass_post_action_proof(self):
        snapshot = make_age_snapshot(left_age=1, right_age=1, visible_blob="age panel")
        exact_verify = self.module._verify_exact_age_selected(snapshot, 1)

        proof = self.module._s07_age_post_action_proof({}, snapshot, 1, exact_verify)

        self.assertTrue(proof["s07_age_action_planned"])
        self.assertFalse(proof["s07_age_action_executed"])
        self.assertFalse(proof["s07_age_post_fresh_verify_passed"])
        self.assertFalse(proof["s07_age_one_post_action_proof_passed"])
        self.assertEqual(proof["post_action_failure_reason"], "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED")

    def test_target_age_one_allows_fresh_structured_one_to_one_after_action(self):
        initial = make_age_snapshot()
        final = make_age_snapshot(left_age=1, right_age=1, visible_blob="age panel")
        context = {"client": FakeClient(), "issues": DummyIssues(), "task_params": {}}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 1)

        self.assertTrue(evidence["success"])
        self.assertTrue(evidence["AGE_FILTER_DONE"])
        self.assertTrue(evidence["s07_age_action_executed"])
        self.assertTrue(evidence["s07_age_post_fresh_verify_passed"])
        self.assertEqual(evidence["s07_age_post_action_proof_kind"], "actual_slider_range")
        self.assertIsNone(evidence["age_filter_verify_text"])

    def test_target_age_one_passes_with_post_action_fresh_one_to_one_text(self):
        initial = make_age_snapshot()
        final = make_age_snapshot(left_age=1, right_age=1, visible_blob="1-1年")
        context = {"client": FakeClient(), "issues": DummyIssues(), "task_params": {}}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 1)

        self.assertTrue(evidence["success"])
        self.assertTrue(evidence["AGE_FILTER_DONE"])
        self.assertTrue(evidence["s07_age_one_post_action_proof_passed"])
        self.assertEqual(evidence["AGE_FILTER_DONE_source"], "post_action_fresh_verify_text")
        self.assertEqual(evidence["s07_age_post_fresh_verify_text"], "1-1年")

    def test_target_age_one_rejects_broad_range_text_after_action(self):
        snapshot = make_age_snapshot(left_age=0, right_age=1, visible_blob="1年以下")
        exact_verify = self.module._verify_exact_age_selected(snapshot, 1)

        proof = self.module._s07_age_post_action_proof(
            {"swipe_command_sent": True, "left_slider_target_age": 1, "right_slider_target_age": 1},
            snapshot,
            1,
            exact_verify,
        )

        self.assertFalse(exact_verify["exact_age_verified"])
        self.assertEqual(exact_verify["stop_code"], "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED")
        self.assertFalse(proof["s07_age_one_post_action_proof_passed"])
        self.assertEqual(proof["proof_kind"], "broad_range_rejected")
        self.assertTrue(proof["s07_age_broad_text_rejected"])
        self.assertEqual(proof["post_action_failure_reason"], "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED")

    def test_target_age_one_rejects_ambiguous_one_year_text_after_action(self):
        snapshot = make_age_snapshot(left_age=0, right_age=1, visible_blob="1年")
        exact_verify = self.module._verify_exact_age_selected(snapshot, 1)

        proof = self.module._s07_age_post_action_proof(
            {"swipe_command_sent": True, "left_slider_target_age": 1, "right_slider_target_age": 1},
            snapshot,
            1,
            exact_verify,
        )

        self.assertFalse(exact_verify["exact_age_verified"])
        self.assertEqual(exact_verify["s07_age_verify_text_class"], "ambiguous")
        self.assertFalse(proof["s07_age_one_post_action_proof_passed"])
        self.assertEqual(proof["proof_kind"], "ambiguous_text_rejected")
        self.assertEqual(proof["post_action_failure_reason"], "S07_AGE_ONE_POST_ACTION_VERIFY_FAILED")

    def test_target_age_one_stale_xml_fails_even_with_visible_text(self):
        initial = make_age_snapshot()
        final = make_age_snapshot(left_age=1, right_age=1, visible_blob="1-1年", xml_stale=True)
        context = {"client": FakeClient(), "issues": DummyIssues(), "task_params": {}}

        with (
            mock.patch.object(self.module, "_capture", return_value=final),
            mock.patch.object(self.module, "_ensure_current_page_contract", lambda *_args, **_kwargs: None),
            mock.patch.object(self.module.time, "sleep", lambda _seconds: None),
        ):
            evidence = self.module._set_exact_age_from_ticks(context, initial, 1)

        self.assertFalse(evidence["success"])
        self.assertEqual(evidence["failure_reason"], "S07_POST_ACTION_FRESH_EVIDENCE_MISSING")
        self.assertTrue(evidence["s07_age_post_fresh_xml_stale"])

    def test_target_age_zero_fresh_text_proves_exact_filter(self):
        snapshot = make_age_snapshot(left_age=0, right_age=0, visible_blob="0年以下")
        exact_verify = self.module._verify_exact_age_selected(snapshot, 0)

        proof = self.module._s07_age_post_action_proof(
            {"skip_reason": "age_already_exact", "left_slider_target_age": 0, "right_slider_target_age": 0},
            snapshot,
            0,
            exact_verify,
            reused_internal_fresh=True,
        )

        self.assertTrue(proof["s07_age_zero_post_action_proof_passed"])
        self.assertTrue(proof["s07_age_post_fresh_verify_passed"])
        self.assertEqual(proof["left_slider_action"], "NOOP")
        self.assertEqual(proof["right_slider_action"], "DRAG_TO_LEFT_SLIDER_ZERO_POSITION")
        self.assertEqual(proof["actual_left_age"], 0)
        self.assertEqual(proof["actual_right_age"], 0)

    def test_target_age_zero_planned_only_without_post_fresh_fails(self):
        snapshot = {"visible_blob": "0年以下", "nodes": []}
        exact_verify = self.module._verify_exact_age_selected(snapshot, 0)

        proof = self.module._s07_age_post_action_proof(
            {"left_slider_target_age": 0, "right_slider_target_age": 0},
            snapshot,
            0,
            exact_verify,
        )

        self.assertFalse(proof["s07_age_post_fresh_done"])
        self.assertEqual(proof["post_action_failure_reason"], "S07_POST_ACTION_FRESH_EVIDENCE_MISSING")

    def test_view_result_click_blocked_when_age_filter_not_verified(self):
        gate = self.module._s07_view_result_preclick_gate(
            {"COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": False},
            {"post_action_fresh_pair_id": "a|b", "s07_age_post_fresh_verify_passed": True, "age_filter_verify_text": "1-1年"},
            view_result_node(),
            12,
            1,
        )

        self.assertFalse(gate["ok"])
        self.assertEqual(gate["stop_code"], "S07_VIEW_RESULT_BLOCKED_BY_UNVERIFIED_AGE_FILTER")
        self.assertIn("AGE_FILTER_DONE_not_true", gate["reasons"])

    def test_view_result_click_allowed_after_post_action_proof(self):
        gate = self.module._s07_view_result_preclick_gate(
            {"COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": True},
            {
                "post_action_fresh_pair_id": "a|b",
                "s07_age_post_fresh_verify_passed": True,
                "age_filter_verify_text": "1-1年",
                "s07_age_verify_text_class": "exact_range",
                "proof_kind": "exact_range",
            },
            view_result_node(),
            12,
            1,
        )

        self.assertTrue(gate["ok"])
        self.assertEqual(gate["bottom_view_result_text"], "查看12辆")

    def test_view_result_click_blocked_when_old_age_done_has_broad_text(self):
        gate = self.module._s07_view_result_preclick_gate(
            {"COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": True},
            {
                "post_action_fresh_pair_id": "a|b",
                "s07_age_post_fresh_verify_passed": True,
                "age_filter_verify_text": "1年以下",
                "s07_age_verify_text_class": "broad_range",
                "proof_kind": "broad_range_rejected",
            },
            view_result_node(),
            12,
            1,
        )

        self.assertFalse(gate["ok"])
        self.assertEqual(gate["s07_view_result_preclick_gate_decision"], "block")
        self.assertEqual(gate["s07_view_result_preclick_gate_block_reason"], "S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED")
        self.assertIn("S07_AGE_ONE_BROAD_RANGE_TEXT_REJECTED", gate["reasons"])

    def test_feishu_feedback_s07_post_action_verify_failed_accurate(self):
        reply = format_result_reply(
            task_id="FS_TEST",
            pricing_result=None,
            status="FAILED",
            run_meta={"target_age_years": 1, "registration_date": "2025.02"},
            errors=["S07_AGE_ONE_POST_ACTION_VERIFY_FAILED"],
        )

        self.assertIn("车龄筛选", reply.text)
        self.assertIn("1-1年", reply.text)
        for forbidden in ("未开始", "手机执行环境", "未成功打开到前台", "APP 未前台"):
            self.assertNotIn(forbidden, reply.text)

    def test_feishu_feedback_s07_zero_post_action_verify_failed_accurate(self):
        reply = format_result_reply(
            task_id="FS_TEST",
            pricing_result=None,
            status="FAILED",
            run_meta={"target_age_years": 0, "registration_date": "2026.02"},
            errors=["S07_AGE_ZERO_POST_ACTION_VERIFY_FAILED"],
        )

        self.assertIn("车龄筛选", reply.text)
        self.assertIn("实际结果验证未通过", reply.text)
        for forbidden in ("未开始", "手机执行环境", "未成功打开到前台", "APP 未前台"):
            self.assertNotIn(forbidden, reply.text)


if __name__ == "__main__":
    unittest.main()
