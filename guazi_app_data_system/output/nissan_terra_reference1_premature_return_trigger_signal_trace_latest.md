# Nissan Terra Reference #1 Premature Return Trigger Signal Trace

## Conclusion

Final classification: **S14_COLLECT_DONE_AS_REFERENCE_COLLECT_DONE_MISUSED_NO_EARLY_SCORE_PRUNING**.

No executable early-score pruning path was found. The observed return/continue was triggered after a single S14 repair item sequence set `S14_COLLECT_DONE=true`, then S15 accepted the reference as complete, computed `reference_score=78.0`, and only then applied `reference_score < target_score` to continue to reference #2.

The 78.0 score is **not trustworthy** because repair details were incomplete before S15: S13 reported 驾驶侧历史修复次数=10, but only one concrete repair item, 左前翼子板, was collected.

## Trigger Timeline

- 1. S13 parsed history repair count: page=S13, action=read_s13_region_history_repair_count, trigger=raw XML nodes + bounds local binding associated 历史修复 with count 10
- 2. S13 clicked one concrete repair item: page=S13, action=S13_ONLY_ALLOWED_ACTION_CLICK_REPAIR_ITEM, trigger=Only one concrete repair item was selected under 驾驶侧.
- 3. S14 current item image sequence completed: page=S14, action=collect_image_sequence_until_terminal, trigger=S14 image sequence for 左前翼子板 produced 3 records and terminal snapshot evidence.
- 4. S14 returned to reliable S10: page=S14/S10, action=fixed_return_to_s10_stop_on_first_s10, trigger=After S14_COLLECT_DONE, fixed return routine captured reliable S10 snapshot.
- 5. S15 allowed and reference score computed: page=S15, action=score_target_and_reference, trigger=handle_s15 accepted s14_collect_done=true, S14 image metrics complete, repair_items key present, and returned_list_source_verified=true.
- 6. Score gate continued to next reference: page=S15/S10, action=CONTINUE_NEXT_REFERENCE, trigger=reference_score=78.0 < target_score=92.0 after S15 scoring.

## Key Signals

- expected_repair_item_count: 10
- enumerated_repair_item_count: 1
- collected_repair_item_count: 1
- S14_COLLECT_DONE: true
- all_repair_items_collect_done: false
- s15_entry_allowed: true

## Code Evidence

- scripts/runtime_s10_to_s16_mainline.py:3312-3329 - _s14_completion_evidence() computes all_target_repairs_recorded from current s14_image_records only; it does not compare against S13 total repair count.
- scripts/runtime_s10_to_s16_mainline.py:7847-7857 - After current S14 image sequence evidence passes, context.s14_collect_done and current_reference.s14_collect_done are set true; repair_items is set from damage_by_part.
- scripts/runtime_s10_to_s16_mainline.py:8143-8181 - handle_s15 blocks only when s14_collect_done false, S14 image metrics incomplete, or repair_items missing. It does not require collected_repair_item_count >= expected_repair_item_count.
- scripts/runtime_s10_to_s16_mainline.py:8220-8244 - Reference score is computed after S15 entry using available panel_repairs; reference_score_gte_target_score is set after scoring.
- scripts/runtime_s10_to_s16_mainline.py:8575-8580 - CONTINUE_NEXT_REFERENCE is recorded only after handle_s15 returns S10 because selected score is below target.

## Recommendation

Next patch: **PATCH_S13_S14_ALL_REPAIR_ITEMS_REQUIRED_BEFORE_S15**. S15 must require whole-reference repair-detail completion, not just the current S14 item sequence.

## Read-Only Confirmation

No code was modified, no device run was started, no reference was collected, no pricing was rerun, and result JSON was not overwritten.

Final status: **NISSAN_TERRA_REFERENCE1_PREMATURE_RETURN_TRIGGER_SIGNAL_TRACE_DONE**
