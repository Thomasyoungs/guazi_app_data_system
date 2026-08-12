import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import rule_source_sync_check  # noqa: E402
from rule_source_sync_check import check_rule_source_sync, validate_adb_target_device_config  # noqa: E402


PRODUCTION_ADB_CONFIG = """
active_adb_serial: "6TGYHPZCETCSK6L"
device_alias: "Redmi Note 12 5G"
strict_device_selection: true
allow_default_when_single_device: false
"""


TEMPORARY_ADB_OVERRIDE_CONFIG = """
production_device:
  active_adb_serial: "6TGYHPZCETCSK6L"
  device_alias: "Redmi Note 12 5G"

active_adb_serial: "1d76fbdd0923"
device_alias: "Temporary Android device 1d76fbdd0923 / model 22101317C"
strict_device_selection: true
allow_default_when_single_device: false
adb_runtime_env_mode: "user_shell"
use_isolated_adb_home: false
adb_path_strategy: "path_first"
explicit_adb_vendor_keys: ""

temporary_device_override:
  enabled: true
  temporary_adb_serial: "1d76fbdd0923"
  temporary_device_alias: "Temporary Android device 1d76fbdd0923 / model 22101317C"
  temporary_device_model: "22101317C"
  original_adb_serial: "6TGYHPZCETCSK6L"
  original_device_alias: "Redmi Note 12 5G"
  reason: "test temporary override"
  created_at: "2026-06-23T08:34:23+08:00"
"""


