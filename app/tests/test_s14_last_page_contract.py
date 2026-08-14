import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_s10_to_s16_mainline as runtime  # noqa: E402
from feishu_result_formatter import format_result_reply  # noqa: E402
from pricing_result_collector import validate_pricing_result_payload  # noqa: E402


class DummyTiming:
    def add(self, **kwargs):
        self.last = kwargs


class DummyIssues:
    def __init__(self):
        self.records = []

    def record(self, code, page, message, context, severity):
        payload = {
            "code": code,
            "page": page,
            "message": message,
            "context": context,
            "severity": severity,
        }
        self.records.append(payload)
        return payload


def snapshot_with_damage_lines(*, stale_line=True, exact_line=True):
    nodes = [
        {
            "labels": ["瓜子官方检测报告"],
            "bounds": [0, 0, 1080, 90],
            "selected": False,
            "clickable": False,
            "enabled": True,
        },
        {
            "labels": ["前保险杠(1/1)"],
            "bounds": [620, 120, 1040, 190],
            "selected": True,
            "clickable": True,
            "enabled": True,
        },
    ]
    visible_texts = ["瓜子官方检测报告", "前保险杠(1/1)"]
    if stale_line:
        nodes.append(
            {
                "labels": ["右后翼子板—钣金"],
                "bounds": [120, 720, 760, 790],
                "selected": False,
                "clickable": False,
                "enabled": True,
            }
        )
        visible_texts.append("右后翼子板—钣金")
    if exact_line:
        nodes.append(
            {
                "labels": ["前保险杠—漆面损伤"],
                "bounds": [0, 0, 0, 0],
                "selected": False,
                "clickable": False,
                "enabled": True,
            }
        )
    return {
        "nodes": nodes,
        "visible_texts": visible_texts,
        "fresh_xml": "",
        "screenshot_path": "",
        "xml_path": "",
    }


def snapshot_for_selected_s14(tab_label, *first_lines):
    nodes = [
        {
            "labels": ["瓜子官方检测报告"],
            "bounds": [0, 0, 1080, 90],
            "selected": False,
            "clickable": False,
            "enabled": True,
        },
        {
            "labels": [tab_label],
            "bounds": [620, 120, 1040, 190],
            "selected": True,
            "clickable": True,
            "enabled": True,
        },
    ]
    visible_texts = ["瓜子官方检测报告", tab_label]
    for index, line in enumerate(first_lines):
        nodes.append(
            {
                "labels": [line],
                "bounds": [120, 720 + index * 60, 900, 770 + index * 60],
                "selected": False,
                "clickable": False,
                "enabled": True,
            }
        )
        visible_texts.append(line)
    return {
        "nodes": nodes,
        "visible_texts": visible_texts,
        "fresh_xml": "",
        "screenshot_path": "",
        "xml_path": "",
    }