class RuleSourceSyncCheckTest(unittest.TestCase):
    def _legacy_scan_fixture(
        self,
        *,
        blacklist_rules,
        allowlist_entries,
        files,
        scan_roots=("scripts", "src", "config", "tests"),
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "config").mkdir(parents=True, exist_ok=True)
        blacklist = {
            "version": "test",
            "active_scan_roots": list(scan_roots),
            "historical_path_markers": ["evidence", "backup", "reports"],
            "rules": blacklist_rules,
        }
        allowlist = {
            "version": "test",
            "entries": allowlist_entries,
        }
        (root / "config" / "legacy_rule_blacklist.yaml").write_text(json.dumps(blacklist), encoding="utf-8")
        (root / "config" / "legacy_rule_allowlist.yaml").write_text(json.dumps(allowlist), encoding="utf-8")
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return temp, root

    def _scan_legacy_fixture(self, **kwargs):
        temp, root = self._legacy_scan_fixture(**kwargs)
        self.addCleanup(temp.cleanup)
        errors: list[str] = []
        warnings: list[str] = []
        result = rule_source_sync_check._scan_legacy_rule_blacklist(root, errors, warnings)
        return result, errors, warnings

    def _legacy_rule(self, rule_id, pattern):
        return {"id": rule_id, "pattern": pattern, "active_must_fail": True}

    def _allow(self, rule_id, file_pattern, context, *, max_occurrences=1, reason="test allow"):
        return {
            "legacy_rule_id": rule_id,
            "file_pattern": file_pattern,
            "allowed_context": context,
            "reason": reason,
            "max_occurrences": max_occurrences,
            "runtime_reachable": False,
            "expires_when": "test expires",
            "owner": "test",
            "related_test": "tests/test_rule_source_sync_check.py",
        }

    def test_rule_source_sync_fails_on_unallowlisted_legacy_warning(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_dynamic_button_rect")],
            allowlist_entries=[],
            files={"scripts/live.py": 'click_source = "screenshot_dynamic_button_rect"\n'},
            scan_roots=("scripts",),
        )

        self.assertTrue(any("LEGACY_RULE_ACTIVE_RESIDUE_FAILED" in item for item in errors), errors)

    def test_legacy_allowlist_requires_reason_and_runtime_unreachable(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_dynamic_button_rect")],
            allowlist_entries=[
                {
                    "legacy_rule_id": "old_s11_screenshot_click",
                    "file_pattern": "scripts/live.py",
                    "allowed_context": "DEBUG_ONLY_ALLOWED",
                    "max_occurrences": 1,
                    "runtime_reachable": True,
                    "expires_when": "test",
                    "owner": "test",
                    "related_test": "tests/test_rule_source_sync_check.py",
                }
            ],
            files={"scripts/live.py": 'snapshot["screenshot_dynamic_button_rect"] = {}\n'},
            scan_roots=("scripts",),
        )

        self.assertIn("LEGACY_RULE_ALLOWLIST_INVALID_ENTRY:0:missing:reason", errors)
        self.assertIn("LEGACY_RULE_ALLOWLIST_RUNTIME_REACHABLE_NOT_FALSE:0", errors)

    def test_legacy_allowlist_max_occurrences_enforced(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_dynamic_button_rect")],
            allowlist_entries=[
                self._allow(
                    "old_s11_screenshot_click",
                    "scripts/debug.py",
                    "DEBUG_ONLY_ALLOWED",
                    max_occurrences=1,
                )
            ],
            files={"scripts/debug.py": 'a = "screenshot_dynamic_button_rect"\nb = "screenshot_dynamic_button_rect"\n'},
            scan_roots=("scripts",),
        )

        self.assertTrue(any("LEGACY_RULE_ALLOWLIST_MAX_OCCURRENCES_EXCEEDED" in item for item in errors), errors)

    def test_s11_screenshot_detector_allowlisted_debug_only(self):
        result, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_button_layout_detector")],
            allowlist_entries=[
                self._allow(
                    "old_s11_screenshot_click",
                    "scripts/runtime_s10_to_s16_mainline.py",
                    "DEBUG_ONLY_ALLOWED",
                    reason="debug trace only",
                )
            ],
            files={"scripts/runtime_s10_to_s16_mainline.py": 'snapshot["s11_screenshot_button_layout_detector"] = {}\n'},
            scan_roots=("scripts",),
        )

        self.assertEqual(errors, [])
        self.assertEqual(result["legacy_allowlisted_warnings"][0]["classification"], "DEBUG_ONLY_ALLOWED")

    def test_s11_screenshot_detector_cannot_generate_click_target(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_dynamic_button_rect")],
            allowlist_entries=[
                self._allow(
                    "old_s11_screenshot_click",
                    "scripts/runtime_s10_to_s16_mainline.py",
                    "DEBUG_ONLY_ALLOWED",
                    reason="debug trace only",
                )
            ],
            files={"scripts/runtime_s10_to_s16_mainline.py": 'click_source = "screenshot_dynamic_button_rect"\n'},
            scan_roots=("scripts",),
        )

        self.assertTrue(any("ACTIVE_EXECUTION_PATH_MUST_FIX" in item for item in errors), errors)

    def test_s11_screenshot_dynamic_button_rect_active_path_fails_rule_sync(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_dynamic_button_rect")],
            allowlist_entries=[],
            files={"src/live.py": 'binding_source = "screenshot_dynamic_button_rect"\n'},
            scan_roots=("src",),
        )

        self.assertTrue(any("LEGACY_RULE_ACTIVE_RESIDUE_FAILED" in item for item in errors), errors)

    def test_s07_full_track_ratio_active_path_fails_rule_sync(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[
                self._legacy_rule("old_s07_full_track_ratio_with_unlimited", "full_track_ratio_with_unlimited")
            ],
            allowlist_entries=[],
            files={"scripts/s07.py": 'action_algorithm_used = "full_track_ratio_with_unlimited"\n'},
            scan_roots=("scripts",),
        )

        self.assertTrue(any("LEGACY_RULE_ACTIVE_RESIDUE_FAILED" in item for item in errors), errors)

    def test_s07_target_age_plus_one_active_path_fails_rule_sync(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s07_target_age_plus_one", "target_age_plus_one")],
            allowlist_entries=[],
            files={"scripts/s07.py": 'target_x_algorithm = "target_age_plus_one"\n'},
            scan_roots=("scripts",),
        )

        self.assertTrue(any("LEGACY_RULE_ACTIVE_RESIDUE_FAILED" in item for item in errors), errors)

    def test_s07_ghost_handle_binding_active_path_fails_rule_sync(self):
        _, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s07_ghost_handle_binding", "ghost_handle_binding")],
            allowlist_entries=[],
            files={"scripts/s07.py": 'selected_handle_source = "ghost_handle_binding"\n'},
            scan_roots=("scripts",),
        )

        self.assertTrue(any("LEGACY_RULE_ACTIVE_RESIDUE_FAILED" in item for item in errors), errors)

    def test_s07_legacy_terms_allowlisted_only_in_negative_tests_or_debug(self):
        result, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s07_full_track_ratio_with_unlimited", "full_track_ratio_with_unlimited")],
            allowlist_entries=[
                self._allow(
                    "old_s07_full_track_ratio_with_unlimited",
                    "tests/*.py",
                    "NEGATIVE_TEST_FIXTURE_ALLOWED",
                    reason="negative test only",
                )
            ],
            files={"tests/test_negative.py": 'record["action_algorithm_used"] = "full_track_ratio_with_unlimited"\n'},
            scan_roots=("tests",),
        )

        self.assertEqual(errors, [])
        self.assertEqual(result["legacy_allowlisted_warnings"][0]["classification"], "NEGATIVE_TEST_FIXTURE_ALLOWED")
        self.assertIs(result["legacy_allowlisted_warnings"][0]["runtime_reachable"], False)

    def test_negative_test_fixture_runtime_reachable_is_always_false(self):
        result, errors, _ = self._scan_legacy_fixture(
            blacklist_rules=[self._legacy_rule("old_s11_screenshot_click", "screenshot_dynamic_button_rect")],
            allowlist_entries=[
                self._allow(
                    "old_s11_screenshot_click",
                    "tests/*.py",
                    "NEGATIVE_TEST_FIXTURE_ALLOWED",
                    reason="negative test only",
                )
            ],
            files={"tests/test_negative.py": 'click_source = "screenshot_dynamic_button_rect"\n'},
            scan_roots=("tests",),
        )

        self.assertEqual(errors, [])
        self.assertEqual(result["legacy_allowlisted_warnings"][0]["classification"], "NEGATIVE_TEST_FIXTURE_ALLOWED")
        self.assertIs(result["legacy_allowlisted_warnings"][0]["runtime_reachable"], False)

    def test_adb_target_default_production_config_passes(self):
        self.assertEqual(validate_adb_target_device_config(PRODUCTION_ADB_CONFIG), [])

    def test_adb_target_direct_temp_serial_without_override_fails(self):
        config = PRODUCTION_ADB_CONFIG.replace("6TGYHPZCETCSK6L", "1d76fbdd0923").replace(
            "Redmi Note 12 5G", "Temporary Android device 1d76fbdd0923"
        )

        errors = validate_adb_target_device_config(config)

        self.assertIn("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_REQUIRED", errors)

    def test_adb_target_temporary_override_complete_passes(self):
        self.assertEqual(validate_adb_target_device_config(TEMPORARY_ADB_OVERRIDE_CONFIG, backup_exists=True), [])

    def test_adb_target_temporary_override_requires_reason(self):
        config = TEMPORARY_ADB_OVERRIDE_CONFIG.replace('  reason: "test temporary override"\n', "")

        errors = validate_adb_target_device_config(config, backup_exists=True)

        self.assertIn("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_REASON_MISSING", errors)

    def test_adb_target_temporary_override_requires_production_device(self):
        config = TEMPORARY_ADB_OVERRIDE_CONFIG.replace(
            'production_device:\n  active_adb_serial: "6TGYHPZCETCSK6L"\n  device_alias: "Redmi Note 12 5G"\n\n',
            "",
        )

        errors = validate_adb_target_device_config(config, backup_exists=True)

        self.assertIn("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_PRODUCTION_DEVICE_MISSING", errors)

    def test_adb_target_temporary_override_rejects_default_device_fallback(self):
        config = TEMPORARY_ADB_OVERRIDE_CONFIG.replace(
            "allow_default_when_single_device: false", "allow_default_when_single_device: true"
        )

        errors = validate_adb_target_device_config(config, backup_exists=True)

        self.assertIn("ADB_TARGET_DEVICE_ALLOW_DEFAULT_NOT_FALSE", errors)

    def test_adb_target_temporary_override_requires_strict_selection(self):
        config = TEMPORARY_ADB_OVERRIDE_CONFIG.replace("strict_device_selection: true", "strict_device_selection: false")

        errors = validate_adb_target_device_config(config, backup_exists=True)

        self.assertIn("ADB_TARGET_DEVICE_STRICT_SELECTION_NOT_TRUE", errors)

    def test_adb_target_temporary_override_requires_original_production_serial(self):
        config = TEMPORARY_ADB_OVERRIDE_CONFIG.replace(
            'original_adb_serial: "6TGYHPZCETCSK6L"', 'original_adb_serial: "1d76fbdd0923"'
        )

        errors = validate_adb_target_device_config(config, backup_exists=True)

        self.assertIn("ADB_TARGET_DEVICE_TEMPORARY_ORIGINAL_SERIAL_NOT_REDMI_NOTE_12", errors)

    def test_adb_target_temporary_override_requires_active_matches_temporary(self):
        config = TEMPORARY_ADB_OVERRIDE_CONFIG.replace(
            'active_adb_serial: "1d76fbdd0923"', 'active_adb_serial: "another-device"', 1
        )

        errors = validate_adb_target_device_config(config, backup_exists=True)

        self.assertIn("ADB_TARGET_DEVICE_ACTIVE_SERIAL_NOT_TEMPORARY_SERIAL", errors)

    def test_adb_target_temporary_override_requires_backup(self):
        errors = validate_adb_target_device_config(TEMPORARY_ADB_OVERRIDE_CONFIG, backup_exists=False)

        self.assertIn("ADB_TARGET_DEVICE_TEMPORARY_OVERRIDE_BACKUP_MISSING", errors)

    def test_rule_manifest_points_to_latest_v149_v111_v33_v126_files(self):
        manifest = json.loads((ROOT / "config" / "rule_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["data_flow_contract"]["version"], "V1.50")
        self.assertIn("V1.50", manifest["data_flow_contract"]["file"])
        self.assertEqual(manifest["scoring_rule"]["version"], "V1.11")
        self.assertIn("V1.11", manifest["scoring_rule"]["file"])
        self.assertEqual(
            manifest["reference_selection_rule"]["version"],
            "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
        )
        self.assertEqual(
            manifest["reference_selection_rule"]["early_exit_rule_id"],
            "V33_S14_LOW_SCORE_UPPER_BOUND_SKIP_AND_RECOLLECT_CONTRACT",
        )
        self.assertEqual(manifest["competition_coefficient_rule"]["version"], "V1.2.6")
        self.assertIn("V1.2.6", manifest["competition_coefficient_rule"]["file"])
        self.assertEqual(manifest["pricing_rule"]["version"], "V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT")
        self.assertIn("V3.3", manifest["pricing_rule"]["file"])
        self.assertEqual(manifest["pricing_rule"]["profit_rate"], 0.08)
        self.assertIn("0.08", manifest["pricing_rule"]["profit_formula"])
        self.assertEqual(
            manifest["pricing_rule"]["guazi_service_fee_tiers"],
            [
                {"min_price_yuan": 200000, "service_fee_yuan": 5000},
                {"min_price_yuan": 150000, "service_fee_yuan": 4000},
                {"min_price_yuan": 100000, "service_fee_yuan": 3500},
                {"min_price_yuan": 50000, "service_fee_yuan": 3000},
                {"min_price_yuan": 0, "service_fee_yuan": 2500},
            ],
        )
        self.assertFalse(manifest["reference_selection_rule"]["competition_coefficient_affects_selection"])
        self.assertTrue(any(".bak_before_s05_trim_scroll" in item["file"] for item in manifest["ignored_rule_source_files"]))

    def test_rule_source_sync_explicitly_checks_scoring_rule_v111(self):
        result = check_rule_source_sync(ROOT)

        self.assertTrue(result["ok"], result)
        self.assertNotIn("SCORING_RULE_VERSION_NOT_V1_11", result["errors"])
        self.assertNotIn("FIELDS_ACTIVE_SCORING_RULE_VERSION_NOT_V1_11", result["errors"])

    def test_rule_source_sync_rejects_non_v111_scoring_rule(self):
        with mock.patch.object(rule_source_sync_check, "EXPECTED_SCORING_RULE_VERSION", "V9"):
            result = rule_source_sync_check.check_rule_source_sync(ROOT)

        self.assertFalse(result["ok"], result)
        self.assertIn("SCORING_RULE_VERSION_NOT_V1_11", result["errors"])
        self.assertIn("FIELDS_SCORING_RULE_VERSION_NOT_V1_11", result["errors"])
        self.assertIn("FIELDS_ACTIVE_SCORING_RULE_VERSION_NOT_V1_11", result["errors"])

    def test_rule_source_policy_is_folder_only_and_chat_cannot_override(self):
        manifest = json.loads((ROOT / "config" / "rule_manifest.json").read_text(encoding="utf-8"))
        policy = manifest["rule_source_policy"]

        self.assertTrue(policy["folder_only"])
        self.assertTrue(policy["chat_snippet_cannot_override"])
        self.assertTrue(policy["require_latest_file_read_before_patch"])
        self.assertTrue(policy["no_hot_reload_running_task"])

    def test_pages_yaml_contains_s14_slogin_and_v3_contract(self):
        pages_text = (ROOT / "config" / "pages.yaml").read_text(encoding="utf-8")

        for keyword in (
            "S14_SUBPAGE_WITH_SYSTEM_BACK",
            "whole_vehicle_completion_gate",
            "reference_score_usable_for_boundary_requires_whole_vehicle_complete",
            "S_LOGIN",
            "LOGIN_REQUIRED_MANUAL",
            "V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT",
            "boundary_requires_reference_score_trustworthy",
            "boundary_requires_reference_score_usable_for_boundary",
        ):
            self.assertIn(keyword, pages_text)
        self.assertNotIn("reference_score_ge_target", pages_text)
        self.assertNotIn("score_ge_target_direct_to_s16", pages_text)

    def test_runner_has_requeue_run_identity_and_utf8_subprocess(self):
        runner_text = (ROOT / "scripts" / "pricing_runner.py").read_text(encoding="utf-8")

        for keyword in (
            "--requeue-second-stage",
            "run_id",
            "generation_id",
            'encoding="utf-8"',
            'errors="replace"',
            "STALE_RUN_RESULT_IGNORED",
            "technical_status",
            "business_status",
            "pricing_result_business_status",
            "_find_revalidation_pricing_result",
            "_pricing_result_identity_error",
            "--manual-confirm-price",
            "--manual-review-note",
            "--send-result",
            "RESULT_SENT",
            "MANUAL_REVIEW_CONFIRMED",
            "manual_confirm_result.json",
        ):
            self.assertIn(keyword, runner_text)

    def test_formatter_collector_nested_manual_review_contract_is_checked(self):
        collector_text = (ROOT / "scripts" / "pricing_result_collector.py").read_text(encoding="utf-8")
        formatter_text = (ROOT / "scripts" / "feishu_result_formatter.py").read_text(encoding="utf-8")
        sync_check_text = (ROOT / "scripts" / "rule_source_sync_check.py").read_text(encoding="utf-8")
        fields = json.loads((ROOT / "config" / "fields.yaml").read_text(encoding="utf-8-sig"))

        self.assertIn("resolve_pricing_result_field", collector_text)
        self.assertIn("pricing_result_business_status", collector_text)
        self.assertIn("FULL_CHAIN_MANUAL_REVIEW_DONE", collector_text)
        self.assertIn("_format_manual_review_reply", formatter_text)
        self.assertIn("_check_result_mapping_contract", sync_check_text)
        self.assertIn("FORMATTER_MANUAL_REVIEW_BOUNDARY_NULL_WARNINGS_PRESENT", sync_check_text)
        self.assertEqual(fields["result_field_mapping"]["read_order"], ["top_level", "s17_payload", "pricing", "confirmed_aliases"])
        self.assertIn("FULL_CHAIN_MANUAL_REVIEW_DONE", fields["result_field_mapping"]["manual_review_statuses"])
        self.assertEqual(fields["pricing"]["profit_rate"], 0.08)
        self.assertEqual(fields["manual_review_confirmation"]["status"], "MANUAL_REVIEW_CONFIRMED")
        self.assertTrue(fields["manual_review_confirmation"]["do_not_overwrite_suggested_purchase_price_yuan"])

    def test_rule_source_sync_script_passes(self):
        result = check_rule_source_sync(ROOT)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "RULE_SOURCE_SYNC_PASSED")


if __name__ == "__main__":
    unittest.main()