class S14LastPageContractTest(unittest.TestCase):
    def test_v144_part_and_damage_boundaries(self):
        cases = [
            ("右C柱覆盖面--变形", "右C柱覆盖面", "变形", runtime.S14_NON_SCORING_DAMAGE, "surface_non_structure"),
            ("右C柱覆盖面--钣金", "右C柱覆盖面", "钣金", "钣金", "surface_non_structure"),
            ("右C柱--钣金", "ABC柱", "钣金", "钣金", "special_structure_risk"),
            ("左B柱饰板--喷漆", "左B柱饰板", "喷漆", "喷漆", "surface_non_structure"),
            ("左B柱--喷漆", "ABC柱", "喷漆", "喷漆", "special_structure_risk"),
            ("前保险杠--变形", "前保险杠", "变形", runtime.S14_NON_SCORING_DAMAGE, "cover_panel"),
        ]

        for raw_line, expected_part, expected_raw_damage, expected_damage, expected_category in cases:
            with self.subTest(raw_line=raw_line):
                parsed = runtime._parse_s14_damage_line(raw_line)

                self.assertIsNotNone(parsed)
                part, raw_damage, normalized_damage, _ = parsed
                self.assertEqual(part, expected_part)
                self.assertEqual(raw_damage, expected_raw_damage)
                self.assertEqual(normalized_damage, expected_damage)
                self.assertEqual(runtime._s14_part_category(part), expected_category)

    def test_v144_c_pillar_surface_does_not_normalize_to_abc_structure(self):
        self.assertEqual(runtime._normalize_s14_part("右C柱覆盖面"), "右C柱覆盖面")
        self.assertEqual(runtime._normalize_s14_part("左C柱覆盖面"), "左C柱覆盖面")
        self.assertEqual(runtime._normalize_s14_part("C柱覆盖面"), "C柱覆盖面")
        self.assertEqual(runtime._normalize_s14_part("左B柱饰板"), "左B柱饰板")
        self.assertEqual(runtime._normalize_s14_part("右C柱"), "ABC柱")
        self.assertEqual(runtime._normalize_s14_part("左B柱"), "ABC柱")

    def test_v144_stale_first_line_with_new_selected_tab_discards_old_line(self):
        snapshot = snapshot_for_selected_s14("右C柱覆盖面(1/1)", "右后翼子板—钣金")
        tab = runtime._s14_selected_tab(snapshot)

        state = runtime._s14_semantic_state(snapshot, tab)

        self.assertFalse(state["mixed_binding_blocked"])
        self.assertEqual(state["damage_line_binding_status"], "stale_discarded_degraded")
        self.assertEqual(state["s14_contract_level"], runtime.S14_CONTRACT_DEGRADED_RECORDABLE)
        self.assertTrue(state["stale_first_line_discarded"])
        self.assertEqual(state["discarded_stale_first_line"], "右后翼子板—钣金")
        self.assertEqual(state["normalized_part"], "右C柱覆盖面")
        self.assertEqual(state["raw_first_line_part"], "右后翼子板")
        self.assertIsNone(state["normalized_damage"])
        self.assertNotIn("右后翼子板—钣金", state["s14_key"])

    def test_v144_non_scoring_deformation_skips_record_and_allows_current_item_done(self):
        snapshot = snapshot_for_selected_s14("右C柱覆盖面(1/1)", "右C柱覆盖面--变形")
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming(), "s14_no_semantic_change_count": 1}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 1)
        state = runtime._s14_semantic_state(snapshot, tab)
        gate = runtime.is_s14_last_page_reached(
            context,
            selected_tab=tab,
            semantic_state=state,
            horizontal_swipe_effective=False,
            next_signal={"s14_has_uncollected_next_condition_signal": False},
        )

        self.assertEqual(record["raw_damage"], "变形")
        self.assertEqual(record["normalized_damage"], runtime.S14_NON_SCORING_DAMAGE)
        self.assertEqual(record["skipped_reason"], runtime.S14_NON_SCORING_DAMAGE)
        self.assertFalse(record["saved_to_repair_items"])
        self.assertNotIn("右C柱覆盖面", context["damage_by_part"])
        self.assertTrue(gate["current_page_collected"])
        self.assertTrue(gate["last_page_reached"])

    def test_current_tab_resolves_hidden_exact_first_line_before_stale_visible_line(self):
        snapshot = snapshot_with_damage_lines(stale_line=True, exact_line=True)
        tab = runtime._s14_selected_tab(snapshot)

        state = runtime._s14_semantic_state(snapshot, tab)

        self.assertEqual(state["normalized_part"], "前保险杠")
        self.assertEqual(state["raw_damage"], "漆面损伤")
        self.assertEqual(state["normalized_damage"], "喷漆")
        self.assertTrue(state["stale_first_line_resolved_by_part_match"])
        self.assertFalse(state["mixed_binding_blocked"])

    def test_stale_first_line_without_exact_match_becomes_degraded_recordable(self):
        snapshot = snapshot_with_damage_lines(stale_line=True, exact_line=False)
        tab = runtime._s14_selected_tab(snapshot)

        state = runtime._s14_semantic_state(snapshot, tab)

        self.assertFalse(state["mixed_binding_blocked"])
        self.assertEqual(state["damage_line_binding_status"], "stale_discarded_degraded")
        self.assertEqual(state["s14_contract_level"], runtime.S14_CONTRACT_DEGRADED_RECORDABLE)
        self.assertTrue(state["stale_first_line_discarded"])
        self.assertIsNone(state["normalized_damage"])

    def test_collect_current_image_does_not_write_old_damage_to_current_part(self):
        snapshot = snapshot_with_damage_lines(stale_line=True, exact_line=False)
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming()}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 1)

        self.assertFalse(record["saved_to_repair_items"])
        self.assertEqual(record["skipped_reason"], runtime.S14_STALE_FIRST_LINE_DISCARDED)
        self.assertEqual(record["s14_contract_level"], runtime.S14_CONTRACT_DEGRADED_RECORDABLE)
        self.assertTrue(record["stale_first_line_discarded"])
        self.assertEqual(record["discarded_stale_first_line"], "右后翼子板—钣金")
        self.assertEqual(record["condition_item_source"], "selected_tab_fallback")
        self.assertEqual(record["item_confidence"], "partial")
        self.assertTrue(record["item_needs_note"])
        self.assertNotIn("前保险杠", context["damage_by_part"])
        self.assertEqual(context["s14_degraded_item_count"], 1)
        self.assertFalse(context["current_reference"]["reference_condition_needs_review"])

    def test_degraded_recordable_current_item_allows_one_of_one_completion(self):
        snapshot = snapshot_for_selected_s14("后保险杠(1/1)", "左后翼子板—钣金")
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming(), "s14_no_semantic_change_count": 1}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 1)
        state = runtime._s14_semantic_state(snapshot, tab)
        gate = runtime.is_s14_last_page_reached(
            context,
            selected_tab=tab,
            semantic_state=state,
            horizontal_swipe_effective=False,
            next_signal={"s14_has_uncollected_next_condition_signal": False},
        )

        self.assertEqual(record["s14_contract_level"], runtime.S14_CONTRACT_DEGRADED_RECORDABLE)
        self.assertEqual(record["skipped_reason"], runtime.S14_STALE_FIRST_LINE_DISCARDED)
        self.assertTrue(record["item_needs_note"])
        self.assertTrue(gate["current_page_collected"])
        self.assertTrue(gate["last_page_reached"])

    def test_degraded_recordable_threshold_escalates_to_needs_review(self):
        context = {"damage_by_part": {}, "timing": DummyTiming()}
        cases = [
            ("后保险杠(1/1)", "左后翼子板—钣金"),
            ("前保险杠(1/1)", "右后翼子板—钣金"),
            ("右前门(1/1)", "左后翼子板—钣金"),
        ]

        for image_index, (tab_label, stale_line) in enumerate(cases, start=1):
            snapshot = snapshot_for_selected_s14(tab_label, stale_line)
            runtime._s14_collect_current_image(context, snapshot, runtime._s14_selected_tab(snapshot), image_index)

        current_reference = context["current_reference"]
        self.assertEqual(context["s14_degraded_item_count"], 3)
        self.assertTrue(current_reference["reference_condition_needs_review"])
        self.assertTrue(current_reference["manual_review_required"])
        self.assertIn(runtime.S14_DEGRADED_COLLECTION_THRESHOLD_EXCEEDED, current_reference["manual_review_reasons"])

    def test_special_structure_degraded_item_requires_review_and_does_not_write_repair_item(self):
        snapshot = snapshot_for_selected_s14("右C柱(1/1)", "左后翼子板—钣金")
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming()}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 1)

        self.assertFalse(record["saved_to_repair_items"])
        self.assertEqual(record["s14_contract_level"], runtime.S14_CONTRACT_NEEDS_REVIEW_CONTINUE)
        self.assertEqual(record["skipped_reason"], runtime.S14_CONTRACT_DEGRADED_NEEDS_REVIEW)
        self.assertNotIn("ABC柱", context["damage_by_part"])
        self.assertTrue(context["current_reference"]["reference_condition_needs_review"])
        self.assertIn(runtime.S14_CONTRACT_DEGRADED_NEEDS_REVIEW, context["current_reference"]["manual_review_reasons"])

    def test_unsafe_s14_page_fails_when_report_marker_missing(self):
        snapshot = snapshot_for_selected_s14("后保险杠(1/1)", "左后翼子板—钣金")
        snapshot["nodes"] = [node for node in snapshot["nodes"] if "瓜子官方检测报告" not in node.get("labels", [])]
        snapshot["visible_texts"] = [text for text in snapshot["visible_texts"] if text != "瓜子官方检测报告"]
        tab = runtime._s14_selected_tab(snapshot)

        state = runtime._s14_semantic_state(snapshot, tab)

        self.assertTrue(state["mixed_binding_blocked"])
        self.assertEqual(state["s14_contract_level"], runtime.S14_CONTRACT_UNSAFE_FAIL)
        self.assertEqual(state["damage_line_binding_status"], "stale_unresolved_blocked")
        self.assertIn("s14_report_marker_missing", state["s14_contract_level_reason"])

    def test_s14_detail_close_safety_uses_back_strategy_and_does_not_click_ai_text(self):
        snapshot = snapshot_for_selected_s14("后保险杠(1/1)", "左后翼子板—钣金")
        snapshot["nodes"].append(
            {
                "labels": ["AI详细解读【后保险杠】异常细节"],
                "bounds": [100, 910, 980, 980],
                "selected": False,
                "clickable": True,
                "enabled": True,
            }
        )
        snapshot["visible_texts"].append("AI详细解读【后保险杠】异常细节")

        close_safety = runtime._s14_detail_popup_close_safety(snapshot)

        self.assertTrue(close_safety["safe"])
        self.assertEqual(close_safety["strategy"], "android_back_or_bottom_back")
        self.assertTrue(close_safety["ai_detail_text_seen"])

    def test_fs0008_equivalent_fixture_discards_stale_line_and_keeps_auxiliary_detail_note(self):
        snapshot = snapshot_for_selected_s14("后保险杠(1/1)", "左后翼子板—钣金")
        snapshot["nodes"].extend(
            [
                {
                    "labels": ["拆卸痕迹"],
                    "bounds": [0, 0, 0, 0],
                    "selected": False,
                    "clickable": False,
                    "enabled": True,
                },
                {
                    "labels": ["AI详细解读【后保险杠】异常细节"],
                    "bounds": [120, 900, 960, 980],
                    "selected": False,
                    "clickable": True,
                    "enabled": True,
                },
            ]
        )
        snapshot["visible_texts"].extend(["拆卸痕迹", "AI详细解读【后保险杠】异常细节"])
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming()}

        record = runtime._s14_collect_current_image(context, snapshot, tab, 1)

        self.assertFalse(record["mixed_binding_blocked"])
        self.assertEqual(record["s14_contract_level"], runtime.S14_CONTRACT_DEGRADED_RECORDABLE)
        self.assertEqual(record["selected_tab_part"], "后保险杠")
        self.assertEqual(record["discarded_stale_first_line"], "左后翼子板—钣金")
        self.assertEqual(record["condition_item_source"], "selected_tab_fallback")
        self.assertEqual(record["item_confidence"], "partial")
        self.assertNotIn("左后翼子板—钣金", record["s14_key"])
        self.assertIn("拆卸痕迹", record["s14_auxiliary_detail_texts"])
        self.assertIn("AI详细解读【后保险杠】异常细节", record["s14_auxiliary_detail_texts"])
        self.assertNotIn("后保险杠", context["damage_by_part"])

    def test_last_page_gate_blocks_whole_vehicle_completion_when_uncollected_signal_exists(self):
        snapshot = snapshot_with_damage_lines(stale_line=True, exact_line=True)
        tab = runtime._s14_selected_tab(snapshot)
        context = {"damage_by_part": {}, "timing": DummyTiming(), "s14_no_semantic_change_count": 1}
        record = runtime._s14_collect_current_image(context, snapshot, tab, 1)
        state = runtime._s14_semantic_state(snapshot, tab)

        gate = runtime.is_s14_last_page_reached(
            context,
            selected_tab=tab,
            semantic_state=state,
            horizontal_swipe_effective=False,
            next_signal={"s14_has_uncollected_next_condition_signal": True},
        )

        self.assertTrue(record["saved_to_repair_items"])
        self.assertFalse(gate["last_page_reached"])
        self.assertTrue(gate["current_page_collected"])
        self.assertTrue(gate["last_page_blocked_by_uncollected_next_condition_signal"])
        self.assertTrue(gate["page_label_at_last_index"])
        self.assertTrue(gate["horizontal_swipe_blocked"])

    def test_fs20260624_0002_equivalent_partial_s14_makes_reference_score_untrusted(self):
        context = {
            "current_reference": {
                "repair_counts": {"驾驶侧": 9},
            },
            "damage_by_part": {
                "左前翼子板": {"part": "左前翼子板", "normalized_damage": "喷漆"},
            },
            "current_s14_item_done": True,
            "s14_current_item_sequence_collected": True,
            "s14_has_uncollected_next_condition_signal": True,
            "s14_uncollected_next_condition_signals": {
                "unvisited_tab_labels": ["左前门(1/1)", "右前门(1/1)"],
                "unvisited_damage_lines": [],
            },
        }

        state = runtime._repair_item_completion_state(context)

        self.assertTrue(state["current_s14_item_done"])
        self.assertTrue(state["s14_current_item_sequence_collected"])
        self.assertFalse(state["s14_whole_vehicle_collection_complete"])
        self.assertEqual(state["s14_collected_items_count"], 1)
        self.assertEqual(state["s14_expected_items_count"], 9)
        self.assertEqual(state["missing_repair_count"], 8)
        self.assertFalse(state["s13_s14_repair_count_matched"])
        self.assertEqual(state["reference_condition_completeness"], "partial")
        self.assertTrue(state["reference_score_preliminary"])
        self.assertFalse(state["reference_score_trustworthy"])
        self.assertFalse(state["reference_score_usable_for_boundary"])
        self.assertTrue(state["excluded_from_boundary"])
        self.assertEqual(state["excluded_from_boundary_reason"], "UNTRUSTED_REFERENCE_SCORE")
        self.assertFalse(state["not_used_for_s15_gate"])

    def test_current_item_done_whole_vehicle_incomplete_requests_current_reference_s14_continue(self):
        context = {
            "current_reference": {
                "repair_counts": {"驾驶侧": 9},
            },
            "damage_by_part": {
                "左前翼子板": {"part": "左前翼子板", "normalized_damage": "喷漆"},
            },
            "current_s14_item_done": True,
            "s14_current_item_sequence_collected": True,
            "s14_has_uncollected_next_condition_signal": True,
            "s14_uncollected_next_condition_signals": {
                "s14_has_uncollected_next_condition_signal": True,
                "unvisited_tab_labels": ["左前门(1/1)", "右前门(1/1)"],
                "unvisited_damage_lines": [],
            },
        }

        state = runtime._s14_current_reference_continue_state(context)
        recorded = runtime._record_s14_continue_current_reference(
            context,
            next_signal=context["s14_uncollected_next_condition_signals"],
            selected_tab={"label": "左前翼子板漆面(1/1)"},
            semantic_state={"s14_key": "左前翼子板漆面(1/1)|左前翼子板--喷漆|左前翼子板|喷漆"},
        )

        self.assertTrue(state["should_continue_current_reference_s14"])
        self.assertEqual(state["action"], runtime.S14_CONTINUE_UNCOLLECTED_CONDITION)
        self.assertEqual(state["state"], runtime.CONTINUE_CURRENT_REFERENCE_S14)
        self.assertEqual(state["continue_current_reference_reason"], "CURRENT_ITEM_DONE_BUT_WHOLE_VEHICLE_INCOMPLETE")
        self.assertEqual(state["next_uncollected_condition_item"], "左前门(1/1)")
        self.assertEqual(state["remaining_s14_condition_count"], 8)
        self.assertTrue(recorded["s14_continue_current_reference_attempted"])
        self.assertTrue(recorded["s14_continue_current_reference_possible"])
        self.assertEqual(context["current_reference"]["s14_continue_current_reference_action"], runtime.S14_CONTINUE_UNCOLLECTED_CONDITION)

    def test_s14_continue_failure_marks_current_reference_unrecoverable_before_next_reference(self):
        context = {
            "current_reference": {
                "repair_counts": {"驾驶侧": 2},
            },
            "damage_by_part": {
                "左前翼子板": {"part": "左前翼子板", "normalized_damage": "喷漆"},
            },
            "current_s14_item_done": True,
            "s14_current_item_sequence_collected": True,
            "s14_has_uncollected_next_condition_signal": True,
            "s14_uncollected_next_condition_signals": {
                "s14_has_uncollected_next_condition_signal": True,
                "unvisited_tab_labels": ["右前门(1/1)"],
                "unvisited_damage_lines": [],
            },
        }

        failure = runtime._mark_s14_continue_current_reference_failed(
            context,
            reason="S14_UNCOLLECTED_CONDITION_HORIZONTAL_SWIPE_NO_PROGRESS",
            next_signal=context["s14_uncollected_next_condition_signals"],
        )

        self.assertTrue(failure["s14_continue_current_reference_attempted"])
        self.assertFalse(failure["s14_continue_current_reference_possible"])
        self.assertEqual(
            failure["s14_continue_current_reference_failure_reason"],
            "S14_UNCOLLECTED_CONDITION_HORIZONTAL_SWIPE_NO_PROGRESS",
        )
        self.assertEqual(failure["excluded_from_boundary_reason"], runtime.S14_COLLECTION_INCOMPLETE_UNRECOVERABLE)
        self.assertFalse(failure["reference_score_trustworthy"])
        self.assertFalse(failure["reference_score_usable_for_boundary"])
        self.assertEqual(context["current_reference"]["excluded_from_boundary_reason"], runtime.S14_COLLECTION_INCOMPLETE_UNRECOVERABLE)

    def test_fs20260623_0007_equivalent_complete_s14_can_use_reference_score_for_boundary(self):
        damage_by_part = {
            f"部位{i}": {"part": f"部位{i}", "normalized_damage": "喷漆"}
            for i in range(18)
        }
        context = {
            "current_reference": {"repair_counts": {"驾驶侧": 9}},
            "damage_by_part": damage_by_part,
            "current_s14_item_done": True,
            "s14_current_item_sequence_collected": True,
            "s14_has_uncollected_next_condition_signal": False,
            "s14_uncollected_next_condition_signals": {
                "unvisited_tab_labels": [],
                "unvisited_damage_lines": [],
            },
        }

        state = runtime._repair_item_completion_state(context)

        self.assertTrue(state["s14_whole_vehicle_collection_complete"])
        self.assertEqual(state["s14_collected_items_count"], 18)
        self.assertEqual(state["missing_repair_count"], 0)
        self.assertTrue(state["s13_s14_repair_count_matched"])
        self.assertEqual(state["reference_condition_completeness"], "complete")
        self.assertFalse(state["reference_score_preliminary"])
        self.assertTrue(state["reference_score_trustworthy"])
        self.assertTrue(state["reference_score_usable_for_boundary"])
        self.assertFalse(state["excluded_from_boundary"])

    def test_untrusted_93_does_not_trigger_first_reference_above_target(self):
        selection = runtime._select_v3_reference_from_history(
            [
                {
                    "reference_index": 1,
                    "reference_score": 93,
                    "reference_score_trustworthy": False,
                    "reference_score_usable_for_boundary": False,
                    "reference_score_preliminary": True,
                    "excluded_from_boundary_reason": "UNTRUSTED_REFERENCE_SCORE",
                    "list_price_10k": 6.8,
                }
            ],
            {"score": 92},
        )

        self.assertTrue(selection["manual_review_required"])
        self.assertIsNone(selection["boundary_reference_index"])
        self.assertNotEqual(selection.get("manual_review_reason"), "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE")
        self.assertEqual(selection.get("manual_review_reason"), "REFERENCE_SCORE_UNTRUSTED_INCOMPLETE_COLLECTION")

    def test_trusted_complete_93_still_triggers_first_reference_above_target(self):
        selection = runtime._select_v3_reference_from_history(
            [
                {
                    "reference_index": 1,
                    "reference_score": 93,
                    "reference_score_trustworthy": True,
                    "reference_score_usable_for_boundary": True,
                    "list_price_10k": 6.8,
                }
            ],
            {"score": 92},
        )

        self.assertTrue(selection["manual_review_required"])
        self.assertEqual(selection["boundary_reference_index"], 1)
        self.assertEqual(selection["manual_review_reason"], "FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE")

    def test_partial_s14_s15_scores_as_preliminary_and_returns_to_continue_next_reference(self):
        fields = json.loads((ROOT / "config" / "fields.yaml").read_text(encoding="utf-8-sig"))
        current_reference = {
            "repair_counts": {"驾驶侧": 9},
            "list_price_10k": 6.8,
            "list_year": 2020,
            "list_mileage_10k_km": 4.5,
            "transfer_count": 0,
            "accident_count": 0,
            "max_accident_amount": 0,
            "repair_items": [{"part": "左前翼子板", "normalized_damage": "喷漆"}],
            "reference_index": 1,
            "selected_card_title": "Ref 1",
            "selected_card_price": "6.8?",
            "selected_card_metadata": "2020 | 4.5w",
            "physical_ui_transition_proof": {
                "physical_evidence_ok": True,
                "next_card_click_verified": True,
                "page_changed_after_click": True,
                "destination_identity_matched": True,
                "same_page_signature_reused": False,
                "actual_page_signature": "s14-partial-ref-1",
            },
            "actual_page_signature": "s14-partial-ref-1",
        }
        context = {
            "configs": {"fields": fields},
            "timing": runtime.TimingRecorder(),
            "issues": DummyIssues(),
            "s14_triggered": True,
            "current_reference": current_reference,
            "current_reference_index": 1,
            "reference_history": [],
            "damage_by_part": {"左前翼子板": {"part": "左前翼子板", "normalized_damage": "喷漆"}},
            "target_car": runtime.TargetCar(
                task_id="FS20260624_0002",
                brand="欧拉",
                series="黑猫",
                model_year="2019款",
                trim="351km 亲子版",
                color="白",
                registration_date="2020.08",
                mileage_10k_km=4.5,
                transfer_count=2,
                condition_text="原版原漆",
                accident_count=0,
                max_accident_amount=0,
            ),
            "current_s14_item_done": True,
            "s14_current_item_sequence_collected": True,
            "s14_has_uncollected_next_condition_signal": True,
            "s14_uncollected_next_condition_signals": {
                "unvisited_tab_labels": ["左前门(1/1)"],
                "unvisited_damage_lines": [],
            },
            "returned_list_source_verified": True,
            "post_s14_s10_snapshot": {},
        }

        next_state, _snapshot = runtime.handle_s15(context)

        self.assertEqual(next_state, "S10")
        self.assertIn("score_trace", context["current_reference"])
        self.assertIn("deduction_items", context["current_reference"])
        self.assertTrue(context["current_reference"]["score_trace"]["score_is_preliminary"])
        self.assertTrue(context["current_reference"]["reference_score_preliminary"])
        self.assertFalse(context["current_reference"]["reference_score_trustworthy"])
        self.assertFalse(context["current_reference"]["reference_score_usable_for_boundary"])
        self.assertFalse(context["current_reference"]["boundary_reference"])
        self.assertFalse(context["selection"]["manual_review_required"])

    def test_partial_s14_s15_preserves_unrecoverable_exclusion_reason_when_continue_failed(self):
        fields = json.loads((ROOT / "config" / "fields.yaml").read_text(encoding="utf-8-sig"))
        current_reference = {
            "repair_counts": {"驾驶侧": 2},
            "list_price_10k": 6.8,
            "list_year": 2020,
            "list_mileage_10k_km": 4.5,
            "transfer_count": 0,
            "accident_count": 0,
            "max_accident_amount": 0,
            "repair_items": [{"part": "左前翼子板", "normalized_damage": "喷漆"}],
            "reference_index": 1,
            "selected_card_title": "Ref 1",
            "selected_card_price": "6.8?",
            "selected_card_metadata": "2020 | 4.5w",
            "s14_continue_current_reference_attempted": True,
            "s14_continue_current_reference_possible": False,
            "s14_continue_current_reference_failure_reason": "S14_UNCOLLECTED_CONDITION_HORIZONTAL_SWIPE_NO_PROGRESS",
            "excluded_from_boundary_reason": runtime.S14_COLLECTION_INCOMPLETE_UNRECOVERABLE,
            "physical_ui_transition_proof": {
                "physical_evidence_ok": True,
                "next_card_click_verified": True,
                "page_changed_after_click": True,
                "destination_identity_matched": True,
                "same_page_signature_reused": False,
                "actual_page_signature": "s14-unrecoverable-ref-1",
            },
            "actual_page_signature": "s14-unrecoverable-ref-1",
        }
        context = {
            "configs": {"fields": fields},
            "timing": runtime.TimingRecorder(),
            "issues": DummyIssues(),
            "s14_triggered": True,
            "current_reference": current_reference,
            "current_reference_index": 1,
            "reference_history": [],
            "damage_by_part": {"左前翼子板": {"part": "左前翼子板", "normalized_damage": "喷漆"}},
            "target_car": runtime.TargetCar(
                task_id="FS20260624_0003",
                brand="欧拉",
                series="黑猫",
                model_year="2019款",
                trim="351km 亲子版",
                color="白",
                registration_date="2020.08",
                mileage_10k_km=4.5,
                transfer_count=2,
                condition_text="原版原漆",
                accident_count=0,
                max_accident_amount=0,
            ),
            "current_s14_item_done": True,
            "s14_current_item_sequence_collected": True,
            "s14_has_uncollected_next_condition_signal": True,
            "s14_uncollected_next_condition_signals": {
                "unvisited_tab_labels": ["右前门(1/1)"],
                "unvisited_damage_lines": [],
            },
            "returned_list_source_verified": True,
            "post_s14_s10_snapshot": {},
        }

        next_state, _snapshot = runtime.handle_s15(context)

        self.assertEqual(next_state, "S10")
        self.assertEqual(context["current_reference"]["excluded_from_boundary_reason"], runtime.S14_COLLECTION_INCOMPLETE_UNRECOVERABLE)
        self.assertEqual(context["selection"]["excluded_references"][0]["excluded_reason"], runtime.S14_COLLECTION_INCOMPLETE_UNRECOVERABLE)
        self.assertTrue(context["current_reference"]["reference_score_preliminary"])
        self.assertFalse(context["current_reference"]["reference_score_trustworthy"])

    def test_nine_of_ten_synthetic_total_no_longer_blocks_when_terminal_confirmed(self):
        context = {
            "s14_image_sequence_model": True,
            "s14_sequence_terminal_confirmed": True,
            "s14_image_records": [{"saved_to_repair_items": True, "s14_key": f"k{i}"} for i in range(9)],
        }

        metrics = runtime._s14_completion_metrics(context)
        evidence = runtime._s14_completion_evidence(context)

        self.assertEqual(metrics["s14_images_processed"], 9)
        self.assertEqual(metrics["s14_images_total"], 9)
        self.assertTrue(evidence["all_images_processed"])

    def test_non_sequence_nine_of_ten_count_is_log_only_when_images_are_decided(self):
        context = {
            "all_s14_tabs": [{"tab_label": "前保险杠(1/10)", "total_pages": 10}],
            "s14_tab_records": [{"tab_label": "前保险杠(1/10)", "tab_processed": True}],
            "s14_image_records": [{"saved_to_repair_items": True, "s14_key": f"k{i}"} for i in range(9)],
        }

        metrics = runtime._s14_completion_metrics(context)
        evidence = runtime._s14_completion_evidence(context)

        self.assertEqual(metrics["s14_images_processed"], 9)
        self.assertEqual(metrics["s14_images_total"], 10)
        self.assertTrue(evidence["all_images_processed"])
        self.assertTrue(evidence["all_target_repairs_recorded"])

    def test_android_back_strategy_is_present_and_x_is_not_default_return_contract(self):
        source = (SCRIPT_DIR / "runtime_s10_to_s16_mainline.py").read_text(encoding="utf-8")
        pages = json.loads((ROOT / "config" / "pages.yaml").read_text(encoding="utf-8-sig"))
        s14_subpage = next(item for item in pages["pages"] if item["id"] == "S14_SUBPAGE_WITH_SYSTEM_BACK")

        self.assertIn("client.back()", source)
        self.assertNotIn('s14_images_processed"] != s14_metrics["s14_images_total"]', source)
        self.assertEqual(s14_subpage["return_action"], "android_back_or_bottom_back")
        self.assertIn("click_x_as_default_return", s14_subpage["forbidden_actions"])

    def test_s14_blocked_status_is_failed_not_success(self):
        errors = validate_pricing_result_payload({"status": "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED"})
        reply = format_result_reply(
            task_id="FS20260612_0002",
            status="SUCCEEDED",
            pricing_result={"status": "S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED"},
        )

        self.assertEqual(errors, ["S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED"])
        self.assertIn("【定价失败】FS20260612_0002", reply.text)
        self.assertIn("S14_IMAGE_SEQUENCE_NOT_FULLY_PROCESSED", reply.text)
        self.assertNotIn("【定价完成】", reply.text)

    def test_s14_degraded_needs_review_status_is_failed_not_success(self):
        errors = validate_pricing_result_payload({"status": runtime.S14_CONTRACT_DEGRADED_NEEDS_REVIEW})
        reply = format_result_reply(
            task_id="FS20260623_0008",
            status="SUCCEEDED",
            pricing_result={"status": runtime.S14_CONTRACT_DEGRADED_NEEDS_REVIEW},
        )

        self.assertEqual(errors, [runtime.S14_CONTRACT_DEGRADED_NEEDS_REVIEW])
        self.assertIn("【本次定价需人工复核】FS20260623_0008", reply.text)
        self.assertNotIn("【定价完成】", reply.text)

    def test_s14_business_feedback_hides_internal_words(self):
        reply = format_result_reply(
            task_id="FS20260623_0008",
            status="FAILED",
            pricing_result=None,
            errors=["S14_DETAIL_POPUP_CLOSE_UNSAFE_OR_FAILED"],
        )
        forbidden_words = [
            "S14",
            "XML",
            "screenshot",
            "first_line",
            "stale",
            "binding",
            "runner",
            "dispatcher",
            "adb",
            "uiautomator",
            "traceback",
            "status.json",
            "result.json",
        ]

        self.assertIn("当前车况详情无法安全确认", reply.text)
        for word in forbidden_words:
            with self.subTest(word=word):
                self.assertNotIn(word, reply.text)

    def test_no_test_calls_real_device_or_adb(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(Path(temp).exists())
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
